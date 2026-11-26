"""Verified bounded archives shared by individual and multi-session exports.

The pinned Codex archive engine supplies measured compression, conservative
size limits, independently readable parts and exact reconstruction receipts.
This adapter keeps Antigravity's nested native files and ownership contract.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import tempfile

from .storage import ExportError, safe_name
from .progress import Progress
from ._vendor.codex_archive.archive_pipeline import (
    ArchiveSettings, _create_archive_parts, _check_member_signatures,
)
from ._vendor.codex_archive.bounded_archive import (
    MAX_ARCHIVE_BYTES, MIN_ARCHIVE_BYTES, validate_max_bytes,
    archive_destinations, archive_stem_digest, publish_parts,
)
from ._vendor.codex_archive.file_access import validate_export_paths
from ._vendor.codex_archive.storage import work_directory_parent, storage_failure


def settings_from_options(options, archiver=None):
    settings = ArchiveSettings(
        method=options.get('method', 'LZMA'), backend=options.get('backend', 'auto'),
        level=options.get('level', 9), dictionary=options.get('dictionary', '256m'),
        word_size=options.get('word_size', 64), threads=options.get('threads', 19),
        archiver=str(archiver or options.get('archiver') or ''),
        max_bytes=options.get('max_archive_bytes', MAX_ARCHIVE_BYTES),
        work_dir=options.get('work_dir', ''), timeout=options.get('timeout', 3600),
    )
    settings.validate()
    return settings


def _checksums(members, reporter):
    values = {}
    total = sum(p.stat().st_size for p in members.values())
    with reporter.stage('Checksum archive inputs', total=total) as meter:
        for name, source in members.items():
            digest, size = hashlib.sha256(), 0
            with source.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
                    size += len(block)
                    meter.advance(len(block), detail=name)
            values[name] = (digest.hexdigest(), size)
    return values


def package_members(destination: Path, members: dict[str, Path], options, *,
                    progress=None, archiver=None, expected_checksums=None):
    """Publish one bounded set; never delete caller-owned members.

    Members must be immutable exporter-owned captures, not live application
    files. Every part is decoded and verified before any publication. The final
    caller owns cleanup, allowing failed batches to keep their useful receipts.
    """
    settings = settings_from_options(options, archiver)
    reporter = progress or Progress('off', heartbeat=False)
    destination = Path(destination).expanduser().resolve()
    members = {name: Path(path).expanduser().absolute() for name, path in members.items()}
    if not members:
        raise ExportError('Cannot publish an empty archive set')
    folded = set()
    for name, path in members.items():
        safe_name(name)
        if name.casefold() in folded:
            raise ExportError('Case-insensitive archive member collision: ' + name)
        folded.add(name.casefold())
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or path.is_symlink()
                or getattr(info, 'st_file_attributes', 0) & 0x400):
            raise ExportError('Archive member is not a regular captured file: ' + name)
    if any('/'.join(name.split('/')[:i]).casefold() in folded
           for name in members for i in range(1, len(name.split('/')))):
        raise ExportError('Archive filename conflicts with a member directory')
    members = {name: path.resolve() for name, path in members.items()}
    validate_export_paths({'archive': destination}, members.values())
    if destination.exists():
        raise FileExistsError('Refusing to replace an existing archive: ' + str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    parent = work_directory_parent(destination.parent, settings.work_dir)
    backend = settings.effective_backend()
    if backend == '7zip' and not archiver:
        from .archive import find_archiver
        archiver = find_archiver(options.get('archiver'))
    # Hash before compression, then require the exact recorded native checksums.
    checksums = _checksums(members, reporter)
    for name, expected in (expected_checksums or {}).items():
        if checksums.get(name) != tuple(expected):
            raise ExportError('Captured native bytes changed before packaging: ' + name)
    try:
        with tempfile.TemporaryDirectory(prefix='.antigravity-archive-', dir=parent) as temporary:
            parts, signatures, strategy = _create_archive_parts(
                members, settings, work=Path(temporary), destination=destination,
                backend=backend, archiver=Path(archiver) if archiver else None,
                reporter=reporter, expected_checksums=checksums,
            )
            _check_member_signatures(members, signatures)
            # Compute part hashes while the exact verified files remain staged.
            receipts = []
            with reporter.stage('Checksum verified archives', total=sum(p.size_bytes for p in parts)) as meter:
                for part in parts:
                    digest = hashlib.sha256()
                    with part.path.open('rb') as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b''):
                            digest.update(block)
                            meter.advance(len(block))
                    receipts.append({'bytes': part.size_bytes, 'sha256': digest.hexdigest(),
                                     'members': part.members, 'provenance': part.provenance})
            destinations, publications = publish_parts(
                parts, destination, max_bytes=settings.max_bytes, reporter=reporter, consume=True,
            )
            _check_member_signatures(members, signatures)
            for receipt, path in zip(receipts, destinations):
                receipt['archive'] = str(path)
            methods = {name: method for part in parts for name, method in part.member_methods.items()}
            return {
                'archive': str(destinations[0]), 'archives': [str(path) for path in destinations],
                'archive_count': len(parts), 'archive_verified': True,
                'archive_sha256': receipts[0]['sha256'], 'archive_bytes': receipts[0]['bytes'],
                'archive_total_bytes': sum(r['bytes'] for r in receipts),
                'archive_parts': receipts, 'archive_max_bytes': settings.max_bytes,
                'archive_publications': publications, 'archive_work_strategy': strategy,
                'archive_member_count': len(members), 'archive_member_methods': methods,
                'archive_compression_planning': parts[0].compression_planning,
                'archive_packing': parts[0].packing,
                'archive_member_dictionary_bytes': {
                    name: value for part in parts for name, value in part.lzma_dictionary_bytes.items()},
                'member_verification': [{'name': name, 'sha256': digest, 'bytes': size}
                                        for name, (digest, size) in sorted(checksums.items())],
            }
    except BaseException as error:
        enriched = storage_failure(error, stage='Antigravity archive packaging',
                                   work_dir=parent, output_dir=destination.parent)
        if enriched is not error:
            raise enriched from error
        raise


def verify_archive_set(paths, max_dictionary_mib=512, *, progress=None):
    """Read and fully verify a complete independent ZIP set without extraction.

    Missing/duplicate/mixed parts, unsafe member names, forged offsets, CRC
    failures, and mismatched original bytes are errors. This proves archive
    reconstruction integrity, not that a receiving model ingested the content.
    """
    import json
    import re
    import zipfile
    from .archive import lzma_dictionary, verify_zip
    from .storage import hash_file, safe_inventory, CHUNK
    if type(max_dictionary_mib) is not int or max_dictionary_mib < 1:
        raise ExportError('max_dictionary_mib must be a positive integer')
    supplied = [Path(path).expanduser().resolve() for path in paths]
    if not supplied or len(set(supplied)) != len(supplied):
        raise ExportError('Supply a nonempty set of distinct archive paths')
    reporter = progress or Progress('off', heartbeat=False)
    identity, metadata, locations, originals, reports = None, [], {}, {}, []
    sha_pattern = re.compile(r'[0-9a-f]{64}')
    def number(value):
        return type(value) is int and value >= 0
    def signature(path):
        s = path.stat()
        return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    signatures = {path: signature(path) for path in supplied}
    for path in supplied:
        with zipfile.ZipFile(path) as archive:
            inventory = safe_inventory(archive)
            index_names = [name for name in inventory if re.fullmatch(r'_*ARCHIVE_PART\.json', name)]
            if not index_names:
                if len(supplied) != 1:
                    raise ExportError('An ordinary ZIP cannot be mixed into a multipart set')
                result = verify_zip(path, max_dictionary_mib=max_dictionary_mib, progress=progress)
                return {**result, 'status': 'ok', 'archive_count': 1,
                        'multipart': False, 'complete_set_verified': True,
                        'verification_scope': 'ordinary ZIP member CRC/SHA; no multipart reconstruction needed'}
            if len(index_names) != 1:
                raise ExportError('Ambiguous reconstruction metadata')
            index_name = index_names[0]
            index_info = inventory[index_name]
            if index_info.file_size > 32 * 1024 * 1024:
                raise ExportError('Multipart metadata exceeds the 32 MiB inspection bound')
            for info in inventory.values():
                dictionary = lzma_dictionary(archive, info)
                if dictionary and dictionary > max_dictionary_mib * 1024 * 1024:
                    raise ExportError('LZMA dictionary policy limit exceeded; not evidence of corruption')
            part = json.loads(archive.read(index_info))
            if not isinstance(part, dict) or part.get('schema') != 'antigravity-export.archive-part.v1':
                raise ExportError('Unsupported multipart metadata schema')
            fields = ('schema', 'export_id', 'parts', 'max_archive_bytes', 'requested_method',
                      'original_member_count', 'inventory_sha256')
            current = {key: part.get(key) for key in fields}
            if identity is None: identity = current
            if current != identity: raise ExportError('Mixed archive sets or inconsistent reconstruction metadata')
            for field in ('part', 'parts', 'original_member_count'):
                if not number(part.get(field)) or part[field] < 1:
                    raise ExportError('Invalid multipart ordinal/count metadata')
            validate_max_bytes(part['max_archive_bytes'])
            if path.stat().st_size > part['max_archive_bytes']:
                raise ExportError('Archive exceeds its declared size ceiling')
            if part['requested_method'] not in ('LZMA', 'Deflate'):
                raise ExportError('Unsupported requested archive method')
            if (not isinstance(part.get('export_id'), str)
                    or not re.fullmatch(r'[0-9a-f]{32}', part['export_id'])
                    or not isinstance(part.get('inventory_sha256'), str)
                    or not sha_pattern.fullmatch(part['inventory_sha256'])):
                raise ExportError('Invalid multipart identity/hash metadata')
            rows = part.get('members')
            if not isinstance(rows, list) or not rows:
                raise ExportError('Multipart archive contains no member descriptors')
            expected_names = {index_name}
            for row in rows:
                if not isinstance(row, dict): raise ExportError('Invalid multipart member descriptor')
                name, original = row.get('member'), row.get('original')
                if not isinstance(name, str) or not isinstance(original, str):
                    raise ExportError('Invalid multipart member name')
                safe_name(name); safe_name(original)
                if name in expected_names: raise ExportError('Duplicate multipart member descriptor')
                expected_names.add(name)
                if name not in inventory: raise ExportError('Missing multipart member ' + name)
                for key in ('offset', 'bytes', 'original_bytes'):
                    if not number(row.get(key)): raise ExportError('Invalid member byte coordinate')
                if row['offset'] + row['bytes'] > row['original_bytes']:
                    raise ExportError('Multipart member extends beyond its original size')
                for key in ('sha256', 'original_sha256'):
                    if not isinstance(row.get(key), str) or not sha_pattern.fullmatch(row[key]):
                        raise ExportError('Invalid member SHA-256 metadata')
                if inventory[name].file_size != row['bytes']:
                    raise ExportError('Member size disagrees with reconstruction metadata')
                original_value = (row['original_sha256'], row['original_bytes'])
                if original in originals and originals[original] != original_value:
                    raise ExportError('Inconsistent original-file hash/size metadata')
                originals[original] = original_value
                locations.setdefault(original, []).append((row['offset'], path, name, row))
            if set(inventory) != expected_names or len(archive.infolist()) != len(inventory):
                raise ExportError('Multipart ZIP inventory does not match its descriptors')
            allowed = {0, 14 if part['requested_method'] == 'LZMA' else 8}
            if any(info.compress_type not in allowed for info in inventory.values()):
                raise ExportError('Multipart ZIP contains an unrequested compression method')
            metadata.append(part)
            reports.append({'path': str(path), 'bytes': path.stat().st_size,
                            'sha256': hash_file(path), 'part': part['part']})
    if sorted(part['part'] for part in metadata) != list(range(1, identity['parts'] + 1)):
        raise ExportError('Missing or duplicated archive parts')
    if len(originals) != identity['original_member_count']:
        raise ExportError('Original member inventory is incomplete')
    if len({name.casefold() for name in originals}) != len(originals):
        raise ExportError('Case-colliding original member names')
    inventory_rows = [(name, *originals[name]) for name in sorted(originals)]
    inventory_hash = hashlib.sha256(json.dumps(inventory_rows, ensure_ascii=False,
                                               separators=(',', ':')).encode('utf-8')).hexdigest()
    if inventory_hash != identity['inventory_sha256']:
        raise ExportError('Original member inventory hash mismatch')
    verified = []
    with reporter.stage('Verify complete archive set', total=sum(size for _, size in originals.values())) as meter:
        for original, expected in sorted(originals.items()):
            if expected[1] == 0 and len(locations[original]) != 1:
                raise ExportError('Empty original has duplicate segments: ' + original)
            digest, offset = hashlib.sha256(), 0
            for start, path, name, row in sorted(locations[original], key=lambda item: item[0]):
                if start != offset: raise ExportError('Archive coverage gap or overlap: ' + original)
                piece_hash, size = hashlib.sha256(), 0
                with zipfile.ZipFile(path) as archive, archive.open(name) as source:
                    for block in iter(lambda: source.read(CHUNK), b''):
                        piece_hash.update(block); digest.update(block); size += len(block)
                        meter.advance(len(block), detail=name)
                if (piece_hash.hexdigest(), size) != (row['sha256'], row['bytes']):
                    raise ExportError('Multipart segment checksum mismatch: ' + name)
                offset += size
            if (digest.hexdigest(), offset) != expected:
                raise ExportError('Reconstructed original checksum/size mismatch: ' + original)
            verified.append({'name': original, 'sha256': digest.hexdigest(), 'bytes': offset,
                             'segments': len(locations[original])})
    if any(signature(path) != signatures[path] for path in supplied):
        raise ExportError('An archive changed during complete-set verification')
    return {'status': 'ok', 'multipart': True, 'complete_set_verified': True,
            'all_members_decoded_and_crc_checked': True, 'archive_count': len(supplied),
            'export_id': identity['export_id'], 'max_archive_bytes': identity['max_archive_bytes'],
            'archives': sorted(reports, key=lambda row: row['part']), 'originals': verified,
            'inventory_sha256': inventory_hash,
            'verification_scope': 'every part CRC/SHA, contiguous original bytes, original inventory SHA-256'}
