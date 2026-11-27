"""Rendering keeps ambiguous native coordinates separate from inferred chronology."""
from __future__ import annotations

from contextlib import closing, redirect_stderr
import io
import json
import re
import sqlite3
import unittest
import zipfile

from test_exporter import Base, ID, base_records, wire


FIRST_PREVIEW = 'FIRST_RECORDED_PREVIEW_FIXTURE'
SECOND_PREVIEW = 'SECOND_RECORDED_PREVIEW_FIXTURE'
FINAL_SOURCE = 'FINAL_CAPTURED_SOURCE_OBSERVATION_FIXTURE'
SIDECAR = 'UNASSIGNED_FULL_OUTPUT_FIXTURE\n```\nOriginal café 日本語 tool bytes.\n'
DIAGNOSTICS = {4: 'DB_ONLY_QUOTA_PAYLOAD_FIXTURE_4', 8: 'DB_ONLY_QUOTA_PAYLOAD_FIXTURE_8'}


class SourceOrderRenderTests(Base):
    def export_members(self, *controls):
        with redirect_stderr(io.StringIO()) as errors:
            result = self.export('--tokenizer', 'none', '--records-jsonl', '--handoff', *controls)
        self.diagnostic_output = errors.getvalue()
        with zipfile.ZipFile(result['archive']) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        manifest_name = next(name for name in members if name.endswith('.manifest.json'))
        stem = manifest_name.removesuffix('.manifest.json')
        return members, json.loads(members[manifest_name]), members[stem+'.md'].decode('utf-8'), stem

    def configure_ambiguous_source(self):
        rows = base_records()
        marker = f'file:///C:/Users/example/.gemini/antigravity/brain/{ID}/.system_generated/steps/2/output.txt'
        rows[2]['content'] = 'Output was truncated. Full output: '+marker+'\n'+FIRST_PREVIEW
        repeated = dict(rows[2])
        repeated['content'] = 'Output was truncated. Full output: '+marker+'\n'+SECOND_PREVIEW
        rows.extend([repeated, {'step_index': 9, 'source': 'MODEL', 'type': 'PLANNER_RESPONSE',
                                'status': 'DONE', 'created_at': '2026-11-09T19:48:06Z',
                                'content': FINAL_SOURCE}])
        self.source_rows = rows
        self.log.write_bytes(b''.join((json.dumps(row, ensure_ascii=False)+'\n').encode('utf-8') for row in rows))
        sidecar = self.root/'antigravity'/'brain'/ID/'.system_generated'/'steps'/'2'/'output.txt'
        sidecar.write_text(SIDECAR, encoding='utf-8')
        database = self.root/'antigravity'/'conversations'/(ID+'.db')
        with closing(sqlite3.connect(database)) as db, db:
            for step, marker in DIAGNOSTICS.items():
                payload = wire(1, (marker+' quota HTTP 429 reset 2026-11-30T01:02:03Z').encode('utf-8'))
                db.execute('UPDATE steps SET step_payload=? WHERE idx=?', (payload, step))

    def test_database_diagnostics_follow_all_source_events_without_turn_or_latest_inference(self):
        self.configure_ambiguous_source()
        _, manifest, markdown, _ = self.export_members()
        source_headings = re.findall(r'^## Step [^\n]+', markdown, re.MULTILINE)
        self.assertEqual(len(source_headings), len(self.source_rows))
        self.assertEqual(len(set(source_headings)), len(source_headings))
        self.assertEqual([int(value) for value in re.findall(r'^Message ordinal: ([0-9]+)', markdown, re.MULTILINE)],
                         list(range(1, len(self.source_rows)+1)))
        final_source_position = markdown.index(FINAL_SOURCE)
        appendix_position = markdown.index('## Database-only session diagnostics')
        self.assertGreater(appendix_position, final_source_position)
        for step, marker in DIAGNOSTICS.items():
            self.assertGreater(markdown.index('## Database-only record '+str(step)+' '), appendix_position)
            self.assertGreater(markdown.index(marker), appendix_position)
        self.assertIn('temporal placement unknown', markdown)
        scope = manifest['database_diagnostic_scope']
        self.assertFalse(scope['temporal_association_inferred'])
        self.assertEqual(scope['included'], 2)
        self.assertEqual(scope['unknown_selection_membership_omitted'], 0)
        interruption = manifest['interruption']
        self.assertIsNone(interruption['latest_database_only_quota_step'])
        self.assertEqual(interruption['highest_database_only_quota_step'], 8)
        self.assertEqual(interruption['unassigned_database_only_quota_steps'], [4, 8])
        self.assertEqual(interruption['timestamp_strings_in_latest_quota_step'], [])
        self.assertTrue(all(turn['database_only_quota_steps'] == [] for turn in manifest['turns']))
        self.assertTrue(all(turn['execution_complete'] == 'not_established_by_transcript'
                            for turn in manifest['turns']))

    def test_partial_source_omits_ambiguous_database_payloads_and_output_sidecar(self):
        self.configure_ambiguous_source()
        # Includes the final source record and a repeated native ID whose other
        # occurrence is outside the selection; neither fact assigns DB/sidecar evidence.
        members, manifest, markdown, stem = self.export_members('--from-message', '8')
        self.assertEqual(manifest['selection']['records'], 2)
        self.assertIn(SECOND_PREVIEW, markdown)
        self.assertIn(FINAL_SOURCE, markdown)
        self.assertNotIn(FIRST_PREVIEW, markdown)
        self.assertEqual([json.loads(line) for line in members[stem+'.records.jsonl'].splitlines()],
                         self.source_rows[7:])
        self.assertEqual(manifest['database_only_diagnostics'], [])
        scope = manifest['database_diagnostic_scope']
        self.assertEqual(scope['included'], 0)
        self.assertEqual(scope['unknown_selection_membership_omitted'], 2)
        self.assertFalse(scope['temporal_association_inferred'])
        self.assertEqual(manifest['recovered_tool_outputs'], [])
        self.assertEqual(len(manifest['unavailable_recovery']), 1)
        missing = manifest['unavailable_recovery'][0]
        self.assertEqual(missing['native_step_occurrences'], 2)
        self.assertIn('omitted from partial selection', missing['reason'])
        for name, data in members.items():
            self.assertNotIn(SIDECAR.encode('utf-8'), data, name)
            for marker in DIAGNOSTICS.values():
                self.assertNotIn(marker.encode('utf-8'), data, name)
        self.assertNotIn('## Database-only record', markdown)
        self.assertNotIn('## Unassigned recorded output sidecar', markdown)

    def test_full_source_keeps_repeated_previews_and_one_unassigned_sidecar(self):
        self.configure_ambiguous_source()
        members, manifest, markdown, stem = self.export_members()
        self.assertEqual(markdown.count(FIRST_PREVIEW), 1)
        self.assertEqual(markdown.count(SECOND_PREVIEW), 1)
        self.assertEqual(markdown.count(SIDECAR), 1)
        self.assertNotIn('### Additional recorded full-output sidecar', markdown)
        self.assertGreater(markdown.index('## Unassigned recorded output sidecar'), markdown.index(FINAL_SOURCE))
        self.assertIn('not attributed to any particular message, turn, or revision', markdown)
        recovered = manifest['recovered_tool_outputs']
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]['step'], 2)
        self.assertEqual(recovered[0]['occurrence_assignment'], 'unknown')
        self.assertEqual(recovered[0]['native_step_occurrences'], 2)
        self.assertEqual(manifest['unavailable_recovery'], [])
        self.assertEqual([json.loads(line) for line in members[stem+'.records.jsonl'].splitlines()], self.source_rows)
        self.assertEqual(members[stem+'.jsonl'], self.log.read_bytes())

    def test_unique_step_still_recovers_output_for_full_and_partial_views(self):
        for controls in ((), ('--message', '3')):
            with self.subTest(controls=controls):
                _, manifest, markdown, _ = self.export_members(*controls)
                self.assertIn(base_records()[2]['content'], markdown)
                self.assertEqual(markdown.count('FULL SIDECAR OUTPUT'), 1)
                self.assertIn('### Additional recorded full-output sidecar', markdown)
                self.assertNotIn('## Unassigned recorded output sidecar', markdown)
                self.assertEqual(len(manifest['recovered_tool_outputs']), 1)
                self.assertEqual(manifest['recovered_tool_outputs'][0]['step'], 2)
                self.assertEqual(manifest['unavailable_recovery'], [])


if __name__ == '__main__':
    unittest.main()
