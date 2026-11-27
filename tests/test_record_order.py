"""Synthetic fidelity regressions for repeated and nonmonotonic native steps."""
from __future__ import annotations

from argparse import Namespace
from contextlib import closing, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from antigravity_exporter.model import EventIndex, ORDER_EXAMPLE_LIMIT, audit_database, interruption_summary
from antigravity_exporter.selection import resolve_since, turn_listing
from antigravity_exporter.render import render
from antigravity_exporter.storage import ExportError, Source


ID = '11111111-2222-4333-8444-555555555555'


def event(step, content='synthetic event', *, user=False, **extra):
    return {'step_index': step, 'source': 'USER_EXPLICIT' if user else 'MODEL',
            'type': 'USER_INPUT' if user else 'GENERIC', 'status': 'DONE',
            'content': content, **extra}


def line(record, *, newline=b'\n', compact=True):
    return json.dumps(record, ensure_ascii=False,
                      separators=(',', ':') if compact else None).encode('utf-8') + newline


class RecordOrderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.serial = 0

    def index(self, records=None, *, raw=None, **kwargs):
        self.serial += 1
        source = self.root / f'source-{self.serial}.jsonl'
        source.write_bytes(raw if raw is not None else b''.join(line(row) for row in records))
        index = EventIndex(source, self.root / f'index-{self.serial}.db', **kwargs)
        self.addCleanup(index.close)
        return index

    def arguments(self, **kwargs):
        return Namespace(mode='thread', **kwargs)

    def database(self, index, steps):
        root = self.root / f'store-{self.serial}'
        path = root / 'antigravity' / 'conversations' / (ID + '.db')
        path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(path)) as con, con:
            con.execute('CREATE TABLE trajectory_meta (cascade_id TEXT)')
            con.execute('INSERT INTO trajectory_meta VALUES (?)', (ID,))
            con.execute('CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER, step_payload BLOB)')
            con.executemany('INSERT INTO steps VALUES (?,15,3,?)', [(step, b'') for step in steps])
        with Source(root=root) as source:
            return audit_database(source, {'variant': 'antigravity', 'id': ID}, index)

    def test_identical_repeated_step_is_preserved_as_distinct_source_record(self):
        rows = [event(0, user=True), event(1), event(1)]
        index = self.index(rows)
        selected = index.selection(self.arguments())
        self.assertEqual(list(index.records()), list(enumerate(rows, 1)))
        self.assertEqual(selected['records'], 3)
        self.assertTrue(selected['all_records_selected'])
        self.assertEqual(selected['record_identity'], 'source_ordinal')
        self.assertTrue(selected['source_order_preserved'])
        order = index.audit['step_order']
        self.assertFalse(order['monotonic_unique'])
        self.assertEqual(order['duplicate_occurrences'], 1)
        self.assertEqual(order['duplicate_identical_source_bytes'], 1)
        self.assertEqual(order['duplicate_different_source_bytes'], 0)
        self.assertEqual(order['backward_transitions'], 0)

    def test_different_bytes_and_later_identical_occurrence_are_counted_separately(self):
        rows = [event(0, user=True), event(1, 'first bytes'), event(1, 'other bytes'), event(1, 'first bytes')]
        index = self.index(rows)
        order = index.audit['step_order']
        self.assertEqual(order['distinct_steps'], 2)
        self.assertEqual(order['duplicate_step_ids'], 1)
        self.assertEqual(order['duplicate_occurrences'], 2)
        self.assertEqual(order['duplicate_identical_source_bytes'], 1)
        self.assertEqual(order['duplicate_different_source_bytes'], 1)
        examples = order['examples']['duplicate_occurrences']
        self.assertEqual([row['classification'] for row in examples], ['different_source_bytes', 'identical_source_bytes'])
        self.assertIsNone(examples[0]['identical_prior_ordinal'])
        self.assertEqual(examples[1]['identical_prior_ordinal'], 2)
        self.assertEqual(examples[1]['first_occurrence_ordinal'], 2)
        self.assertEqual(examples[1]['source_sha256'], hashlib.sha256(line(rows[1])).hexdigest())

    def test_byte_comparison_does_not_infer_semantic_revision_from_whitespace(self):
        record = event(1)
        index = self.index(raw=line(event(0, user=True)) + line(record) + line(record, compact=False, newline=b'\r\n'))
        order = index.audit['step_order']
        self.assertEqual(order['duplicate_identical_source_bytes'], 0)
        self.assertEqual(order['duplicate_different_source_bytes'], 1)
        self.assertIn('semantic revision or replay is not inferred', order['duplicate_comparison'])

    def test_backward_unique_steps_close_gaps_without_sorting_source_records(self):
        rows = [event(0, user=True), event(3), event(1), event(2)]
        index = self.index(rows)
        index.selection(self.arguments())
        self.assertEqual([row['step_index'] for _, row in index.records()], [0, 3, 1, 2])
        self.assertEqual(index.audit['missing_step_ranges'], [])
        self.assertEqual(index.audit['step_order']['backward_transitions'], 1)
        self.assertEqual(index.audit['step_order']['duplicate_occurrences'], 0)
        self.assertEqual(index.audit['last_step'], 3)
        self.assertEqual(index.audit['last_source_step'], 2)
        self.assertEqual(index.turns[0]['last_step'], 2)
        self.assertEqual(index.turns[0]['max_step'], 3)

    def test_missing_ranges_are_derived_from_distinct_native_identifiers(self):
        index = self.index([event(5, user=True), event(2), event(5), event(7)])
        self.assertEqual(index.audit['missing_step_ranges'], [[0, 1], [3, 4], [6, 6]])
        self.assertEqual(index.audit['first_step'], 2)
        self.assertEqual(index.audit['first_source_step'], 5)
        self.assertEqual(index.audit['step_order']['distinct_steps'], 3)
        self.assertEqual(index.turns[0]['min_step'], 2)

    def test_strict_step_order_rejects_repetition_or_backward_move_without_source_mutation(self):
        for steps in ([0, 1, 1], [0, 3, 1]):
            with self.subTest(steps=steps):
                raw = b''.join(line(event(step)) for step in steps)
                with self.assertRaisesRegex(ExportError, '--strict-step-order'):
                    self.index(raw=raw, strict_step_order=True)
                self.assertEqual((self.root / f'source-{self.serial}.jsonl').read_bytes(), raw)

    def test_strict_step_order_still_accepts_increasing_gapped_coordinates(self):
        index = self.index([event(0, user=True), event(3)], strict_step_order=True)
        self.assertTrue(index.audit['step_order']['strict_requested'])
        self.assertTrue(index.audit['step_order']['monotonic_unique'])
        self.assertEqual(index.audit['missing_step_ranges'], [[1, 2]])

    def test_native_step_selector_returns_every_occurrence_and_message_selects_one(self):
        rows = [event(0, user=True), event(1, 'one'), event(2), event(1, 'two')]
        index = self.index(rows)
        selected = index.selection(self.arguments(step=1))
        self.assertEqual(selected['records'], 2)
        self.assertEqual(selected['distinct_steps'], 1)
        self.assertFalse(selected['all_records_selected'])
        self.assertEqual(list(index.records()), [(2, rows[1]), (4, rows[3])])
        index.selection(Namespace(mode='message', message=4))
        self.assertEqual(list(index.records()), [(4, rows[3])])

    def test_native_ranges_filter_identifiers_while_retaining_source_order(self):
        rows = [event(0, user=True), event(4), event(2), event(1), event(2, 'second occurrence')]
        index = self.index(rows)
        index.selection(self.arguments(from_step=1, to_step=2))
        self.assertEqual([ordinal for ordinal, _ in index.records()], [3, 4, 5])
        self.assertEqual([row['step_index'] for _, row in index.records()], [2, 1, 2])

    def test_last_n_steps_preserves_last_record_occurrences(self):
        rows = [event(0, user=True), event(1), event(2), event(1)]
        index = self.index(rows)
        index.selection(self.arguments(last_n_steps=2))
        self.assertEqual(list(index.records()), [(3, rows[2]), (4, rows[3])])

    def test_since_turn_uses_source_ordinal_and_keeps_later_lower_step(self):
        rows = [event(0, 'earlier prompt', user=True), event(8),
                event(5, 'selected prompt', user=True), event(2), event(7)]
        index = self.index(rows)
        args = self.arguments(since_turn=2)
        audit = resolve_since(index, args)
        self.assertEqual(args.from_message, 3)
        self.assertFalse(hasattr(args, 'from_step'))
        self.assertEqual(audit['first_step'], 5)
        self.assertEqual(audit['first_ordinal'], 3)
        self.assertEqual(audit['selection_coordinate'], 'source_ordinal')
        index.selection(args)
        self.assertEqual(list(index.records()), list(enumerate(rows[2:], 3)))

    def test_repeated_user_step_requires_occurrence_and_preserves_observed_turns(self):
        rows = [event(0, 'same original prompt', user=True), event(1),
                event(0, 'same original prompt', user=True), event(1, 'later bytes')]
        index = self.index(rows)
        self.assertEqual(len(index.turns), 2)
        self.assertEqual([row['first_ordinal'] for row in turn_listing(index)], [1, 3])
        with self.assertRaisesRegex(ExportError, 'matches 2 user turns'):
            resolve_since(index, self.arguments(since_id='step:0'))
        args = self.arguments(since='step:0', since_occurrence=2)
        resolve_since(index, args)
        index.selection(args)
        self.assertEqual(list(index.records()), list(enumerate(rows[2:], 3)))

    def test_all_native_bytes_include_repeated_records_gaps_and_partial_tail(self):
        raw = (line(event(0, user=True), newline=b'\r\n') + b'\n' + line(event(2)) + b'\0\0\n'
               + line(event(1)) + line(event(2)) + b'{"step_index":')
        index = self.index(raw=raw)
        selected = index.selection(self.arguments())
        path, receipt = index.selected_source(self.root / 'copy.jsonl', selected)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(receipt['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertTrue(receipt['byte_exact_captured_prefix'])
        self.assertTrue(receipt['partial_tail_retained'])
        self.assertEqual(selected['records'], 4)

    def test_disjoint_native_selection_keeps_exact_duplicate_occurrence_bytes(self):
        rows = [line(event(0, user=True)), line(event(2, 'one'), newline=b'\r\n'),
                line(event(1)), line(event(2, 'two'), compact=False)]
        index = self.index(raw=b''.join(rows))
        selected = index.selection(self.arguments(step=2))
        path, receipt = index.selected_source(self.root / 'selected.jsonl', selected)
        self.assertEqual(path.read_bytes(), rows[1] + rows[3])
        self.assertEqual(len(receipt['ranges']), 2)
        self.assertTrue(receipt['byte_exact_source_records'])
        self.assertFalse(receipt['byte_exact_captured_prefix'])

    def test_order_examples_are_bounded_while_all_occurrences_are_counted(self):
        rows = [event(step) for step in [1, 0] * (ORDER_EXAMPLE_LIMIT + 5)]
        index = self.index(rows)
        order = index.audit['step_order']
        self.assertEqual(order['duplicate_occurrences'], len(rows) - 2)
        self.assertEqual(order['duplicate_identical_source_bytes'], len(rows) - 2)
        self.assertEqual(order['backward_transitions'], ORDER_EXAMPLE_LIMIT + 5)
        for kind, count in [('duplicate_occurrences', len(rows) - 2),
                            ('backward_transitions', ORDER_EXAMPLE_LIMIT + 5)]:
            self.assertEqual(len(order['examples'][kind]), ORDER_EXAMPLE_LIMIT)
            self.assertEqual(order['examples_truncated'][kind], count - ORDER_EXAMPLE_LIMIT)

    def test_database_step_sets_can_match_without_one_to_one_record_match(self):
        index = self.index([event(0, user=True), event(1), event(1, 'other bytes'), event(2)])
        audit, diagnostics = self.database(index, [0, 1, 2])
        self.assertEqual(diagnostics, [])
        self.assertTrue(audit['step_sets_match'])
        self.assertFalse(audit['one_to_one_step_match'])
        self.assertFalse(audit['matched_exactly'])
        self.assertFalse(audit['payloads_compared'])
        self.assertEqual(audit['jsonl_records'], 4)
        self.assertEqual(audit['jsonl_unique_steps'], 3)
        self.assertEqual(audit['jsonl_duplicate_occurrences'], 1)

    def test_database_unique_identifier_match_does_not_assert_chronology(self):
        index = self.index([event(0, user=True), event(2), event(1)])
        audit, diagnostics = self.database(index, [0, 1, 2])
        self.assertEqual(diagnostics, [])
        self.assertTrue(audit['step_sets_match'])
        self.assertTrue(audit['one_to_one_step_match'])
        self.assertFalse(audit['payloads_compared'])
        self.assertIn('chronology are not compared', audit['comparison_scope'])

    def test_empty_database_does_not_hide_repeated_log_only_steps(self):
        index = self.index([event(0, user=True), event(1), event(1)])
        audit, diagnostics = self.database(index, [])
        self.assertEqual(diagnostics, [])
        self.assertEqual(audit['sqlite_range'], [None, None])
        self.assertEqual(audit['sqlite_records'], 0)
        self.assertEqual(audit['log_only_steps'], [0, 1])
        self.assertFalse(audit['step_sets_match'])
        self.assertFalse(audit['matched_exactly'])

    def test_nonmonotonic_or_repeated_steps_prevent_quota_turn_or_time_inference(self):
        diagnostics = [{'step_index': 3, 'step_type': 17, 'quota_bytes_observed': True,
                        'http_429_bytes_observed': True, 'timestamp_strings_observed': ['2030-01-01T00:00:00Z']}]
        for steps in ([0, 1, 1], [0, 2, 1]):
            with self.subTest(steps=steps):
                index = self.index([event(step, user=(number == 0)) for number, step in enumerate(steps)])
                summary = interruption_summary(index, diagnostics)
                self.assertEqual(summary['database_only_quota_steps'], [3])
                self.assertEqual(summary['unassigned_database_only_quota_steps'], [3])
                self.assertEqual(summary['highest_database_only_quota_step'], 3)
                self.assertIsNone(summary['latest_database_only_quota_step'])
                self.assertEqual(summary['timestamp_strings_in_latest_quota_step'], [])
                self.assertEqual(index.turns[0]['database_only_quota_steps'], [])
                self.assertEqual(index.turns[0]['execution_complete'], 'not_established_by_transcript')
                self.assertIn('ambiguous', index.turns[0]['database_only_quota_attribution'])

    def test_human_turn_listing_distinguishes_source_span_from_native_range(self):
        from antigravity_exporter.cli import main
        index = self.index([event(10, user=True), event(2, 'first'), event(2, 'second')])
        output = io.StringIO()
        with patch.dict(os.environ, {'AGY_EXPORT_CONFIG': str(self.root/'config.json')}), redirect_stdout(output):
            code = main(['turns', '--jsonl', str(index.source), '--no-pager', '--no-progress'])
        self.assertEqual(code, 0)
        text = output.getvalue()
        self.assertIn('Turn', text)
        self.assertIn('1–3', text)
        self.assertIn('2–10', text)
        self.assertNotIn('10–2', text)
        self.assertRegex(text.casefold(), r'message|ordinal')

    def test_per_record_omission_receipts_identify_repeated_step_occurrences(self):
        index = self.index([event(0, user=True), event(2, 'AAAAA'), event(2, 'BBBBB')])
        selection = index.selection(self.arguments())
        session = {'id': ID, 'variant': 'antigravity', 'title': 'Synthetic fixture'}
        info = {'sha256': hashlib.sha256(index.source.read_bytes()).hexdigest()}
        for style in ('none', 'summary', 'tail', 'full'):
            with self.subTest(style=style):
                result = render(index, session, selection, info, {}, [], self.root/(style+'.md'),
                                {'tool_outputs': style, 'max_tool_chars': 3})
                self.assertEqual([item['source_ordinal'] for item in result['view_omissions']], [2, 3])
                self.assertEqual([item['step'] for item in result['view_omissions']], [2, 2])

    def test_unique_sidecar_receipts_include_the_source_occurrence(self):
        text = (f'The output was truncated. Full output: file:///synthetic/.gemini/antigravity/brain/{ID}'
                '/.system_generated/steps/2/output.txt')
        index = self.index([event(0, user=True), event(2, text)])
        selection = index.selection(self.arguments())
        session = {'id': ID, 'variant': 'antigravity', 'title': 'Synthetic fixture'}
        info = {'sha256': hashlib.sha256(index.source.read_bytes()).hexdigest()}
        for kind in ('present', 'missing', 'invalid_utf8'):
            with self.subTest(kind=kind):
                store = self.root/kind
                path = store/'antigravity'/'brain'/ID/'.system_generated'/'steps'/'2'/'output.txt'
                path.parent.mkdir(parents=True)
                if kind != 'missing':
                    path.write_bytes(b'synthetic sidecar' if kind == 'present' else b'\xff')
                with Source(root=store) as source:
                    result = render(index, session, selection, info, {}, [], self.root/(kind+'.md'),
                                    {'recover_tool_outputs': True}, src=source)
                receipts = result['recovered_outputs' if kind == 'present' else 'unavailable_recovery']
                self.assertEqual(len(receipts), 1)
                self.assertEqual(receipts[0]['source_ordinal'], 2)
                self.assertEqual(receipts[0]['step'], 2)

    def test_full_export_retains_database_prefix_diagnostics_without_turn_assignment(self):
        from antigravity_exporter.cli import parser, run
        store = self.root/'prefix-store'
        logs = store/'antigravity'/'brain'/ID/'.system_generated'/'logs'
        logs.mkdir(parents=True)
        (logs/'transcript_full.jsonl').write_bytes(line(event(10, user=True)) + line(event(11)))
        annotation = store/'antigravity'/'annotations'/(ID+'.pbtxt')
        annotation.parent.mkdir()
        annotation.write_text('title:"Synthetic prefix fixture"', encoding='utf-8')
        path = store/'antigravity'/'conversations'/(ID+'.db')
        path.parent.mkdir()
        quota = b'quota HTTP 429 reset 2030-01-01T00:00:00Z'
        note = b'SYNTHETIC_PREFIX_DIAGNOSTIC'
        with closing(sqlite3.connect(path)) as con, con:
            con.execute('CREATE TABLE trajectory_meta (cascade_id TEXT)')
            con.execute('INSERT INTO trajectory_meta VALUES (?)', (ID,))
            con.execute('CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER, step_payload BLOB)')
            con.executemany('INSERT INTO steps VALUES (?,?,3,?)', [
                (4, 17, b'\x0a'+bytes([len(quota)])+quota),
                (8, 15, b'\x0a'+bytes([len(note)])+note), (10, 15, b''), (11, 15, b'')])
        for name, controls in [('full', []), ('partial', ['--from-step', '11'])]:
            with self.subTest(name=name), patch.dict(os.environ, {'AGY_EXPORT_CONFIG': str(self.root/'config.json')}):
                args = parser().parse_args(['export', '--root', str(store), '--id', ID, '--no-archive',
                                            '--archive-backend', 'python', '--archive-method', 'Deflate',
                                            '--archive-level', '6', '--tokenizer', 'none', '--no-progress',
                                            '--out-dir', str(self.root/name), *controls])
                result = run(args)
                metadata = json.loads(Path(result['manifest']).read_text(encoding='utf-8'))
                markdown = Path(result['markdown']).read_text(encoding='utf-8')
                self.assertEqual(metadata['turns'][0]['database_only_quota_steps'], [])
                self.assertEqual(metadata['turns'][0]['execution_complete'], 'not_established_by_transcript')
                self.assertEqual(metadata['interruption']['unassigned_database_only_quota_steps'], [4])
                if name == 'full':
                    self.assertTrue(metadata['selection']['all_records_selected'])
                    self.assertEqual([item['step_index'] for item in metadata['database_only_diagnostics']], [4, 8])
                    self.assertIn(note.decode(), markdown)
                    self.assertIn(quota.decode(), markdown)
                    self.assertEqual(metadata['database_diagnostic_scope']['omitted_count'], 0)
                else:
                    self.assertFalse(metadata['selection']['all_records_selected'])
                    self.assertEqual(metadata['database_only_diagnostics'], [])
                    self.assertNotIn(note.decode(), markdown)
                    self.assertNotIn(quota.decode(), markdown)


if __name__ == '__main__':
    unittest.main()
