"""Public synthetic archive, progress, and access regressions; no user data."""
from __future__ import annotations
import hashlib, io, json, random, shutil, subprocess, sys, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
from antigravity_exporter.archive_set import package_members, settings_from_options, verify_archive_set
from antigravity_exporter.progress import Progress, SevenZipProgressParser, run_seven_zip
from antigravity_exporter.file_access import _repair_plan
from antigravity_exporter.storage import ExportError
from antigravity_exporter._vendor.codex_archive.archive_stream import RangeTooLarge
OPTIONS = {'method': 'Deflate', 'backend': 'python', 'level': 6}

def reconstruct(paths):
    segments, originals = {}, {}
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            if 'ARCHIVE_PART.json' not in archive.namelist():
                originals.update({info.filename: archive.read(info) for info in archive.infolist()})
                continue
            part = json.loads(archive.read('ARCHIVE_PART.json'))
            for row in part['members']:
                raw = archive.read(row['member'])
                if hashlib.sha256(raw).hexdigest() != row['sha256']:
                    raise AssertionError('Part checksum mismatch')
                segments.setdefault(row['original'], []).append((row['offset'], raw, row))
    for name, rows in segments.items():
        raw = bytearray()
        for offset, chunk, row in sorted(rows):
            if len(raw) != offset: raise AssertionError('Gap or overlapping reconstruction')
            raw.extend(chunk)
        if hashlib.sha256(raw).hexdigest() != row['original_sha256']:
            raise AssertionError('Reconstructed original checksum mismatch')
        originals[name] = bytes(raw)
    return originals

class ArchiveSetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name); self.work = self.root / 'captures'; self.work.mkdir()
        self.output = self.root / 'output' / 'antigravity_test.zip'
    def members(self, values):
        members = {}
        for number, (name, data) in enumerate(values.items()):
            path = self.work / str(number); path.write_bytes(data); members[name] = path
        return members
    def test_fitting_triplet_keeps_three_files_and_all_sources(self):
        data = {'thread.md': b'# Example\n', 'thread.manifest.json': b'{"schema":1}\n', 'thread.jsonl': b'{"type":"user"}\n'}
        sources = self.members(data); result = package_members(self.output, sources, OPTIONS)
        self.assertEqual(result['archive_count'], 1); self.assertTrue(result['archive_verified'])
        self.assertEqual(reconstruct(result['archives']), data)
        self.assertTrue(all(path.exists() for path in sources.values()))
        self.assertEqual(set(self.output.parent.iterdir()), {self.output})
        self.assertFalse(list(self.output.parent.glob('.antigravity-*')))
    def test_large_giant_record_bounds_actual_zip_and_reconstructs(self):
        raw = ('{"text":"日本語🙂' + random.Random(9).randbytes(120_000).hex() + '"}\n').encode()
        data = {'thread.jsonl': raw, 'thread.md': b'# Small\n'}
        result = package_members(self.output, self.members(data), {**OPTIONS, 'max_archive_bytes': 32768})
        self.assertGreater(result['archive_count'], 1)
        self.assertTrue(all(Path(p).stat().st_size <= 32768 for p in result['archives']))
        self.assertEqual(reconstruct(result['archives']), data)
        fragments = [row for part in result['archive_parts'] for row in part['provenance']['members'] if row['original'] == 'thread.jsonl']
        self.assertTrue(any(row['kind'] == 'byte_fragment' and row['member'].endswith('.bytes') for row in fragments))
    def test_nested_native_paths_and_empty_members_reconstruct(self):
        data = {'native/antigravity/brain/example/notes.txt': random.Random(13).randbytes(50000),
                'native/antigravity/brain/other/notes.txt': random.Random(17).randbytes(50000), 'empty.txt': b''}
        result = package_members(self.output, self.members(data), {**OPTIONS, 'max_archive_bytes': 32768})
        self.assertEqual(reconstruct(result['archives']), data)
        self.assertTrue(all(Path(p).stat().st_size <= 32768 for p in result['archives']))
    @unittest.skipUnless(shutil.which('7zz') or shutil.which('7z') or shutil.which('7za'), 'real external 7-Zip unavailable')
    def test_real_streamed_lzma_reconstruction(self):
        archiver = shutil.which('7zz') or shutil.which('7z') or shutil.which('7za')
        data = {'thread.md': random.Random(19).randbytes(90000),
                'native/example/output.txt': random.Random(25).randbytes(50000)}
        result = package_members(self.output, self.members(data),
            {'method':'LZMA','backend':'7zip','dictionary':'16m','threads':2,'max_archive_bytes':32768},
            archiver=archiver)
        self.assertGreater(result['archive_count'], 1)
        self.assertEqual(reconstruct(result['archives']), data)
        self.assertTrue(verify_archive_set(result['archives'])['complete_set_verified'])

    def test_verify_complete_set_missing_mixed_and_corrupt_parts(self):
        data = {'thread.jsonl': random.Random(35).randbytes(60000)}
        result = package_members(self.output, self.members(data), {**OPTIONS, 'max_archive_bytes': 16384})
        report = verify_archive_set(result['archives'])
        self.assertTrue(report['complete_set_verified'])
        self.assertEqual(report['originals'][0]['sha256'], hashlib.sha256(data['thread.jsonl']).hexdigest())
        with self.assertRaises(ExportError): verify_archive_set(result['archives'][:-1])
        with self.assertRaises(ExportError): verify_archive_set([*result['archives'], result['archives'][0]])
        first = Path(result['archives'][0])
        with zipfile.ZipFile(first) as archive:
            rows = [(info, archive.read(info)) for info in archive.infolist()]
        with zipfile.ZipFile(first, 'w') as archive:
            for info, raw in rows:
                if info.filename == 'ARCHIVE_PART.json':
                    value = json.loads(raw); value['members'][0]['offset'] += 1
                    raw = json.dumps(value).encode()
                archive.writestr(info, raw)
        with self.assertRaises(ExportError): verify_archive_set(result['archives'])

    def test_native_expected_checksum_mismatch_never_publishes(self):
        members = self.members({'thread.jsonl': b'synthetic changed bytes'})
        with self.assertRaisesRegex(ExportError, 'changed before packaging'):
            package_members(self.output, members, OPTIONS, expected_checksums={'thread.jsonl': ('0' * 64, 8)})
        self.assertFalse(self.output.exists())
    def test_existing_output_never_replaced(self):
        self.output.parent.mkdir(); self.output.write_bytes(b'existing unrelated content')
        with self.assertRaises(FileExistsError): package_members(self.output, self.members({'thread.md': b'# New'}), OPTIONS)
        self.assertEqual(self.output.read_bytes(), b'existing unrelated content')
    def test_partial_publication_failure_rolls_back_only_our_parts(self):
        data = {'one.bin': random.Random(21).randbytes(70000)}; calls = 0
        from antigravity_exporter._vendor.codex_archive import file_access
        original = file_access.publish_file
        def collision(source, destination, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                destination.write_bytes(b'racing writer'); raise FileExistsError(str(destination))
            return original(source, destination, **kwargs)
        with patch.object(file_access, 'publish_file', collision):
            with self.assertRaises(FileExistsError):
                package_members(self.output, self.members(data), {**OPTIONS, 'max_archive_bytes': 32768})
        remaining = list(self.output.parent.glob('*.zip')); self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].read_bytes(), b'racing writer')
    def test_invalid_case_colliding_and_traversal_names(self):
        for names in ({'A.md': b'1', 'a.md': b'2'}, {'../escape': b'1'}, {'/absolute': b'1'}, {'C:/absolute': b'1'}, {'a': b'1', 'a/b': b'2'}):
            with self.subTest(names=list(names)), self.assertRaises((ExportError, ValueError)):
                package_members(self.output, self.members(names), OPTIONS)
    def test_invalid_archive_cap_rejected(self):
        for cap in (True, 0, 16383, 512_000_001, 16384.0, '16384'):
            with self.subTest(cap=cap), self.assertRaises(ValueError): settings_from_options({**OPTIONS, 'max_archive_bytes': cap})
    def test_all_stage_counts_settle_after_successful_overflow_retry(self):
        stream = io.StringIO(); progress = Progress('json', stream=stream, heartbeat=False, interval=0)
        data = {'thread.md': random.Random(29).randbytes(160000)}
        package_members(self.output, self.members(data), {**OPTIONS, 'max_archive_bytes': 32768}, progress=progress); progress.finish()
        events = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual(events[-1]['event'], 'export_complete'); self.assertEqual(events[-1]['stages_failed'], 0)
        self.assertEqual(events[-1]['stages_settled'], events[-1]['stages_total'])
    @unittest.skipUnless(shutil.which('7zz') or shutil.which('7z') or shutil.which('7za'), 'real external 7-Zip unavailable')
    def test_real_lzma_nested_members(self):
        archiver = shutil.which('7zz') or shutil.which('7z') or shutil.which('7za')
        data = {'thread.md': b'# Thread\n' * 100, 'native/example/notes.txt': b'example\n' * 100}
        result = package_members(self.output, self.members(data), {'method':'LZMA','backend':'7zip','dictionary':'16m','threads':2}, archiver=archiver)
        self.assertEqual(reconstruct(result['archives']), data)
        with zipfile.ZipFile(result['archive']) as archive: self.assertIn(zipfile.ZIP_LZMA, {row.compress_type for row in archive.infolist()})

