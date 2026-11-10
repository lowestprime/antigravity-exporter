"""Portable Antigravity exporter CLI. All app data access is read-only."""
from __future__ import annotations
import argparse
import contextlib
import ctypes
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

from . import __version__
from .storage import (CHUNK, ExportError, Source, VARIANTS, UUID_RE, capture_file,
                      hash_file, publish_fresh, safe_inventory, write_json)
from .model import (EventIndex, audit_database, capture_transcript, interruption_summary,
                    resolve, sessions)
from .render import Redactor, count_text, handoff_text, make_review, render
from .archive import build_zip, diagnose, preflight, verify_zip
from .progress import Progress
from .naming import DEFAULT_TEMPLATE, STABLE_TEMPLATE, FIELDS, safe_slug, export_stamp, mode_descriptor, count_suffix, filename_stem

DEFAULTS={'out_dir':str(Path.home()/'Documents'/'Antigravity Exports'),
          'archive':True,'cleanup':True,'copy_source':True,'review':False,
          'method':'LZMA','backend':'auto','level':9,'dictionary':'256m',
          'word_size':64,'threads':19,'archiver':'','timeout':3600,
          'thinking':True,'redact':False,'tokenizer':'auto','token_encoding':'cl100k_base',
          'template':DEFAULT_TEMPLATE, 'filename_counts':True, 'include_session_short_id':False,
          'recover_tool_outputs':True,'progress_style':'auto'}

def config_path():
    explicit=os.environ.get('AGY_EXPORT_CONFIG')
    return Path(explicit).expanduser() if explicit else Path(os.environ.get('LOCALAPPDATA',Path.home()/'.config'))/'AntigravityExporter'/'config.json'

def validate_config(c):
    if set(c)-set(DEFAULTS): raise ExportError('Unknown configuration key(s): '+','.join(sorted(set(c)-set(DEFAULTS))))
    for key,value in c.items():
        if type(value)!=type(DEFAULTS[key]): raise ExportError(f'Wrong type for configuration {key}')
    if c.get('method',DEFAULTS['method']) not in ('LZMA','Deflate'): raise ExportError('method must be LZMA or Deflate')
    if c.get('backend',DEFAULTS['backend']) not in ('auto','7zip','python'): raise ExportError('Invalid archive backend')
    if not 0<=c.get('level',9)<=9: raise ExportError('Archive level must be 0–9')
    if not 1<=c.get('threads',19)<=256: raise ExportError('Archive threads must be 1–256')
    if not 5<=c.get('word_size',64)<=273: raise ExportError('LZMA word size must be 5–273')
    if c.get('dictionary','256m') not in ('16m','32m','64m','128m','256m','512m'): raise ExportError('Supported LZMA dictionaries: 16m–512m')
    if not 10<=c.get('timeout',3600)<=86400: raise ExportError('Archive timeout must be 10–86400 seconds')
    if c.get('tokenizer','none') not in ('none','regex','auto','tiktoken'): raise ExportError('Invalid tokenizer')
    if c.get('progress_style','auto') not in ('auto','rich','plain','json','off'): raise ExportError('Invalid progress style')
    import string
    try:
        for _,field,fmt,conversion in string.Formatter().parse(c.get('template',DEFAULTS['template'])):
            if field and (field not in FIELDS or fmt or conversion): raise ExportError('Unsafe/unknown filename template field')
    except ValueError as e: raise ExportError('Invalid filename template') from e

def load_config():
    path=config_path(); c={}
    if path.exists():
        if path.is_symlink(): raise ExportError('Configuration symlink rejected')
        if path.stat().st_size>65536: raise ExportError('Configuration file too large')
        c=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(c,dict): raise ExportError('Invalid config object')
        validate_config(c)
    return {**DEFAULTS,**c}

def save_config(c):
    validate_config(c); path=config_path(); path.parent.mkdir(parents=True,exist_ok=True)
    if path.is_symlink(): raise ExportError('Configuration symlink rejected')
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        write_json(temp,c); os.replace(temp,path)
    finally:
        with contextlib.suppress(OSError): temp.unlink()

def _positive(s):
    n=int(s)
    if n<1: raise argparse.ArgumentTypeError('must be >= 1')
    return n

