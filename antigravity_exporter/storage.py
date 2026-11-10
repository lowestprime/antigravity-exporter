"""Bounded capture, read-only SQLite snapshots, safe ZIPs, and fresh publication."""
from __future__ import annotations
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tempfile
import time
import uuid
import zipfile

CHUNK = 1024 * 1024
VARIANTS = ('antigravity', 'antigravity-ide', 'antigravity-cli')
UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)

class ExportError(RuntimeError):
    pass

def hash_file(path: Path, *, progress=None, stage='checksum') -> str:
    h = hashlib.sha256(); size=path.stat().st_size; done=0
    if progress: progress.update(stage,0,size,detail=path.name,force=True)
    with path.open('rb') as f:
        for b in iter(lambda: f.read(CHUNK), b''):
            h.update(b); done+=len(b)
            if progress: progress.update(stage,done,size,detail=path.name)
    return h.hexdigest()

def write_json(path: Path, value) -> None:
    with path.open('w', encoding='utf-8', newline='\n') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')

def safe_name(name: str) -> str:
    """Portable ZIP member names. No extraction, traversal, device names or ADS."""
    if not name or '\\' in name or re.search(r'[<>:"|?*\x00-\x1f]', name):
        raise ExportError(f'Unsafe archive path: {name!r}')
    p = PurePosixPath(name)
    if p.is_absolute() or any(x in ('', '.', '..') for x in name.rstrip('/').split('/')):
        raise ExportError(f'Unsafe archive path: {name!r}')
    for part in p.parts:
        if part.endswith((' ', '.')) or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.I):
            raise ExportError(f'Windows-unsafe archive path: {name!r}')
    return p.as_posix()

