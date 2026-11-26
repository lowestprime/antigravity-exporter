"""Synthetic regressions for anchor selection and presentation-only sorting."""
from __future__ import annotations
from argparse import Namespace
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from antigravity_exporter.model import EventIndex
from antigravity_exporter.selection import resolve_since, turn_listing, parse_since_time
from antigravity_exporter.storage import ExportError
from antigravity_exporter.listings import DisplaySettings, print_listing, sort_rows
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
        self.assertEqual(args.from_step,0)
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
        self.assertEqual(args.from_step,5)
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


class TimestampTests(unittest.TestCase):
    def test_dst_uses_recorded_instant(self):
        try: formatter=TimestampFormatter(True,'America/Los_Angeles')
        except ValueError: self.skipTest('IANA database unavailable')
        self.assertIn('PST',formatter.format('2026-01-01T12:00:00Z'))
        self.assertIn('PDT',formatter.format('2026-07-01T12:00:00Z'))

    def test_zero_naive_invalid_and_disabled(self):
        formatter=TimestampFormatter(True,'UTC')
        self.assertIn('January 1, 1970',formatter.format(0))
        self.assertIn('timezone unspecified',formatter.format('2026-01-01T12:00:00'))
        self.assertEqual(formatter.format('not a time'),'')
        self.assertEqual(TimestampFormatter(False).markdown('2026-01-01T00:00:00Z'),'')
        self.assertIsNone(parse_source_timestamp(True))

if __name__=='__main__':unittest.main()