def parser():
    p=argparse.ArgumentParser(description='Read-only Antigravity conversation export, audit and archive verification')
    p.add_argument('--version',action='version',version=__version__)
    sub=p.add_subparsers(dest='command',required=True)
    for cmd in ('sessions','browse','audit','turns','records','responses','export'):
        q=sub.add_parser(cmd)
        source=q.add_mutually_exclusive_group(); source.add_argument('--root'); source.add_argument('--zip',dest='zip_source')
        q.add_argument('--id','--session-id','--conversation-id',action='append',dest='ids')
        q.add_argument('--variant',choices=VARIANTS); q.add_argument('--project')
        q.add_argument('--latest-session',action='store_true'); q.add_argument('--all',action='store_true')
        q.add_argument('--sessions-file'); q.add_argument('--jsonl',dest='direct_jsonl')
        q.add_argument('--json',action='store_true')
        q.add_argument('--verify-db',action=argparse.BooleanOptionalAction,default=True)
        q.add_argument('--verify-chunks',action='store_true'); q.add_argument('--allow-reduced',action='store_true')
        q.add_argument('--strict-events',action='store_true'); q.add_argument('--skip-invalid',action='store_true')
        q.add_argument('--max-record-bytes',type=_positive,default=256*1024*1024)
        q.add_argument('--progress-style',choices=['auto','rich','plain','json','off']); q.add_argument('--no-progress',action='store_true')
        if cmd in ('export','browse'):
            q.add_argument('--mode',choices=['thread','turn','response','response-block','message','range','chat','chat-actions','actions',
                                            'last-response','last-assistant','last-substantial','checkpoint-context'],default='thread')
            q.add_argument('--turn','--response',type=_positive); q.add_argument('--message',type=_positive)
            q.add_argument('--from-message',type=_positive); q.add_argument('--to-message',type=_positive)
            q.add_argument('--from-step',type=int); q.add_argument('--to-step',type=int); q.add_argument('--step',type=int)
            q.add_argument('--last-n-turns',type=_positive); q.add_argument('--last-n-steps',type=_positive)
            q.add_argument('--min-chars',type=_positive,default=500)
            q.add_argument('--out-dir'); q.add_argument('--name'); q.add_argument('--filename-template')
            q.add_argument('--no-filename-counts',action='store_false',dest='filename_counts',default=None)
            q.add_argument('--include-session-short-id',action='store_true',default=None)
            q.add_argument('--stable-filenames',action='store_true'); q.add_argument('--collision',choices=['rename','error','skip'],default='rename')
            q.add_argument('--tool-outputs',choices=['none','summary','tail','full'],default='full')
            q.add_argument('--max-tool-chars',type=int,default=0)
            q.add_argument('--include-thinking',dest='thinking',action='store_true',default=None)
            q.add_argument('--no-thinking',dest='thinking',action='store_false')
            q.add_argument('--literal','--fence-turns',action='store_true',dest='literal')
            q.add_argument('--recover-tool-outputs',action=argparse.BooleanOptionalAction,default=None)
            q.add_argument('--redact',action=argparse.BooleanOptionalAction,default=None)
            q.add_argument('--share',action='store_true',help='Credential-redacted views; no raw native source or binary artifacts')
            q.add_argument('--raw-jsonl','--records-jsonl',action='store_true',dest='records_jsonl',help='Selected JSON objects; redacted when --redact is active')
            q.add_argument('--copy-source',action=argparse.BooleanOptionalAction,default=None)
            q.add_argument('--no-source-copy',action='store_false',dest='copy_source')
            q.add_argument('--include-native','--archival',action='store_true',help='Include scoped SQLite/annotation snapshots')
            q.add_argument('--include-artifacts',action='store_true',help='Include every accessible file in the selected brain directory')
            q.add_argument('--archive',action=argparse.BooleanOptionalAction,default=None)
            q.add_argument('--archive-cleanup',action=argparse.BooleanOptionalAction,dest='cleanup',default=None)
            q.add_argument('--review',action=argparse.BooleanOptionalAction,default=None)
            q.add_argument('--review-chunk-bytes',type=_positive,default=1000000)
            q.add_argument('--handoff',action='store_true')
            q.add_argument('--archive-method',choices=['LZMA','Deflate'],dest='method')
            q.add_argument('--archive-backend',choices=['auto','7zip','python'],dest='backend')
            q.add_argument('--archiver-path',dest='archiver'); q.add_argument('--archive-level',type=int,dest='level')
            q.add_argument('--archive-dictionary',dest='dictionary'); q.add_argument('--archive-word-size',type=int,dest='word_size')
            q.add_argument('--archive-threads',type=int,dest='threads'); q.add_argument('--archive-timeout',type=int,dest='timeout')
            q.add_argument('--tokenizer',choices=['none','regex','auto','tiktoken']); q.add_argument('--token-encoding')
            q.add_argument('--require-tiktoken',action='store_true'); q.add_argument('--exact-max-bytes',type=_positive,default=64*1024*1024)
            q.add_argument('--fast',action='store_true',help='Skip tokenizer work; never discard source content')
            q.add_argument('--stdout',action='store_true'); q.add_argument('--no-file',action='store_true')
            q.add_argument('--clipboard',action='store_true'); q.add_argument('--open-after',action='store_true')
            q.add_argument('--choose-out-dir',action='store_true'); q.add_argument('--remember-out-dir',action='store_true')
            q.add_argument('--save-as',action='store_true')
    q=sub.add_parser('config'); q.add_argument('--set',action='append',default=[]); q.add_argument('--get'); q.add_argument('--reset',action='store_true')
    q=sub.add_parser('doctor'); q.add_argument('--archiver-path')
    q=sub.add_parser('verify'); q.add_argument('path'); q.add_argument('--metadata',action='store_true'); q.add_argument('--max-dictionary-mib',type=_positive,default=512)
    q=sub.add_parser('review-from'); q.add_argument('path'); q.add_argument('--out-dir'); q.add_argument('--review-chunk-bytes',type=_positive,default=1000000)
    q.add_argument('--archive',action=argparse.BooleanOptionalAction,default=True)
    q=sub.add_parser('diagnose-access'); q.add_argument('path')
    return p

