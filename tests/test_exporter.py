"""Synthetic tests contain no private user content. Optional real ZIPs stay external."""
from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import warnings
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from antigravity_exporter import __version__
from antigravity_exporter.storage import Source,ExportError,capture_file,publish_fresh,safe_name,safe_inventory,backup_database,hash_file
from antigravity_exporter.model import EventIndex,sessions,resolve,capture_transcript,audit_database,interruption_summary,protobuf_strings
from antigravity_exporter.render import Redactor,fence,make_review,count_text
from antigravity_exporter.archive import build_zip,verify_zip,diagnose,preflight,_run_external
from antigravity_exporter.cli import parser,run,verify_export,DEFAULTS,normalized_argv,validate_config,_sanitize

ID='11111111-2222-4333-8444-555555555555'
PROJECT='aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'

def vint(value):
    out=bytearray()
    while value>127: out.append((value&127)|128); value>>=7
    out.append(value); return bytes(out)

def wire(number,value): return vint((number<<3)|2)+vint(len(value))+value

def base_records():
    return [
        {'step_index':0,'source':'USER_EXPLICIT','type':'USER_INPUT','status':'DONE','created_at':'2026-11-09T07:29:25Z','content':'Initial request. Café 日本語 🧠'},
        {'step_index':1,'source':'MODEL','type':'PLANNER_RESPONSE','status':'DONE','created_at':'2026-11-09T07:30:25Z','thinking':'Recorded fixture reasoning','tool_calls':[{'name':'run_command','args':{'command':'echo fixture'}}]},
        {'step_index':2,'source':'MODEL','type':'GENERIC','status':'DONE','created_at':'2026-11-09T07:31:25Z','content':f"The tool's output was truncated. Full output: file:///C:/Users/example/.gemini/antigravity/brain/{ID}/.system_generated/steps/2/output.txt\npreview"},
        {'step_index':3,'source':'MODEL','type':'PLANNER_RESPONSE','status':'DONE','created_at':'2026-11-09T07:32:25Z','content':'Waiting for the task, not a completed answer.'},
        # Step 4 exists only in SQLite.
        {'step_index':5,'source':'USER_EXPLICIT','type':'USER_INPUT','status':'DONE','created_at':'2026-11-09T19:45:06Z','content':'Please continue.'},
        {'step_index':6,'source':'MODEL','type':'PLANNER_RESPONSE','status':'DONE','created_at':'2026-11-09T19:46:06Z','content':'Still waiting for the task.'},
        {'step_index':7,'source':'MODEL','type':'GENERIC','status':'RUNNING','created_at':'2026-11-09T19:47:06Z','content':'Task output.', 'extra_metadata':{'nested':[1,2,3]}}
    ]

