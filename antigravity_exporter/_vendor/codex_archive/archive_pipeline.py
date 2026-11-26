"""Archive construction adapted from Codex Exporter v0.5.2; see third-party notices."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import hashlib
import json
import os
import shutil
import tempfile
import zipfile

def _hash_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            h.update(block)
            n += len(block)
    return h.hexdigest(), n


@dataclass(frozen=True)
class ArchiveSettings:
    copy_source: bool = True
    archive: bool = True
    cleanup: bool = True
    backend: str = 'auto'
    level: int = 9
    method: str = 'LZMA'
    dictionary: str = '256m'
    word_size: int = 64
    threads: int = 19
    archiver: str = ''
    review: bool = False
    review_chunk_bytes: int = 1_000_000
    max_bytes: int = 512_000_000
    work_dir: str = ''
    timeout: int = 3600

    def validate(self) -> None:
        from .bounded_archive import validate_max_bytes
        validate_max_bytes(self.max_bytes)
        if not isinstance(self.work_dir, (str, Path)):
            raise ValueError('archive work_dir must be a directory path or empty for the output volume')
        if not isinstance(self.review, bool):
            raise ValueError('review must be a boolean')
        if not 4096 <= self.review_chunk_bytes <= 1_500_000:
            raise ValueError("review_chunk_bytes must be 4096..1500000")
        if self.backend not in ('auto', 'python', '7zip'):
            raise ValueError('archive backend must be auto, python or 7zip')
        if self.backend == 'python' and self.method.upper() != 'DEFLATE':
            raise ValueError('Python archive backend supports Deflate; use auto or 7zip for LZMA')
        if not (0 <= self.level <= 9):
            raise ValueError('archive level must be 0..9')
        if self.method.upper() not in ('LZMA', 'DEFLATE'):
            raise ValueError('archive method must be LZMA or Deflate')
        if not (5 <= self.word_size <= 273):
            raise ValueError('archive word size must be 5..273')
        if not (1 <= self.threads <= 256):
            raise ValueError('archive threads must be 1..256')
        if not __import__('re').fullmatch(r'[1-9][0-9]*[kKmMgG]', self.dictionary):
            raise ValueError('archive dictionary must be like 256m')
        if not self.archive and self.cleanup:
            # Cleanup is intentionally ineffective when archiving is disabled.
            return


    def effective_backend(self) -> str:
        """Resolve the validated backend for preflight and packaging alike."""
        self.validate()
        if self.backend == 'auto':
            return '7zip' if self.method.upper() == 'LZMA' or self.archiver else 'python'
        return self.backend


def _file_signature(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _check_member_signatures(members: dict[str, Path], expected: dict[str, tuple]) -> None:
    """Retain loose files if a detected concurrent change invalidates verification."""
    for name, path in members.items():
        try:
            unchanged = _file_signature(path) == expected[name]
        except OSError:
            unchanged = False
        if not unchanged:
            raise RuntimeError(f'Exported member changed during packaging: {name}; no cleanup performed')


def _publish_archive(staged: Path, destination: Path, *, reporter=None) -> str:
    """Publish exclusively with the destination folder's Windows permissions."""
    from .file_access import publish_file
    return publish_file(staged, destination, reporter=reporter, stage_label='Publish archive')


def _write_python_archive(staged: Path, members: dict[str, Path], level: int, *, reporter=None) -> None:
    """ZIP method 8 with measured input-byte progress and bounded memory."""
    from .progress import ProgressReporter
    reporter = reporter or ProgressReporter(enabled=False)
    total = sum(path.stat().st_size for path in members.values())
    with reporter.stage('Compress archive', total=total, detail=f'Deflate level {level}; input bytes') as meter:
        with zipfile.ZipFile(staged, 'x', compression=zipfile.ZIP_DEFLATED,
                             compresslevel=level, allowZip64=True, strict_timestamps=False) as archive:
            for number, (name, path) in enumerate(members.items(), 1):
                info = zipfile.ZipInfo.from_file(path, arcname=name, strict_timestamps=False)
                info.compress_type = zipfile.ZIP_DEFLATED
                info._compresslevel = level
                meter.update(detail=f'{number}/{len(members)}: {name}')
                with path.open('rb') as source, archive.open(info, 'w', force_zip64=info.file_size >= zipfile.ZIP64_LIMIT) as item:
                    for block in iter(lambda: source.read(1024 * 1024), b''):
                        item.write(block)
                        meter.advance(len(block))