def normalized_argv(argv):
    if not argv: return ['--help']
    aliases={'--list-sessions':'sessions','--browse':'browse','--show-config':'config','--show-archive-config':'config',
             '--diagnose-archive':'verify','--review-from':'review-from','--diagnose-access':'diagnose-access'}
    if argv[0] in aliases: return [aliases[argv[0]],*argv[1:]]
    if argv[0]=='--print-filename-template': return ['config','--get','template']
    if argv[0]=='--reset-filename-template': return ['config','--set','template='+DEFAULT_TEMPLATE]
    if argv[0]=='--print-out-dir': return ['config','--get','out_dir']
    if argv[0] in ('--set-default-out-dir','--set-filename-template') and len(argv)==2:
        key='out_dir' if argv[0]=='--set-default-out-dir' else 'template'; return ['config','--set',key+'='+argv[1]]
    if argv[0]=='--reset-config': return ['config','--reset']
    if argv[0]=='--set-archive-default':
        result=['config']
        for value in argv:
            if value=='--set-archive-default': result.append('--set')
            else: result.append(value)
        return result
    if argv[0].startswith('--') and argv[0] not in ('--help','--version'):
        return ['export',*argv]
    return argv

def _sanitize(text):
    text=re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_',str(text))
    text=re.sub(r'\s+','_',text).strip(' ._')
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',text,re.I): text='_'+text
    return text[:180] or 'antigravity_export'

def _choose_directory(default):
    try:
        import tkinter as tk
        from tkinter import filedialog
        root=tk.Tk(); root.withdraw()
        try: value=filedialog.askdirectory(initialdir=str(default),title='Antigravity export folder')
        finally: root.destroy()
    except (ImportError,RuntimeError) as e: raise ExportError('Native folder chooser unavailable; supply --out-dir') from e
    if not value: raise ExportError('Folder selection cancelled')
    return value

def _save_as(default):
    import tkinter as tk
    from tkinter import filedialog
    root=tk.Tk(); root.withdraw()
    try: value=filedialog.asksaveasfilename(initialfile=default+'.zip',defaultextension='.zip',filetypes=[('ZIP archive','*.zip')])
    finally: root.destroy()
    if not value: raise ExportError('Save As cancelled')
    return Path(value).expanduser().resolve()

def _clipboard(text):
    if os.name=='nt':
        # CF_UNICODETEXT via Win32, with immediate readback. No PowerShell encoding boundary.
        import ctypes.wintypes as wt
        user=ctypes.WinDLL('user32',use_last_error=True); kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GlobalAlloc.argtypes=[wt.UINT,ctypes.c_size_t]; kernel.GlobalAlloc.restype=wt.HGLOBAL
        kernel.GlobalLock.argtypes=[wt.HGLOBAL]; kernel.GlobalLock.restype=ctypes.c_void_p
        kernel.GlobalUnlock.argtypes=[wt.HGLOBAL]; kernel.GlobalFree.argtypes=[wt.HGLOBAL]
        user.SetClipboardData.argtypes=[wt.UINT,wt.HANDLE]; user.SetClipboardData.restype=wt.HANDLE
        user.GetClipboardData.argtypes=[wt.UINT]; user.GetClipboardData.restype=wt.HANDLE
        data=text.encode('utf-16-le')+b'\x00\x00'; handle=kernel.GlobalAlloc(0x42,len(data))
        if not handle: raise ExportError('Clipboard allocation failed')
        pointer=kernel.GlobalLock(handle)
        if not pointer: kernel.GlobalFree(handle); raise ExportError('Clipboard lock failed')
        ctypes.memmove(pointer,data,len(data)); kernel.GlobalUnlock(handle)
        opened=False; transferred=False
        try:
            for _ in range(20):
                if user.OpenClipboard(None): opened=True; break
                time.sleep(0.05)
            if not opened: raise ExportError('Clipboard is busy')
            if not user.EmptyClipboard() or not user.SetClipboardData(13,handle): raise ExportError('Clipboard write failed')
            transferred=True
            check=user.GetClipboardData(13); pointer=kernel.GlobalLock(check)
            if not pointer: raise ExportError('Clipboard readback lock failed')
            try: observed=ctypes.wstring_at(pointer)
            finally: kernel.GlobalUnlock(check)
            if observed!=text: raise ExportError('Clipboard readback mismatch')
        finally:
            if opened: user.CloseClipboard()
            if not transferred: kernel.GlobalFree(handle)
    else:
        command=next((x for x in (['pbcopy'],['wl-copy'],['xclip','-selection','clipboard']) if shutil.which(x[0])),None)
        if not command: raise ExportError('No platform clipboard utility available')
        subprocess.run(command,input=text.encode('utf-8'),check=True)

