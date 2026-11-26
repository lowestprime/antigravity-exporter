"""Capture plain, gzip, or Zstandard direct JSONL without altering the input.

Zstandard framing follows the upstream format specification:
https://github.com/facebook/zstd/blob/dev/doc/zstd_compression_format.md
The small framing pass checks complete frame boundaries. Decoding and content
checksums are handled by python-zstandard, with bounded Python output buffers.
"""
from __future__ import annotations

import contextlib
import gzip
import hashlib
import os
from pathlib import Path
import zlib

from .storage import CHUNK, ExportError, capture_file, hash_file


def _check_zstd_frames(path: Path):
    """Require complete standard/skippable frames, without materializing output."""
    total = path.stat().st_size
    frames = 0
    skipped = 0
    checksum_frames = 0
    with path.open('rb') as stream:
        def exact(size):
            data = stream.read(size)
            if len(data) != size:
                raise ExportError('Incomplete Zstandard frame in captured input; retry after compression completes')
            return data

        def skip(size):
            if size > total - stream.tell():
                raise ExportError('Incomplete Zstandard block in captured input; retry after compression completes')
            stream.seek(size, os.SEEK_CUR)

        while stream.tell() < total:
            magic = int.from_bytes(exact(4), 'little')
            if 0x184D2A50 <= magic <= 0x184D2A5F:
                skip(int.from_bytes(exact(4), 'little'))
                skipped += 1
                continue
            if magic != 0xFD2FB528:
                raise ExportError('Invalid Zstandard frame magic or trailing non-frame bytes')
            descriptor = exact(1)[0]
            if descriptor & 8:
                raise ExportError('Reserved Zstandard frame descriptor bit is set')
            single = bool(descriptor & 32)
            checksum = bool(descriptor & 4)
            content_flag = descriptor >> 6
            if not single:
                exact(1)  # Window_Descriptor; the actual decoder enforces its limits.
            dictionary_size = (0, 1, 2, 4)[descriptor & 3]
            exact(dictionary_size)
            content_size_bytes = (1 if single else 0, 2, 4, 8)[content_flag]
            exact(content_size_bytes)
            while True:
                block_header = int.from_bytes(exact(3), 'little')
                final = bool(block_header & 1)
                kind = (block_header >> 1) & 3
                block_size = block_header >> 3
                if kind == 3:
                    raise ExportError('Reserved Zstandard block type is present')
                if block_size > 128 * 1024:
                    raise ExportError('Zstandard block exceeds the format size limit')
                skip(1 if kind == 1 else block_size)
                if final:
                    break
            if checksum:
                exact(4)
                checksum_frames += 1
            frames += 1
    if not frames:
        raise ExportError('Captured input has no Zstandard data frames')
    return {'complete_data_frames': frames, 'skippable_metadata_frames': skipped,
            'frames_with_content_checksum': checksum_frames,
            'frame_boundaries_complete': True}


def capture_direct(source: Path, stage: Path, *, progress=None, max_decoded_bytes=None):
    """Return (plain JSONL snapshot, provenance) in a caller-owned private stage.

    The fixed physical prefix is captured and verified before decompression.
    Appends after that boundary are excluded. Compressed captures must end at a
    complete frame/member; an incomplete compressed tail is never silently used.
    """
    source = Path(source).expanduser()
    stage = Path(stage)
    stage.mkdir(parents=True, exist_ok=True)
    destination = stage / 'source.jsonl'
    lower = source.name.lower()
    if max_decoded_bytes is not None and (type(max_decoded_bytes) is not int or max_decoded_bytes < 1):
        raise ExportError('max_decoded_bytes must be a positive integer')
    if lower.endswith('.jsonl'):
        info = capture_file(source, destination, allow_growth=True, progress=progress)
        if max_decoded_bytes is not None and info['bytes'] > max_decoded_bytes:
            destination.unlink()
            raise ExportError('Decoded direct input exceeds max_decoded_bytes')
        return destination, {**info, 'relative_source': str(source), 'source_encoding': 'plain',
                             'fidelity': 'user-selected-direct-JSONL'}
    if lower.endswith('.jsonl.gz'):
        encoding = 'gzip'
    elif lower.endswith(('.jsonl.zst', '.jsonl.zstd')):
        encoding = 'zstandard'
    else:
        raise ExportError('Direct input must be .jsonl, .jsonl.gz, .jsonl.zst, or .jsonl.zstd')
    decoder_module = None
    if encoding == 'zstandard':
        try:
            import zstandard as decoder_module
        except ImportError as exc:
            raise ExportError('Zstandard input requires the optional zstandard package in the exporter Python environment') from exc
    physical = stage / ('source.captured.' + ('gz' if encoding == 'gzip' else 'zst'))
    capture = capture_file(source, physical, allow_growth=True, progress=progress)
    frames = _check_zstd_frames(physical) if encoding == 'zstandard' else {}
    digest = hashlib.sha256()
    size = 0
    created = False
    try:
        with contextlib.ExitStack() as stack:
            incoming = stack.enter_context(physical.open('rb'))
            if encoding == 'gzip':
                decoded = stack.enter_context(gzip.GzipFile(fileobj=incoming, mode='rb'))
            else:
                decoded = stack.enter_context(decoder_module.ZstdDecompressor().stream_reader(
                    incoming, read_across_frames=True, closefd=False))
            outgoing = stack.enter_context(destination.open('xb'))
            created = True
            while True:
                block = decoded.read(CHUNK)
                if not block:
                    break
                size += len(block)
                if max_decoded_bytes is not None and size > max_decoded_bytes:
                    raise ExportError('Decoded direct input exceeds max_decoded_bytes')
                outgoing.write(block)
                digest.update(block)
                if progress:
                    progress.update('decode-direct-source', incoming.tell(), capture['bytes'],
                                    detail=f'{size:,} decoded bytes')
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if hash_file(destination, progress=progress, stage='verify-decoded-source') != digest.hexdigest():
            raise ExportError('Decoded direct input readback hash mismatch')
    except BaseException as exc:
        if created:
            with contextlib.suppress(OSError):
                destination.unlink()
        decode_errors = (EOFError, gzip.BadGzipFile, zlib.error)
        if decoder_module is not None:
            decode_errors += (decoder_module.ZstdError,)
        if isinstance(exc, decode_errors):
            raise ExportError(f'Invalid or incomplete {encoding} input: {exc}') from exc
        raise
    return destination, {
        'relative_source': str(source), 'source_encoding': encoding,
        'fidelity': 'user-selected-direct-JSONL', 'capture_method': 'verified-prefix-then-stream-decode',
        'bytes': size, 'sha256': digest.hexdigest(), 'decoded_readback_verified': True,
        'compressed_source_capture': capture,
        'complete_compression_stream_verified': True,
        'compression_stream': frames or {'member_crc_and_eof_verified': True},
        'later_compressed_bytes_observed': capture.get('later_bytes_observed', 0),
        'atomic_multifile_snapshot': False,
    }
