"""Project export regressions with synthetic repeated native coordinates."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unittest
import zipfile

from antigravity_exporter.archive_set import verify_archive_set
from antigravity_exporter.cli import run, verify_export
from antigravity_exporter.storage import ExportError
from test_exporter import Base, ID, PROJECT

SECOND_ID = '22222222-2222-4333-8444-555555555555'
PROJECT_NAME = 'fixture-project'


def source_rows():
    def row(step, content, user=False):
        return {'step_index': step, 'source': 'USER_EXPLICIT' if user else 'MODEL',
                'type': 'USER_INPUT' if user else 'PLANNER_RESPONSE', 'status': 'DONE',
                'created_at': '2026-11-08T12:00:00Z', 'content': content}

    repeated = row(696, 'Repeated source bytes: café 日本語 🧠')
    return [row(0, 'Initial request for the second fixture.', user=True),
            repeated, row(697, 'First recorded body at native step 697.'),
            row(698, 'Later high-water event.'),
            row(697, 'Another recorded body at native step 697.'),
            dict(repeated), row(695, 'A subsequently recorded lower coordinate.'),
            row(699, 'A subsequent recorded user occurrence.', user=True),
            row(700, 'Final fixture event.')]


class ProjectExportTests(Base):
    def setUp(self):
        super().setUp()
        self.second_rows = source_rows()
        self.second_raw = b''.join((json.dumps(row, ensure_ascii=False)+'\r\n').encode('utf-8')
                                   for row in self.second_rows)
        variant = self.root/'antigravity'
        logs = variant/'brain'/SECOND_ID/'.system_generated'/'logs'
        logs.mkdir(parents=True)
        self.second_log = logs/'transcript_full.jsonl'
        self.second_log.write_bytes(self.second_raw)
        (logs/'transcript.jsonl').write_bytes(self.second_raw)
        chunks = logs/'chunks'/'transcript_full'
        chunks.mkdir(parents=True)
        for number, offset in enumerate(range(0, len(self.second_raw), 71)):
            (chunks/f'{number:08d}.jsonl').write_bytes(self.second_raw[offset:offset+71])
        # Deliberately equal titles exercise per-session member naming in a
        # grouped project archive; recorded timestamps put the valid source first.
        title = (variant/'annotations'/(ID+'.pbtxt')).read_bytes()
        (variant/'annotations'/(SECOND_ID+'.pbtxt')).write_bytes(title)
        with closing(sqlite3.connect(variant/'conversation_summaries.db')) as db, db:
            db.execute('INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,?)',
                       (SECOND_ID, 'Fixture title', PROJECT, len(self.second_rows),
                        '2026-11-08T19:47:06Z', '[]', 'IDLE'))
        with closing(sqlite3.connect(variant/'conversations'/(SECOND_ID+'.db'))) as db, db:
            db.execute('CREATE TABLE trajectory_meta (cascade_id TEXT)')
            db.execute('INSERT INTO trajectory_meta VALUES (?)', (SECOND_ID,))
            db.execute('CREATE TABLE steps (idx INTEGER PRIMARY KEY,step_type INTEGER,status INTEGER,step_payload BLOB,error_details BLOB)')
            db.executemany('INSERT INTO steps VALUES (?,?,?,?,?)',
                           [(step, 15, 3, b'', b'') for step in sorted({r['step_index'] for r in self.second_rows})])
        self.expected_raw = {ID: self.raw, SECOND_ID: self.second_raw}

    def export_project(self, *controls):
        return run(self.args('export', '--root', str(self.root), '--project', PROJECT_NAME,
                            '--mode', 'thread', '--out-dir', str(self.out),
                            '--archive-method', 'Deflate', '--archive-backend', 'python',
                            '--archive-level', '6', '--tokenizer', 'none', '--no-progress',
                            *controls))

    def check_session_members(self, members, manifest_name):
        manifest_bytes = members[manifest_name]
        manifest = json.loads(manifest_bytes)
        session_id = manifest['session']['id']
        stem = manifest_name.removesuffix('.manifest.json')
        raw = self.expected_raw[session_id]
        expected_rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        self.assertEqual(members[stem+'.jsonl'], raw)
        self.assertEqual(manifest['source_copy']['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertTrue(manifest['source_copy']['byte_exact_captured_prefix'])
        self.assertEqual(manifest['selection']['records'], len(expected_rows))
        self.assertEqual(manifest['source_coverage']['records'], len(expected_rows))
        self.assertEqual([json.loads(line) for line in members[stem+'.records.jsonl'].splitlines()],
                         expected_rows)
        markdown = members[stem+'.md']
        self.assertEqual(len(re.findall(rb'^## Step [0-9]+ ', markdown, re.MULTILINE)), len(expected_rows))
        for name, receipt in manifest['members'].items():
            self.assertEqual(len(members[name]), receipt['bytes'], name)
            self.assertEqual(hashlib.sha256(members[name]).hexdigest(), receipt['sha256'], name)
        review = json.loads(members[stem+'.review/INDEX.json'])
        bodies = []
        for part in review['parts']:
            data = members[stem+'.review/'+part['file']]
            self.assertLessEqual(len(data), review['part_cap_bytes'])
            self.assertEqual(hashlib.sha256(data).hexdigest(), part['file_sha256'])
            body = data[part['header_bytes']:]
            self.assertEqual(hashlib.sha256(body).hexdigest(), part['body_sha256'])
            bodies.append(body)
        self.assertEqual(b''.join(bodies), markdown)
        self.assertEqual(review['source_sha256'], hashlib.sha256(markdown).hexdigest())
        self.assertTrue(review['reconstruction_verified'])
        if session_id == SECOND_ID:
            order = manifest['source_coverage']['step_order']
            self.assertFalse(order['monotonic_unique'])
            self.assertEqual(order['distinct_steps'], 7)
            self.assertEqual(order['duplicate_occurrences'], 2)
            self.assertEqual(order['duplicate_identical_source_bytes'], 1)
            self.assertEqual(order['duplicate_different_source_bytes'], 1)
            self.assertEqual(order['backward_transitions'], 3)
            self.assertTrue(manifest['database_audit']['step_sets_match'])
            self.assertFalse(manifest['database_audit']['matched_exactly'])
        return manifest

    def test_full_project_archive_preserves_repeated_source_rows_and_review(self):
        results = self.export_project('--archive', '--copy-source', '--records-jsonl',
                                      '--include-thinking', '--tool-outputs', 'full',
                                      '--include-native', '--include-artifacts', '--verify-db',
                                      '--verify-chunks', '--handoff', '--review',
                                      '--review-chunk-bytes', '4096')
        self.assertEqual([item['id'] for item in results], [ID, SECOND_ID])
        self.assertEqual(results[0]['archives'], results[1]['archives'])
        self.assertEqual([item['selected_records'] for item in results], [7, 9])
        self.assertEqual(results[0]['archive_count'], 1)
        with zipfile.ZipFile(results[0]['archive']) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        batch = json.loads(members['BATCH_INDEX.json'])
        self.assertTrue(batch['selection_complete'])
        self.assertEqual([item['id'] for item in batch['sessions']], [ID, SECOND_ID])
        self.assertEqual([item['selected_records'] for item in batch['sessions']], [7, 9])
        for entry in batch['sessions']:
            manifest = self.check_session_members(members, entry['manifest'])
            self.assertEqual(hashlib.sha256(members[entry['manifest']]).hexdigest(), entry['manifest_sha256'])
            session_id = entry['id']
            self.assertEqual(members[f'native/antigravity/brain/{session_id}/.system_generated/logs/transcript_full.jsonl'],
                             self.expected_raw[session_id])
            self.assertTrue(manifest['source_capture']['chunk_audit']['equals_monolithic'])
        verified = verify_export(results[0]['archive'])
        self.assertTrue(verified['embedded_manifest_hashes_matched'])
        self.assertEqual(len(verified['manifests_checked']), 2)
        self.assertTrue(verify_archive_set(results[0]['archives'])['complete_set_verified'])
        self.assertFalse(list(self.out.glob('*.reserve')))
        self.assertEqual(self.log.read_bytes(), self.raw)
        self.assertEqual(self.second_log.read_bytes(), self.second_raw)

    def test_strict_step_order_names_the_failed_source_before_project_publication(self):
        with self.assertRaises(ExportError) as error:
            self.export_project('--strict-step-order')
        message = str(error.exception)
        self.assertIn('antigravity:'+SECOND_ID, message)
        self.assertIn('transcript_full.jsonl', message)
        self.assertIn('step 697', message)
        self.assertRegex(message, r'byte [0-9]+')
        self.assertRegex(message, r'(?:source record|ordinal) [0-9]+')
        self.assertFalse(list(self.out.glob('*.files')))
        self.assertFalse(list(self.out.glob('*.zip')))
        self.assertFalse(list(self.out.glob('*.reserve')))

    def test_late_malformed_source_does_not_publish_earlier_loose_export(self):
        malformed = b'{invalid JSON\n'
        self.second_log.write_bytes(malformed)
        self.out.mkdir()
        unrelated = self.out/'unrelated.txt'
        unrelated.write_bytes(b'existing user output')
        work = self.dir/'work'
        with self.assertRaisesRegex(ExportError, 'malformed JSONL'):
            self.export_project('--no-archive', '--work-dir', str(work))
        self.assertEqual(list(self.out.iterdir()), [unrelated])
        self.assertEqual(unrelated.read_bytes(), b'existing user output')
        self.assertEqual(list(work.iterdir()), [])
        self.assertEqual(self.log.read_bytes(), self.raw)
        self.assertEqual(self.second_log.read_bytes(), malformed)

    def test_successful_loose_project_publishes_separate_verified_session_directories(self):
        results = self.export_project('--no-archive', '--copy-source', '--records-jsonl',
                                      '--verify-db', '--verify-chunks', '--handoff', '--review',
                                      '--review-chunk-bytes', '4096')
        self.assertEqual([item['id'] for item in results], [ID, SECOND_ID])
        directories = [Path(item['loose_directory']) for item in results]
        self.assertEqual(len(set(directories)), 2)
        self.assertEqual({path.resolve() for path in self.out.iterdir()},
                         {path.resolve() for path in directories})
        for item, directory in zip(results, directories):
            self.assertTrue(directory.name.endswith('.files'))
            self.assertNotIn('archive', item)
            self.assertTrue(item['loose_outputs_retained'])
            members = {path.relative_to(directory).as_posix(): path.read_bytes()
                       for path in directory.rglob('*') if path.is_file()}
            manifest_name = Path(item['manifest']).name
            manifest = self.check_session_members(members, manifest_name)
            self.assertEqual(manifest['session']['id'], item['id'])
            self.assertEqual(hashlib.sha256(members[manifest_name]).hexdigest(), item['manifest_sha256'])
            self.assertTrue(Path(item['markdown']).is_file())


if __name__ == '__main__':
    unittest.main()
