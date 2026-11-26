"""Quoted quota phrases in non-error rows must never become false interruptions."""
from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from antigravity_exporter.model import EventIndex, audit_database, interruption_summary
from antigravity_exporter.storage import Source

ID = '11111111-2222-4333-8444-555555555555'


def wire(text):
    raw = text.encode()
    assert len(raw) < 128
    return b'\x0a' + bytes([len(raw)]) + raw


class InterruptionEvidenceTests(unittest.TestCase):
    def test_normal_payload_mentions_are_diagnostics_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / 'antigravity' / 'conversations' / (ID + '.db')
            db.parent.mkdir(parents=True)
            with closing(sqlite3.connect(db)) as con, con:
                con.execute('CREATE TABLE trajectory_meta (cascade_id TEXT)')
                con.execute('INSERT INTO trajectory_meta VALUES (?)', (ID,))
                con.execute('CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER, step_payload BLOB)')
                con.executemany('INSERT INTO steps VALUES (?,?,?,?)', [
                    (0, 14, 3, b''),
                    (1, 17, 3, wire('quota HTTP 429 reset 2026-11-16T00:00:00Z')),
                    (2, 132, 3, wire('A tool quotes quota HTTP 429 reset 2099-01-01T00:00:00Z')),
                    (3, 15, 3, wire('An assistant describes rate limit handling.')),
                    (4, 14, 3, wire('A user quotes resource_exhausted.')),
                    (5, 17, 3, wire('ordinary unrelated error')),
                ])
            transcript = root / 'synthetic.jsonl'
            transcript.write_text(json.dumps({'step_index': 0, 'type': 'USER_INPUT', 'source': 'USER_EXPLICIT',
                                             'status': 'DONE', 'content': 'Continue'}) + '\n')
            index = EventIndex(transcript, root / 'index.db')
            try:
                with Source(root=root) as source:
                    audit, diagnostics = audit_database(source, {'id': ID, 'variant': 'antigravity'}, index)
                self.assertEqual(audit['db_only_steps'], [1, 2, 3, 4, 5])
                by_step = {row['step_index']: row for row in diagnostics}
                self.assertEqual(by_step[1]['evidence_class'], 'quota-error-step')
                self.assertEqual(by_step[2]['evidence_class'], 'text-mention-in-opaque-row')
                self.assertTrue(by_step[2]['quota_bytes_observed'])
                self.assertFalse(by_step[2]['quota_error_step'])
                self.assertEqual(by_step[5]['evidence_class'], 'error-step-without-quota-marker')
                summary = interruption_summary(index, diagnostics)
                self.assertEqual(summary['database_only_quota_steps'], [1])
                self.assertEqual(summary['latest_database_only_quota_step'], 1)
                self.assertEqual(summary['timestamp_strings_in_latest_quota_step'], ['2026-11-16T00:00:00Z'])
                self.assertEqual(index.turns[0]['database_only_quota_steps'], [1])
                self.assertFalse(summary['non_error_quota_mentions_are_interruptions'])
                self.assertTrue(summary['timestamps_are_historical_observations_not_live_service_predictions'])
            finally:
                index.close()

    def test_historical_prefix_error_does_not_mark_later_user_turn_interrupted(self):
        index = SimpleNamespace(turns=[{'turn': 1, 'first_step': 100, 'execution_complete': 'not established'},
                                      {'turn': 2, 'first_step': 200, 'execution_complete': 'not established'}])
        diagnostics = [
            {'step_index': step, 'step_type': 17, 'quota_bytes_observed': True, 'http_429_bytes_observed': True,
             'timestamp_strings_observed': []} for step in (20, 150, 201)
        ]
        summary = interruption_summary(index, diagnostics)
        self.assertEqual(summary['database_only_quota_steps'], [20, 150, 201])
        self.assertEqual(index.turns[0]['database_only_quota_steps'], [150])
        self.assertEqual(index.turns[1]['database_only_quota_steps'], [201])

    def test_non_error_mention_alone_does_not_invent_completion_or_failure(self):
        index = SimpleNamespace(turns=[{'turn': 1, 'first_step': 0, 'execution_complete': 'not established'}])
        summary = interruption_summary(index, [{'step_index': 1, 'step_type': 15,
            'quota_bytes_observed': True, 'http_429_bytes_observed': False, 'timestamp_strings_observed': []}])
        self.assertEqual(summary['database_only_quota_steps'], [])
        self.assertIsNone(summary['latest_database_only_quota_step'])
        self.assertEqual(index.turns[0]['execution_complete'], 'not established')


if __name__ == '__main__':
    unittest.main()
