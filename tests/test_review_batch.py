"""Batch review selection and multipart honesty, using only synthetic content."""
from __future__ import annotations
import hashlib, io, json, os, random, struct, tempfile, unittest, zipfile
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch
from antigravity_exporter.archive_set import package_members, verify_archive_set
from antigravity_exporter.cli import parser, review_existing, verify_export
from antigravity_exporter.storage import ExportError

ID1='11111111-2222-4333-8444-555555555555'
ID2='aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
OPTIONS={'method':'Deflate','backend':'python','level':6}

class ReviewBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.environment=patch.dict(os.environ,{'AGY_EXPORT_CONFIG':str(self.root/'config.json')})
        self.environment.start();self.addCleanup(self.environment.stop)
        (self.root/'config.json').write_text(json.dumps({'progress_style':'off'}))
    def members(self,large=False):
        members={};payload={}
        for number,sid in enumerate((ID1,ID2),1):
            stem=f'example{number}'
            md=(f'# Session {number}\n'+'example\n'*50).encode()
            if large and number==1:md=random.Random(67).randbytes(100000).hex().encode()
            values={stem+'.md':md,stem+'.jsonl':b'{"type":"user"}\n'}
            manifest={'schema':'antigravity-export/4','session':{'id':sid,'variant':'antigravity'},
                      'members':{name:{'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)} for name,raw in values.items()}}
            values[stem+'.manifest.json']=json.dumps(manifest).encode()
            for name,raw in values.items():
                path=self.root/name;path.write_bytes(raw);members[name]=path;payload[name]=raw
        return members,payload
    def request(self,path,**updates):
        args=parser().parse_args(['review-from',str(path),'--out-dir',str(self.root/'reviews')])
        for key,value in updates.items():setattr(args,key,value)
        return args
    def rebuild_review(self,result):
        root=Path(result['review_directory'])/'review'
        index=json.loads((root/'INDEX.json').read_text())
        data=b''.join((root/row['file']).read_bytes()[row['header_bytes']:] for row in index['parts'])
        self.assertEqual(hashlib.sha256(data).hexdigest(),index['source_sha256'])
        return data,index
    def test_batch_review_requires_selection_then_uuid_selects_exact_transcript(self):
        members,payload=self.members();archive=package_members(self.root/'batch.zip',members,OPTIONS)['archive']
        with self.assertRaisesRegex(ExportError,'unambiguous'):review_existing(self.request(archive))
        result=review_existing(self.request(archive,id='antigravity:'+ID2[:8]))
        text,index=self.rebuild_review(result)
        self.assertEqual(text,payload['example2.md'])
        self.assertEqual(index['conversion_source']['selected_markdown_member'],'example2.md')
        self.assertFalse(result['conversion_source']['native_jsonl_opened'])
        self.assertFalse(result['conversion_source']['entire_input_zip_verified'])
    def test_member_selects_exact_markdown_and_conflicting_selectors_reject(self):
        members,payload=self.members();archive=package_members(self.root/'batch.zip',members,OPTIONS)['archive']
        result=review_existing(self.request(archive,member='example1.md',archive=False))
        self.assertEqual(self.rebuild_review(result)[0],payload['example1.md']);self.assertNotIn('archive',result)
        with self.assertRaisesRegex(ExportError,'different transcripts'):
            review_existing(self.request(archive,id=ID2,member='example1.md'))
    def test_bad_native_crc_not_opened_or_claimed_verified_by_review(self):
        members,payload=self.members();archive=Path(package_members(self.root/'batch.zip',members,OPTIONS)['archive'])
        with zipfile.ZipFile(archive) as z:info=z.getinfo('example1.jsonl')
        with archive.open('r+b') as stream:
            stream.seek(info.header_offset);header=stream.read(30);n,e=struct.unpack_from('<HH',header,26)
            stream.seek(info.header_offset+30+n+e+max(0,info.compress_size//2))
            byte=stream.read(1);stream.seek(-1,1);stream.write(bytes([byte[0]^255]))
        result=review_existing(self.request(archive,id=ID1))
        self.assertEqual(self.rebuild_review(result)[0],payload['example1.md'])
        self.assertNotIn('example1.jsonl',result['conversion_source']['decoded_crc_checked_members'])
        self.assertFalse(result['conversion_source']['entire_input_zip_verified'])
    def test_verify_part_does_not_claim_complete_manifest_or_set(self):
        members,_=self.members(large=True)
        result=package_members(self.root/'large.zip',members,{**OPTIONS,'max_archive_bytes':32768})
        self.assertGreater(result['archive_count'],1)
        for part in result['archives']:
            report=verify_export(part)
            self.assertTrue(report['all_members_decoded_and_crc_checked'])
            self.assertFalse(report['embedded_manifest_hashes_matched'])
            self.assertFalse(report['complete_set_verified'])
        self.assertTrue(verify_archive_set(result['archives'])['complete_set_verified'])
        with self.assertRaisesRegex(ExportError,'multipart fragment'):
            review_existing(self.request(result['archives'][0]))
    def test_review_conversion_respects_16384_cap_and_keeps_original(self):
        raw=random.Random(71).randbytes(100000).hex().encode();source=self.root/'input.md';source.write_bytes(raw)
        result=review_existing(self.request(source,max_archive_bytes=16384))
        self.assertGreater(result['archive_count'],1)
        self.assertTrue(all(Path(path).stat().st_size<=16384 for path in result['archives']))
        self.assertTrue(verify_archive_set(result['archives'])['complete_set_verified'])
        self.assertEqual(source.read_bytes(),raw);self.assertEqual(self.rebuild_review(result)[0],raw)
        self.assertFalse(list((self.root/'reviews').glob('.antigravity-review-*')))
    def test_selectors_rejected_for_plain_markdown(self):
        source=self.root/'input.md';source.write_text('# Source')
        with self.assertRaisesRegex(ExportError,'ZIP inputs only'):review_existing(self.request(source,id=ID1))

if __name__=='__main__':unittest.main()
