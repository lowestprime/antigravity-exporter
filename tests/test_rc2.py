"""Public synthetic regressions for rc2; no user transcripts or credentials."""
import argparse
from datetime import datetime,timezone,timedelta
import io,json,os,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from antigravity_exporter.progress import Progress,SevenZipFrames,clean,cells
from antigravity_exporter.naming import export_stamp,abbrev_tz,safe_slug,count_suffix,filename_stem,DEFAULT_TEMPLATE
from antigravity_exporter.storage import capture_file,ExportError,open_shared_read,publish_fresh
from antigravity_exporter.cli import parser,run,DEFAULTS,load_config,save_config

class FakeTTY(io.StringIO):
    encoding='utf-8'
    def isatty(self):return True

class Rc2ProgressTests(unittest.TestCase):
    def test_109_chunks_are_one_stage(self):
        stream=io.StringIO();p=Progress('plain',stream=stream,heartbeat=False)
        with p.group('verify-transcript-chunks',total=109,unit='files'):
            for n in range(109):
                p.update('capture',102400,102400)
                p.update('verify-source-prefix',102400,102400)
                p.update('verify-transcript-chunks',n+1,109,unit='files')
        p.finish()
        self.assertNotIn('capture',stream.getvalue())
        self.assertLessEqual(len(stream.getvalue().splitlines()),4)
        self.assertIn('109 files',stream.getvalue())
    def test_rate_waits_for_measurement_interval(self):
        stream=io.StringIO();clock=lambda:0.0;p=Progress('json',stream=stream,clock=clock,heartbeat=False)
        p.update('copy',100,100);p.finish()
        events=[json.loads(x) for x in stream.getvalue().splitlines()]
        self.assertTrue(all(e.get('rate_per_second') is None for e in events))
    def test_percent_never_fake_byte_rate(self):
        stream=io.StringIO();p=Progress('json',stream=stream,heartbeat=False)
        p.update('compress',20,100,unit='percent');p.finish()
        for e in map(json.loads,stream.getvalue().splitlines()):self.assertIsNone(e.get('rate_per_second'))
    def test_failure_not_success(self):
        stream=io.StringIO();p=Progress('json',stream=stream,heartbeat=False)
        p.update('copy',1,20);p.finish(False)
        self.assertEqual(json.loads(stream.getvalue().splitlines()[-1])['status'],'failed')
    def test_unicode_panel_and_ascii_fallback(self):
        stream=FakeTTY()
        with patch.dict(os.environ,{'TERM':'xterm-256color'},clear=False),patch('antigravity_exporter.progress.windows_vt',return_value=True):
            p=Progress('rich',stream=stream,width=80,heartbeat=False);p.update('copy',5,10,detail='日本語');p.finish()
        self.assertIn('\x1b[2K',stream.getvalue());self.assertIn('50.0%',stream.getvalue())
    def test_redirected_rich_is_plain(self):
        p=Progress('rich',stream=io.StringIO(),heartbeat=False);self.assertEqual(p.style,'plain');p.finish()
    def test_no_color(self):
        with patch.dict(os.environ,{'NO_COLOR':'1','TERM':'xterm'}),patch('antigravity_exporter.progress.windows_vt',return_value=True):
            p=Progress('rich',stream=FakeTTY(),heartbeat=False);self.assertFalse(p.color);p.finish()
    def test_terminal_injection_removed(self):
        self.assertEqual(clean('\x1b[31mhello\x1b[0m\r\nworld'),'hello world')
    def test_nan_counter_refused(self):
        p=Progress('off',heartbeat=False)
        with self.assertRaises(ValueError):p.update('bad',float('nan'),1)
        p.finish()
    def test_heartbeat_changes_no_counter(self):
        s=io.StringIO();p=Progress('json',stream=s,interval=.02)
        p.update('idle',2,10);time.sleep(.05);p.finish()
        events=[json.loads(x) for x in s.getvalue().splitlines() if json.loads(x).get('event','').startswith('stage_')]
        self.assertTrue(all(e['completed']==2 for e in events));self.assertFalse(p.thread.is_alive())
    def test_7zip_split_frames_member(self):
        events=[]
        class Sink:
            def update(self,*a,**k):events.append((a,k))
        f=SevenZipFrames(Sink());f.feed('+ sample.md\r  2');f.feed('5%\b\b\b\b 50% 1 + other.md\r');f.feed('',True)
        self.assertEqual(events[-1][0][1],50);self.assertIn('other.md',events[-1][1]['detail'])
    def test_publication_has_three_stages(self):
        with tempfile.TemporaryDirectory() as d:
            a=Path(d)/'a';a.write_bytes(b'hello');stream=io.StringIO();p=Progress('json',stream=stream,heartbeat=False)
            publish_fresh(a,Path(d)/'b',progress=p);p.finish()
            names={json.loads(x).get('stage') for x in stream.getvalue().splitlines()}
            self.assertTrue({'publication-source-checksum','publication-copy','publication-readback'}<=names)

class Rc2FilenameTests(unittest.TestCase):
    def test_stamp_exact_codex_shape(self):
        t=datetime(2026,11,9,17,37,52,tzinfo=timezone(timedelta(hours=-8),'Pacific Standard Time'))
        self.assertEqual(export_stamp(t),'MON_11092026_053752_PM-PST')
    def test_summer_abbreviation(self):self.assertEqual(abbrev_tz('Pacific Daylight Time'),'PDT')
    def test_slug(self):self.assertEqual(safe_slug('My Session / Windows?'),'my_session_windows')
    def test_estimated_count(self):self.assertEqual(count_suffix({'lines':20,'tokens':42}),'_20lines_approx42tokens')
    def test_exact_count(self):self.assertEqual(count_suffix({'lines':20,'tokens':42,'exact_for_selected_encoding':True,'encoding':'cl100k_base'}),'_20lines_42cl100k_base_tokens')
    def test_uncounted_is_not_zero(self):self.assertEqual(count_suffix({'lines':20,'tokens':None}),'_20lines')
    def test_md_not_doubled(self):self.assertEqual(filename_stem('{title}.md',{'title':'example'}),'example')
    def test_legacy_template_works(self):self.assertEqual(filename_stem('{title}_{privacy}',{'title':'example','privacy':'PRIVATE'}),'example_PRIVATE')
    def test_saved_preferences_unchanged(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'AGY_EXPORT_CONFIG':str(Path(d)/'config.json')}):
            c=dict(DEFAULTS);c['template']='legacy_{title}';c['tokenizer']='none';save_config(c)
            self.assertEqual(load_config()['template'],'legacy_{title}');self.assertEqual(load_config()['tokenizer'],'none')
    def test_syntax_flags(self):
        args=parser().parse_args(['export','--id','11111111-2222-4333-8444-555555555555','--no-filename-counts','--include-session-short-id'])
        self.assertFalse(args.filename_counts);self.assertTrue(args.include_session_short_id)

class Rc2WindowsReadTests(unittest.TestCase):
    def test_read_handle_is_not_writable(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'source';path.write_bytes(b'original')
            with open_shared_read(path) as f:
                self.assertEqual(f.read(),b'original')
                with self.assertRaises((OSError,io.UnsupportedOperation)):f.write(b'changed')
    @unittest.skipUnless(os.name=='nt','Windows sharing semantics only')
    def test_shared_handle_allows_rotation(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'source';path.write_bytes(b'old')
            with open_shared_read(path) as f:
                path.rename(path.with_suffix('.old'));path.write_bytes(b'new')
                self.assertEqual(f.read(),b'old')
