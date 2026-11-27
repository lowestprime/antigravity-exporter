"""Portable Antigravity exporter CLI. All app data access is read-only."""
from __future__ import annotations
import argparse
import contextlib
import copy
import hashlib
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
from .catalog import has_full_transcript, has_transcript
from .archive import build_zip, diagnose, preflight, verify_zip
from .progress import Progress
from .archive_set import package_members
from .selection import resolve_since, turn_listing, preview
from .listings import DisplaySettings, sort_rows, print_listing, session_listing_lines
from .timestamps import TimestampFormatter
from .naming import DEFAULT_TEMPLATE, STABLE_TEMPLATE, FIELDS, safe_slug, export_stamp, mode_descriptor, count_suffix, filename_stem

DEFAULTS={'out_dir':str(Path.home()/'Documents'/'Antigravity Exports'),
          'archive':True,'cleanup':True,'copy_source':True,'review':False,
          'method':'LZMA','backend':'auto','level':9,'dictionary':'256m',
          'word_size':64,'threads':19,'archiver':'','timeout':3600,
          'thinking':True,'redact':False,'tokenizer':'auto','token_encoding':'cl100k_base',
          'template':DEFAULT_TEMPLATE, 'filename_counts':True, 'include_session_short_id':False,
          'recover_tool_outputs':True,'progress_style':'auto',
          'max_archive_bytes':512000000,'work_dir':'','separate_archives':False,
          'timestamps':True,'timestamp_timezone':'local','sort':'','sort_order':'auto','pager':'auto','page_size':0}

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
    if not 16384<=c.get('max_archive_bytes',512000000)<=512000000: raise ExportError('Archive cap must be 16384–512000000 bytes')
    from .timestamps import TimestampFormatter
    from .listings import DisplaySettings
    TimestampFormatter(c.get('timestamps',True),c.get('timestamp_timezone','local'))
    DisplaySettings(c.get('sort',''),c.get('sort_order','auto'),c.get('pager','auto'),c.get('page_size',0))
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
    p=argparse.ArgumentParser(description='Antigravity Exporter '+__version__+' — recorded-source export, inclusive selections and bounded verified archives',epilog='Run export --help for all controls. Bare invocation does not modify data. Defaults: private source copy; verified ZIP; LZMA/9/256m/64/19; 512000000-byte cap; review off. Saved config overrides defaults; inspect with config.')
    p.add_argument('--version',action='version',version=__version__)
    sub=p.add_subparsers(dest='command',required=True)
    for cmd in ('sessions','projects','browse','audit','turns','records','responses','export'):
        q=sub.add_parser(cmd)
        source=q.add_mutually_exclusive_group(); source.add_argument('--root'); source.add_argument('--zip',dest='zip_source')
        q.add_argument('--id','--session-id','--conversation-id',action='append',dest='ids')
        q.add_argument('--variant',choices=VARIANTS); q.add_argument('--project')
        q.add_argument('--latest-session','--latest',action='store_true'); q.add_argument('--all',action='store_true')
        q.add_argument('--sessions-file'); q.add_argument('--jsonl',dest='direct_jsonl')
        q.add_argument('--json',action='store_true')
        q.add_argument('--verify-db',action=argparse.BooleanOptionalAction,default=True)
        q.add_argument('--verify-chunks',action='store_true'); q.add_argument('--allow-reduced',action='store_true')
        q.add_argument('--strict-events',action='store_true'); q.add_argument('--skip-invalid',action='store_true')
        q.add_argument('--strict-step-order',action='store_true',help='Reject repeated/backward native step IDs; default preserves every valid source record in captured order')
        q.add_argument('--max-record-bytes',type=_positive,default=256*1024*1024)
        q.add_argument('--progress-style',choices=['auto','rich','plain','json','off']); q.add_argument('--no-progress',action='store_true')
        q.add_argument('--sort'); q.add_argument('--sort-order',choices=['auto','asc','desc'])
        q.add_argument('--page-size',type=int); q.add_argument('--pager',choices=['auto','always','never'])
        q.add_argument('--no-pager',dest='pager',action='store_const',const='never')
        q.add_argument('--query','--browse-query',dest='query',default=''); q.add_argument('--full-session-titles',action='store_true')
        q.add_argument('--work-dir'); q.add_argument('--keep-failed-work',action='store_true')
        if cmd in ('export','browse'):
            q.add_argument('--browse-sort',default='recent')
            q.add_argument('--browse-status',default='all',choices=['all','available','live','archived','missing'])
            q.add_argument('--browse-page-size',type=_positive)
            q.add_argument('--mode',choices=['thread','turn','response','response-block','message','range','chat','chat-actions','actions',
                                            'last-response','last-assistant','last-substantial','checkpoint-context'],default='thread')
            since=q.add_mutually_exclusive_group()
            for selector in ('since','since-id','since-text','since-time'):since.add_argument('--'+selector)
            since.add_argument('--since-turn',type=_positive);q.add_argument('--since-occurrence',type=_positive)
            q.add_argument('--timestamps',action=argparse.BooleanOptionalAction,default=None)
            q.add_argument('--timestamp-timezone','--timestamp-zone',dest='timestamp_timezone')
            q.add_argument('--no-frontmatter',action='store_true');q.add_argument('--no-map',action='store_true')
            q.add_argument('--plain',action='store_true');q.add_argument('--ui-style',action='store_true')
            q.add_argument('--unknown-sidecar',action='store_true')
            q.add_argument('--turn','--response',type=_positive); q.add_argument('--message',type=_positive)
            q.add_argument('--from-message',type=_positive); q.add_argument('--to-message',type=_positive)
            q.add_argument('--from-step',type=int); q.add_argument('--to-step',type=int); q.add_argument('--step',type=int)
            q.add_argument('--last-n-turns',type=_positive); q.add_argument('--last-n-steps',type=_positive)
            q.add_argument('--min-chars',type=_positive,default=500)
            q.add_argument('--out-dir'); q.add_argument('--name'); q.add_argument('--filename-template',help='Safe template fields: '+', '.join(sorted(FIELDS)))
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
            q.add_argument('--max-archive-bytes','--archive-max-bytes',dest='max_archive_bytes',type=_positive)
            q.add_argument('--separate-archives',action=argparse.BooleanOptionalAction,default=None)
            q.add_argument('--missing-source',choices=['error','skip'],default='error',help='Default stops before rendering; skip explicitly omits catalog-only entries and records them')
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
    q=sub.add_parser('verify-set');q.add_argument('paths',nargs='+');q.add_argument('--max-dictionary-mib',type=_positive,default=512)
    q=sub.add_parser('verify'); q.add_argument('path'); q.add_argument('--metadata',action='store_true'); q.add_argument('--max-dictionary-mib',type=_positive,default=512)
    q=sub.add_parser('review-from');q.add_argument('--progress-style',choices=['auto','rich','plain','json','off'],default='off'); q.add_argument('path');q.add_argument('--member');q.add_argument('--id');q.add_argument('--work-dir');q.add_argument('--archive-max-bytes','--max-archive-bytes',dest='max_archive_bytes',type=_positive); q.add_argument('--out-dir'); q.add_argument('--review-chunk-bytes',type=_positive,default=1000000)
    q.add_argument('--archive',action=argparse.BooleanOptionalAction,default=True)
    q=sub.add_parser('diagnose-access'); q.add_argument('path')
    q=sub.add_parser('repair-access'); q.add_argument('path')
    q=sub.add_parser('tokenizer');q.add_argument('--token-encoding');q.add_argument('--tokenizer',choices=['none','auto','regex','tiktoken']);q.add_argument('--require-tiktoken',action='store_true')
    return p