def _open(path):
    if os.name=='nt': os.startfile(str(path))
    else: subprocess.Popen(['open' if sys.platform=='darwin' else 'xdg-open',str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

def reserve(out:Path,stem:str,collision):
    for suffix in range(0,10000):
        candidate=stem+('' if suffix==0 else f'_{suffix+1}')
        if (out/(candidate+'.zip')).exists() or (out/(candidate+'.files')).exists():
            if collision=='skip': return None,None
            if collision=='error': raise ExportError('Output already exists')
            continue
        lock=out/(candidate+'.reserve')
        try:
            with lock.open('x',encoding='utf-8') as f: f.write(f'{os.getpid()}\n')
            return candidate,lock
        except FileExistsError:
            if collision=='skip': return None,None
            if collision=='error': raise ExportError('Output name is reserved by another export')
    raise ExportError('Unable to reserve unique export name')

def export_one(src,session,index,info,db_audit,supplements,args,options,stage,progress,archiver):
    selection=index.selection(args)
    interruptions=interruption_summary(index,supplements)
    opts={**options,'tool_outputs':args.tool_outputs,'max_tool_chars':args.max_tool_chars,
          'records_jsonl':args.records_jsonl or args.share,'literal':args.literal}
    generated=stage/'generated'; generated.mkdir()
    md=generated/'rendered.md'
    with progress.group('render',total=selection['records'],unit='records'):
        rendered=render(index,session,selection,info,db_audit,supplements,md,opts,src=src,progress=progress)
    with progress.group('count-rendered-text',unit='bytes'):
        counts=count_text(md,options['tokenizer'],options['token_encoding'],args.exact_max_bytes)
    privacy='REDACTED' if options['redact'] else 'PRIVATE'
    timestamp=datetime.now().astimezone().strftime('%Y%m%d_%H%M%S_%f_%Z')
    template=args.filename_template or options['template']
    validate_config({'template':template})
    short=session['id'].replace('-','')[-8:]
    include_short=args.stable_filenames or options['include_session_short_id'] or getattr(args,'batch_count',1)>1
    values={'title':safe_slug(args.name or session['title']),'id':session['id'],'short_id':session['id'][:8],
            'session_id':safe_slug(session['id']), 'session_short':short,
            'session_short_part':('_'+short) if include_short else '',
            'project':safe_slug(session.get('project','')),'mode':mode_descriptor(args),'timestamp':timestamp,
            'stamp':export_stamp(), 'privacy':privacy,'first_step':selection['first_step'],'last_step':selection['last_step'],
            'lines':counts['lines'],'tokens':counts.get('tokens') if counts.get('tokens') is not None else 'uncounted',
            'encoding':counts.get('encoding','none'),'token_encoding':safe_slug(counts.get('encoding','none')),
            'token_method':'tiktoken' if counts.get('exact_for_selected_encoding') else 'regex_estimate' if counts.get('tokens') is not None else 'none',
            'counts':count_suffix(counts) if options['filename_counts'] else ''}
    if args.stable_filenames: template=STABLE_TEMPLATE
    stem=filename_stem(template,values)
    if args.stdout or args.clipboard:
        text=md.read_text(encoding='utf-8')
        if args.stdout: sys.stdout.write(text); sys.stdout.flush()
        if args.clipboard: _clipboard(text)
    if args.no_file:
        return {'id':session['id'],'no_file':True,'clipboard':args.clipboard,'selected':selection['records']}
    out=Path(options['out_dir']).expanduser().resolve()
    if args.choose_out_dir: out=Path(_choose_directory(out)).resolve()
    if args.save_as:
        if not options['archive']: raise ExportError('--save-as requires an archive export')
        target=_save_as(stem); out=target.parent; stem=_sanitize(target.stem)
    if src and src.root and (out==src.root or src.root in out.parents): raise ExportError('Output cannot be inside Antigravity source root')
    if args.direct_jsonl and (out/ (stem+'.zip')).resolve()==Path(args.direct_jsonl).resolve(): raise ExportError('Output aliases direct source')
    out.mkdir(parents=True,exist_ok=True)
    stem,lock=reserve(out,stem,args.collision)
    if stem is None: return {'id':session['id'],'skipped_existing':True}
    try:
        md_named=generated/(stem+'.md'); md.rename(md_named); md=md_named
        members={stem+'.md':md}
        if options['copy_source']:
            members[stem+'.jsonl']=index.source
        if rendered['records_jsonl']:
            members[stem+'.records.jsonl']=rendered['records_jsonl']
        if args.handoff:
            handoff=generated/'HANDOFF_INDEX.md'
            handoff.write_text(handoff_text(session,index,db_audit,interruptions,selection),encoding='utf-8')
            members['HANDOFF_INDEX.md']=handoff
        native_inventory=[]
        if src and args.include_native:
            for rel in (f"{session['variant']}/conversations/{session['id']}.db",f"{session['variant']}/annotations/{session['id']}.pbtxt"):
                if src.exists(rel):
                    p,receipt=src.snapshot(rel,database=rel.endswith('.db')); members['native/'+rel]=p; native_inventory.append(receipt)
        if src and args.include_artifacts:
            paths=src.files(f"{session['variant']}/brain/{session['id']}/")
            with progress.group('collect-artifacts',total=len(paths),unit='files'):
                for number,rel in enumerate(paths,1):
                    p,receipt=src.snapshot(rel,growth=rel.endswith(('.jsonl','.log')))
                    members['native/'+rel]=p; native_inventory.append(receipt)
                    progress.update('collect-artifacts',number,len(paths),unit='files',detail=rel)
        if (args.include_native or args.include_artifacts) and not src: raise ExportError('Native artifacts require a store/ZIP source, not just direct JSONL')
        review=None
        if options['review']:
            extra,review=make_review(md,generated/'review',cap=args.review_chunk_bytes,progress=progress)
            members.update(extra)
        progress.update('prepare-manifest',detail='Hashing generated and captured members',force=True)
        inventory={name:{'bytes':path.stat().st_size,'sha256':hash_file(path)} for name,path in sorted(members.items())}
        redactor=Redactor(options['redact'])
        manifest={'schema':'antigravity-export/3','exporter_version':__version__,
                  'captured_at_utc':datetime.now(timezone.utc).isoformat(),'session':session,
                  'source_capture':info,'source_coverage':index.audit,'selection':selection,'turns':index.turns,
                  'database_audit':db_audit,'database_only_diagnostics':redactor.apply(supplements),
                  'interruption':interruptions,'rendered_text':counts,
                  'redaction':{'enabled':options['redact'],'best_effort':True,'anonymized':False,
                               'credential_values_replaced':rendered['credential_values_replaced']},
                  'source_copy':{'included':options['copy_source'],'byte_exact_captured_prefix':options['copy_source'],
                                 'scope':'entire captured transcript, not just selected view'},
                  'recovered_tool_outputs':rendered['recovered_outputs'],'unavailable_recovery':rendered['unavailable_recovery'],
                  'view_omissions':rendered['view_omissions'],'native_inventory':native_inventory,
                  'review':review,'filename_policy':{'template':template,'stamp':values['stamp'],'session_short':short,'counts':options['filename_counts']},
                  'archive_policy':{k:options[k] for k in ('archive','method','backend','level','dictionary','word_size','threads','cleanup')},
                  'archive_backend_executable':archiver,'members':inventory,
                  'manifest_self_hash':'Provided in verification receipt, not self-referentially embedded',
                  'safety':'No app-owned source was deleted or modified by this exporter. Multi-file atomicity is not claimed.'}
        manifest=redactor.apply(manifest)
        manifest_path=generated/(stem+'.manifest.json'); write_json(manifest_path,manifest); members[manifest_path.name]=manifest_path
        result={'id':session['id'],'exporter_version':__version__,'selected_records':selection['records'],
                'database_coverage_matches':db_audit.get('matched_exactly'),
                'db_only_steps':db_audit.get('db_only_steps',[]),'privacy':privacy,'counts':counts,
                'latest_quota_record':interruptions['latest_database_only_quota_step'],
                'manifest_sha256':hash_file(manifest_path),'native_source_included':options['copy_source'],
                'original_files_never_deleted':True}
        if options['archive']:
            archive_path=stage/(stem+'.zip')
            check=build_zip(archive_path,members,options,progress=progress,archiver=archiver)
            final=out/(stem+'.zip')
            receipt=publish_fresh(archive_path,final,progress=progress)
            result.update(archive=receipt['path'],archive_sha256=receipt['sha256'],archive_bytes=receipt['bytes'],
                          archive_verified=True,archive_member_count=len(check['members']),
                          member_verification=check['members'])
        if not options['archive'] or not options['cleanup']:
            loose=out/(stem+'.files'); loose.mkdir(exist_ok=False)
            total_bytes=sum(path.stat().st_size for path in members.values()); copied=0
            with progress.group('publish-loose-files',total=total_bytes,unit='bytes'):
                for name,path in members.items():
                    target=loose/name; target.parent.mkdir(parents=True,exist_ok=True)
                    publish_fresh(path,target,progress=progress)
                    copied+=path.stat().st_size
                    progress.update('publish-loose-files',copied,total_bytes,detail=name)
            result['loose_directory']=str(loose); result['manifest']=str(loose/manifest_path.name)
            result['markdown']=str(loose/md.name)
        result['cleanup_requested']=options['cleanup']
        result['loose_outputs_retained']=not options['archive'] or not options['cleanup']
        if args.remember_out_dir:
            c=load_config(); c['out_dir']=str(out); save_config(c)
        if args.open_after: _open(Path(result.get('archive') or result['markdown']))
        return result
    finally:
        with contextlib.suppress(OSError): lock.unlink()

def verify_export(path, metadata=False,max_dictionary_mib=512):
    report=diagnose(path,metadata,max_dictionary_mib)
    if report['status']!='ok' or metadata: return report
    names=[x['name'] for x in report['members'] if x['name'].endswith('.manifest.json')]
    if len(names)==1:
        with zipfile.ZipFile(path) as z:
            entry=z.getinfo(names[0])
            if entry.file_size>64*1024*1024: raise ExportError('Embedded manifest exceeds inspection limit')
            manifest=json.loads(z.read(entry))
        if manifest.get('schema')=='antigravity-export/3':
            actual={m['name']:m for m in report['members']}
            expected=manifest.get('members',{})
            if set(actual)!=set(expected)|{names[0]}: raise ExportError('Manifest/ZIP inventory mismatch')
            for name,value in expected.items():
                if actual[name]['sha256']!=value['sha256'] or actual[name]['bytes']!=value['bytes']:
                    raise ExportError('Manifest hash/size mismatch for '+name)
            report['embedded_manifest_hashes_matched']=True
    return report

def review_existing(args):
    source=Path(args.path).expanduser().resolve(); stage=Path(tempfile.mkdtemp(prefix='agy-review-')); success=False
    try:
        captured=stage/'input'
        capture_file(source,captured)
        if source.suffix.lower()=='.zip':
            with zipfile.ZipFile(captured) as z:
                inv=safe_inventory(z)
                names=[n for n in inv if n.lower().endswith('.md') and '/' not in n and not n.upper().startswith(('HANDOFF','READ_FIRST'))]
                if len(names)!=1: raise ExportError('Archive must contain one unambiguous top-level Markdown transcript')
                md=stage/'source.md'
                with z.open(inv[names[0]]) as f,md.open('xb') as out:
                    shutil.copyfileobj(f,out,CHUNK)
        elif source.suffix.lower() in ('.md','.txt'):
            md=stage/'source.md'; shutil.copyfile(captured,md)
        else: raise ExportError('review-from accepts a ZIP, Markdown, or UTF-8 TXT file')
        review_dir=stage/'review'; members,index=make_review(md,review_dir,cap=args.review_chunk_bytes)
        out=Path(args.out_dir or load_config()['out_dir']).expanduser().resolve(); out.mkdir(parents=True,exist_ok=True)
        stem=_sanitize(source.stem+'.review_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        loose=out/(stem+'.files'); loose.mkdir()
        for name,path in members.items():
            dest=loose/name; dest.parent.mkdir(parents=True,exist_ok=True); publish_fresh(path,dest)
        result={'review_directory':str(loose),'reconstruction_verified':True,'parts':len(index['parts'])}
        if args.archive:
            arc=stage/'review.zip'; build_zip(arc,members,{**DEFAULTS,'method':'Deflate','level':6,'backend':'python'})
            result['archive']=publish_fresh(arc,out/(stem+'.zip'))['path']
        success=True; return result
    finally:
        if success: shutil.rmtree(stage,ignore_errors=True)
        else: print('Review failure; recoverable staged files: '+str(stage),file=sys.stderr)

def run(args):
    if args.command=='config':
        c=dict(DEFAULTS) if args.reset else load_config()
        for entry in args.set:
            if '=' not in entry: raise ExportError('Use --set KEY=VALUE')
            k,v=entry.split('=',1)
            if k not in DEFAULTS: raise ExportError('Unknown config key '+k)
            if isinstance(DEFAULTS[k],bool):
                if v.lower() not in ('true','false'): raise ExportError('Boolean value must be true/false')
                c[k]=v.lower()=='true'
            elif isinstance(DEFAULTS[k],int): c[k]=int(v)
            else: c[k]=v
        validate_config(c)
        if args.set or args.reset: save_config(c)
        if args.get:
            if args.get not in c: raise ExportError('Unknown config key '+args.get)
            return c[args.get]
        return {'config_path':str(config_path()),**c}
    if args.command=='verify': return verify_export(args.path,args.metadata,args.max_dictionary_mib)
    if args.command=='review-from': return review_existing(args)
    if args.command=='diagnose-access':
        p=Path(args.path).expanduser().resolve()
        report={'path':str(p),'exists':p.exists(),'platform':sys.platform}
        try:
            with p.open('rb') as f: f.read(1)
            report['readable']=True
        except OSError as e: report.update(readable=False,error=str(e))
        if os.name=='nt':
            result=subprocess.run([str(Path(os.environ['WINDIR'])/'System32'/'icacls.exe'),str(p)],capture_output=True,text=True,errors='replace')
            report['icacls']=result.stdout+result.stderr; report['icacls_exit']=result.returncode
        return report
    if args.command=='doctor':
        c=load_config(); external=None; error=None
        try: external=preflight({**c,'archiver':args.archiver_path or c['archiver']})
        except ExportError as e: error=str(e)
        return {'version':__version__,'python':sys.version,'executable':sys.executable,
                'sqlite_version':__import__('sqlite3').sqlite_version,'platform':sys.platform,
                'codecs':{k:importlib.util.find_spec(k) is not None for k in ('lzma','zlib','tiktoken','zstandard')},
                'source_root_exists':(Path.home()/'.gemini').is_dir(),'archiver':external,'archive_preflight_error':error,
                'no_network_requests_or_package_installation_performed':True}
    c=load_config(); options=dict(c)
    for key in c:
        value=getattr(args,key,None)
        if value is not None: options[key]=value
    if getattr(args,'fast',False): options['tokenizer']='none'
    if getattr(args,'require_tiktoken',False): options['tokenizer']='tiktoken'
    if getattr(args,'share',False): options.update(redact=True,copy_source=False)
    if getattr(args,'no_file',False): options.update(archive=False,copy_source=False)
    validate_config(options)
    if options['redact'] and options['copy_source']:
        raise ExportError('--redact cannot be combined with a private native source copy. Use --share or --no-source-copy.')
    if options['redact'] and (getattr(args,'include_native',False) or getattr(args,'include_artifacts',False)):
        raise ExportError('Redacted views cannot bundle raw binary/native artifacts; make a separate PRIVATE archival export')
    if getattr(args,'no_file',False) and not (args.stdout or args.clipboard): raise ExportError('--no-file requires --stdout or --clipboard')
    for low,high in (('from_step','to_step'),('from_message','to_message')):
        a,b=getattr(args,low,None),getattr(args,high,None)
        if a is not None and b is not None and a>b: raise ExportError(f'{low} exceeds {high}')
    if any(getattr(args,key,0) is not None and getattr(args,key,0)<0 for key in ('step','from_step','to_step','max_tool_chars')):
        raise ExportError('Step numbers and maximum tool characters cannot be negative')
    progress=Progress('off' if args.no_progress else options['progress_style'])
    work=Path(tempfile.mkdtemp(prefix='agy-export-')); success=False
    started=time.monotonic()
    try:
        if args.direct_jsonl:
            if args.zip_source: raise ExportError('--jsonl cannot be combined with --zip')
            if args.ids and len(args.ids)>1: raise ExportError('Direct JSONL accepts at most one identity label')
            original=Path(args.direct_jsonl).expanduser().resolve()
            if original.suffix.lower()!='.jsonl': raise ExportError('Direct input must be plain .jsonl; compressed rollout decoding is not implemented')
            ident=args.ids[0] if args.ids else str(uuid.uuid5(uuid.NAMESPACE_URL,original.as_uri()))
            if not UUID_RE.fullmatch(ident): raise ExportError('Direct source identity label must be a UUID')
            catalog=[{'id':ident,'title':original.stem,'variant':'direct','project':'','identity_is_user_label':True}]
            src=None
        else:
            src=Source(root=args.root,archive=args.zip_source,stage=work/'source',progress=progress)
            catalog=sessions(src)
        if args.command=='sessions':
            if args.project: catalog=[s for s in catalog if s['project'].casefold()==args.project.casefold() or s['project_id']==args.project]
            if args.variant: catalog=[s for s in catalog if s['variant']==args.variant]
            success=True; return catalog
        if args.command=='browse':
            if not sys.stdin.isatty(): raise ExportError('browse requires an interactive terminal; use sessions --json for automation')
            for i,s in enumerate(catalog,1): print(f"{i:3} {s['id']}  {s['title']}  [{s.get('project','')}]",file=sys.stderr)
            value=input('Session numbers, separated by commas: ')
            numbers=[int(s.strip()) for s in value.split(',')]
            if any(n<1 or n>len(catalog) for n in numbers): raise ExportError('Invalid session selection')
            args.ids=[catalog[n-1]['id'] for n in numbers]
        ids=list(args.ids or [])
        if args.sessions_file:
            ids.extend(x.strip() for x in Path(args.sessions_file).read_text(encoding='utf-8-sig').splitlines() if x.strip() and not x.lstrip().startswith('#'))
        chosen=catalog if args.direct_jsonl else resolve(catalog,ids,args.project,args.variant,args.latest_session,args.all)
        if not chosen: raise ExportError('No sessions matched')
        if getattr(args,'stdout',False) and len(chosen)>1: raise ExportError('--stdout is single-session only')
        if getattr(args,'clipboard',False) and len(chosen)>1: raise ExportError('--clipboard is single-session only')
        if getattr(args,'save_as',False) and len(chosen)>1: raise ExportError('--save-as is single-session only')
        args.batch_count=len(chosen)
        is_export=args.command in ('export','browse')
        archiver=preflight(options) if is_export else None
        result=[]
        for number,session in enumerate(chosen,1):
            stage=work/('job-'+str(number)); stage.mkdir()
            progress.update('resolve-session',detail=session['id'],force=True)
            if args.direct_jsonl:
                log=stage/'source.jsonl'; info=capture_file(original,log,allow_growth=True,progress=progress)
                info.update(relative_source=str(original),fidelity='user-selected-direct-JSONL')
            else: log,info=capture_transcript(src,session,allow_reduced=args.allow_reduced,verify_chunks=args.verify_chunks)
            index=EventIndex(log,stage/'index.db',max_record_bytes=args.max_record_bytes,strict_events=args.strict_events,skip_invalid=args.skip_invalid,progress=progress)
            try:
                db_audit,supplements=(audit_database(src,session,index) if args.verify_db and src else ({'available':False,'coverage':'not_checked'},[]))
                interrupts=interruption_summary(index,supplements)
                if args.command=='audit': result.append({'session':session,'source':info,'coverage':index.audit,'turns':index.turns,'database':db_audit,'database_only_records':supplements,'interruption':interrupts})
                elif args.command=='turns': result.append({'session':session,'turns':index.turns,'database':db_audit,'interruption':interrupts})
                elif args.command in ('records','responses'): result.append({'session':session,'records':list(index.listing(args.command=='responses'))})
                else: result.append(export_one(src,session,index,info,db_audit,supplements,args,options,stage,progress,archiver))
            finally: index.close()
        if src: src.close()
        success=True
        if len(result)==1:
            result[0]['elapsed_seconds']=round(time.monotonic()-started,3)
            return result[0]
        return result
    finally:
        # Close SQLite/ZIP handles before removing private staging on Windows.
        if 'src' in locals() and src: src.close()
        if success:
            progress.update('cleanup',detail='Remove exporter-owned temporary staging only',force=True)
            shutil.rmtree(work,ignore_errors=True)
        progress.finish(success=success)
        if not success: print('Export stopped. Recoverable staged copies (PRIVATE): '+str(work),file=sys.stderr)

def main(argv=None):
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'): stream.reconfigure(encoding='utf-8',errors='strict')
    args=parser().parse_args(normalized_argv(list(sys.argv[1:] if argv is None else argv)))
    try:
        result=run(args)
        if getattr(args,'stdout',False): return 0
        if args.command=='sessions' and not args.json:
            for s in result: print(f"{s['id']}  {s['variant']}  {s['title']}  full={s['full']}  db={s['db']}  project={s['project']}")
        elif args.command in ('export','browse') and not getattr(args,'json',False):
            for item in result if isinstance(result,list) else [result]:
                print(item.get('archive') or item.get('markdown') or json.dumps(item))
        elif isinstance(result,str): print(result)
        else: print(json.dumps(result,ensure_ascii=False,indent=2))
        if isinstance(result,dict) and result.get('status') not in (None,'ok'): return 3
        return 0
    except KeyboardInterrupt:
        print('Cancelled. Original Antigravity data was not changed.',file=sys.stderr); return 130
    except (ExportError,OSError,ValueError,zipfile.BadZipFile,RuntimeError,ImportError,sqlite3.Error) as e:
        print(f'ERROR: {type(e).__name__}: {e}',file=sys.stderr); return 2

if __name__=='__main__': raise SystemExit(main())
