"""Synthetic regressions for anchor selection and presentation-only sorting."""
from __future__ import annotations
from argparse import Namespace
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from antigravity_exporter.model import EventIndex
from antigravity_exporter.selection import resolve_since, turn_listing, parse_since_time
from antigravity_exporter.storage import ExportError
from antigravity_exporter.listings import DisplaySettings, print_listing, sort_rows, session_listing_lines, SESSION_HEADERS
from antigravity_exporter.browser import SessionBrowser, browse_sessions
from antigravity_exporter.timestamps import TimestampFormatter, parse_source_timestamp


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.path = root / 'synthetic.jsonl'
        records = [
            {'step_index':0,'source':'USER_EXPLICIT','type':'USER_INPUT','status':'DONE','content':'A long\n FIRST anchor 🧠', 'created_at':'2026-01-01T00:00:00Z'},
            {'step_index':1,'source':'MODEL','type':'PLANNER_RESPONSE','status':'DONE','content':'answer'},
            {'step_index':3,'source':'USER_EXPLICIT','type':'USER_INPUT','status':'DONE','content':'repeated anchor', 'message_id':'native-one','created_at':'2026-01-01T01:00:00Z'},
            {'step_index':4,'source':'MODEL','type':'PLANNER_RESPONSE','status':'DONE','content':'answer'},
            {'step_index':5,'source':'USER_EXPLICIT','type':'USER_INPUT','status':'DONE','content':'Repeated  anchor','message_id':'native-two','created_at':'2026-01-01T02:00:00Z'},
            {'step_index':6,'source':'MODEL','type':'PLANNER_RESPONSE','status':'DONE','content':'last'}]
        self.path.write_bytes(b''.join(json.dumps(r,ensure_ascii=False).encode('utf-8')+b'\n' for r in records))
        self.index = EventIndex(self.path,root/'index.db')
        self.addCleanup(self.index.close)

    def arguments(self, **kwargs):
        return Namespace(mode='thread',**kwargs)

    def test_step_anchor_keeps_zero_and_original_coordinate(self):
        args = self.arguments(since_id='step:0')
        audit = resolve_since(self.index,args)
        self.assertEqual(args.from_message,1)
        self.assertEqual(audit['first_step'],0)
        self.assertEqual(audit['turn'],1)
        self.assertTrue(audit['inclusive'])

    def test_text_searches_complete_normalized_prompt(self):
        args=self.arguments(since_text='first  ANCHOR 🧠')
        self.assertEqual(resolve_since(self.index,args)['turn'],1)

    def test_ambiguous_text_requires_positive_occurrence(self):
        with self.assertRaisesRegex(ExportError,'matches 2 user turns'):
            resolve_since(self.index,self.arguments(since_text='repeated anchor'))
        args=self.arguments(since_text='repeated anchor',since_occurrence=2)
        self.assertEqual(resolve_since(self.index,args)['turn'],3)
        self.assertEqual(args.from_message,5)
        with self.assertRaisesRegex(ExportError,'exceeds 2'):
            resolve_since(self.index,self.arguments(since_text='repeated anchor',since_occurrence=3))

    def test_ids_native_exact_prefix_and_coordinate_are_distinct(self):
        self.assertEqual(resolve_since(self.index,self.arguments(since_id='native-one'))['turn'],2)
        with self.assertRaisesRegex(ExportError,'matches 2'):
            resolve_since(self.index,self.arguments(since_id='native-'))
        with self.assertRaisesRegex(ExportError,'No recorded'):
            resolve_since(self.index,self.arguments(since_id='step:'))

    def test_since_time_chooses_first_user_at_or_after_instant(self):
        self.assertEqual(resolve_since(self.index,self.arguments(since_time='2026-01-01T00:30:00Z'))['turn'],2)
        with self.assertRaisesRegex(ExportError,'UTC offset'):
            resolve_since(self.index,self.arguments(since_time='2026-01-01T00:30:00'))

    def test_competing_selectors_rejected(self):
        for kwargs in ({'since_turn':0},{'since_turn':1,'since_text':'first'},
                       {'since_turn':1,'from_step':0},{'since_occurrence':1}):
            with self.assertRaises(ExportError): resolve_since(self.index,self.arguments(**kwargs))

    def test_source_modified_after_indexing_rejected(self):
        raw=self.path.read_bytes();self.path.write_bytes(raw.replace(b'anchor',b'ANCHOR',1))
        with self.assertRaisesRegex(ExportError,'changed after indexing'):
            resolve_since(self.index,self.arguments(since_turn=1))

    def test_generic_numeric_selector_remains_text(self):
        with self.assertRaisesRegex(ExportError,'No recorded'):
            resolve_since(self.index,self.arguments(since='2'))
        self.assertEqual(resolve_since(self.index,self.arguments(since='turn:2'))['turn'],2)

    def test_sorted_listing_never_renumbers_turns(self):
        rows=turn_listing(self.index)
        ordered=sort_rows(rows,kind='turns',sort='turn:desc')
        self.assertEqual([r['turn'] for r in ordered],[3,2,1])
        self.assertEqual([r['first_step'] for r in ordered],[5,3,0])
        self.assertEqual(rows[0]['source_sha256'],hashlib.sha256(self.path.read_bytes().splitlines(keepends=True)[0]).hexdigest())