def find_archiver(explicit: str = '') -> Path:
    """Prefer installed compatible CLI backends; never assume a GUI executable is a CLI."""
    candidates: list[Path] = []
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            p2 = shutil.which(explicit)
            if p2:
                p = Path(p2)
        if not p.is_file():
            raise FileNotFoundError(f'7-Zip-compatible archiver not found: {explicit}')
        return p.resolve()
    for exe in ('7z', '7zz', '7za', '7z.exe', '7zz.exe', '7za.exe', '7zG.exe', 'NanaZipC.exe'):
        loc = shutil.which(exe)
        if loc and exe.lower() != '7zg.exe':
            candidates.append(Path(loc))
    program_files = [os.environ.get('ProgramFiles', ''), os.environ.get('ProgramW6432', ''),
                     os.environ.get('ProgramFiles(x86)', ''), os.environ.get('LOCALAPPDATA', '')]
    suffixes = ('7-Zip/7z.exe', '7-Zip-Zstandard/7z.exe', '7-Zip-ZS/7z.exe',
                'PeaZip/res/bin/7z/7z.exe', 'PeaZip/res/bin/7z/7z64/7z.exe',
                'Programs/PeaZip/res/bin/7z/7z.exe')
    for root in filter(None, program_files):
        candidates.extend(Path(root) / x for x in suffixes)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError('No 7-Zip-compatible CLI found. Supply --archiver-path PATH; '
                            'PeaZip/NanaZip GUI executables alone are not sufficient.')


def _verify_members(archive: Path, expected: dict[str, Path], method: str, *, reporter=None,
                    checksums: dict[str, tuple[str, int]] | None = None) -> dict[str, str]:
    """Independently verify full ZIP members, accepting only safe Store fallback.

    7-Zip can choose ZIP Store (method 0) for inputs that do not benefit from
    compression, even when -mm=LZMA is requested. Requiring method 14 for each
    member rejects valid archives with tiny manifest files. We still reject any
    unrequested compression algorithm, enforce the exact flat file inventory,
    and SHA-256-verify the fully decoded bytes against each loose source file.
    """
    from .progress import ProgressReporter
    reporter = reporter or ProgressReporter(enabled=False)
    requested = zipfile.ZIP_LZMA if method.upper() == 'LZMA' else zipfile.ZIP_DEFLATED
    allowed = {requested, zipfile.ZIP_STORED}
    methods = {zipfile.ZIP_STORED: 'Store', zipfile.ZIP_LZMA: 'LZMA',
               zipfile.ZIP_DEFLATED: 'Deflate'}
    with zipfile.ZipFile(archive, 'r', allowZip64=True) as z:
        infos = z.infolist()
        if (len(infos) != len(expected) or len({i.filename for i in infos}) != len(infos)
                or {i.filename for i in infos} != set(expected)):
            raise RuntimeError('ZIP member inventory mismatch: expected only distinct safe export files')
        from .bounded_archive import _safe_name
        if any(i.is_dir() or not _safe_name(i.filename) or
               i.flag_bits & 1 or i.compress_type not in allowed for i in infos):
            raise RuntimeError('ZIP member path, encryption or compression method mismatch')
        # The three tiny inputs in the synthetic probe can all be stored. For
        # substantive exports, if *none* use the requested method, reject the
        # archive rather than silently call a full Store ZIP an LZMA ZIP.
        if (all(i.compress_type == zipfile.ZIP_STORED for i in infos)
                and sum(i.file_size for i in infos) >= 64 * 1024):
            raise RuntimeError('Substantive ZIP uses Store for every member; '
                               'requested compression method was not demonstrated')
        total = sum(info.file_size for info in infos) * 2
        with reporter.stage('Verify archive I/O (two passes)', total=total,
                            detail='Processed bytes count ZIP decoding plus source reads; not export size') as meter:
            for number, info in enumerate(infos, 1):
                digest = hashlib.sha256()
                size = 0
                meter.update(detail=f'{number}/{len(infos)}: decode {info.filename}')
                with z.open(info, 'r') as entry:
                    for block in iter(lambda: entry.read(1024 * 1024), b''):
                        digest.update(block)
                        size += len(block)
                        meter.advance(len(block))
                original = hashlib.sha256()
                original_size = 0
                meter.update(detail=f'{number}/{len(infos)}: compare {info.filename}')
                with expected[info.filename].open('rb') as source:
                    for block in iter(lambda: source.read(1024 * 1024), b''):
                        original.update(block)
                        original_size += len(block)
                        meter.advance(len(block))
                if digest.hexdigest() != original.hexdigest() or size != original_size:
                    raise RuntimeError(f'Archive content mismatch: {info.filename}')
                if checksums is not None:
                    checksums[info.filename] = (digest.hexdigest(), size)
        return {info.filename: methods[info.compress_type] for info in infos}