def normalized_argv(argv):
    if not argv: return ['--help']
    aliases={'--list-sessions':'sessions','--browse':'browse','--show-config':'config','--show-archive-config':'config',
             '--diagnose-archive':'verify','--review-from':'review-from','--diagnose-access':'diagnose-access',
             '--repair-access':'repair-access','--list-turns':'turns','--list-responses':'responses','--list-records':'records','--tokenizer-info':'tokenizer'}
    if argv[0] in aliases: return [aliases[argv[0]],*argv[1:]]
    if argv[0].startswith('--browse-'):return ['browse',*argv]
    if argv[0]=='--print-filename-template': return ['config','--get','template']
    if argv[0]=='--reset-filename-template': return ['config','--set','template='+DEFAULT_TEMPLATE]
    if argv[0]=='--print-out-dir': return ['config','--get','out_dir']
    if argv[0] in ('--set-default-out-dir','--set-filename-template') and len(argv)==2:
        key='out_dir' if argv[0]=='--set-default-out-dir' else 'template'; return ['config','--set',key+'='+argv[1]]
    if argv[0]=='--reset-config': return ['config','--reset']
    if argv[0] in ('--set-archive-default','--set-default'):
        result=['config']
        for value in argv:
            if value in ('--set-archive-default','--set-default'): result.append('--set')
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
    import glob
    from ._vendor.codex_archive.bounded_archive import archive_stem_digest

    def occupied(candidate):
        # A completed bounded set has part names but no plain <stem>.zip.
        # Long multipart names retain this digest when their stem is shortened.
        return ((out/(candidate+'.zip')).exists() or (out/(candidate+'.files')).exists()
                or any(out.glob(glob.escape(candidate)+'.part*-of-*.zip'))
                or any(out.glob('*-'+archive_stem_digest(out/(candidate+'.zip'))+'.part*-of-*.zip')))

    for suffix in range(0,10000):
        candidate=stem+('' if suffix==0 else f'_{suffix+1}')
        if occupied(candidate):
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
    selector_audit=resolve_since(index,args)
    selection=index.selection(args)
    if selector_audit:selection['since']=selector_audit
    interruptions=interruption_summary(index,supplements)
    redactor=Redactor(options['redact'])
    opts={**options,'tool_outputs':args.tool_outputs,'max_tool_chars':args.max_tool_chars,
          'records_jsonl':args.records_jsonl or args.share,'literal':args.literal,
          'frontmatter':not args.no_frontmatter and not args.plain,'export_map':not args.no_map and not args.plain,
          'plain':args.plain,'ui_style':args.ui_style,'unknown_sidecar':args.unknown_sidecar}
    generated=stage/'generated'; generated.mkdir()
    md=generated/'rendered.md'
    with progress.group('render',total=selection['records'],unit='records'):
        rendered=render(index,session,selection,info,db_audit,supplements,md,opts,src=src,progress=progress)
    diagnostic_steps=set(rendered['supplemental_db_rows_rendered'])
    selected_diagnostics=[item for item in supplements if item['step_index'] in diagnostic_steps]
    counts=count_text(md,options['tokenizer'],options['token_encoding'],args.exact_max_bytes,progress=progress)
    privacy='REDACTED' if options['redact'] else 'PRIVATE'
    timestamp=datetime.now().astimezone().strftime('%Y%m%d_%H%M%S_%f_%Z')
    template=args.filename_template or options['template']
    validate_config({'template':template})
    short=session['id'].replace('-','')[-8:]
    include_short=args.stable_filenames or options['include_session_short_id'] or getattr(args,'batch_count',1)>1
    values={'title':safe_slug(redactor.text(args.name or session['title'])),'id':session['id'],'short_id':session['id'][:8],
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
    if stem is None: return {'id':session['id'],'variant':session['variant'],'skipped_existing':True,'reason':'output collision explicitly skipped'}
    deferred=False
    try:
        md_named=generated/(stem+'.md'); md.rename(md_named); md=md_named
        members={stem+'.md':md}
        source_copy={'included':False,'scope':'omitted by explicit policy'}
        if options['copy_source']:
            native,source_copy=index.selected_source(generated/(stem+'.selected.jsonl'),selection,progress=progress)
            if source_copy.get('byte_exact_captured_prefix') and source_copy['sha256']!=info['sha256']:
                raise ExportError('Captured native source checksum changed after capture')
            members[stem+'.jsonl']=native
        if rendered['records_jsonl']:
            members[stem+'.records.jsonl']=rendered['records_jsonl']
        if rendered.get('unknown_sidecar'):
            members[stem+'.unknown.jsonl']=rendered['unknown_sidecar']
        if args.handoff:
            handoff=generated/'HANDOFF_INDEX.md'
            handoff.write_text(redactor.text(handoff_text(session,index,db_audit,interruptions,selection)),encoding='utf-8')
            members[stem+'.HANDOFF_INDEX.md' if getattr(args,'batch_count',1)>1 else 'HANDOFF_INDEX.md']=handoff
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
            members.update({(stem+'.review/'+name.removeprefix('review/')) if getattr(args,'batch_count',1)>1 else name:path for name,path in extra.items()})
        progress.update('prepare-manifest',detail='Hashing generated and captured members',force=True)
        inventory={name:{'bytes':path.stat().st_size,'sha256':hash_file(path)} for name,path in sorted(members.items())}
        for receipt in native_inventory:
            name='native/'+receipt['relative_source']
            if inventory[name]['sha256']!=receipt['sha256'] or inventory[name]['bytes']!=receipt['bytes']:
                raise ExportError('Captured native artifact changed before packaging: '+name)
        manifest={'schema':'antigravity-export/4','exporter_version':__version__,
                  'captured_at_utc':datetime.now(timezone.utc).isoformat(),'session':session,
                  'batch_selection':{'skipped_sessions':getattr(args,'skipped_sessions',[])},
                  'source_capture':info,'source_coverage':{**index.audit,'native_copy_contains_entire_captured_prefix':source_copy.get('byte_exact_captured_prefix',False)},'selection':selection,'turns':index.turns,
                  'database_audit':db_audit,'database_only_diagnostics':selected_diagnostics,
                  'database_diagnostic_scope':rendered['database_diagnostic_scope'],
                  'interruption':interruptions,'rendered_text':counts,
                  'redaction':{'enabled':options['redact'],'best_effort':True,'anonymized':False,
                               'credential_values_replaced':rendered['credential_values_replaced']},
                  'source_copy':source_copy,'timestamp_policy':TimestampFormatter(options['timestamps'],options['timestamp_timezone']).metadata(),
                  'recovered_tool_outputs':rendered['recovered_outputs'],'unavailable_recovery':rendered['unavailable_recovery'],
                  'view_omissions':rendered['view_omissions'],'native_inventory':native_inventory,
                  'native_artifact_scope':'entire selected session store' if (args.include_native or args.include_artifacts) else 'not requested',
                  'review':review,'filename_policy':{'template':template,'stamp':values['stamp'],'session_short':short,'counts':options['filename_counts']},
                  'archive_policy':{k:options[k] for k in ('archive','method','backend','level','dictionary','word_size','threads','cleanup','max_archive_bytes','separate_archives')},
                  'archive_backend_executable':archiver,'members':inventory,
                  'manifest_self_hash':'Provided in verification receipt, not self-referentially embedded',
                  'safety':'No app-owned source was deleted or modified by this exporter. Multi-file atomicity is not claimed.'}
        manifest=redactor.apply(manifest)
        manifest_path=generated/(stem+'.manifest.json'); write_json(manifest_path,manifest); members[manifest_path.name]=manifest_path
        result={'id':session['id'],'variant':session['variant'],'title':redactor.text(session['title']),'exporter_version':__version__,'selected_records':selection['records'],
                'database_coverage_matches':db_audit.get('matched_exactly'),
                'database_step_sets_match':db_audit.get('step_sets_match'),
                'source_step_order':index.audit.get('step_order',{}),
                'db_only_steps':db_audit.get('db_only_steps',[]),'privacy':privacy,'counts':counts,
                'latest_quota_record':interruptions['latest_database_only_quota_step'],
                'manifest_sha256':hash_file(manifest_path),'native_source_included':options['copy_source'],
                'original_files_never_deleted':True,'source_selection':selection,
                'skipped_sessions':getattr(args,'skipped_sessions',[]),
                'non_error_database_rows_absent_from_log':sum(r.get('step_type')!=17 for r in supplements),
                'native_artifact_scope':'entire selected session store' if (args.include_native or args.include_artifacts) else 'not requested'}
        expected={name:(value['sha256'],value['bytes']) for name,value in inventory.items()}
        expected[manifest_path.name]=(hash_file(manifest_path),manifest_path.stat().st_size)
        if getattr(args,'defer_archive',False):
            deferred=True
            result.update(_members=members,_expected=expected,_manifest_name=manifest_path.name,
                          _markdown_name=md.name,_stem=stem,_lock=lock,_out=out,_stage=stage)
            return result
        if options['archive']:
            result.update(package_members(out/(stem+'.zip'),members,options,progress=progress,
                                          archiver=archiver,expected_checksums=expected))
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
        if not deferred:
            with contextlib.suppress(OSError): lock.unlink()

def verify_export(path, metadata=False, max_dictionary_mib=512):
    """Verify one ZIP; do not mistake an individually valid part for a full set."""
    report=diagnose(path,metadata,max_dictionary_mib)
    if report['status']!='ok' or metadata:return report
    actual={m['name']:m for m in report['members']}
    part_names=[name for name in actual if re.fullmatch(r'_*ARCHIVE_PART\.json',name)]
    if part_names:
        if len(part_names)!=1:raise ExportError('Ambiguous multipart reconstruction metadata')
        with zipfile.ZipFile(path) as z:
            entry=z.getinfo(part_names[0])
            if entry.file_size>32*1024*1024:raise ExportError('Multipart metadata exceeds inspection limit')
            part=json.loads(z.read(entry))
        if not isinstance(part,dict) or part.get('schema')!='antigravity-export.archive-part.v1':
            raise ExportError('Unsupported multipart reconstruction metadata')
        report.update(embedded_manifest_hashes_matched=False,manifests_checked=[],
                      complete_set_verified=False,multipart=True,
                      multipart_part={k:part.get(k) for k in ('export_id','part','parts','max_archive_bytes')},
                      multipart_set_verification='This part passed CRC/SHA inspection. Supply every part to verify-set for complete reconstruction.')
        return report
    names=[n for n in actual if '/' not in n and n.endswith('.manifest.json')]
    checked=[];expected_names=set()
    with zipfile.ZipFile(path) as z:
        for name in names:
            entry=z.getinfo(name)
            if entry.file_size>64*1024*1024:raise ExportError('Embedded manifest exceeds inspection limit')
            manifest=json.loads(z.read(entry))
            if not isinstance(manifest,dict) or manifest.get('schema') not in ('antigravity-export/3','antigravity-export/4'):continue
            expected=manifest.get('members',{})
            if not isinstance(expected,dict):raise ExportError('Invalid embedded member inventory')
            for member,value in expected.items():
                if member not in actual:raise ExportError('Manifest/ZIP inventory mismatch: '+member)
                if actual[member]['sha256']!=value['sha256'] or actual[member]['bytes']!=value['bytes']:
                    raise ExportError('Manifest hash/size mismatch for '+member)
            checked.append(name);expected_names.add(name);expected_names.update(expected)
        if checked:
            if 'BATCH_INDEX.json' in actual:expected_names.add('BATCH_INDEX.json')
            if set(actual)!=expected_names:raise ExportError('Unexpected archive member not covered by export manifests')
    report.update(embedded_manifest_hashes_matched=bool(checked),manifests_checked=checked,
                  complete_set_verified=True,multipart=False,multipart_set_verification=None)
    return report



def publish_prepared(items,args,options,work,progress,archiver,skipped):
    """Publish prepared sessions only after selection/rendering all succeeded."""
    prepared=[item for item in items if '_members' in item]
    if not prepared:return items
    collisions=[{'id':item['id'],'variant':item['variant'],'reason':'output collision explicitly skipped'}
                for item in items if item.get('skipped_existing')]
    skipped=list(skipped)+collisions
    if collisions:
        # Rendering finishes before packaging; disclose every omitted requested
        # identity in both the batch index and each prepared session manifest.
        for item in prepared:
            path=item['_members'][item['_manifest_name']]
            manifest=json.loads(path.read_text(encoding='utf-8'))
            manifest['batch_selection'].update(skipped_sessions=skipped,selection_complete=False)
            write_json(path,manifest)
            checksum=hash_file(path)
            item['_expected'][item['_manifest_name']]=(checksum,path.stat().st_size)
            item['manifest_sha256']=checksum
    grouped=options['archive'] and len(prepared)>1 and not options['separate_archives']
    groups=[prepared] if grouped else [[item] for item in prepared]
    batch_locks=[]
    try:
        for group in groups:
            first=group[0];out=first['_out'];members={};expected={}
            if any(item['_out']!=out for item in group):raise ExportError('Grouped batch outputs require one output directory')
            for item in group:
                for name,path in item['_members'].items():
                    if name in members and expected[name]!=item['_expected'][name]:raise ExportError('Conflicting batch member '+name)
                    members[name]=path;expected[name]=item['_expected'][name]
            if len(group)>1 or skipped:
                batch={'schema':'antigravity-batch/1','exporter_version':__version__,
                       'selection_complete':not skipped,'skipped_sessions':skipped,
                       'sessions':[{k:item[k] for k in ('id','variant','title','selected_records','manifest_sha256')}|
                                   {'manifest':item['_manifest_name'],'native_jsonl_included':item['native_source_included']} for item in group],
                       'snapshot_boundary':'Each source has its own verified capture; no atomic snapshot across sessions is claimed.'}
                batch_path=work/('BATCH_INDEX-'+uuid.uuid4().hex+'.json');write_json(batch_path,batch)
                members['BATCH_INDEX.json']=batch_path;expected['BATCH_INDEX.json']=(hash_file(batch_path),batch_path.stat().st_size)
            if len(group)>1:
                stem=_sanitize('antigravity_'+(safe_slug(args.project)+'_batch' if args.project else 'batch')+('' if args.stable_filenames else '_'+export_stamp()))
                stem,lock=reserve(out,stem,args.collision)
                if stem is None:
                    for item in group:item['skipped_existing']=True
                    continue
                batch_locks.append(lock)
            else:stem=first['_stem']
            result=(package_members(out/(stem+'.zip'),members,{**options,'work_dir':str(work)},
                                    progress=progress,archiver=archiver,expected_checksums=expected)
                    if options['archive'] else {})
            for item in group:
                item.update(result);item['batch_sessions']=len(group);item['skipped_sessions']=skipped
                item['cleanup_requested']=options['cleanup']
                item['loose_outputs_retained']=not options['archive'] or not options['cleanup']
                if item['loose_outputs_retained']:
                    loose=out/(item['_stem']+'.files');loose.mkdir(exist_ok=False)
                    loose_members=members if not options['archive'] else item['_members']
                    loose_expected=expected if not options['archive'] else item['_expected']
                    total_bytes=sum(path.stat().st_size for path in loose_members.values());copied=0
                    try:
                        with progress.group('publish-loose-files',total=total_bytes,unit='bytes'):
                            for name,path in loose_members.items():
                                target=loose/name;target.parent.mkdir(parents=True,exist_ok=True)
                                receipt=publish_fresh(path,target,progress=progress)
                                if (receipt['sha256'],receipt['bytes'])!=loose_expected[name]:
                                    raise ExportError('Prepared member changed before loose publication: '+name)
                                copied+=receipt['bytes']
                                progress.update('publish-loose-files',copied,total_bytes,detail=name)
                    except BaseException:
                        # This exclusive directory was created by this export.
                        shutil.rmtree(loose)
                        raise
                    item.update(loose_directory=str(loose),manifest=str(loose/item['_manifest_name']),markdown=str(loose/item['_markdown_name']))
            if args.open_after:_open(Path(result['archive'] if options['archive'] else first['markdown']))
        if args.remember_out_dir:
            c=load_config();c['out_dir']=str(prepared[0]['_out']);save_config(c)
        return items
    finally:
        for item in prepared:
            with contextlib.suppress(OSError):item['_lock'].unlink()
            for key in tuple(item):
                if key.startswith('_'):item.pop(key)
        for lock in batch_locks:
            with contextlib.suppress(OSError):lock.unlink()

def review_existing(args):
    """Convert an explicitly selected transcript, never unrelated native records."""
    source=Path(args.path).expanduser().resolve();config=load_config()
    out=Path(args.out_dir or config['out_dir']).expanduser().resolve()
    if '.gemini' in [p.casefold() for p in out.parts]:raise ExportError('Review output cannot be inside app-owned Antigravity data')
    work_value=getattr(args,'work_dir',None) or config.get('work_dir')
    parent=Path(work_value).expanduser().resolve() if work_value else out
    if '.gemini' in [p.casefold() for p in parent.parts]:raise ExportError('Review work directory cannot be inside app-owned Antigravity data')
    out.mkdir(parents=True,exist_ok=True);parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.antigravity-review-',dir=parent));success=False;lock=None
    progress=Progress(getattr(args,'progress_style',None) or config['progress_style'])
    try:
        captured=stage/'input';capture=capture_file(source,captured,progress=progress)
        member=getattr(args,'member',None);selector=getattr(args,'id',None)
        checked_members=[];selected_manifest=None;selected_member=None
        if source.suffix.lower()=='.zip':
            with zipfile.ZipFile(captured) as z:
                inv=safe_inventory(z)
                if any(re.fullmatch(r'_*ARCHIVE_PART\.json',name) for name in inv):
                    raise ExportError('review-from does not treat a multipart fragment as a complete transcript. Use --review during the original export, or reconstruct and verify the full Markdown before conversion.')
                names=[n for n in inv if n.lower().endswith('.md') and '/' not in n and
                       not n.upper().startswith(('HANDOFF','READ_FIRST')) and not n.upper().endswith('.HANDOFF_INDEX.MD')]
                if selector:
                    value=str(selector).strip();variant=None
                    if ':' in value:variant,value=value.split(':',1)
                    if not value or not re.fullmatch(r'[0-9a-fA-F-]+',value):
                        raise ExportError('Review --id requires a session UUID/prefix, optionally variant:UUID')
                    matches=[]
                    for name in inv:
                        if '/' in name or not name.endswith('.manifest.json'):continue
                        if inv[name].file_size>64*1024*1024:raise ExportError('Embedded selection manifest exceeds inspection limit')
                        from .archive import lzma_dictionary
                        dictionary=lzma_dictionary(z,inv[name])
                        if dictionary and dictionary>512*1024*1024:raise ExportError('Selection manifest exceeds the 512 MiB decoder dictionary policy')
                        metadata=json.loads(z.read(inv[name]));checked_members.append(name)
                        if not isinstance(metadata,dict) or metadata.get('schema') not in ('antigravity-export/3','antigravity-export/4'):continue
                        session=metadata.get('session',{})
                        if (str(session.get('id','')).casefold().startswith(value.casefold()) and
                            (variant is None or str(session.get('variant','')).casefold()==variant.casefold())):
                            md_name=name.removesuffix('.manifest.json')+'.md'
                            if md_name in inv and md_name in metadata.get('members',{}):matches.append((md_name,name,metadata))
                    if len(matches)!=1:raise ExportError(f'Review session selector matched {len(matches)} transcripts; use an unambiguous UUID or --member')
                    names=[matches[0][0]];selected_manifest=matches[0]
                if member:
                    if member not in inv or not member.lower().endswith('.md'):
                        raise ExportError('Review --member must be an exact existing Markdown ZIP member')
                    if selector and member not in names:raise ExportError('Review --member and --id select different transcripts')
                    names=[member]
                if len(names)!=1:raise ExportError('Archive must contain one unambiguous transcript; use --id or --member to select one batch session')
                selected_member=names[0]
                from .archive import lzma_dictionary
                dictionary=lzma_dictionary(z,inv[selected_member])
                if dictionary and dictionary>512*1024*1024:raise ExportError('Selected Markdown exceeds the 512 MiB decoder dictionary policy')
                md=stage/'source.md'
                with z.open(inv[selected_member]) as f,md.open('xb') as output:shutil.copyfileobj(f,output,CHUNK)
                checked_members.append(selected_member)
                if selected_manifest:
                    expected=selected_manifest[2]['members'][selected_member]
                    if (hash_file(md),md.stat().st_size)!=(expected['sha256'],expected['bytes']):
                        raise ExportError('Selected Markdown does not match its embedded manifest')
        elif source.suffix.lower() in ('.md','.txt'):
            if member or selector:raise ExportError('--member and --id apply to ZIP inputs only')
            md=stage/'source.md';shutil.copyfile(captured,md)
        else:raise ExportError('review-from accepts a ZIP, Markdown, or UTF-8 TXT file')
        review_dir=stage/'review'
        with progress.group('review-split',total=md.stat().st_size,unit='bytes'):
            members,index=make_review(md,review_dir,cap=args.review_chunk_bytes,progress=progress)
        provenance={'source_container':source.name,'source_container_sha256':capture['sha256'],
                    'selected_markdown_member':selected_member,'selected_manifest':selected_manifest[1] if selected_manifest else None,
                    'decoded_crc_checked_members':checked_members,'entire_input_zip_verified':False if source.suffix.lower()=='.zip' else None,
                    'native_jsonl_opened':False}
        index['conversion_source']=provenance;write_json(review_dir/'INDEX.json',index)
        stem=filename_stem('{title}',{'title':_sanitize(source.stem+'.review_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))})
        stem,lock=reserve(out,stem,'rename')
        loose=out/(stem+'.files');loose.mkdir()
        with progress.group('publish-review-text',total=len(members),unit='files'):
            for number,(name,path) in enumerate(members.items(),1):
                dest=loose/name;dest.parent.mkdir(parents=True,exist_ok=True);publish_fresh(path,dest,progress=progress)
                progress.update('publish-review-text',number,len(members),unit='files',detail=name)
        result={'review_directory':str(loose),'reconstruction_verified':True,'parts':len(index['parts']),
                'conversion_source':provenance}
        if args.archive:
            options={**config,'method':'Deflate','level':6,'backend':'python','archiver':'',
                     'max_archive_bytes':getattr(args,'max_archive_bytes',None) or config['max_archive_bytes'],
                     'work_dir':str(parent)}
            result.update(package_members(out/(stem+'.zip'),members,options,progress=progress))
        success=True;return result
    finally:
        if lock:
            with contextlib.suppress(OSError):lock.unlink()
        try:
            with progress.group('cleanup',detail='Remove exporter-owned review staging only'):shutil.rmtree(stage)
        except OSError as cleanup_error:
            progress.finish(False)
            if success:raise ExportError('Review output exists, but temporary cleanup failed: '+str(stage)) from cleanup_error
            print('Review temporary cleanup also failed: '+str(stage),file=sys.stderr)
        finally:progress.finish(success)


def run(args):
    if args.command=='config':
        c=dict(DEFAULTS) if args.reset else load_config()
        for entry in args.set:
            if '=' not in entry:raise ExportError('Use --set KEY=VALUE')
            k,v=entry.split('=',1)
            k={'max_bytes':'max_archive_bytes','filename_template':'template'}.get(k,k)
            if k not in DEFAULTS:raise ExportError('Unknown config key '+k)
            if isinstance(DEFAULTS[k],bool):
                if v.lower() not in ('true','false'):raise ExportError('Boolean value must be true/false')
                c[k]=v.lower()=='true'
            elif isinstance(DEFAULTS[k],int):c[k]=int(v)
            else:c[k]=v
        validate_config(c)
        if args.set or args.reset:save_config(c)
        if args.get:
            if args.get not in c:raise ExportError('Unknown config key '+args.get)
            return c[args.get]
        return {'config_path':str(config_path()),**c}
    if args.command=='verify':return verify_export(args.path,args.metadata,args.max_dictionary_mib)
    if args.command=='verify-set':
        from .archive_set import verify_archive_set
        return verify_archive_set(args.paths,max_dictionary_mib=args.max_dictionary_mib)
    if args.command=='review-from':return review_existing(args)
    if args.command in ('diagnose-access','repair-access'):
        from .file_access import diagnose_access, repair_export_access
        return diagnose_access(Path(args.path).expanduser()) if args.command=='diagnose-access' else repair_export_access([Path(args.path).expanduser()])
    if args.command=='tokenizer':
        from importlib.metadata import PackageNotFoundError, version as distribution_version
        c=load_config()
        configured='tiktoken' if args.require_tiktoken else args.tokenizer or c['tokenizer']
        present=importlib.util.find_spec('tiktoken') is not None
        if configured=='tiktoken' and not present:
            raise ExportError('tiktoken is not installed in the selected Python environment')
        version=None
        if present:
            with contextlib.suppress(PackageNotFoundError):version=distribution_version('tiktoken')
        return {'version':__version__,'python':sys.executable,'configured':configured,
                'encoding':args.token_encoding or c['token_encoding'],'tiktoken_installed':present,
                'tiktoken_version':version,'tiktoken_metadata_found':version is not None,
                'availability_probe_only':True,'tiktoken_required':configured=='tiktoken',
                'regex':'approximation only; never provider billing tokens',
                'exact_limit_default_bytes':64*1024*1024,'no_network_or_installation_performed':True}
    if args.command=='doctor':
        c=load_config();external=None;error=None
        try:external=preflight({**c,'archiver':args.archiver_path or c['archiver']})
        except ExportError as e:error=str(e)
        return {'version':__version__,'python':sys.version,'executable':sys.executable,
                'sqlite_version':sqlite3.sqlite_version,'platform':sys.platform,
                'codecs':{k:importlib.util.find_spec(k) is not None for k in ('lzma','zlib','tiktoken','zstandard')},
                'source_root_exists':(Path.home()/'.gemini').is_dir(),'archiver':external,'archive_preflight_error':error,
                'max_archive_bytes':c['max_archive_bytes'],'work_dir':c['work_dir'],
                'no_network_requests_or_package_installation_performed':True}
    c=load_config();options=dict(c)
    for key in c:
        value=getattr(args,key,None)
        if value is not None:options[key]=value
    if getattr(args,'fast',False):options['tokenizer']='none'
    if getattr(args,'require_tiktoken',False):options['tokenizer']='tiktoken'
    if getattr(args,'share',False):options.update(redact=True,copy_source=False)
    if getattr(args,'no_file',False):options.update(archive=False,copy_source=False)
    validate_config(options)
    if options['redact'] and options['copy_source']:
        raise ExportError('--redact cannot be combined with a private native source copy. Use --share or --no-source-copy.')
    if options['redact'] and (getattr(args,'include_native',False) or getattr(args,'include_artifacts',False)):
        raise ExportError('Redacted views cannot bundle raw binary/native artifacts; make a separate PRIVATE archival export')
    if getattr(args,'no_file',False) and not (args.stdout or args.clipboard):raise ExportError('--no-file requires --stdout or --clipboard')
    if getattr(args,'stdout',False) and args.json:raise ExportError('--stdout Markdown and --json results cannot share stdout')
    for low,high in (('from_step','to_step'),('from_message','to_message')):
        a,b=getattr(args,low,None),getattr(args,high,None)
        if a is not None and b is not None and a>b:raise ExportError(f'{low} exceeds {high}')
    if any(getattr(args,key,0) is not None and getattr(args,key,0)<0 for key in ('step','from_step','to_step','max_tool_chars')):
        raise ExportError('Step numbers and maximum tool characters cannot be negative')
    args._display=DisplaySettings(options['sort'],options['sort_order'],options['pager'],options['page_size'])
    args._timestamp_timezone=options['timestamp_timezone']
    progress=Progress('off' if args.no_progress else options['progress_style'])
    work_parent=Path(options['work_dir']).expanduser().resolve() if options['work_dir'] else None
    if work_parent:
        root=Path(args.root or Path.home()/'.gemini').expanduser().resolve()
        if root==work_parent or root in work_parent.parents or '.gemini' in [p.casefold() for p in work_parent.parts]:
            raise ExportError('Work directory cannot be inside app-owned Antigravity data')
        work_parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='agy-export-',dir=work_parent));success=False;src=None;result=[]
    started=time.monotonic();skipped=[]
    try:
        if args.direct_jsonl:
            if args.zip_source or args.root:raise ExportError('--jsonl cannot be combined with --zip or --root')
            if args.ids and len(args.ids)>1:raise ExportError('Direct JSONL accepts at most one identity label')
            original=Path(args.direct_jsonl).expanduser().resolve()
            ident=args.ids[0] if args.ids else str(uuid.uuid5(uuid.NAMESPACE_URL,original.as_uri()))
            if not UUID_RE.fullmatch(ident):raise ExportError('Direct source identity label must be a UUID')
            catalog=[{'id':ident,'title':original.stem,'variant':'direct','project':'','project_id':'','full':True,'db':False,
                      'selector':ident,'identity_is_user_label':True}]
        else:
            src=Source(root=args.root,archive=args.zip_source,stage=work/'source',progress=progress)
            progress.update('discover-sessions',detail='Resolve catalog pointers to storage identities',force=True)
            catalog=sessions(src)
        if args.command in ('sessions','projects'):
            if args.project:catalog=[s for s in catalog if s['project'].casefold()==args.project.casefold() or s['project_id']==args.project]
            if args.variant:catalog=[s for s in catalog if s['variant']==args.variant]
            if args.query:
                needle=args.query.casefold();catalog=[s for s in catalog if needle in ' '.join(str(s.get(k,'')) for k in ('id','title','project','variant')).casefold()]
            if args.command=='projects':
                projects={}
                for session in catalog:
                    key=session.get('project_id') or session.get('project') or '(unassigned)'
                    row=projects.setdefault(key,{'id':key,'project':session.get('project') or '(unassigned)','sessions':0,'exportable':0})
                    row['sessions']+=1;row['exportable']+=has_full_transcript(session)
                catalog=list(projects.values())
            catalog=sort_rows(catalog,kind=args.command,sort=options['sort'],order=options['sort_order'])
            success=True;return catalog
        if args.command=='browse':
            progress._settle_implicit()
            from .browser import browse_sessions
            args.ids=browse_sessions(lambda:catalog,query=args.query,status=args.browse_status,sort=args.browse_sort,sort_order=options['sort_order'],page_size=args.browse_page_size or options['page_size'] or None)
            if not args.ids:success=True;return []
        ids=list(args.ids or [])
        if args.sessions_file:
            ids.extend(x.strip() for x in Path(args.sessions_file).read_text(encoding='utf-8-sig').splitlines() if x.strip() and not x.lstrip().startswith('#'))
        audit_all=args.command=='audit' and not(ids or args.project or args.latest_session)
        with progress.group('resolve-selection',detail='Validate every selected storage identity'):
            chosen=catalog if args.direct_jsonl else resolve(catalog,ids,args.project,args.variant,args.latest_session,args.all or audit_all)
        if not chosen:raise ExportError('No sessions matched')
        for flag in ('stdout','clipboard','save_as'):
            if getattr(args,flag,False) and len(chosen)>1:raise ExportError('--'+flag.replace('_','-')+' is single-session only')
        is_export=args.command in ('export','browse')
        # Diagnose the whole selection before costly captures or partial output.
        if is_export and src:
            absent=[s for s in chosen if not has_transcript(s,allow_reduced=args.allow_reduced)]
            if absent and args.missing_source=='error':
                labels=', '.join(s.get('selector',s['id']) for s in absent)
                raise ExportError('Selected storage identities have no complete transcript/chunks: '+labels+'. Inspect audit; --missing-source skip is explicit omission, --allow-reduced is explicit reduced-log acceptance.')
            skipped=[{'id':s['id'],'variant':s['variant'],'reason':s.get('source_availability','no full transcript')} for s in absent]
            chosen=[s for s in chosen if s not in absent]
            if not chosen:raise ExportError('No exportable sessions remain; no archive was created')
            if skipped:print(f'WARNING: {len(skipped)} source-unavailable session(s) explicitly omitted; see BATCH_INDEX.',file=sys.stderr)
        args.batch_count=len(chosen);args.defer_archive=is_export;args.skipped_sessions=skipped
        archiver=preflight(options) if is_export else None
        for number,session in enumerate(chosen,1):
            stage=work/('job-'+str(number));stage.mkdir()
            progress.update('resolve-session',detail=session.get('selector',session['id']),force=True)
            if args.command=='audit' and src and not has_transcript(session,allow_reduced=args.allow_reduced):
                result.append({'session':session,'source':None,'coverage':{'status':'source_unavailable','fidelity_loss_accepted':False}});continue
            index=None
            source_label=str(original) if args.direct_jsonl else f"{session['variant']}/brain/{session['id']}/.system_generated/logs/"
            try:
                if args.direct_jsonl:
                    from .inputs import capture_direct
                    log,info=capture_direct(original,stage,progress=progress)
                else:log,info=capture_transcript(src,session,allow_reduced=args.allow_reduced,verify_chunks=args.verify_chunks)
                source_label=info.get('relative_source',source_label)
                index=EventIndex(log,stage/'index.db',max_record_bytes=args.max_record_bytes,
                                 strict_events=args.strict_events,skip_invalid=args.skip_invalid,
                                 strict_step_order=args.strict_step_order,progress=progress)
                order=index.audit['step_order']
                if is_export and not order['monotonic_unique']:
                    print(f"WARNING: {session.get('selector',session['id'])}: preserving all {index.count} source records in captured order; "
                          f"{order['duplicate_occurrences']} repeated native-step occurrence(s), {order['backward_transitions']} backward transition(s). "
                          'See source_coverage.step_order in the manifest; no records were deduplicated or reordered.',file=sys.stderr)
                db_audit,supplements=(audit_database(src,session,index) if args.verify_db and src else ({'available':False,'coverage':'not_checked'},[]))
                interrupts=interruption_summary(index,supplements)
                unavailable_history=[r['step_index'] for r in supplements if r.get('step_type')!=17]
                if is_export and unavailable_history:
                    print(f"WARNING: {session.get('selector',session['id'])}: {len(unavailable_history)} non-error SQLite row(s) absent from readable JSONL. Opaque payloads are not reconstructed; --include-native preserves the SQLite snapshot.",file=sys.stderr)
                if args.command=='audit':result.append({'session':session,'source':info,'coverage':index.audit,'turns':index.turns,'database':db_audit,'database_only_records':supplements,'interruption':interrupts})
                elif args.command=='turns':
                    result.append({'session':session,'turns':sort_rows(turn_listing(index),kind='turns',sort=options['sort'],order=options['sort_order']),
                                   'database':db_audit,'interruption':interrupts,'source_coverage':index.audit})
                elif args.command in ('records','responses'):
                    result.append({'session':session,'records':sort_rows(list(index.listing(args.command=='responses')),kind=args.command,sort=options['sort'],order=options['sort_order'])})
                else:result.append(export_one(src,session,index,info,db_audit,supplements,copy.copy(args),options,stage,progress,archiver))
            except ExportError as exc:
                hint='' if args.keep_failed_work else ' Use --keep-failed-work to retain exporter staging for diagnosis.'
                raise ExportError(f"Session {session.get('selector',session['id'])}; source {source_label}: {exc}"+hint) from exc
            finally:
                if index is not None:index.close()
        if is_export:result=publish_prepared(result,args,options,work,progress,archiver,skipped)
        success=True
        if len(result)==1:
            result[0]['elapsed_seconds']=round(time.monotonic()-started,3);return result[0]
        return result
    finally:
        if src:src.close()
        progress._settle_implicit(success=success)
        # Reserved names belong only to this job; clear them on every exit path.
        for item in result:
            if item.get('_lock'):
                with contextlib.suppress(OSError):item['_lock'].unlink()
        try:
            if not success and args.keep_failed_work:
                print('Export stopped. Recoverable staged copies (PRIVATE): '+str(work),file=sys.stderr)
            else:
                with progress.group('cleanup',detail='Remove exporter-owned temporary staging only'):
                    shutil.rmtree(work)
        except OSError as cleanup_error:
            progress.finish(success=False)
            if success:raise ExportError('Export artifacts were published, but temporary cleanup failed: '+str(work)) from cleanup_error
            print('Temporary cleanup also failed: '+str(work),file=sys.stderr)
        finally:progress.finish(success=success)


def main(argv=None):
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='strict')
    values=normalized_argv(list(sys.argv[1:] if argv is None else argv))
    if values==['--help']:
        parser().print_help()
        try:defaults=load_config();print('\nEffective saved preferences:\n'+json.dumps(defaults,ensure_ascii=False,indent=2))
        except (ExportError,OSError,ValueError) as e:print('\nConfiguration warning: '+str(e)+'\nUse config --reset to recover.',file=sys.stderr)
        return 0
    args=parser().parse_args(values)
    try:
        result=run(args)
        if getattr(args,'stdout',False):return 0
        listing=args.command in ('sessions','projects','turns','records','responses')
        if listing and not args.json:
            lines=[]
            if args.command=='sessions':
                lines=session_listing_lines(result,full_titles=args.full_session_titles,
                                            timezone_name=args._timestamp_timezone,settings=args._display)
            elif args.command=='projects':
                for s in result:lines.append(f"{s['project']}  {s['sessions']} sessions  {s['exportable']} full  id={s['id']}")
            else:
                for block in result if isinstance(result,list) else [result]:
                    lines.append(block['session'].get('selector',block['session']['id'])+'  '+preview(block['session']['title'],140))
                    if args.command=='turns':
                        for t in block['turns']:lines.append(f"Turn {t['turn']:4}  messages {t['first_ordinal']}–{t['last_ordinal']}  native steps {t.get('min_step',t['first_step'])}–{t.get('max_step',t['last_step'])}  {t.get('start_utc') or 'time unknown'}  {t['preview']}")
                    else:
                        for r in block['records']:lines.append(f"Message {r['message']:5}  step {r['step']:6}  turn {r['turn']:4}  {r['type']}  {r['status']}  {r['time_utc'] or 'time unknown'}")
            print_listing(lines,settings=args._display,title='Antigravity '+args.command)
        elif args.command in ('export','browse') and not getattr(args,'json',False):
            printed=set()
            for item in result if isinstance(result,list) else [result]:
                paths=item.get('archives') or [item.get('archive') or item.get('markdown') or json.dumps(item)]
                for path in paths:
                    if path not in printed:print(path);printed.add(path)
        elif isinstance(result,str):print(result)
        else:print(json.dumps(result,ensure_ascii=False,indent=2))
        if args.command=='repair-access' and any(not entry.get('repaired',False) for entry in result):return 3
        if isinstance(result,dict) and result.get('status') not in (None,'ok'):return 3
        return 0
    except KeyboardInterrupt:
        print('Cancelled. Original Antigravity data was not changed.',file=sys.stderr);return 130
    except (ExportError,OSError,ValueError,zipfile.BadZipFile,RuntimeError,ImportError,sqlite3.Error) as e:
        print(f'ERROR: {type(e).__name__}: {e}',file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