class ListingBrowserTests(unittest.TestCase):
    def test_multi_column_sort_null_last_and_numeric_steps(self):
        rows=[{'id':'a','project':'Z','summary_step_count':10},
              {'id':'b','project':'A','summary_step_count':3},
              {'id':'c','project':'A','summary_step_count':20},
              {'id':'d','project':'A','summary_step_count':None}]
        ordered=sort_rows(rows,kind='sessions',sort='project:asc,steps:desc')
        self.assertEqual([r['id'] for r in ordered],['c','b','d','a'])

    def test_redirected_pager_never_consumes_stdin_or_truncates(self):
        source=io.StringIO('leave me intact\n');target=io.StringIO()
        print_listing((f'row {n}' for n in range(100)),settings=DisplaySettings(pager='always',page_size=2),input_stream=source,output_stream=target)
        self.assertEqual(source.tell(),0)
        self.assertEqual(len(target.getvalue().splitlines()),100)

    def test_browser_keeps_variants_distinct_and_stdout_clean(self):
        rows=[{'id':'11111111-2222-4333-8444-555555555555','variant':v,'title':'test','full':True,'project':'Demo'} for v in ('antigravity','antigravity-ide')]
        ui=io.StringIO()
        result=browse_sessions(lambda:rows,input_stream=io.StringIO('1,2\n'),output_stream=ui)
        self.assertEqual(set(result),{r['variant']+':'+r['id'] for r in rows})
        self.assertIn('Antigravity session browser',ui.getvalue())

    def test_browser_cannot_export_catalog_only_row(self):
        row={'id':'11111111-2222-4333-8444-555555555555','variant':'antigravity','title':'test','full':False,'chunked_full':False}
        browser=SessionBrowser(lambda:[row],input_stream=io.StringIO(),output_stream=io.StringIO())
        browser.refresh();browser.render()
        with self.assertRaisesRegex(ValueError,'unavailable'):browser.command('1')