def make_store(path,records=None,db=True):
    root=Path(path)/'.gemini'; variant=root/'antigravity'; ident=ID
    logs=variant/'brain'/ident/'.system_generated'/'logs'; logs.mkdir(parents=True)
    rows=records if records is not None else base_records()
    raw=b''.join((json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n').encode() for r in rows)
    (logs/'transcript_full.jsonl').write_bytes(raw)
    (logs/'transcript.jsonl').write_bytes(raw)
    chunks=logs/'chunks'/'transcript_full'; chunks.mkdir(parents=True)
    for i,start in enumerate(range(0,len(raw),71)): (chunks/f'{i:08d}.jsonl').write_bytes(raw[start:start+71])
    annotation=variant/'annotations'; annotation.mkdir()
    (annotation/(ident+'.pbtxt')).write_text('title:"Fixture: Session / Windows?"',encoding='utf-8')
    project=root/'config'/'projects'; project.mkdir(parents=True)
    (project/(PROJECT+'.json')).write_text(json.dumps({'id':PROJECT,'name':'fixture-project'}))
    output=variant/'brain'/ident/'.system_generated'/'steps'/'2'/'output.txt'; output.parent.mkdir(parents=True)
    output.write_text('FULL SIDECAR OUTPUT\n```\nMore tool output\n',encoding='utf-8')
    summary=sqlite3.connect(variant/'conversation_summaries.db')
    summary.execute('CREATE TABLE conversation_summaries (conversation_id TEXT,title TEXT,project_id TEXT,step_count INTEGER,last_modified_time TEXT,workspace_uris TEXT,status TEXT)')
    summary.execute('INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,?)',(ident,'Fixture title',PROJECT,len(rows)+2,'2026-11-09T19:47:06Z','[]','IDLE'))
    summary.commit(); summary.close()
    if db:
        directory=variant/'conversations'; directory.mkdir()
        con=sqlite3.connect(directory/(ident+'.db'))
        con.execute('CREATE TABLE trajectory_meta (cascade_id TEXT)'); con.execute('INSERT INTO trajectory_meta VALUES (?)',(ident,))
        con.execute('CREATE TABLE steps (idx INTEGER PRIMARY KEY,step_type INTEGER,status INTEGER,step_payload BLOB,error_details BLOB)')
        for r in rows: con.execute('INSERT INTO steps VALUES (?,?,?,?,?)',(r['step_index'],15,3,b'',b''))
        if not any(r['step_index'] in (4,8) for r in rows):
            for i,stamp in [(4,'2026-11-16T07:29:26Z'),(8,'2026-11-10T00:45:06Z')]:
                blob=wire(17,wire(1,('quota reached HTTP 429 reset '+stamp).encode()))
                con.execute('INSERT INTO steps VALUES (?,?,?,?,?)',(i,17,3,blob,b''))
        con.commit(); con.close()
    return root,logs/'transcript_full.jsonl',raw

def zip_store(root,path):
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for p in root.rglob('*'):
            if p.is_file(): z.write(p,'.gemini/'+p.relative_to(root).as_posix())
    return path

class Base(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.dir=Path(self.temp.name); self.root,self.log,self.raw=make_store(self.dir)
        self.out=self.dir/'output'
        self.env=patch.dict(os.environ,{'AGY_EXPORT_CONFIG':str(self.dir/'config.json')}); self.env.start(); self.addCleanup(self.env.stop)
    def args(self,*argv): return parser().parse_args(normalized_argv(list(argv)))
    def export(self,*argv):
        return run(self.args('export','--root',str(self.root),'--id',ID,'--archive-method','Deflate','--archive-backend','python','--archive-level','6','--out-dir',str(self.out),'--no-progress',*argv))
    def index(self,**kwargs):
        idx=EventIndex(self.log,self.dir/('index-'+str(time.time_ns())+'.db'),**kwargs); self.addCleanup(idx.close); return idx

class SnapshotTests(Base):
    def test_capture_exact_bytes(self):
        p=self.dir/'copy'; result=capture_file(self.log,p); self.assertEqual(p.read_bytes(),self.raw); self.assertTrue(result['captured_prefix_verified'])
    def test_append_preserves_fixed_prefix(self):
        log=self.log; original=self.raw
        class Append:
            did=False
            def update(self,*args,**kwargs):
                if not self.did:
                    self.did=True
                    with log.open('ab') as f:f.write(b'new later bytes\n')
        dest=self.dir/'copy'; receipt=capture_file(log,dest,allow_growth=True,progress=Append())
        self.assertEqual(dest.read_bytes(),original); self.assertEqual(receipt['later_bytes_observed'],16)
    def test_rewrite_rejected(self):
        log=self.log
        class Rewrite:
            did=False
            def update(self,*args,**kwargs):
                if not self.did:
                    self.did=True
                    with log.open('r+b') as f:f.write(b'X')
        with self.assertRaises(ExportError):capture_file(log,self.dir/'copy',allow_growth=True,progress=Rewrite())
    def test_truncation_rejected(self):
        log=self.log
        class Truncate:
            did=False
            def update(self,*args,**kwargs):
                if not self.did:self.did=True; log.write_bytes(b'')
        with self.assertRaises(ExportError):capture_file(log,self.dir/'copy',allow_growth=True,progress=Truncate())
    def test_replacement_rejected(self):
        log=self.log
        class Replace:
            did=False
            def update(self,*args,**kwargs):
                if not self.did:
                    self.did=True; log.rename(log.with_suffix('.old')); log.write_bytes(b'replacement')
        with self.assertRaises(ExportError):capture_file(log,self.dir/'copy',allow_growth=True,progress=Replace())
    def test_fresh_publication(self):
        dest=self.dir/'published'; result=publish_fresh(self.log,dest)
        self.assertEqual(dest.read_bytes(),self.raw);self.assertTrue(result['readback_verified']); self.assertNotEqual(dest.stat().st_ino,self.log.stat().st_ino)
    def test_publication_does_not_overwrite(self):
        dest=self.dir/'published'; dest.write_bytes(b'keep')
        with self.assertRaises(ExportError): publish_fresh(self.log,dest)
        self.assertEqual(dest.read_bytes(),b'keep')
    def test_symlink_input_rejected(self):
        link=self.dir/'linked'
        try:link.symlink_to(self.log)
        except OSError:self.skipTest('symlinks not permitted')
        with self.assertRaises(ExportError):capture_file(link,self.dir/'copy')
    def test_sqlite_wal_backup(self):
        source=self.dir/'wal.db'; con=sqlite3.connect(source)
        con.execute('PRAGMA journal_mode=WAL'); con.execute('CREATE TABLE t(v)'); con.execute('INSERT INTO t VALUES (7)'); con.commit()
        try:
            dest=self.dir/'snap.db'; result=backup_database(source,dest)
            with closing(sqlite3.connect(dest)) as check:self.assertEqual(check.execute('SELECT v FROM t').fetchone()[0],7)
            self.assertTrue(result['wal_commits_included'])
        finally: con.close()

class SourceTests(Base):
    def test_catalog_title_project(self):
        with Source(root=self.root) as s:
            row=sessions(s)[0]; self.assertEqual(row['project'],'fixture-project'); self.assertEqual(row['title'],'Fixture: Session / Windows?')
    def test_variant_root(self):
        with Source(root=self.root/'antigravity') as s:self.assertEqual(s.variants(),['antigravity'])
    def test_zip_source(self):
        archive=zip_store(self.root,self.dir/'source.zip')
        with Source(archive=archive) as s:
            row=sessions(s)[0]; p,info=capture_transcript(s,row,verify_chunks=True)
            self.assertEqual(p.read_bytes(),self.raw); self.assertTrue(info['chunk_audit']['equals_monolithic'])
    def test_chunks_fallback(self):
        self.log.unlink()
        with Source(root=self.root) as s:
            p,info=capture_transcript(s,sessions(s)[0]); self.assertEqual(p.read_bytes(),self.raw); self.assertEqual(info['fidelity'],'full-chunks-reassembled')
    def test_chunk_gap_rejected(self):
        self.log.unlink(); (self.log.parent/'chunks'/'transcript_full'/'00000001.jsonl').unlink()
        with Source(root=self.root) as s:
            with self.assertRaises(ExportError):capture_transcript(s,sessions(s)[0])
    def test_reduced_requires_opt_in(self):
        self.log.unlink(); shutil.rmtree(self.log.parent/'chunks')
        with Source(root=self.root) as s:
            row=sessions(s)[0]
            with self.assertRaises(ExportError):capture_transcript(s,row)
            p,info=capture_transcript(s,row,allow_reduced=True);self.assertIn('REDUCED',info['fidelity'])
    def test_ambiguous_store_rejected(self):
        rows=[{'id':ID,'variant':'antigravity','project':'p','project_id':'x'}, {'id':ID,'variant':'antigravity-ide','project':'p','project_id':'x'}]
        with self.assertRaises(ExportError): resolve(rows,[ID])
        self.assertEqual(resolve(rows,[ID],variant='antigravity')[0]['variant'],'antigravity')
    def test_prefix_selector(self):
        with Source(root=self.root) as s:self.assertEqual(resolve(sessions(s),[ID[:8]])[0]['id'],ID)
    def test_unknown_id_rejected(self):
        with Source(root=self.root) as s:
            with self.assertRaises(ExportError):resolve(sessions(s),['99999999'])
    def test_summary_query_does_not_render_all_logs(self):
        self.log.write_bytes(b'broken')
        with Source(root=self.root) as s:self.assertTrue(sessions(s)[0]['full'])
    def test_zip_wal_replayed(self):
        db=self.root/'antigravity'/'conversations'/(ID+'.db'); con=sqlite3.connect(db)
        con.execute('PRAGMA journal_mode=WAL'); con.execute('INSERT INTO steps VALUES (9,17,3,?,?)',(b'test',b''));con.commit()
        try:
            archive=zip_store(self.root,self.dir/'wal.zip')
            with Source(archive=archive) as s:
                p,receipt=s.snapshot(f'antigravity/conversations/{ID}.db',database=True)
                with closing(sqlite3.connect(p)) as check:self.assertEqual(check.execute('SELECT COUNT(*) FROM steps WHERE idx=9').fetchone()[0],1)
                self.assertTrue(receipt['archive_wal_applied'])
        finally:con.close()

class IndexTests(Base):
    def test_gaps_preserved(self):
        idx=self.index(); self.assertEqual(idx.audit['missing_step_ranges'],[[4,4]]);self.assertEqual(idx.count,7)
    def test_turn_semantics(self):
        idx=self.index(); self.assertEqual(len(idx.turns),2)
        self.assertEqual(idx.turns[0]['assistant_text_messages'],1)
        self.assertEqual(idx.turns[0]['execution_complete'],'not_established_by_transcript')
    def test_valid_eof_no_newline(self):
        self.log.write_bytes(self.raw.rstrip(b'\n'));self.assertEqual(self.index().count,7)
    def test_partial_tail_retained_not_rendered(self):
        self.log.write_bytes(self.raw+b'{"step_index":9,')
        idx=self.index();self.assertEqual(idx.count,7);self.assertTrue(idx.partial_tail)
    def test_completed_invalid_fails(self):
        self.log.write_bytes(self.raw+b'not json\n')
        with self.assertRaises(ExportError):self.index()
    def test_invalid_opt_in_audited(self):
        self.log.write_bytes(self.raw+b'not json\n');idx=self.index(skip_invalid=True);self.assertEqual(len(idx.issues),1)
    def test_duplicate_fails(self):
        self.log.write_bytes(self.raw+self.raw.splitlines(keepends=True)[0])
        with self.assertRaises(ExportError):self.index()
    def test_record_size_limit(self):
        with self.assertRaises(ExportError):self.index(max_record_bytes=30)
    def test_unknown_event_preserved(self):
        records=base_records();records[-1]['type']='NEW_FUTURE_KIND';self.log.write_bytes(b''.join((json.dumps(r)+'\n').encode() for r in records))
        idx=self.index();self.assertEqual(idx.unknown['NEW_FUTURE_KIND'],1)
        with self.assertRaises(ExportError):self.index(strict_events=True)
    def test_inclusive_step_range(self):
        idx=self.index();a=self.args('export','--from-step','1','--to-step','5');s=idx.selection(a)
        self.assertEqual(s['records'],4);self.assertEqual([r['step_index'] for _,r in idx.records()],[1,2,3,5])
    def test_last_turn(self):
        idx=self.index();s=idx.selection(self.args('export','--last-n-turns','1'))
        self.assertEqual(s['first_step'],5);self.assertEqual(s['records'],3)
    def test_last_assistant_is_not_final(self):
        idx=self.index();s=idx.selection(self.args('export','--mode','last-assistant'));self.assertEqual(s['first_step'],6)
    def test_last_substantial_does_not_invent(self):
        with self.assertRaises(ExportError):self.index().selection(self.args('export','--mode','last-substantial','--min-chars','1000'))
    def test_response_block_excludes_user(self):
        idx=self.index();s=idx.selection(self.args('export','--mode','response-block','--turn','2'))
        self.assertEqual(s['records'],2);self.assertEqual(s['first_step'],6)
    def test_message_ordinal_is_distinct_from_step(self):
        idx=self.index();s=idx.selection(self.args('export','--mode','message','--message','5'));self.assertEqual(s['first_step'],5)
    def test_db_reconciliation_and_latest_quota(self):
        idx=self.index()
        with Source(root=self.root) as src:
            audit,rows=audit_database(src,sessions(src)[0],idx)
            self.assertEqual(audit['db_only_steps'],[4,8]);self.assertFalse(audit['matched_exactly'])
            summary=interruption_summary(idx,rows)
            self.assertEqual(summary['latest_database_only_quota_step'],8)
            self.assertEqual(summary['timestamp_strings_in_latest_quota_step'],['2026-11-10T00:45:06Z'])
            self.assertEqual(idx.turns[0]['execution_complete'],'interrupted_by_recorded_quota_event')
            audit2,_=audit_database(src,sessions(src)[0],idx);self.assertEqual(audit['snapshot']['sha256'],audit2['snapshot']['sha256'])
    def test_db_identity_mismatch(self):
        db=self.root/'antigravity'/'conversations'/(ID+'.db')
        with closing(sqlite3.connect(db)) as con, con:con.execute("UPDATE trajectory_meta SET cascade_id='different'")
        with Source(root=self.root) as src:
            with self.assertRaises(ExportError):audit_database(src,sessions(src)[0],self.index())
    def test_generic_wire_leaf(self):
        blob=wire(17,wire(3,b'HTTP 429 quota'))
        result=protobuf_strings(blob);self.assertEqual(result[0]['text'],'HTTP 429 quota');self.assertEqual(result[0]['field_path'],'17[1].3[1]')
    def test_bad_wire_not_guessed(self):self.assertEqual(protobuf_strings(b'not a protobuf'),[])

class RenderArchiveTests(Base):
    def test_default_three_member_bundle(self):
        result=self.export();self.assertEqual(result['archive_member_count'],3);self.assertTrue(result['archive_verified'])
        self.assertFalse(result['loose_outputs_retained'])
        with zipfile.ZipFile(result['archive']) as z:
            native=next(n for n in z.namelist() if n.endswith('.jsonl'));self.assertEqual(z.read(native),self.raw)
        self.assertTrue(verify_export(result['archive'])['embedded_manifest_hashes_matched'])
    def test_source_unchanged_after_full_export(self):
        hashes={p:hash_file(p) for p in self.root.rglob('*') if p.is_file()}
        self.export('--include-native','--include-artifacts','--review','--handoff')
        self.assertEqual(hashes,{p:hash_file(p) for p in self.root.rglob('*') if p.is_file()})
    def test_frozen_db_reused_for_native_and_audit(self):
        result=self.export('--include-native')
        with zipfile.ZipFile(result['archive']) as z:
            m=json.loads(z.read(next(n for n in z.namelist() if n.endswith('.manifest.json'))))
            dbname=next(n for n in z.namelist() if n.endswith('.db'))
            self.assertEqual(hashlib.sha256(z.read(dbname)).hexdigest(),m['database_audit']['snapshot']['sha256'])
    def test_recovery_appended_not_replaced(self):
        result=self.export()
        with zipfile.ZipFile(result['archive']) as z:
            md=z.read(next(n for n in z.namelist() if n.endswith('.md'))).decode()
            self.assertIn('preview',md);self.assertIn('FULL SIDECAR OUTPUT',md);self.assertIn('Database-only record 8',md)
    def test_recovery_never_follows_external_path(self):
        rows=base_records();rows[2]['content']='output was truncated: file:///C:/private/secret.txt'
        self.log.write_bytes(b''.join((json.dumps(r)+'\n').encode() for r in rows))
        result=self.export()
        with zipfile.ZipFile(result['archive']) as z:
            md=z.read(next(n for n in z.namelist() if n.endswith('.md'))).decode();self.assertNotIn('FULL SIDECAR OUTPUT',md)
    def test_redaction_native_copy_gate(self):
        with self.assertRaises(ExportError):self.export('--redact')
    def test_share_disallows_native(self):
        with self.assertRaises(ExportError):self.export('--share','--include-native')
    def test_share_redacts_structured_fields(self):
        rows=base_records();rows[0]['password']='fixture-secret-value';rows[0]['content']='token sk-'+('x'*32)
        self.log.write_bytes(b''.join((json.dumps(r)+'\n').encode() for r in rows))
        result=self.export('--share')
        with zipfile.ZipFile(result['archive']) as z:
            for n in z.namelist():
                data=z.read(n);self.assertNotIn(b'fixture-secret-value',data);self.assertNotIn(('sk-'+'x'*32).encode(),data)
            self.assertFalse(any('/conversations/' in n for n in z.namelist()))
    def test_nested_unknown_fields_preserved(self):
        result=self.export('--share')
        with zipfile.ZipFile(result['archive']) as z:
            n=next(n for n in z.namelist() if n.endswith('.records.jsonl'))
            rows=[json.loads(x) for x in z.read(n).splitlines()];self.assertEqual(rows[-1]['extra_metadata'],{'nested':[1,2,3]})
    def test_no_archive_cleanup_retains_files(self):
        result=self.export('--no-archive-cleanup');self.assertTrue(Path(result['markdown']).exists());self.assertTrue(Path(result['archive']).exists())
    def test_no_archive_loose_only(self):
        result=self.export('--no-archive');self.assertNotIn('archive',result);self.assertTrue(Path(result['manifest']).exists())
    def test_stable_name_collision_rename(self):
        a=self.export('--stable-filenames');b=self.export('--stable-filenames');self.assertNotEqual(a['archive'],b['archive'])
    def test_collision_error_and_skip(self):
        self.export('--stable-filenames')
        with self.assertRaises(ExportError):self.export('--stable-filenames','--collision','error')
        self.assertTrue(self.export('--stable-filenames','--collision','skip')['skipped_existing'])
    def test_output_inside_source_rejected(self):
        with self.assertRaises(ExportError):self.export('--out-dir',str(self.root/'bad-output'))
    def test_source_copy_of_filtered_view_still_full(self):
        result=self.export('--last-n-turns','1')
        self.assertEqual(result['selected_records'],3)
        with zipfile.ZipFile(result['archive']) as z:
            self.assertEqual(z.read(next(n for n in z.namelist() if n.endswith('.jsonl'))),self.raw)
    def test_tail_output_omission_is_recorded(self):
        result=self.export('--tool-outputs','tail','--max-tool-chars','10')
        with zipfile.ZipFile(result['archive']) as z:
            m=json.loads(z.read(next(n for n in z.namelist() if n.endswith('.manifest.json'))));self.assertTrue(m['view_omissions'])
    def test_fence_exceeds_literal_delimiter(self):
        rendered=fence('``````\ncontent\n');self.assertTrue(rendered.startswith('```````text\n'))
    def test_plain_regex_count_label(self):
        p=self.dir/'a.md';p.write_text('Hello world!\n',encoding='utf-8');c=count_text(p,'regex');self.assertEqual(c['tokens'],3);self.assertFalse(c['exact_for_selected_encoding'])
    def test_review_utf8_and_exact_reconstruction(self):
        md=self.dir/'unicode.md';md.write_text(('🧠日本語Café\r\n'+'a'*3000)*10,encoding='utf-8',newline='')
        members,index=make_review(md,self.dir/'parts',cap=4096)
        body=b''
        for part in index['parts']:
            p=self.dir/'parts'/part['file']; self.assertLessEqual(p.stat().st_size,4096);p.read_text(encoding='utf-8')
            body+=p.read_bytes()[part['header_bytes']:]
        self.assertEqual(body,md.read_bytes())
    def test_lzma_python_reader(self):
        zpath=self.dir/'lzma.zip'
        with zipfile.ZipFile(zpath,'w',zipfile.ZIP_LZMA) as z:z.writestr('test.txt','fixture'*100)
        report=verify_zip(zpath);self.assertEqual(report['members'][0]['method_id'],14);self.assertTrue(report['all_members_decoded_and_crc_checked'])
    def test_dictionary_limit_is_policy_not_corruption(self):
        zpath=self.dir/'lzma.zip'
        with zipfile.ZipFile(zpath,'w',zipfile.ZIP_LZMA) as z:z.writestr('test.txt','fixture'*100)
        report=diagnose(zpath,max_dictionary_mib=1);self.assertEqual(report['status'],'dictionary_policy_limit');self.assertIn('dictionary',report['error'])
    def test_metadata_does_not_claim_decode(self):
        result=self.export();report=verify_export(result['archive'],metadata=True);self.assertFalse(report['all_members_decoded_and_crc_checked'])
    def test_missing_path_classified(self):self.assertEqual(diagnose(self.dir/'absent.zip')['status'],'path_missing')
    def test_malformed_zip_classified(self):
        p=self.dir/'bad.zip';p.write_bytes(b'bad');self.assertEqual(diagnose(p)['status'],'invalid_zip_or_crc')
    def test_manifest_tamper_detected(self):
        result=self.export();changed=self.dir/'changed.zip'
        with zipfile.ZipFile(result['archive']) as old,zipfile.ZipFile(changed,'w',zipfile.ZIP_DEFLATED) as new:
            for n in old.namelist():new.writestr(n,b'altered' if n.endswith('.md') else old.read(n))
        with self.assertRaises(ExportError):verify_export(changed)
    def test_review_from_export_without_live_store(self):
        result=self.export();r=run(self.args('review-from',result['archive'],'--out-dir',str(self.out)))
        self.assertTrue(r['reconstruction_verified']);self.assertTrue(Path(r['archive']).exists())
    def test_lzma_backend_rejects_python_policy(self):
        with self.assertRaises(ExportError):preflight({**DEFAULTS,'backend':'python'})
    def test_external_failure_is_reaped(self):
        with self.assertRaises(ExportError):_run_external([sys.executable,'-c','import sys; print("fixture error"); sys.exit(7)'],str(self.dir),None,5)
    def test_external_timeout_is_reaped(self):
        start=time.monotonic()
        with self.assertRaises(ExportError):_run_external([sys.executable,'-c','import time; time.sleep(20)'],str(self.dir),None,0.1)
        self.assertLess(time.monotonic()-start,8)
    def test_unicode_filename_safe(self):
        text=_sanitize('CON');self.assertEqual(text,'_CON');self.assertNotIn(':',_sanitize('name: with / bad?'))

class ZipSafetyTests(unittest.TestCase):
    def test_unsafe_paths(self):
        for name in ('../escape','/absolute','C:/drive','a\\b','a/../b','a//b','file:stream','CON','a.','a?b'):
            with self.subTest(name=name),self.assertRaises(ExportError):safe_name(name)
    def test_duplicate_case_collisions(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:z.writestr('A.txt','x');z.writestr('a.txt','y')
        stream.seek(0)
        with zipfile.ZipFile(stream) as z:
            with self.assertRaises(ExportError):safe_inventory(z)
    def test_symlink_member(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            i=zipfile.ZipInfo('link');i.create_system=3;i.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(i,'target')
        stream.seek(0)
        with zipfile.ZipFile(stream) as z:
            with self.assertRaises(ExportError):safe_inventory(z)
    def test_template_arbitrary_attribute_rejected(self):
        with self.assertRaises(ExportError):validate_config({'template':'{title.__class__}'})
    def test_wrong_config_type_rejected(self):
        with self.assertRaises(ExportError):validate_config({'cleanup':'false'})
    def test_config_archive_setting_bounds(self):
        for c in ({'level':10},{'dictionary':'8g'},{'threads':0},{'word_size':400}):
            with self.assertRaises(ExportError):validate_config(c)
    def test_legacy_config_flag(self):self.assertEqual(normalized_argv(['--show-archive-config']),['config'])

class RealArchiveTests(unittest.TestCase):
    def test_external_real_samples(self):
        paths=os.environ.get('AGY_REAL_FIXTURES','').split(os.pathsep)
        paths=[Path(x) for x in paths if x]
        if not paths:self.skipTest('Set AGY_REAL_FIXTURES to externally supplied private snapshots')
        counts=[]
        for path in paths:
            with self.subTest(path=path.name),tempfile.TemporaryDirectory() as d,Source(archive=path) as src:
                for n,s in enumerate(sessions(src)):
                    if not s['full']:continue
                    log,info=capture_transcript(src,s,verify_chunks=True)
                    idx=EventIndex(log,Path(d)/f'{n}.db')
                    try:
                        audit,rows=audit_database(src,s,idx)
                        self.assertEqual(audit['quick_check'],'ok');self.assertTrue(audit['cascade_id_matches'])
                        if info.get('chunk_audit',{}).get('files'):self.assertTrue(info['chunk_audit']['equals_monolithic'])
                        counts.append(idx.count)
                    finally:idx.close()
        self.assertIn(383,counts);self.assertIn(883,counts)

if __name__=='__main__':unittest.main()

class CurrentManifestShapeRegression(unittest.TestCase):
    """Reproduce reported counts/holes, not private NVIDIA transcript contents."""
    def test_720_log_722_database_rows_with_two_interruptions(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            records=[]
            for step in range(721):
                if step==626: continue
                user=step in (0,627)
                records.append({'step_index':step,'source':'USER_EXPLICIT' if user else 'MODEL',
                                'type':'USER_INPUT' if user else 'GENERIC','status':'DONE',
                                'content':'synthetic current-shaped record'})
            root,log,raw=make_store(base,records,db=False)
            dbpath=root/'antigravity'/'conversations'/(ID+'.db'); dbpath.parent.mkdir()
            with closing(sqlite3.connect(dbpath)) as con, con:
                con.execute('CREATE TABLE trajectory_meta (cascade_id TEXT)')
                con.execute('INSERT INTO trajectory_meta VALUES (?)',(ID,))
                con.execute('CREATE TABLE steps (idx INTEGER PRIMARY KEY,step_type INTEGER,status INTEGER,step_payload BLOB)')
                for step in range(722):
                    payload=b''
                    if step in (626,721):
                        timestamp='2026-11-16T07:29:26Z' if step==626 else '2026-11-10T00:45:06Z'
                        payload=wire(17,wire(1,('quota HTTP 429 reset '+timestamp).encode()))
                    con.execute('INSERT INTO steps VALUES (?,?,?,?)',(step,17 if payload else 132,3,payload))
            with Source(root=root) as source:
                index=EventIndex(log,base/'index.db')
                try:
                    audit,extra=audit_database(source,sessions(source)[0],index)
                    interruption=interruption_summary(index,extra)
                    self.assertEqual(index.audit['records'],720)
                    self.assertEqual(audit['sqlite_records'],722)
                    self.assertEqual(audit['db_only_steps'],[626,721])
                    self.assertEqual(interruption['latest_database_only_quota_step'],721)
                    self.assertEqual(interruption['timestamp_strings_in_latest_quota_step'],['2026-11-10T00:45:06Z'])
                    self.assertEqual(len(index.turns),2)
                    self.assertTrue(all(t['execution_complete']=='interrupted_by_recorded_quota_event' for t in index.turns))
                finally: index.close()


class ExternalArgumentContractTests(unittest.TestCase):
    def test_large_member_list_uses_utf8_response_file(self):
        # A fake producer verifies argument construction, not real 7-Zip behavior.
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); source=base/'data'; source.write_bytes(b'x'*2048)
            members={f'native/folder-{i:04d}/'+('n'*100)+'.txt':source for i in range(350)}
            destination=base/'export.zip'
            def produce(argv,cwd,progress,timeout):
                self.assertLess(sum(len(s)+1 for s in argv),2000)
                self.assertIn('-scsUTF-8',argv)
                response=next(s[1:] for s in argv if s.startswith('@'))
                paths=Path(response).read_text(encoding='utf-8').splitlines()
                self.assertEqual(paths,sorted(members))
                with zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_LZMA) as z:
                    for name in paths: z.write(cwd/name,name)
            with patch('antigravity_exporter.archive._run_external',side_effect=produce):
                receipt=build_zip(destination,members,DEFAULTS,archiver='FAKE-TEST-ENCODER')
            self.assertEqual(len(receipt['members']),350)


class FailedIndexCleanupTests(unittest.TestCase):
    def test_invalid_input_closes_index_connection(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); source=base/'bad.jsonl'; source.write_bytes(b'{bad json}\n')
            connection=sqlite3.connect(base/'index.db')
            with patch('antigravity_exporter.model.sqlite3.connect',return_value=connection):
                with self.assertRaises(ExportError):EventIndex(source,base/'index.db')
            with self.assertRaises(sqlite3.ProgrammingError):connection.execute('SELECT 1')


class CodecDiagnosticTests(unittest.TestCase):
    def test_lzma_decode_error_is_not_an_access_error(self):
        import lzma
        with patch('antigravity_exporter.archive.verify_zip',side_effect=lzma.LZMAError('Corrupt input data')):
            self.assertEqual(diagnose('test.zip')['status'],'invalid_lzma_payload')
    def test_optional_codec_failure_classification(self):
        with patch('antigravity_exporter.archive.verify_zip',side_effect=RuntimeError('Compression requires the (missing) lzma module')):
            self.assertEqual(diagnose('test.zip')['status'],'codec_unavailable')
