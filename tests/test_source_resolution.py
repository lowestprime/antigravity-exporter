"""Synthetic regressions for catalog/storage routing and hub metadata discovery."""
from __future__ import annotations

import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from antigravity_exporter.catalog import HUB_SCHEMA, has_full_transcript, has_transcript, modified_key, read_hub_catalog
from antigravity_exporter.model import capture_transcript, resolve, sessions
from antigravity_exporter.storage import ExportError, Source

SESSION = '11111111-2222-4333-8444-555555555555'
CHILD = '22222222-2222-4333-8444-555555555555'
PROJECT = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'


def vint(value):
    output = bytearray()
    while value > 127:
        output.append((value & 127) | 128)
        value >>= 7
    output.append(value)
    return bytes(output)


def field(number, value):
    if isinstance(value, str):
        value = value.encode('utf-8')
    if isinstance(value, int):
        return vint(number << 3) + vint(value)
    return vint((number << 3) | 2) + vint(len(value)) + value


def hub_entry(ident=SESSION, *, title='Hub title', project=PROJECT, parent='', stamp=1795718405,
              count=3, agent='', extra=b''):
    metadata = field(18, project) + field(6, parent or ident)
    if parent:
        metadata += field(5, parent)
    if agent:
        metadata += field(8, field(1, agent) + field(2, 'Synthetic worker'))
    summary = field(2, count) + field(3, field(1, stamp) + field(2, 342105400)) + field(17, metadata)
    if title:
        summary += field(1, title)
    entry = field(1, ident) + field(2, summary + extra)
    return field(1, entry)


class SourceResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / '.gemini'
        self.root.mkdir()
        project = self.root / 'config' / 'projects' / (PROJECT + '.json')
        project.parent.mkdir(parents=True)
        project.write_text(json.dumps({'id': PROJECT, 'name': 'Example project'}))

    def log(self, ident=SESSION, variant='antigravity', filename='transcript_full.jsonl'):
        path = self.root / variant / 'brain' / ident / '.system_generated' / 'logs' / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'step_index': 0, 'source': 'USER_EXPLICIT', 'type': 'USER_INPUT',
                                    'status': 'DONE', 'content': 'Synthetic request.'}) + '\n')
        return path

    def summary(self, variant='antigravity-cli', *, ident=SESSION, storage='antigravity', stamp='2026-11-25T00:00:00Z', project=PROJECT):
        path = self.root / variant / 'conversation_summaries.db'
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as con, con:
            con.execute('CREATE TABLE IF NOT EXISTS conversation_summaries '
                        '(conversation_id TEXT, title TEXT, project_id TEXT, last_modified_time TEXT, app_data_dir TEXT)')
            con.execute('INSERT INTO conversation_summaries VALUES (?,?,?,?,?)',
                        (ident, 'SQLite title', project, stamp, storage))

    def hub(self, raw=None, variant='antigravity', filename='agyhub_summaries_proto.pb'):
        path = self.root / variant / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw if raw is not None else hub_entry())
        return path

    def test_app_data_dir_routes_metadata_to_real_storage(self):
        self.log(); self.summary()
        with Source(root=self.root) as source:
            catalog = sessions(source)
            self.assertEqual(len(catalog), 1)
            row = resolve(catalog, [SESSION])[0]
            self.assertEqual(row['variant'], 'antigravity')
            self.assertEqual(row['project'], 'Example project')
            self.assertEqual(row['source_availability'], 'full')
            self.assertEqual(row['metadata_provenance'][0]['catalog_variant'], 'antigravity-cli')
            path, _ = capture_transcript(source, row)
            self.assertGreater(path.stat().st_size, 0)

    def test_two_catalogs_same_storage_merge_only_identity(self):
        self.log(); self.summary(); self.summary('antigravity')
        with Source(root=self.root) as source:
            rows = sessions(source)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]['catalog_sources']), 2)

    def test_true_variant_collision_stays_ambiguous(self):
        self.log(); self.log(variant='antigravity-cli')
        with Source(root=self.root) as source:
            rows = sessions(source)
            with self.assertRaisesRegex(ExportError, '2 storage identities'):
                resolve(rows, [SESSION])
            self.assertEqual(resolve(rows, ['antigravity-cli:' + SESSION])[0]['variant'], 'antigravity-cli')
            self.assertEqual(resolve(rows, ['antigravity:' + SESSION[:8]])[0]['variant'], 'antigravity')
            self.assertEqual(len(resolve(rows, ['antigravity:' + SESSION, 'antigravity-cli:' + SESSION])), 2)

    def test_explicit_variant_cannot_be_broadened_by_selector(self):
        self.log(); self.log(variant='antigravity-cli')
        with Source(root=self.root) as source:
            with self.assertRaisesRegex(ExportError, '0 storage identities'):
                resolve(sessions(source), ['antigravity-cli:' + SESSION], variant='antigravity')

    def test_variant_root_stays_scoped_to_selected_variant(self):
        self.log(); self.summary()
        with Source(root=self.root / 'antigravity-cli') as source:
            self.assertEqual(sessions(source), [])

    def test_untrusted_app_data_dir_does_not_follow_external_paths(self):
        self.summary(storage='../outside')
        with Source(root=self.root) as source:
            with self.assertRaisesRegex(ExportError, 'no external path was followed'):
                sessions(source)

    def test_absent_app_data_dir_uses_catalog_variant(self):
        self.log(variant='antigravity-cli'); self.summary(storage='')
        with Source(root=self.root) as source:
            row = sessions(source)[0]
            self.assertEqual(row['variant'], 'antigravity-cli')

    def test_hub_catalog_recovers_project_and_child_relationships(self):
        self.log(); self.log(CHILD)
        self.hub(hub_entry() + hub_entry(CHILD, title='', parent=SESSION, agent='ExampleWorker'))
        with Source(root=self.root) as source:
            rows = resolve(sessions(source), project='Example project')
        self.assertEqual(len(rows), 2)
        child = next(row for row in rows if row['id'] == CHILD)
        self.assertEqual(child['title'], 'ExampleWorker')
        self.assertEqual(child['parent_conversation_id'], SESSION)
        self.assertEqual(child['root_conversation_id'], SESSION)
        self.assertEqual(child['summary_step_count'], 3)
        self.assertEqual(child['modified'], '2026-11-26T18:40:05.3421054Z')

    def test_catalog_timestamps_normalize_fraction_widths_and_timezones(self):
        reference = modified_key('2026-11-26T18:40:05.3421054Z')
        self.assertEqual(reference, modified_key('2026-11-26 18:40:05.342105400+00:00'))
        self.assertEqual(reference, modified_key('2026-11-26T10:40:05.342105400-08:00'))
        self.assertGreater(reference, modified_key('2026-11-25T23:59:59Z'))
        self.assertGreater(reference, modified_key('invalid timestamp'))
        self.assertEqual(modified_key('2026-11-26T18:40:05.1Z'),
                         modified_key('2026-11-26T18:40:05.100000000Z'))
        self.assertLess(modified_key('2026-11-26T18:40:05.100000001Z'),
                        modified_key('2026-11-26T18:40:05.100000002Z'))
        self.assertGreater(modified_key('2026-11-26T18:40:05.100000001Z'),
                           modified_key('2026-11-26T18:40:05.100000000Z'))

    def test_newer_hub_metadata_wins_and_conflicts_remain_auditable(self):
        self.log(); self.summary(); path = self.hub()
        with Source(root=self.root) as source:
            row = sessions(source)[0]
        self.assertEqual(row['title'], 'Hub title')
        self.assertEqual(row['metadata_provenance'][0]['adapter'], HUB_SCHEMA)
        self.assertEqual(row['metadata_provenance'][0]['source_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(row['metadata_conflicts']['title']['recorded_values'], ['Hub title', 'SQLite title'])

    def test_unknown_wire_fields_reported_without_semantic_invention(self):
        self.log(); self.hub(hub_entry(extra=field(511, b'opaque private extension')))
        with Source(root=self.root) as source:
            row = sessions(source)[0]
        provenance = row['metadata_provenance'][0]
        self.assertIn(511, provenance['summary_field_numbers'])
        self.assertFalse(provenance['transcript_content_decoded'])
        self.assertFalse(provenance['raw_catalog_embedded'])
        self.assertNotIn('opaque private extension', json.dumps(row))
        self.assertNotIn('raw_entry_base64', provenance)

    def test_hub_metadata_snapshot_survives_later_mutation(self):
        self.log(); path = self.hub()
        with Source(root=self.root) as source:
            first = sessions(source)[0]
            path.write_bytes(hub_entry(title='Changed after capture'))
            second = sessions(source)[0]
        self.assertEqual(first['title'], second['title'])
        self.assertEqual(first['metadata_provenance'], second['metadata_provenance'])

    def test_hub_truncated_wire_rejected(self):
        self.hub(hub_entry()[:-1])
        with Source(root=self.root) as source:
            with self.assertRaisesRegex(ExportError, 'Truncated'):
                sessions(source)

    def test_hub_wrong_map_key_rejected(self):
        self.hub(hub_entry(ident='not-a-uuid'))
        with Source(root=self.root) as source:
            with self.assertRaisesRegex(ExportError, 'identity'):
                sessions(source)

    def test_duplicate_known_scalar_field_rejected(self):
        self.hub(hub_entry(extra=field(2, 99)))
        with Source(root=self.root) as source:
            with self.assertRaisesRegex(ExportError, 'supported summary layout'):
                sessions(source)

    def test_invalid_timestamp_rejected(self):
        self.hub(hub_entry(stamp=253402300800))
        with Source(root=self.root) as source:
            with self.assertRaisesRegex(ExportError, 'UTC range'):
                sessions(source)

    def test_annotation_title_remains_explicit_display_override(self):
        self.log(); self.hub()
        path = self.root / 'antigravity' / 'annotations' / (SESSION + '.pbtxt')
        path.parent.mkdir(); path.write_text('title: "Chosen visible name"')
        with Source(root=self.root) as source:
            self.assertEqual(sessions(source)[0]['title'], 'Chosen visible name')

    def test_numeric_chunks_and_repeated_capture_use_distinct_staging(self):
        log = self.log(); raw = log.read_bytes(); log.unlink()
        chunks = log.parent / 'chunks' / 'transcript_full'; chunks.mkdir(parents=True)
        for index, offset in enumerate(range(0, len(raw), 5)):
            (chunks / f'{index}.jsonl').write_bytes(raw[offset:offset + 5])
        with Source(root=self.root) as source:
            row = sessions(source)[0]
            first, _ = capture_transcript(source, row)
            second, _ = capture_transcript(source, row)
            self.assertEqual(first.read_bytes(), raw)
            self.assertEqual(second.read_bytes(), raw)
            self.assertNotEqual(first, second)

    def test_duplicate_numeric_chunk_names_rejected(self):
        log = self.log(); log.unlink()
        chunks = log.parent / 'chunks' / 'transcript_full'; chunks.mkdir(parents=True)
        (chunks / '0.jsonl').write_bytes(b'first'); (chunks / '00.jsonl').write_bytes(b'duplicate')
        with Source(root=self.root) as source:
            row = sessions(source)[0]
            self.assertEqual(row['source_availability'], 'invalid-full-chunks')
            with self.assertRaisesRegex(ExportError, 'duplicate numeric names'):
                capture_transcript(source, row)

    def test_empty_chunk_directory_not_advertised_as_full_source(self):
        log = self.log(); log.unlink()
        (log.parent / 'chunks' / 'transcript_full').mkdir(parents=True)
        with Source(root=self.root) as source:
            row = sessions(source)[0]
        self.assertFalse(row['chunked_full'])
        self.assertEqual(row['source_availability'], 'catalog-only')

    def test_chunks_only_session_exports_and_audits(self):
        from antigravity_exporter.cli import parser, run
        log = self.log(); raw = log.read_bytes(); log.unlink()
        chunks = log.parent / 'chunks' / 'transcript_full'; chunks.mkdir(parents=True)
        (chunks / '0.jsonl').write_bytes(raw[:19]); (chunks / '1.jsonl').write_bytes(raw[19:])
        self.summary()
        with Source(root=self.root) as source:
            row = sessions(source)[0]
            self.assertFalse(row['full'])
            self.assertTrue(has_full_transcript(row))
            self.assertTrue(has_transcript(row))
        with patch.dict(os.environ, {'AGY_EXPORT_CONFIG': str(Path(self.temp.name) / 'config.json')}):
            args = parser().parse_args(['export', '--root', str(self.root), '--id', SESSION,
                '--no-progress', '--tokenizer', 'none', '--no-archive', '--out-dir', str(Path(self.temp.name) / 'exports')])
            result = run(args)
            self.assertEqual(result['selected_records'], 1)
            audit = run(parser().parse_args(['audit', '--root', str(self.root), '--id', SESSION, '--no-progress']))
            self.assertEqual(audit['coverage']['records'], 1)
            projects = run(parser().parse_args(['projects', '--root', str(self.root), '--no-progress']))
            self.assertEqual(projects[0]['exportable'], 1)

    def test_zip_and_directory_catalogs_are_equivalent(self):
        self.log(); self.summary(); self.hub()
        archive = Path(self.temp.name) / 'input.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
            for path in self.root.rglob('*'):
                if path.is_file():
                    z.write(path, '.gemini/' + path.relative_to(self.root).as_posix())
        with Source(root=self.root) as source, Source(archive=archive) as zipped:
            left, right = sessions(source), sessions(zipped)
        self.assertEqual([{key: row[key] for key in ('id', 'variant', 'project', 'title', 'selector')} for row in left],
                         [{key: row[key] for key in ('id', 'variant', 'project', 'title', 'selector')} for row in right])


if __name__ == '__main__':
    unittest.main()
