"""ZIP policy, real 7-Zip progress, independent decompression/hash checks."""
from __future__ import annotations
from collections import deque
import importlib.util
import codecs
import json
import os
from pathlib import Path
import queue
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import zipfile
import hashlib
from .progress import SevenZipFrames
from .storage import CHUNK, ExportError, hash_file, safe_inventory, safe_name

def find_archiver(explicit=None):
    choices=[]
    if explicit:
        p=Path(explicit).expanduser()
        if not p.is_file(): raise ExportError(f'Configured archiver does not exist: {p}')
        choices=[str(p.resolve())]
    else:
        for name in ('7zz','7z','7za','NanaZipC'):
            hit=shutil.which(name)
            if hit and hit not in choices: choices.append(hit)
        if os.name=='nt':
            for parent in (os.environ.get('ProgramFiles',''),os.environ.get('ProgramFiles(x86)','')):
                for sub in ('7-Zip-Zstandard/7z.exe','7-Zip/7z.exe','NanaZip/NanaZipC.exe'):
                    p=Path(parent)/sub
                    if p.is_file() and str(p) not in choices: choices.append(str(p))
    for exe in choices:
        try:
            p=subprocess.run([exe,'i'],capture_output=True,timeout=10,stdin=subprocess.DEVNULL)
            text=(p.stdout+p.stderr).decode('utf-8','replace')
            if p.returncode==0 and re.search(r'(?i)(7-zip|7zip|nanazip)',text) and 'LZMA' in text:
                return str(Path(exe).resolve())
        except (OSError,subprocess.TimeoutExpired): pass
    raise ExportError('No compatible external 7-Zip CLI found. Configure --archiver-path, or explicitly use --archive-method Deflate --archive-backend python.')

def preflight(options):
    if not options.get('archive'): return None
    method=options.get('method','LZMA')
    backend=options.get('backend','auto')
    if method=='LZMA' and backend=='python':
        raise ExportError('Python ZIP_LZMA cannot honor level/dictionary/word-size settings. Choose external auto/7zip, or explicit Deflate.')
    if method=='LZMA' or backend=='7zip' or (backend=='auto' and options.get('archiver')):
        return find_archiver(options.get('archiver'))
    return None

def _run_external(argv, cwd, progress, timeout=3600):
    """Continuously drain a child, bound retained diagnostic text, reap on cancel."""
    proc=subprocess.Popen(argv,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                          stdin=subprocess.DEVNULL,shell=False)
    q=queue.Queue(maxsize=64); tail=deque(maxlen=16); started=time.monotonic()
    stop=threading.Event()
    def reader():
        try:
            while True:
                b=os.read(proc.stdout.fileno(),4096)
                if not b: break
                while not stop.is_set():
                    try: q.put(b,timeout=0.1); break
                    except queue.Full: pass
                if stop.is_set(): break
        finally:
            while not stop.is_set():
                try: q.put(None,timeout=0.1); break
                except queue.Full: pass
    thread=threading.Thread(target=reader,daemon=True); thread.start()
    percent=None; frames=SevenZipFrames(progress); decoder=codecs.getincrementaldecoder('utf-8')('replace')
    try:
        while True:
            if time.monotonic()-started>timeout: raise ExportError('Archiver exceeded configured timeout; generated inputs retained')
            try: block=q.get(timeout=0.25)
            except queue.Empty:
                if progress: progress.update('compress',percent,100 if percent is not None else None,unit='percent')
                continue
            if block is None: break
            text=decoder.decode(block); tail.append(text[-2048:]); frames.feed(text)
            percent=frames.last
        frames.feed(decoder.decode(b'',final=True),final=True)
        code=proc.wait(timeout=10)
        if code!=0: raise ExportError(f'Archiver exit {code}: '+''.join(tail)[-6000:])
        if progress: progress.update('compress-finished',detail='archiver exited successfully; verification follows',force=True)
    except BaseException:
        if proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=5)
        raise
    finally:
        stop.set(); thread.join(timeout=2)
        if proc.stdout: proc.stdout.close()

def build_zip(destination:Path, members:dict[str,Path], options, *, progress=None, archiver=None):
    """Build from a private prepared tree. Caller publishes only verified output."""
    for name in members: safe_name(name)
    if destination.exists(): raise ExportError('Staging archive unexpectedly already exists')
    method=options.get('method','LZMA'); level=options.get('level',9)
    if archiver:
        work=destination.parent/'compression-inputs'; work.mkdir()
        staged_bytes=0; staging_total=sum(p.stat().st_size for p in members.values())
        for name,path in members.items():
            target=work/name; target.parent.mkdir(parents=True,exist_ok=True)
            # All inputs are exporter-owned snapshots. Copy, never link app data.
            with path.open('rb') as inp, target.open('xb') as out:
                for block in iter(lambda:inp.read(CHUNK),b''):
                    out.write(block); staged_bytes+=len(block)
                    if progress: progress.update('stage-archive-inputs',staged_bytes,staging_total,detail=name)
        args=[archiver,'a','-tzip',f'-mx={level}',f'-mm={method}',f"-mmt={options.get('threads',19)}",
              '-y','-bb1','-bsp1','-bso1','-bse1','-sccUTF-8']
        if method=='LZMA': args += [f"-md={options.get('dictionary','256m')}",f"-mfb={options.get('word_size',64)}"]
        # A full brain archive can exceed Windows' command-line length limit.
        # Keep the member list in a UTF-8 file outside the archived input tree.
        listing=destination.parent/'compression-members.txt'
        with listing.open('x',encoding='utf-8',newline='\n') as f:
            for name in sorted(members): f.write(name+'\n')
        args += ['-scsUTF-8',str(destination.resolve()),'@'+str(listing.resolve())]
        _run_external(args,work,progress,options.get('timeout',3600))
    else:
        if method!='Deflate': raise ExportError('LZMA policy requires a compatible external archiver')
        total=sum(p.stat().st_size for p in members.values()); done=0
        with zipfile.ZipFile(destination,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=level,allowZip64=True) as z:
            for name,path in sorted(members.items()):
                with path.open('rb') as f,z.open(name,'w',force_zip64=True) as out:
                    for block in iter(lambda:f.read(CHUNK),b''):
                        out.write(block); done+=len(block)
                        if progress: progress.update('compress',done,total,detail=name)
    expected={name:{'bytes':path.stat().st_size,'sha256':hash_file(path)} for name,path in members.items()}
    allowed={0,14} if method=='LZMA' else {0,8}
    return verify_zip(destination,expected=expected,allowed_methods=allowed,progress=progress)