class SessionTableTests(unittest.TestCase):
    def row(self, **changes):
        return {'id':'11111111-2222-4333-8444-555555555555',
                'selector':'antigravity:11111111-2222-4333-8444-555555555555',
                'title':'Example conversation', 'size':13_002_342,
                'modified':'2026-11-27T19:09:16.342105411Z',
                'full':True,'db':True,'project':'Example project', **changes}

    def format(self, rows, **options):
        return session_listing_lines(rows,timezone_name='UTC',output_stream=io.StringIO(),
                                     input_stream=io.StringIO(),**options)

    def test_headers_human_units_date_and_source_identity_without_mutation(self):
        row=self.row();before=copy.deepcopy(row)
        lines=self.format([row])
        self.assertTrue(all(header in lines[0] for header in SESSION_HEADERS))
        self.assertIn(row['selector'],lines[2])
        self.assertIn('12.4 MiB',lines[2])
        self.assertIn('November 27, 2026 at 07:09:16 PM UTC',lines[2])
        self.assertIn('Full log',lines[2]);self.assertIn('Present',lines[2])
        self.assertIn('not filesystem modification time','\n'.join(lines))
        self.assertEqual(row,before)

    def test_headers_and_values_align_by_terminal_cells_with_unicode(self):
        from antigravity_exporter.progress import _cells
        lines=self.format([self.row(title='日🧠e\u0301'),self.row(title='ASCII')])
        expected=_cells(lines[0][:lines[0].index('JSONL size')])
        for line in lines[2:4]:
            self.assertEqual(_cells(line[:line.index('12.4 MiB')]),expected)

    def test_availability_labels_distinguish_chunks_reduced_missing(self):
        for change,label in (({'full':False,'chunked_full':True,'chunks_contiguous':True},'Full chunks'),
                             ({'full':False,'reduced':True},'Reduced'),
                             ({'full':False,'size':None},'Missing'),
                             ({'full':False,'chunked_full':True,'chunks_contiguous':False},'Missing (invalid chunks)')):
            with self.subTest(label=label):
                lines=self.format([self.row(**change)])
                self.assertIn(label,lines[2])
        self.assertIn('0 B',self.format([self.row(size=0)])[2])
        self.assertIn('1.0 KiB',self.format([self.row(size=1024)])[2])
        self.assertIn('N/A',self.format([self.row(size=None)])[2])

    def test_naive_missing_invalid_and_date_only_are_not_invented_utc_instants(self):
        for value,expected in ((None,'Not recorded'),('not a time','Unknown'),
                               ('2026-11-27T19:09:16.342105411','timezone unspecified'),
                               ('2026-11-27','time not recorded')):
            with self.subTest(value=value):
                lines=self.format([self.row(modified=value)])
                self.assertIn(expected,lines[2])
                self.assertNotIn(' UTC',lines[2])

    def test_empty_catalog_keeps_column_headers_and_explains_no_match(self):
        lines=self.format([])
        self.assertTrue(all(header in lines[0] for header in SESSION_HEADERS))
        self.assertIn('No sessions matched.',lines)

    def test_narrow_pager_labels_all_values_and_no_pager_keeps_table(self):
        class Terminal(io.StringIO):
            def isatty(self):return True
        streams={'input_stream':Terminal(),'output_stream':Terminal()}
        with patch('antigravity_exporter.listings._terminal_size',return_value=os.terminal_size((65,20))):
            lines=session_listing_lines([self.row()],settings=DisplaySettings(pager='auto'),**streams)
            self.assertTrue(all(any(line.startswith(header+':') for line in lines) for header in SESSION_HEADERS))
            self.assertTrue(any(self.row()['selector'] in line for line in lines))
            table=session_listing_lines([self.row()],settings=DisplaySettings(pager='never'),**streams)
            self.assertTrue(all(header in table[0] for header in SESSION_HEADERS))

    def test_complete_title_flag_retains_printable_content_and_marks_default_preview(self):
        row=self.row(title='A'*160+'END')
        normal=self.format([row]);complete=self.format([row],full_titles=True)
        self.assertIn('Title previews end with','\n'.join(normal))
        self.assertNotIn('END','\n'.join(normal))
        self.assertIn('END','\n'.join(complete))
        self.assertNotIn('Title previews end with','\n'.join(complete))

    def test_recent_sort_preserves_catalog_nanoseconds_and_missing_last(self):
        rows=[self.row(id='early',modified='2026-11-27T19:09:16.100000001Z'),
              self.row(id='missing',modified=''),
              self.row(id='late',modified='2026-11-27T11:09:16.100000002-08:00'),
              self.row(id='invalid',modified='not a date')]
        self.assertEqual([row['id'] for row in sort_rows(rows,kind='sessions',sort='recent')],
                         ['late','early','missing','invalid'])
        self.assertEqual([row['id'] for row in sort_rows(rows,kind='sessions',sort='oldest')],
                         ['early','late','missing','invalid'])


