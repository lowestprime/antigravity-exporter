"""End-to-end LZMA encoder smoke test using only a generated synthetic store."""
from pathlib import Path
import argparse,json,sys,tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from test_exporter import make_store,ID
from antigravity_exporter.cli import parser,run,verify_export
p=argparse.ArgumentParser();p.add_argument('--archiver',required=True);a=p.parse_args()
with tempfile.TemporaryDirectory() as d:
    root,_,_=make_store(d)
    import os
    os.environ['AGY_EXPORT_CONFIG']=str(Path(d)/'no-user-config.json')
    result=run(parser().parse_args(['export','--root',str(root),'--id',ID,'--out-dir',str(Path(d)/'out'),
        '--archive-method','LZMA','--archive-backend','7zip','--archiver-path',a.archiver,'--no-progress','--tokenizer','none']))
    receipt=verify_export(result['archive'])
    assert receipt.get('embedded_manifest_hashes_matched')
    assert receipt['all_members_decoded_and_crc_checked']
    assert result['archive_member_count']==3
    print('PASS: real external LZMA encode, decode, member hashes, cleanup, and fresh publication')