def safe_inventory(z: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    entries, folded = {}, set()
    for i in z.infolist():
        n = safe_name(i.filename)
        if n.casefold() in folded:
            raise ExportError(f'Duplicate or case-colliding ZIP entry: {n}')
        folded.add(n.casefold())
        if i.flag_bits & 1:
            raise ExportError(f'Encrypted ZIP entry is not supported: {n}')
        mode = (i.external_attr >> 16) & 0xffff
        if stat.S_ISLNK(mode):
            raise ExportError(f'ZIP symlink is not supported: {n}')
        if not i.is_dir():
            entries[n] = i
    return entries

def _identity(s):
    return (s.st_dev, s.st_ino)

def open_shared_read(path: Path):
    """Read-only handle that does not block another process's rename on Windows.

    FILE_SHARE_DELETE permits the owner to rotate its file; it grants no delete
    access to this exporter. The existing handle/path identity checks still
    reject an observed replacement. POSIX keeps normal read-only open semantics.
    """
    if os.name != 'nt':
        return path.open('rb')
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x08000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    try:
        return os.fdopen(descriptor, 'rb')
    except BaseException:
        os.close(descriptor)
        raise

def capture_file(source: Path, destination: Path, *, allow_growth=False, progress=None) -> dict:
    """Capture a fixed handle prefix, verify by a second pass, reject observed rewrite.

    This is not an atomic snapshot of arbitrary in-place writers. The two passes
    plus identity/mtime checks detect observed changes; later appends are deferred.
    """
    if source.is_symlink():
        raise ExportError(f'Refusing a symlink source: {source}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    before_path = source.stat()
    start = time.monotonic()
    with open_shared_read(source) as incoming:
        initial = os.fstat(incoming.fileno())
        if not stat.S_ISREG(initial.st_mode) or _identity(initial) != _identity(before_path):
            raise ExportError('Source identity changed before capture')
        total = initial.st_size
        h = hashlib.sha256()
        count = 0
        with destination.open('xb') as outgoing:
            while count < total:
                b = incoming.read(min(CHUNK, total - count))
                if not b:
                    raise ExportError('Source was truncated during capture')
                outgoing.write(b); h.update(b); count += len(b)
                if progress: progress.update('capture', count, total)
            outgoing.flush(); os.fsync(outgoing.fileno())
        incoming.seek(0)
        verify = hashlib.sha256(); n = 0
        while n < total:
            b = incoming.read(min(CHUNK, total - n))
            if not b: raise ExportError('Source was truncated during prefix verification')
            verify.update(b); n += len(b)
            if progress: progress.update('verify-source-prefix', n, total)
        ending = os.fstat(incoming.fileno())
        try: after_path = source.stat()
        except FileNotFoundError as exc: raise ExportError('Source pathname disappeared during capture') from exc
        if (_identity(initial) != _identity(ending) or _identity(before_path) != _identity(after_path)
                or ending.st_size < total or after_path.st_size < total or verify.digest() != h.digest()):
            raise ExportError('Source was replaced, truncated, or its captured prefix changed')
        if not allow_growth and (ending.st_size != total or ending.st_mtime_ns != initial.st_mtime_ns):
            raise ExportError('Non-log source changed during capture; retry after it is idle')
        if ending.st_size == total and ending.st_mtime_ns != initial.st_mtime_ns:
            raise ExportError('Source modification time changed without an append; retry')
    return {'bytes': total, 'sha256': h.hexdigest(), 'captured_prefix_verified': True,
            'later_bytes_observed': max(0, ending.st_size - total),
            'atomic_multifile_snapshot': False, 'elapsed_seconds': round(time.monotonic()-start, 6)}

def backup_database(path: Path, dest: Path, *, deadline_seconds=30, progress=None) -> dict:
    """SQLite backup incorporates committed WAL state. Never checkpoints source."""
    if path.is_symlink(): raise ExportError(f'Symlink database rejected: {path}')
    initial = path.stat()
    start = time.monotonic()
    def tick(status, remaining, total):
        if time.monotonic()-start > deadline_seconds:
            raise ExportError(f'SQLite snapshot exceeded {deadline_seconds}s; retry when idle')
        if progress: progress.update('sqlite-backup', total-remaining, total, unit='pages')
    src = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=2)
    dst = None
    try:
        src.execute('PRAGMA query_only=ON')
        src.execute('PRAGMA trusted_schema=OFF')
        dst = sqlite3.connect(dest)
        src.backup(dst, pages=256, progress=tick, sleep=0.05)
        dst.commit()
    finally:
        if dst: dst.close()
        src.close()
    if _identity(path.stat()) != _identity(initial):
        raise ExportError('Database pathname was replaced during snapshot')
    return {'bytes': dest.stat().st_size, 'sha256': hash_file(dest),
            'capture_method': 'sqlite-online-backup', 'wal_commits_included': True,
            'atomic_with_transcript': False}

class Source:
    """Directory (.gemini or variant) or immutable archive, never extractall()."""
    def __init__(self, root=None, archive=None, stage: Path | None=None, progress=None):
        self.stage = stage or Path(tempfile.mkdtemp(prefix='agy-source-'))
        self.owns_stage = stage is None
        self.stage.mkdir(parents=True, exist_ok=True)
        self.progress = progress
        self.cache: dict[str, tuple[Path, dict]] = {}
        self.z = None; self.entries = {}; self.original_archive = None
        self.variant_hint = None
        if archive:
            self.original_archive = Path(archive).expanduser().resolve()
            self._archive_stat = self.original_archive.stat()
            self.z = zipfile.ZipFile(self.original_archive)
            inventory = safe_inventory(self.z)
            for name, entry in inventory.items():
                rel = name.removeprefix('.gemini/')
                if rel in self.entries: raise ExportError('Ambiguous normalized ZIP paths')
                self.entries[rel] = entry
            self.root = None
        else:
            p = Path(root or Path.home()/'.gemini').expanduser().resolve()
            if p.name in VARIANTS:
                self.variant_hint = p.name; p = p.parent
            if not p.is_dir(): raise ExportError(f'Source root does not exist: {p}')
            self.root = p
    def close(self):
        if self.z: self.z.close()
        if self.owns_stage: shutil.rmtree(self.stage, ignore_errors=True)
    def __enter__(self): return self
    def __exit__(self, *exc): self.close()
    def path(self, relative):
        safe_name(relative)
        if self.root is None: raise ExportError('ZIP has no live source path')
        candidate = self.root/relative
        resolved = candidate.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ExportError('Source link escapes the selected root')
        # Also reject in-root links: preservation must not dereference arbitrary files.
        q = candidate
        while q != self.root:
            if q.is_symlink() or getattr(q, 'is_junction', lambda: False)():
                raise ExportError(f'Source link or junction rejected: {q}')
            q = q.parent
        return candidate
    def exists(self, relative):
        return relative in self.entries if self.z else self.path(relative).is_file()
    def files(self, prefix):
        if self.z: return sorted(n for n in self.entries if n.startswith(prefix))
        base = self.path(prefix.rstrip('/'))
        if not base.exists(): return []
        if base.is_file(): return [prefix.rstrip('/')]
        found = []
        for directory, dirs, files in os.walk(base, followlinks=False):
            for name in dirs:
                p = Path(directory)/name
                if p.is_symlink() or getattr(p, 'is_junction', lambda:False)():
                    raise ExportError(f'Source directory link rejected: {p}')
            for name in files:
                p = Path(directory)/name
                self.path(p.relative_to(self.root).as_posix())
                found.append(p.relative_to(self.root).as_posix())
        return sorted(found)
    def variants(self):
        if self.variant_hint: return [self.variant_hint]
        if self.z: return [v for v in VARIANTS if any(n.startswith(v+'/') for n in self.entries)]
        return [v for v in VARIANTS if (self.root/v).is_dir()]
    def read_small(self, relative, limit=4*1024*1024):
        if not self.exists(relative): return None
        size = self.entries[relative].file_size if self.z else self.path(relative).stat().st_size
        if size > limit: raise ExportError(f'Metadata file exceeds {limit} bytes: {relative}')
        if self.z:
            return self.z.read(self.entries[relative])
        return self.path(relative).read_bytes()
    def snapshot(self, relative, *, database=False, growth=False):
        if relative in self.cache: return self.cache[relative]
        if not self.exists(relative): raise ExportError(f'Source file missing: {relative}')
        safe_name(relative)
        dest = self.stage/('snapshot-'+uuid.uuid4().hex + ('.db' if database else '.bin'))
        if self.z:
            entry = self.entries[relative]
            # ZIP members are frozen by this archive; CRC checked during full decoding.
            if database:
                dbdir = self.stage/('db-'+uuid.uuid4().hex); dbdir.mkdir()
                rawdb = dbdir/'source.db'
                self._zip_copy(entry, rawdb)
                wal = relative+'-wal'
                if wal in self.entries:
                    self._zip_copy(self.entries[wal], dbdir/'source.db-wal')
                    info = backup_database(rawdb, dest, progress=self.progress)
                    info['archive_wal_applied'] = True
                else:
                    # Preserve archived SQLite bytes exactly if no WAL needs replay.
                    shutil.copyfile(rawdb, dest)
                    info = {'bytes': dest.stat().st_size, 'sha256': hash_file(dest),
                            'capture_method': 'archived-database-bytes', 'archive_wal_applied': False}
            else:
                info = self._zip_copy(entry, dest)
            end = self.original_archive.stat()
            if (_identity(end) != _identity(self._archive_stat) or end.st_size != self._archive_stat.st_size
                    or end.st_mtime_ns != self._archive_stat.st_mtime_ns):
                raise ExportError('Input ZIP changed during export')
        elif database:
            info = backup_database(self.path(relative), dest, progress=self.progress)
        else:
            info = capture_file(self.path(relative), dest, allow_growth=growth, progress=self.progress)
        info['relative_source'] = relative
        self.cache[relative] = (dest, info)
        return dest, info
    def _zip_copy(self, entry, dest):
        h = hashlib.sha256(); n=0
        with self.z.open(entry) as incoming, dest.open('xb') as outgoing:
            for block in iter(lambda: incoming.read(CHUNK), b''):
                outgoing.write(block); h.update(block); n += len(block)
                if self.progress: self.progress.update('decode-source-zip', n, entry.file_size)
        if n != entry.file_size: raise ExportError('ZIP decoded size mismatch')
        return {'bytes': n, 'sha256': h.hexdigest(), 'capture_method': 'zip-member-full-crc-checked'}

def publish_fresh(staged: Path, final: Path, *, progress=None) -> dict:
    """Exclusive fresh destination inherits its parent ACL; never moves private temp."""
    if final.exists(): raise ExportError(f'Refusing to overwrite output: {final}')
    expected = hash_file(staged,progress=progress,stage='publication-source-checksum'); size=staged.stat().st_size; count=0; created=False
    if progress: progress.update('publication-copy',0,size,detail=final.name,force=True)
    try:
        with staged.open('rb') as src, final.open('xb') as dst:
            created=True
            for block in iter(lambda: src.read(CHUNK), b''):
                dst.write(block); count += len(block)
                if progress: progress.update('publication-copy', count, size, detail=final.name)
            dst.flush(); os.fsync(dst.fileno())
        if count != size or hash_file(final,progress=progress,stage='publication-readback') != expected:
            raise ExportError('Published file verification failed')
    except BaseException:
        # Remove only this newly created incomplete file, not any preexisting path.
        # Exclusive open failure means no destination was created by this operation.
        if created and final.exists():
            with contextlib.suppress(OSError): final.unlink()
        raise
    return {'path': str(final), 'bytes': size, 'sha256': expected, 'readback_verified': True}