def _compress_archive(staged: Path, members: dict[str, Path], settings: ArchiveSettings, *,
                      backend: str, archiver: Path | None, reporter=None,
                      max_bytes: int | None = None) -> None:
    """Create a private flat ZIP with the exact requested codec configuration."""
    from .progress import ProgressReporter, run_seven_zip, SEVEN_ZIP_PROGRESS_ARGS
    from .archive_stream import RangeTooLarge
    reporter = reporter or ProgressReporter(enabled=False)
    if backend == 'python':
        _write_python_archive(staged, members, settings.level, reporter=reporter)
        return
    root = None
    for name, path in members.items():
        candidate = Path(path)
        for _ in Path(name).parts:
            candidate = candidate.parent
        if root is None:
            root = candidate
        if candidate != root or root / name != path:
            raise ValueError('External archiver inputs must have their exact names in one private tree')
    if root is None:
        raise ValueError('Cannot compress an empty member set')
    command = [str(archiver), 'a', '-tzip', '-mx=' + str(settings.level),
               '-mm=' + settings.method, '-mmt=' + str(settings.threads),
               '-y', *SEVEN_ZIP_PROGRESS_ARGS]
    if settings.method.upper() == 'LZMA':
        command.extend(['-md=' + settings.dictionary, '-mfb=' + str(settings.word_size)])
    listing = staged.with_name(staged.name + '.members.txt')
    with listing.open('x', encoding='utf-8', newline='\n') as stream:
        for name in sorted(members):
            stream.write(name + '\n')
    command.extend(['-scsUTF-8', str(staged.resolve()), '@' + str(listing.resolve())])
    with reporter.stage('Compress archive', unit='percent',
                        retry_exceptions=(RangeTooLarge,) if max_bytes is not None else (),
                        detail=f'{settings.method} level {settings.level}; '
                               f'{len(members)} files; requested threads {settings.threads}') as meter:
        def monitor() -> None:
            if max_bytes is not None and staged.exists() and staged.stat().st_size > max_bytes:
                raise RangeTooLarge(0, staged.stat().st_size, max_bytes)
        run = run_seven_zip(command, cwd=root, progress=meter,
                            on_tick=monitor if max_bytes is not None else None, timeout=settings.timeout)
        if run.returncode != 0:
            raise RuntimeError(f'Archiver error {run.returncode} ({archiver}): '
                               + (run.stderr or run.stdout)[-1600:])


def _flat_private_inputs(members: dict[str, Path], directory: Path) -> dict[str, Path] | None:
    """Alias small immutable inputs with private links; never duplicate raw bytes."""
    if len({path.parent for path in members.values()}) == 1 and all(
            name == path.name for name, path in members.items()):
        return members
    directory.mkdir()
    aliases = {}
    try:
        for name, source in members.items():
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(source, target)
            aliases[name] = target
        return aliases
    except OSError:
        # Cross-volume or hard-link-restricted filesystems use streamed ranges.
        # Link creation/removal finishes before mutation signatures are captured.
        shutil.rmtree(directory)
        return None


