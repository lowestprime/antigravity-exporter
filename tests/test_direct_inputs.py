"""Independent fixtures for compressed direct capture, corruption and growth."""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from antigravity_exporter.inputs import capture_direct
from antigravity_exporter.storage import ExportError

RAW = b'{"step_index":0,"content":"synthetic"}\n'
HAS_ZSTD = importlib.util.find_spec('zstandard') is not None


class DirectInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root / 'stage'

    def fixture(self, name, data):
        path = self.root / name
        path.write_bytes(data)
        return path

    def test_plain_jsonl_snapshot_and_original_invariant(self):
        source = self.fixture('plain.jsonl', RAW)
        path, info = capture_direct(source, self.stage)
        self.assertEqual(path.read_bytes(), RAW)
        self.assertEqual(source.read_bytes(), RAW)
        self.assertEqual(info['source_encoding'], 'plain')
        self.assertTrue(info['captured_prefix_verified'])

    def test_gzip_native_bytes_and_both_hashes(self):
        encoded = gzip.compress(RAW, mtime=0)
        source = self.fixture('plain.jsonl.gz', encoded)
        path, info = capture_direct(source, self.stage)
        self.assertEqual(path.read_bytes(), RAW)
        self.assertEqual(info['sha256'], hashlib.sha256(RAW).hexdigest())
        self.assertEqual(info['compressed_source_capture']['sha256'], hashlib.sha256(encoded).hexdigest())
        self.assertTrue(info['complete_compression_stream_verified'])
        self.assertEqual(source.read_bytes(), encoded)

    def test_concatenated_gzip_members(self):
        source = self.fixture('joined.jsonl.gz', gzip.compress(RAW) + gzip.compress(RAW))
        path, _ = capture_direct(source, self.stage)
        self.assertEqual(path.read_bytes(), RAW * 2)

    def test_gzip_truncated_trailer_rejected_and_partial_output_removed(self):
        source = self.fixture('bad.jsonl.gz', gzip.compress(RAW)[:-3])
        with self.assertRaisesRegex(ExportError, 'incomplete gzip'):
            capture_direct(source, self.stage)
        self.assertFalse((self.stage / 'source.jsonl').exists())

    def test_gzip_crc_mismatch_rejected(self):
        raw = bytearray(gzip.compress(RAW)); raw[-8] ^= 1
        source = self.fixture('bad.jsonl.gz', raw)
        with self.assertRaisesRegex(ExportError, 'CRC'):
            capture_direct(source, self.stage)

    def test_gzip_appended_complete_member_deferred_after_capture(self):
        source = self.fixture('growing.jsonl.gz', gzip.compress(RAW))
        class Append:
            did = False
            def update(self, *args, **kwargs):
                if not self.did:
                    self.did = True
                    with source.open('ab') as target:
                        target.write(gzip.compress(RAW))
        path, info = capture_direct(source, self.stage, progress=Append())
        self.assertEqual(path.read_bytes(), RAW)
        self.assertGreater(info['later_compressed_bytes_observed'], 0)

    def test_decoded_size_bound_does_not_leave_partial_file(self):
        source = self.fixture('bounded.jsonl.gz', gzip.compress(RAW))
        with self.assertRaisesRegex(ExportError, 'max_decoded_bytes'):
            capture_direct(source, self.stage, max_decoded_bytes=3)
        self.assertFalse((self.stage / 'source.jsonl').exists())

    def test_existing_destination_never_overwritten_or_deleted(self):
        source = self.fixture('small.jsonl.gz', gzip.compress(RAW))
        self.stage.mkdir(); existing = self.stage / 'source.jsonl'; existing.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):
            capture_direct(source, self.stage)
        self.assertEqual(existing.read_bytes(), b'keep')

    def test_unsupported_extension_rejected(self):
        source = self.fixture('unrelated.gz', gzip.compress(RAW))
        with self.assertRaisesRegex(ExportError, 'Direct input must'):
            capture_direct(source, self.stage)

    def test_missing_optional_zstandard_is_actionable(self):
        source = self.fixture('optional.jsonl.zst', b'not opened')
        with patch.dict(sys.modules, {'zstandard': None}):
            with self.assertRaisesRegex(ExportError, 'optional zstandard package'):
                capture_direct(source, self.stage)
        self.assertFalse((self.stage / 'source.captured.zst').exists())

    @unittest.skipUnless(HAS_ZSTD, 'optional zstandard is not installed')
    def test_zstandard_concatenated_frames(self):
        import zstandard as zstd
        compressor = zstd.ZstdCompressor(write_checksum=True)
        encoded = compressor.compress(RAW) + compressor.compress(RAW)
        source = self.fixture('joined.jsonl.zst', encoded)
        path, info = capture_direct(source, self.stage)
        self.assertEqual(path.read_bytes(), RAW * 2)
        self.assertEqual(info['compression_stream']['complete_data_frames'], 2)
        self.assertEqual(info['compression_stream']['frames_with_content_checksum'], 2)
        self.assertEqual(source.read_bytes(), encoded)

    @unittest.skipUnless(HAS_ZSTD, 'optional zstandard is not installed')
    def test_zstandard_without_content_size(self):
        import zstandard as zstd
        encoded = zstd.ZstdCompressor(write_content_size=False).compress(RAW * 10000)
        source = self.fixture('streamed.jsonl.zstd', encoded)
        path, _ = capture_direct(source, self.stage)
        self.assertEqual(path.read_bytes(), RAW * 10000)

    @unittest.skipUnless(HAS_ZSTD, 'optional zstandard is not installed')
    def test_zstandard_truncated_frame_is_not_success(self):
        import zstandard as zstd
        source = self.fixture('bad.jsonl.zst', zstd.ZstdCompressor().compress(RAW)[:-1])
        with self.assertRaisesRegex(ExportError, 'Incomplete Zstandard'):
            capture_direct(source, self.stage)
        self.assertFalse((self.stage / 'source.jsonl').exists())

    @unittest.skipUnless(HAS_ZSTD, 'optional zstandard is not installed')
    def test_zstandard_incomplete_final_frame_after_valid_frame(self):
        import zstandard as zstd
        raw = zstd.ZstdCompressor().compress(RAW)
        source = self.fixture('bad.jsonl.zst', raw + raw[:-2])
        with self.assertRaisesRegex(ExportError, 'Incomplete Zstandard'):
            capture_direct(source, self.stage)

    @unittest.skipUnless(HAS_ZSTD, 'optional zstandard is not installed')
    def test_zstandard_checksum_mismatch_fails_decoder(self):
        import zstandard as zstd
        raw = bytearray(zstd.ZstdCompressor(write_checksum=True).compress(RAW)); raw[-1] ^= 1
        source = self.fixture('bad.jsonl.zst', raw)
        with self.assertRaisesRegex(ExportError, 'zstandard'):
            capture_direct(source, self.stage)
        self.assertFalse((self.stage / 'source.jsonl').exists())

    @unittest.skipUnless(HAS_ZSTD, 'optional zstandard is not installed')
    def test_zstandard_skippable_metadata_preserved_in_physical_hash(self):
        import zstandard as zstd
        skipped = (0x184D2A50).to_bytes(4, 'little') + (5).to_bytes(4, 'little') + b'notes'
        source = self.fixture('with-skip.jsonl.zst', skipped + zstd.ZstdCompressor().compress(RAW) + skipped)
        path, info = capture_direct(source, self.stage)
        self.assertEqual(path.read_bytes(), RAW)
        self.assertEqual(info['compression_stream']['skippable_metadata_frames'], 2)

    @unittest.skipUnless(HAS_ZSTD, 'optional zstandard is not installed')
    def test_zstandard_trailing_junk_rejected(self):
        import zstandard as zstd
        source = self.fixture('junk.jsonl.zst', zstd.ZstdCompressor().compress(RAW) + b'junk')
        with self.assertRaisesRegex(ExportError, 'magic'):
            capture_direct(source, self.stage)


if __name__ == '__main__':
    unittest.main()
