"""Multipart output collision regressions; fixtures contain no private data."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random
import tempfile
import unittest

from antigravity_exporter.cli import reserve
from antigravity_exporter.storage import ExportError
from antigravity_exporter._vendor.codex_archive.bounded_archive import (
    archive_destinations, archive_stem_digest,
)
from test_exporter import Base, base_records


class MultipartReservationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.out = Path(temporary.name)

    def existing_part(self, stem, count=2):
        # Even a surviving later part reserves the original set's basename.
        part = archive_destinations(self.out/(stem+'.zip'), count)[-1]
        part.write_bytes(b'prior archive bytes')
        return part

    def test_existing_multipart_honors_rename_skip_and_error(self):
        original = self.existing_part('thread')
        self.assertEqual(reserve(self.out, 'thread', 'skip'), (None, None))
        with self.assertRaisesRegex(ExportError, 'Output already exists'):
            reserve(self.out, 'thread', 'error')
        candidate, lock = reserve(self.out, 'thread', 'rename')
        self.assertEqual(candidate, 'thread_2')
        self.assertTrue(lock.is_file())
        self.assertEqual(original.read_bytes(), b'prior archive bytes')
        self.assertFalse((self.out/'thread.reserve').exists())

    def test_rename_checks_multipart_outputs_of_renamed_candidates(self):
        first = self.existing_part('thread', count=2)
        second = self.existing_part('thread_2', count=3)
        candidate, _ = reserve(self.out, 'thread', 'rename')
        self.assertEqual(candidate, 'thread_3')
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_shortened_ascii_and_unicode_names_honor_every_policy(self):
        for stem in ('x'*237, '漢'*80):
            with self.subTest(stem=stem):
                part = self.existing_part(stem)
                self.assertIn(archive_stem_digest(self.out/(stem+'.zip')), part.name)
                self.assertLessEqual(len(part.name.encode('utf-8')), 255)
                self.assertEqual(reserve(self.out, stem, 'skip'), (None, None))
                with self.assertRaisesRegex(ExportError, 'Output already exists'):
                    reserve(self.out, stem, 'error')
                candidate, lock = reserve(self.out, stem, 'rename')
                self.assertEqual(candidate, stem+'_2')
                self.assertNotEqual(archive_destinations(self.out/(candidate+'.zip'), 2)[-1], part)
                self.assertEqual(part.read_bytes(), b'prior archive bytes')
                self.assertTrue(lock.exists())

    def test_literal_brackets_match_only_the_requested_stem(self):
        self.existing_part('thread1')
        candidate, lock = reserve(self.out, 'thread[12]', 'error')
        self.assertEqual(candidate, 'thread[12]')
        lock.unlink()
        original = self.existing_part('thread[12]')
        self.assertEqual(reserve(self.out, 'thread[12]', 'skip'), (None, None))
        self.assertEqual(original.read_bytes(), b'prior archive bytes')


class MultipartRetryTests(Base):
    def test_stable_bounded_export_can_be_repeated_without_overwriting_parts(self):
        row = base_records()[0]
        row['content'] = random.Random(42).randbytes(60000).hex()
        self.log.write_bytes((json.dumps(row)+'\n').encode('utf-8'))
        controls = ('--stable-filenames', '--max-archive-bytes', '16384',
                    '--tokenizer', 'none', '--no-verify-db', '--collision', 'rename')
        first = self.export(*controls)
        first_hashes = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
                        for name in first['archives']}
        second = self.export(*controls)
        self.assertGreater(first['archive_count'], 1)
        self.assertGreater(second['archive_count'], 1)
        self.assertTrue(set(first['archives']).isdisjoint(second['archives']))
        self.assertTrue(all('_2.part' in Path(name).name for name in second['archives']))
        self.assertEqual(first_hashes, {name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
                                      for name in first['archives']})
        self.assertFalse(list(self.out.glob('*.reserve')))


if __name__ == '__main__':
    unittest.main()