class SessionCliTableTests(unittest.TestCase):
    def test_public_cli_respects_saved_timezone_and_keeps_native_json_values(self):
        from test_exporter import make_store, ID
        from antigravity_exporter.cli import main
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);root,log,raw=make_store(path)
            stamp='2026-11-27T19:09:16.342105411Z'
            with contextlib.closing(sqlite3.connect(root/'antigravity/conversation_summaries.db')) as connection,connection:
                connection.execute('UPDATE conversation_summaries SET last_modified_time=?',(stamp,))
            config=path/'config.json';config.write_text(json.dumps({'timestamp_timezone':'-08:00'}))
            def invoke(*flags):
                stdout,stderr=io.StringIO(),io.StringIO()
                with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
                    code=main(['sessions','--root',str(root),'--no-progress',*flags])
                self.assertEqual(code,0,stderr.getvalue());self.assertEqual(stderr.getvalue(),'')
                return stdout.getvalue()
            with patch.dict(os.environ,{'AGY_EXPORT_CONFIG':str(config)}):
                rows=json.loads(invoke('--json'))
                human=invoke('--no-pager')
                empty=invoke('--no-pager','--query','definitely-no-match')
                after=json.loads(invoke('--json'))
            self.assertEqual(rows,after)
            self.assertEqual(rows[0]['modified'],stamp);self.assertEqual(rows[0]['size'],len(raw))
            self.assertTrue(all(header in human.splitlines()[0] for header in SESSION_HEADERS))
            self.assertIn('November 27, 2026 at 11:09:16 AM UTC-08:00',human)
            self.assertIn(ID,human);self.assertIn('Full log',human)
            self.assertTrue(all(header in empty.splitlines()[0] for header in SESSION_HEADERS))
            self.assertIn('No sessions matched.',empty)
            self.assertEqual(log.read_bytes(),raw)


class TimestampTests(unittest.TestCase):
    def test_dst_uses_recorded_instant(self):
        try: formatter=TimestampFormatter(True,'America/Los_Angeles')
        except ValueError: self.skipTest('IANA database unavailable')
        self.assertIn('PST',formatter.format('2026-01-01T12:00:00Z'))
        self.assertIn('PDT',formatter.format('2026-07-01T12:00:00Z'))

    def test_fractional_widths_are_portable_and_preserve_timezone_policy(self):
        formatter=TimestampFormatter(True,'UTC')
        for digits in ('1','12','1234','12345','1234567','123456789'):
            with self.subTest(digits=digits):
                source='2026-11-27T11:09:16.'+digits+'-08:00'
                self.assertEqual(formatter.format(source),'November 27, 2026 at 07:09:16 PM UTC')
                self.assertIsNotNone(parse_source_timestamp(source))
        self.assertIn('timezone unspecified',formatter.format('2026-11-27T11:09:16.123456789'))
        self.assertIsNone(parse_source_timestamp('2026-13-27T11:09:16.123456789Z'))

    def test_zero_naive_invalid_and_disabled(self):
        formatter=TimestampFormatter(True,'UTC')
        self.assertIn('January 1, 1970',formatter.format(0))
        self.assertIn('timezone unspecified',formatter.format('2026-01-01T12:00:00'))
        self.assertEqual(formatter.format('not a time'),'')
        self.assertEqual(TimestampFormatter(False).markdown('2026-01-01T00:00:00Z'),'')
        self.assertIsNone(parse_source_timestamp(True))

if __name__=='__main__':unittest.main()