class UnifiedProgressTests(unittest.TestCase):
    def test_recent_window_eta_stall_and_finalization(self):
        now = [0.0]; stream = io.StringIO(); progress = Progress('json',stream=stream,clock=lambda:now[0],interval=0,heartbeat=False)
        with progress.stage('render',total=10000,unit='records') as task:
            for second in range(1,41): now[0]=second; task.update(second*100)
            for second in range(41,81): now[0]=second; task.update(4000+(second-40)*10)
            stats=task._stats(now[0]); self.assertAlmostEqual(stats['rate_per_second'],10); self.assertAlmostEqual(stats['eta_seconds'],560)
            now[0]=95; task.heartbeat(); self.assertEqual(task._stats(now[0])['eta_status'],'waiting_for_progress')
            now[0]=96; task.update(10000); self.assertIsNone(task._stats(now[0])['percent'])
        progress.finish(); self.assertEqual(json.loads(stream.getvalue().splitlines()[-1])['status'],'complete')
    def test_native_7zip_bytes_are_not_invented_from_percent(self):
        now=[0.0]; progress=Progress('off',clock=lambda:now[0],heartbeat=False)
        with progress.stage('compression',total=32*1048576) as task:
            parser=SevenZipProgressParser(task); parser.feed('0M\r'); now[0]=2; parser.feed('8M 1 + source.jsonl\r')
            self.assertEqual(task.completed,8*1048576); self.assertEqual(task.progress_basis,'7zip_processed_bytes')
            self.assertAlmostEqual(task._stats(now[0])['eta_seconds'],6)
            parser.feed('50%\r'); self.assertEqual(task.completed,8*1048576)
        progress.finish()
    def test_recoverable_budget_retry_does_not_poison_completion(self):
        stream=io.StringIO(); progress=Progress('json',stream=stream,heartbeat=False)
        try:
            with progress.stage('encode',retry_exceptions=(RangeTooLarge,)): raise RangeTooLarge(100,200,150)
        except RangeTooLarge: pass
        with progress.stage('encode accepted',total=100) as meter: meter.update(100)
        progress.finish(); summary=json.loads(stream.getvalue().splitlines()[-1])
        self.assertEqual(summary['status'],'complete'); self.assertEqual(summary['stages_retried'],1); self.assertEqual(summary['stages_failed'],0)
    def test_external_timeout_reaps_child(self):
        with self.assertRaises(subprocess.TimeoutExpired): run_seven_zip([sys.executable,'-c','import time;time.sleep(5)'],timeout=.1)
    def test_input_generator_failure_propagates(self):
        def broken():
            yield b'prefix'; raise ValueError('synthetic input failed')
        with self.assertRaisesRegex(ValueError,'synthetic input failed'):
            run_seven_zip([sys.executable,'-c','import sys;sys.stdin.buffer.read()'],input_chunks=broken(),timeout=5)

class AccessScopeTests(unittest.TestCase):
    def test_repair_plan_rejects_unrelated_or_app_owned_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); arbitrary=root/'unrelated.zip'; arbitrary.write_bytes(b'x')
            protected=root/'.gemini'; protected.mkdir(); source=protected/'antigravity_test.zip'; source.write_bytes(b'x')
            for path in (arbitrary,source):
                with self.assertRaises(ValueError): _repair_plan([path])
            owned=root/'antigravity_test.zip'; owned.write_bytes(b'x'); self.assertEqual(_repair_plan([owned]),[owned])

    def test_repair_plan_recognizes_only_shallow_standalone_review_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()/'antigravity_example.review.files'; directory=root/'review';directory.mkdir(parents=True)
            for name in ('INDEX.json','READ_FIRST.txt','PART_000001.txt'): (directory/name).write_text('synthetic')
            self.assertEqual(len(_repair_plan([root])),5)
            (root/'native').mkdir()
            with self.assertRaises(ValueError): _repair_plan([root])

    def test_repair_plan_rejects_linked_files_and_parent_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Canonicalize the test's legitimate parent, not paths under test.
            root=Path(tmp).resolve(); directory=root/'actual'; directory.mkdir()
            owned=directory/'antigravity_test.zip'; owned.write_bytes(b'synthetic')
            linked_directory=root/'linked'; linked_file=root/'antigravity_link.zip'
            try:
                linked_directory.symlink_to(directory,target_is_directory=True)
                linked_file.symlink_to(owned)
            except (OSError,NotImplementedError) as exc:
                self.skipTest(f'Symlink creation is unavailable: {exc}')
            self.assertEqual(_repair_plan([owned]),[owned])
            for path in (linked_file,linked_directory/owned.name):
                with self.subTest(path=path.name):
                    with self.assertRaisesRegex(ValueError,'refuses links and reparse points'):
                        _repair_plan([path])

if __name__=='__main__': unittest.main()
