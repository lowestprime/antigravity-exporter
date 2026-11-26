"""Behavior regressions for repaired catalog-to-export transactions; synthetic only."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from test_exporter import Base, ID, PROJECT, base_records, make_store
from antigravity_exporter.cli import run,main,verify_export,parser,normalized_argv
from antigravity_exporter.storage import ExportError
from antigravity_exporter.archive_set import verify_archive_set

class IntegrationTests(Base):
    def members(self,result):
        with zipfile.ZipFile(result['archive']) as z:return {name:z.read(name) for name in z.namelist()}
    def test_unicode_filename_is_bounded_in_bytes(self):
        from antigravity_exporter.naming import filename_stem
        stem=filename_stem('{title}.md',{'title':'日本語🧠'*100})
        self.assertLessEqual(len(stem.encode('utf-8')),220)
        self.assertNotIn('�',stem)
    def test_indexed_source_drift_stops_render(self):
        index=self.index();index.selection(self.args('export'))
        self.log.write_bytes(self.raw.replace(b'Initial',b'Changed'))
        with self.assertRaisesRegex(ExportError,'changed after indexing'):list(index.records())
    def test_since_native_excludes_earlier_turn_and_preserves_original_bytes(self):
        result=self.export('--since-turn','2')
        data=self.members(result);native=next(v for k,v in data.items() if k.endswith('.jsonl'))
        expected=b''.join(self.raw.splitlines(keepends=True)[4:])
        self.assertEqual(native,expected)
        manifest=json.loads(next(v for k,v in data.items() if k.endswith('.manifest.json')))
        self.assertEqual(manifest['selection']['since']['turn'],2)
        self.assertEqual(manifest['source_copy']['sha256'],hashlib.sha256(expected).hexdigest())
        self.assertFalse(manifest['source_copy']['byte_exact_captured_prefix'])
    def test_subset_manifest_does_not_copy_earlier_diagnostic_text(self):
        import sqlite3
        from test_exporter import wire
        db=self.root/'antigravity'/'conversations'/(ID+'.db')
        with sqlite3.connect(db) as connection:
            connection.execute('UPDATE steps SET step_payload=? WHERE idx=4',
                               (wire(1,b'EARLIER_PRIVATE_DIAGNOSTIC fixture marker'),))
        data=self.members(self.export('--since-turn','2','--tokenizer','none'))
        manifest=json.loads(next(v for k,v in data.items() if k.endswith('.manifest.json')))
        self.assertEqual([r['step_index'] for r in manifest['database_only_diagnostics']],[8])
        self.assertEqual(manifest['database_diagnostic_scope']['outside_selection_omitted'],1)
        self.assertEqual(manifest['database_audit']['db_only_steps'],[4,8])
        self.assertFalse(manifest['source_coverage']['native_copy_contains_entire_captured_prefix'])
        self.assertTrue(all(b'EARLIER_PRIVATE_DIAGNOSTIC' not in value for value in data.values()))
    def test_share_redacts_title_in_handoff_names_and_batch_index(self):
        import shutil
        token='ghp_'+'a'*36
        annotation=self.root/'antigravity'/'annotations'/(ID+'.pbtxt')
        annotation.write_text('title:'+json.dumps('Fixture '+token),encoding='utf-8')
        other=self.root/'antigravity-ide'
        shutil.copytree(self.root/'antigravity',other)
        result=run(self.args('export','--root',str(self.root),'--all','--share','--handoff',
            '--archive-method','Deflate','--archive-backend','python','--archive-level','6',
            '--tokenizer','none','--no-progress','--out-dir',str(self.out)))
        data=self.members(result[0])
        self.assertIn('BATCH_INDEX.json',data)
        self.assertNotIn(token,json.dumps(result))
        self.assertTrue(all(token not in name and token.encode() not in value for name,value in data.items()))
    def test_nul_source_gap_retained_within_selected_contiguous_turn(self):
        lines=self.raw.splitlines(keepends=True);lines.insert(5,b'\0'*31+b'\n');self.log.write_bytes(b''.join(lines))
        result=self.export('--since-turn','2')
        data=self.members(result);native=next(v for k,v in data.items() if k.endswith('.jsonl'))
        self.assertIn(b'\0'*31+b'\n',native)
        manifest=json.loads(next(v for k,v in data.items() if k.endswith('.manifest.json')))
        self.assertEqual(len(manifest['source_copy']['source_gaps']),1)
    def test_nul_mixed_with_text_is_not_silently_accepted(self):
        self.log.write_bytes(self.raw+b'\0bad\n')
        with self.assertRaises(ExportError):self.export()
    def test_bounded_cli_parts_reconstruct_original_native(self):
        # A single large literal cannot be split by JSON line; bounded engine
        # must segment it with exact reconstruction offsets instead.
        row=base_records()[0];row['content']=os.urandom(120000).hex()
        raw=(json.dumps(row)+'\n').encode();self.log.write_bytes(raw)
        result=self.export('--max-archive-bytes','16384','--tokenizer','none','--no-verify-db')
        self.assertGreater(len(result['archives']),1)
        self.assertTrue(all(Path(p).stat().st_size<=16384 for p in result['archives']))
        check=verify_archive_set(result['archives'])
        self.assertTrue(check['complete_set_verified'])
        self.assertIn(hashlib.sha256(raw).hexdigest(),json.dumps(check))
    def test_batch_groups_without_duplicate_sessions(self):
        root2,_,_=make_store(self.dir/'second',db=False)
        import shutil
        source=root2/'antigravity'/'brain'/ID
        target=self.root/'antigravity-ide'/'brain'/ID
        target.parent.mkdir(parents=True);shutil.copytree(source,target)
        result=run(self.args('export','--root',str(self.root),'--id','antigravity:'+ID,'--id','antigravity-ide:'+ID,
            '--archive-method','Deflate','--archive-backend','python','--archive-level','6','--tokenizer','none','--no-progress','--out-dir',str(self.out)))
        self.assertEqual(len(result),2);self.assertEqual(result[0]['archives'],result[1]['archives'])
        data=self.members(result[0]);batch=json.loads(data['BATCH_INDEX.json'])
        self.assertEqual(len(batch['sessions']),2)
        self.assertTrue(verify_export(result[0]['archive'])['embedded_manifest_hashes_matched'])
    def test_collision_skip_is_disclosed_in_remaining_batch_archive(self):
        import shutil
        self.export('--stable-filenames','--tokenizer','none')
        other=self.root/'antigravity-ide';shutil.copytree(self.root/'antigravity',other)
        (other/'annotations'/(ID+'.pbtxt')).write_text('title:"Second fixture"',encoding='utf-8')
        results=run(self.args('export','--root',str(self.root),'--all','--stable-filenames',
            '--collision','skip','--archive-method','Deflate','--archive-backend','python',
            '--archive-level','6','--tokenizer','none','--no-progress','--out-dir',str(self.out)))
        self.assertEqual(sum(bool(r.get('skipped_existing')) for r in results),1)
        published=next(r for r in results if r.get('archive'))
        data=self.members(published);batch=json.loads(data['BATCH_INDEX.json'])
        self.assertFalse(batch['selection_complete'])
        self.assertEqual(batch['skipped_sessions'][0]['variant'],'antigravity')
        manifest=json.loads(next(v for k,v in data.items() if k.endswith('.manifest.json')))
        self.assertEqual(manifest['batch_selection']['skipped_sessions'],batch['skipped_sessions'])
        self.assertTrue(verify_export(published['archive'])['embedded_manifest_hashes_matched'])
    def test_invalid_selection_does_not_publish_partial_batch(self):
        with self.assertRaises(ExportError):self.export('--id','99999999')
        self.assertFalse(self.out.exists())
    def test_failed_work_is_cleaned_by_default(self):
        work=self.dir/'work'
        with self.assertRaises(ExportError):self.export('--work-dir',str(work),'--turn','99')
        self.assertEqual(list(work.iterdir()),[])
    def test_explicit_failed_work_retained(self):
        work=self.dir/'work'
        with self.assertRaises(ExportError),contextlib.redirect_stderr(io.StringIO()):self.export('--work-dir',str(work),'--keep-failed-work','--turn','99')
        self.assertEqual(len(list(work.iterdir())),1)
    def test_work_directory_cannot_modify_app_source(self):
        target=self.root/'work'
        with self.assertRaises(ExportError):self.export('--work-dir',str(target))
        self.assertFalse(target.exists())
    def test_share_unknown_fields_are_retained_and_filtered(self):
        rows=base_records();rows[-1]['type']='FUTURE_KIND';rows[-1]['future_field']={'password':'secret-fixture-value'}
        self.log.write_bytes(b''.join((json.dumps(r)+'\n').encode() for r in rows))
        data=self.members(self.export('--share','--unknown-sidecar'))
        side=next(v for k,v in data.items() if k.endswith('.unknown.jsonl'))
        self.assertIn(b'future_field',side);self.assertNotIn(b'secret-fixture-value',side)
        self.assertTrue(all(b'secret-fixture-value' not in b for b in data.values()))
    def test_timestamp_and_render_controls(self):
        data=self.members(self.export('--no-timestamps','--no-frontmatter','--no-map'))
        md=next(v for k,v in data.items() if k.endswith('.md')).decode()
        self.assertFalse(md.startswith('---'));self.assertNotIn('## Export map',md)
        self.assertNotIn('November 9',md)
        data=self.members(self.export('--timestamp-timezone','UTC'))
        md=next(v for k,v in data.items() if k.endswith('.md')).decode()
        self.assertTrue(md.startswith('---'));self.assertIn('## Export map',md)
        self.assertIn('November',md)
    def test_help_recovers_with_invalid_config(self):
        (self.dir/'config.json').write_text('{broken')
        with contextlib.redirect_stdout(io.StringIO()) as out,contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main([]),0)
        self.assertIn('Antigravity Exporter',out.getvalue());self.assertIn('config --reset',err.getvalue())
        run(self.args('config','--reset'))
    def test_stdout_json_conflict_is_explicit(self):
        with self.assertRaises(ExportError):self.export('--stdout','--json')
    def test_all_original_args_remain_in_rendered_edit_card(self):
        rows=base_records();rows[1]['tool_calls']=[{'name':'edit_file','args':{'path':'fixture.py','old':'old','new':'new'}}]
        self.log.write_bytes(b''.join((json.dumps(r)+'\n').encode() for r in rows))
        data=self.members(self.export('--ui-style'))
        md=next(v for k,v in data.items() if k.endswith('.md')).decode()
        self.assertIn('Recorded file operations',md);self.assertIn('"old": "old"',md);self.assertIn('<details>',md)
