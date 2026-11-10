#!/usr/bin/env python3
"""Allowlisted public release builder. Never recursively archive a user's directory."""
from __future__ import annotations
import argparse,hashlib,json,re,subprocess,sys,tarfile,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MANIFEST=ROOT/'PUBLIC_FILES.json'
ALLOWED_ROOTS={'antigravity_exporter','tools','tests','docs','.github'}
ALLOWED_EXTS={'.py','.ps1','.psm1','.md','.txt','.toml','.yaml','.yml','.in','.json'}
ROOT_FILES={'.gitignore','.gitattributes','LICENSE','THIRD_PARTY_NOTICES.md','README.md','SECURITY.md',
    'CONTRIBUTING.md','CHANGELOG.md','MANIFEST.in','pyproject.toml','agy_export.py',
    'AntigravityExporter.psm1','Test-AntigravityExporter.ps1','Install-AntigravityExporter.ps1',
    'Publish-AntigravityRepository.ps1','New-AntigravityRelease.ps1','Export-Conversation.ps1'}
SYNTHETIC_UUIDS={'11111111-2222-4333-8444-555555555555','aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'}
IGNORE_DIRS={'.git','__pycache__','.pytest_cache','.venv','dist','build'}

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def validate(path):
    relative=path.relative_to(ROOT).as_posix()
    if path.is_symlink():raise ValueError('Symlink not public: '+relative)
    if relative not in ROOT_FILES and relative != '.github/CODEOWNERS' and (relative.split('/')[0] not in ALLOWED_ROOTS or path.suffix not in ALLOWED_EXTS):
        raise ValueError('Unexpected public path: '+relative)
    data=path.read_bytes()
    if len(data)>1024*1024:raise ValueError('Oversized file requires explicit public review: '+relative)
    text=data.decode('utf-8')
    if re.search(r'data:'+r'image/[^;\s]+;base64,[A-Za-z0-9+/]{128,}',text):raise ValueError('Embedded image data in '+relative)
    if re.search(r'(?i)(?:C:[\\/]Users[\\/])(?!example[\\/])[^\\/\s]+',text):raise ValueError('Personal user path in '+relative)
    for value in re.findall(r'\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b',text):
        if value not in SYNTHETIC_UUIDS:raise ValueError('Non-synthetic UUID requires review: '+relative)
    secret=re.compile(r'(?:g'+'h[pousr]_|github'+'_pat_)[A-Za-z0-9_]{30,}|s'+'k-[A-Za-z0-9_-]{30,}')
    if secret.search(text):raise ValueError('Potential credential in '+relative)
    return relative,{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}

def candidates():
    paths=[]
    for p in ROOT.rglob('*'):
        rel=p.relative_to(ROOT)
        if any(x in IGNORE_DIRS or x.endswith('.egg-info') for x in rel.parts):continue
        if p.is_file() and p!=MANIFEST:paths.append(p)
    return sorted(paths)

def check(refresh=False,tracked=False):
    actual=dict(validate(p) for p in candidates())
    if refresh:
        MANIFEST.write_text(json.dumps({'schema':'public-source-allowlist/1','files':actual},indent=2,sort_keys=True)+'\n',encoding='utf-8',newline='\n')
    expected=json.loads(MANIFEST.read_text(encoding='utf-8'))['files']
    if actual!=expected:raise ValueError('Public source differs from PUBLIC_FILES.json; review then explicitly --refresh')
    if tracked:
        output=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
        if set(filter(None,output))!=set(expected)|{'PUBLIC_FILES.json'}:raise ValueError('Tracked paths do not match public allowlist')
    return expected

def audit_distribution(path):
    """Inspect distribution members without extracting or executing them."""
    if path.suffix == '.whl':
        with zipfile.ZipFile(path) as archive:
            if archive.testzip(): raise ValueError('Wheel CRC failure')
            entries=[(i.filename, bool((i.external_attr >> 16) & 0o170000 == 0o120000)) for i in archive.infolist()]
    elif path.name.endswith('.tar.gz'):
        with tarfile.open(path, 'r:gz') as archive:
            entries=[(i.name, i.issym() or i.islnk() or i.isdev()) for i in archive.getmembers()]
    else: return
    seen=set()
    for name,unsafe_type in entries:
        parts=name.replace('\\','/').split('/')
        if (unsafe_type or name.startswith('/') or '..' in parts or ':' in name or
            name.casefold() in seen or '__pycache__' in parts or
            name.endswith(('.pyc','.pyo','.db','.jsonl','.log','.zip'))):
            raise ValueError('Unsafe/unexpected distribution member: '+name)
        seen.add(name.casefold())

def build_dist():
    files=check()
    sys.path.insert(0,str(ROOT));from antigravity_exporter import __version__
    out=ROOT/'dist';out.mkdir(exist_ok=True)
    name='antigravity-exporter-'+__version__
    zip_path=out/(name+'.zip')
    with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for path in sorted([*files,'PUBLIC_FILES.json']):
            info=zipfile.ZipInfo(path,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=(0o100644<<16);z.writestr(info,(ROOT/path).read_bytes(),compresslevel=9)
    with zipfile.ZipFile(zip_path) as z:
        if z.testzip():raise ValueError('Distribution CRC verification failed')
        for path,meta in files.items():
            if hashlib.sha256(z.read(path)).hexdigest()!=meta['sha256']:raise ValueError('Distribution member mismatch')
    # All artifacts listed here are built by this checkout, not arbitrary nearby ZIPs.
    allowed={zip_path.name, 'antigravity_thread_exporter-'+__version__+'-py3-none-any.whl',
             'antigravity_thread_exporter-'+__version__+'.tar.gz', 'SHA256SUMS.txt'}
    unexpected=[p.name for p in out.iterdir() if p.name not in allowed]
    if unexpected: raise ValueError('Refusing unrelated/stale distribution files: '+', '.join(sorted(unexpected)))
    paths=[p for p in out.iterdir() if p.name in allowed and p.name!='SHA256SUMS.txt']
    for artifact in paths: audit_distribution(artifact)
    (out/'SHA256SUMS.txt').write_text(''.join(digest(p)+'  '+p.name+'\n' for p in sorted(paths)),encoding='utf-8')
    return zip_path

def main():
    p=argparse.ArgumentParser();p.add_argument('--refresh',action='store_true');p.add_argument('--tracked',action='store_true');p.add_argument('--build',action='store_true')
    args=p.parse_args()
    try:
        files=check(args.refresh,args.tracked)
        if args.build:print(build_dist())
        else:print(f'PASS: {len(files)} public files, hashes and restricted-content checks verified. This is not a guarantee of anonymization.')
    except (ValueError,OSError,subprocess.SubprocessError) as e:raise SystemExit(str(e))
if __name__=='__main__':main()