def lzma_dictionary(z, info):
    if info.compress_type!=14: return None
    z.fp.seek(info.header_offset)
    header=z.fp.read(30)
    if len(header)!=30 or header[:4]!=b'PK\x03\x04': raise ExportError('Malformed local ZIP header')
    name_len,extra_len=struct.unpack_from('<HH',header,26)
    z.fp.seek(name_len+extra_len,1); props=z.fp.read(9)
    if len(props)<9 or struct.unpack_from('<H',props,2)[0]!=5:
        raise ExportError('Unsupported/malformed ZIP LZMA properties envelope')
    dictionary=struct.unpack_from('<I',props,5)[0]
    return dictionary

def verify_zip(path:Path, *, expected=None, allowed_methods=None, metadata_only=False,
               max_dictionary_mib=512, progress=None):
    path=Path(path)
    initial=path.stat(); before=hash_file(path); entries=[]; total=0; done=0
    with zipfile.ZipFile(path) as z:
        inventory=safe_inventory(z)
        if expected is not None and set(inventory)!=set(expected): raise ExportError('ZIP inventory differs from expected members')
        total=sum(i.file_size for i in inventory.values())
        for name,info in sorted(inventory.items()):
            if allowed_methods and info.compress_type not in allowed_methods:
                raise ExportError(f'Unexpected ZIP codec {info.compress_type} for {name}')
            dictionary=lzma_dictionary(z,info)
            row={'name':name,'bytes':info.file_size,'compressed_bytes':info.compress_size,
                 'method_id':info.compress_type,'crc32_declared':f'{info.CRC:08x}',
                 'lzma_dictionary_bytes':dictionary}
            if not metadata_only:
                if dictionary and max_dictionary_mib and dictionary>max_dictionary_mib*1024*1024:
                    raise ExportError(f'LZMA dictionary policy limit exceeded ({dictionary} bytes); not evidence of corruption')
                h=hashlib.sha256(); n=0
                with z.open(info) as f:
                    for block in iter(lambda:f.read(CHUNK),b''):
                        h.update(block); n+=len(block); done+=len(block)
                        if progress: progress.update('verify-archive-members',done,total,detail=name)
                if n!=info.file_size: raise ExportError(f'ZIP member size mismatch: {name}')
                row.update(sha256=h.hexdigest(),crc_checked=True)
                if expected and (expected[name]['sha256']!=h.hexdigest() or expected[name]['bytes']!=n):
                    raise ExportError(f'ZIP member SHA-256 mismatch: {name}')
            entries.append(row)
    ending=path.stat()
    if (initial.st_size,initial.st_mtime_ns,initial.st_ino)!=(ending.st_size,ending.st_mtime_ns,ending.st_ino):
        raise ExportError('ZIP changed during verification')
    return {'path':str(path),'bytes':initial.st_size,'sha256':before,'members':entries,
            'all_members_decoded_and_crc_checked':not metadata_only,
            'expected_hashes_verified':bool(expected) and not metadata_only,
            'decoder_dictionary_limit_mib':max_dictionary_mib,
            'read_buffer_bytes':CHUNK,'buffer_is_not_total_decoder_memory_limit':True,
            'runtime':{'python':sys.version.split()[0],'platform':sys.platform,
                       'lzma_available':importlib.util.find_spec('lzma') is not None}}

def diagnose(path, metadata_only=False, max_dictionary_mib=512):
    try: return {'status':'ok',**verify_zip(Path(path),metadata_only=metadata_only,max_dictionary_mib=max_dictionary_mib)}
    except FileNotFoundError as e: return {'status':'path_missing','error':str(e)}
    except PermissionError as e: return {'status':'access_denied','error':str(e)}
    except (ModuleNotFoundError,NotImplementedError) as e: return {'status':'codec_unavailable','error':str(e)}
    except MemoryError as e: return {'status':'decoder_memory_failure','error':str(e)}
    except zipfile.BadZipFile as e: return {'status':'invalid_zip_or_crc','error':str(e)}
    except ExportError as e:
        text=str(e)
        status=('dictionary_policy_limit' if text.startswith('LZMA dictionary policy limit') else
                'source_changed' if 'changed during verification' in text else 'policy_or_integrity_failure')
        return {'status':status,'error':text}
    except (OSError,RuntimeError) as e:
        text=str(e)
        codec_missing='requires the' in text.lower() and 'module' in text.lower()
        return {'status':'codec_unavailable' if codec_missing else 'read_or_decode_failure','error':text}
    except Exception as e:
        # Optional lzma codec's error type cannot be imported unconditionally.
        if type(e).__name__=='LZMAError' and type(e).__module__ in ('_lzma','lzma'):
            memory='memory' in str(e).lower()
            return {'status':'decoder_memory_failure' if memory else 'invalid_lzma_payload','error':str(e)}
        raise