def _create_archive_parts(members: dict[str, Path], settings: ArchiveSettings, *,
                          work: Path, destination: Path, backend: str,
                          archiver: Path | None, reporter,
                          expected_checksums: dict[str, tuple[str, int]] | None = None,
                          compress_whole=None):
    """Choose bounded streaming before creating an oversized whole archive."""
    from .archive_stream import compress_range
    from .bounded_archive import BoundedPart, build_bounded_archives
    from .storage import require_space
    expected_checksums = expected_checksums or {}
    raw_bytes = sum(path.stat().st_size for path in members.values())
    small = raw_bytes * 1.02 + len(members) * 512 + 22 <= settings.max_bytes
    inputs = members
    if backend == '7zip':
        inputs = _flat_private_inputs(members, work / 'compression-inputs')
    signatures = {name: _file_signature(path) for name, path in members.items()}
    ratios = {}
    profiles = {}
    if not small:
        from .compression_plan import sample_profile
        for number, (name, source) in enumerate(members.items(), 1):
            profiles[name] = sample_profile(source, name, work=work, number=number,
                max_bytes=settings.max_bytes, reporter=reporter,
                compress=lambda *args, **kwargs: compress_range(*args, settings=settings,
                    backend=backend, archiver=archiver, **kwargs))
            ratios[name] = profiles[name].estimate() / max(1, profiles[name].size)
    estimated = sum(path.stat().st_size * ratios.get(name, 1.02)
                    for name, path in members.items()) + len(members) * 512 + 22
    whole = inputs is not None and (small or (backend == '7zip' and estimated < settings.max_bytes * .90))
    checksums: dict[str, tuple[str, int]] = {}
    if whole:
        from .archive_stream import RangeTooLarge
        staged = work / destination.name
        require_space(work, len(members) * 512 + 22, stage='start export archive')
        try:
            (compress_whole or _compress_archive)(staged, inputs, settings, backend=backend,
                archiver=archiver, reporter=reporter, max_bytes=None if small else settings.max_bytes)
            fits = staged.stat().st_size <= settings.max_bytes
        except RangeTooLarge as overflow:
            if overflow.cleanup_error is not None:
                raise
            fits = False
        if fits:
            _verify_members(staged, members, settings.method, reporter=reporter, checksums=checksums)
            _check_member_signatures(members, signatures)
            for name, expected in expected_checksums.items():
                if checksums.get(name) != expected:
                    raise RuntimeError(f'Native JSONL no longer matches its captured snapshot: {name}')
            with zipfile.ZipFile(staged) as archive:
                names = {zipfile.ZIP_LZMA: 'LZMA', zipfile.ZIP_DEFLATED: 'Deflate', zipfile.ZIP_STORED: 'Store'}
                methods = {info.filename: names[info.compress_type] for info in archive.infolist()}
            strategy = 'single_small_archive' if small else 'single_archive_after_ratio_probe'
            parts = [BoundedPart(staged, methods)]
            parts[0].compression_planning = {name: profile.receipt() for name, profile in profiles.items()}
            _record_archive_dictionaries(parts)
            return parts, signatures, strategy
        staged.unlink(missing_ok=True)
    total = sum(path.stat().st_size for name, path in members.items() if name not in expected_checksums)
    with reporter.stage('Checksum archive inputs', total=total,
                        detail='Reuse captured native SHA-256; hash rendered members') as meter:
        done = 0
        for name, path in members.items():
            if name in expected_checksums:
                if path.stat().st_size != expected_checksums[name][1]:
                    raise RuntimeError(f'Native JSONL size no longer matches its snapshot: {name}')
                checksums[name] = expected_checksums[name]
                continue
            digest = hashlib.sha256()
            size = 0
            with path.open('rb') as source:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    digest.update(block)
                    size += len(block)
                    done += len(block)
                    meter.update(done, detail=name)
            checksums[name] = digest.hexdigest(), size
    parts = build_bounded_archives(
        members, work=work / 'bounded', basename=destination.name,
        max_bytes=settings.max_bytes, method=settings.method, reporter=reporter, checksums=checksums,
        estimated_ratios=ratios, compression_profiles=profiles,
        compress_range=lambda path, name, source, offset, count, limit: compress_range(
            path, name, source, offset, count, limit, settings=settings,
            backend=backend, archiver=archiver, reporter=reporter))
    _check_member_signatures(members, signatures)
    for part in parts:
        part.compression_planning = {name: profile.receipt() for name, profile in profiles.items()}
    _record_archive_dictionaries(parts)
    return parts, signatures, 'streamed_bounded_members'


def _record_archive_dictionaries(parts) -> None:
    """Record encoded decoder-memory properties separately from requested limits."""
    from .archive_diagnostics import _lzma_properties
    for part in parts:
        with zipfile.ZipFile(part.path) as archive:
            for info in archive.infolist():
                if info.compress_type == zipfile.ZIP_LZMA:
                    value = _lzma_properties(archive.fp, info, archive.start_dir)
                    part.lzma_dictionary_bytes[info.filename] = value['dictionary_bytes']
