"""Indexed, bounded-memory event handling; no invented completion or model context."""
from __future__ import annotations
from collections import Counter
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from .storage import ExportError, Source, VARIANTS, UUID_RE, CHUNK, hash_file

KNOWN_TYPES = {'USER_INPUT','PLANNER_RESPONSE','GENERIC','CHECKPOINT','SYSTEM_MESSAGE',
               'ERROR_MESSAGE','FINISH','KNOWLEDGE','CONVERSATION_SUMMARY'}
TRUNCATED = re.compile(r'(?i)(?:output (?:was|is) truncated|content truncated|<truncated \d+|partial read \(lines|\btruncated_fields\b)')

def annotation_title(data):
    if data is None: return None
    text=data.decode('utf-8', errors='strict')
    m=re.search(r'\btitle\s*:\s*("(?:[^"\\]|\\.)*")',text)
    if not m: return None
    try: return json.loads(m.group(1))
    except ValueError: return None

def sessions(src: Source) -> list[dict]:
    projects={}
    for name in src.files('config/projects/'):
        if not name.endswith('.json'): continue
        doc=json.loads(src.read_small(name))
        if isinstance(doc,dict): projects[doc.get('id',Path(name).stem)]=doc.get('name','')
    all_sessions=[]
    for variant in src.variants():
        found={}
        dbname=f'{variant}/conversation_summaries.db'
        if src.exists(dbname):
            snap,_=src.snapshot(dbname,database=True)
            con=sqlite3.connect(snap.as_uri()+'?mode=ro',uri=True)
            con.row_factory=sqlite3.Row
            try:
                tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'conversation_summaries' in tables:
                    columns={r[1] for r in con.execute('PRAGMA table_info(conversation_summaries)')}
                    want=[x for x in ['conversation_id','title','project_id','step_count','last_modified_time',
                                     'workspace_uris','status','parent_conversation_id','not_fully_idle'] if x in columns]
                    if 'conversation_id' not in want: raise ExportError('Summary DB has no conversation_id column')
                    for row in con.execute('SELECT '+','.join(want)+' FROM conversation_summaries'):
                        r=dict(row); ident=r.pop('conversation_id')
                        if UUID_RE.fullmatch(ident or ''): found[ident.lower()]=r
            finally: con.close()
        if src.z:
            for name in src.entries:
                m=re.match(re.escape(variant)+r'/(?:conversations/([a-fA-F0-9-]{36})\.(?:db|pb)$|brain/([a-fA-F0-9-]{36})/)',name)
                if m:
                    ident=m.group(1) or m.group(2)
                    if UUID_RE.fullmatch(ident): found.setdefault(ident.lower(),{})
        else:
            for folder in ('brain','conversations'):
                base=src.path(f'{variant}/{folder}')
                if base.is_dir():
                    for entry in base.iterdir():
                        ident=entry.name if entry.is_dir() else entry.stem
                        if UUID_RE.fullmatch(ident): found.setdefault(ident.lower(),{})
        for ident,row in found.items():
            prefix=f'{variant}/brain/{ident}/.system_generated/logs/'
            title=annotation_title(src.read_small(f'{variant}/annotations/{ident}.pbtxt')) or row.get('title') or ident
            project_id=row.get('project_id','')
            project_name=projects.get(project_id,'')
            full=src.exists(prefix+'transcript_full.jsonl')
            reduced=src.exists(prefix+'transcript.jsonl')
            chunked=(any(n.startswith(prefix+'chunks/transcript_full/') for n in src.entries) if src.z else
                     src.path(prefix+'chunks/transcript_full').is_dir())
            all_sessions.append({'id':ident,'title':title,'variant':variant,'project':project_name,
                                 'project_id':project_id,'project_assignment':'summary.project_id' if project_id else 'unavailable',
                                 'summary_step_count':row.get('step_count'),'modified':str(row.get('last_modified_time','')),
                                 'workspace_uris':row.get('workspace_uris',''), 'catalog_status':row.get('status'),
                                 'parent_conversation_id':row.get('parent_conversation_id',''),
                                 'full':full,'reduced':reduced,'chunked_full':chunked,
                                 'db':src.exists(f'{variant}/conversations/{ident}.db')})
    return sorted(all_sessions,key=lambda s:(s['modified'],s['id']),reverse=True)

def resolve(all_sessions, ids=None, project=None, variant=None, latest=False, all_=False):
    pool=[s for s in all_sessions if (not variant or s['variant']==variant) and
          (not project or s['project'].casefold()==project.casefold() or s['project_id']==project)]
    if ids:
        selected=[]
        for term in ids:
            term=term.strip().lower()
            if len(term)<8 or not re.fullmatch('[a-f0-9-]+',term):
                raise ExportError('Use an exact UUID or unique hexadecimal UUID prefix of at least 8 characters')
            matches=[s for s in pool if s['id'].startswith(term)]
            if len(matches)!=1: raise ExportError(f'Session selector {term!r} matched {len(matches)} sessions; specify --variant or full ID')
            if matches[0] not in selected: selected.append(matches[0])
        return selected
    if latest and pool: return [pool[0]]
    if all_ or project: return pool
    raise ExportError('Specify --id UUID, --project NAME, --latest-session, or --all')

def capture_transcript(src: Source, session, *, allow_reduced=False, verify_chunks=False):
    base=f"{session['variant']}/brain/{session['id']}/.system_generated/logs/"
    name=base+'transcript_full.jsonl'
    chunks=None
    if src.exists(name):
        path,info=src.snapshot(name,growth=True)
        info={**info,'fidelity':'full-recorded-log'}
    else:
        chunks=[n for n in src.files(base+'chunks/transcript_full/') if n.endswith('.jsonl')]
        if chunks:
            numeric=[int(Path(n).stem) for n in chunks if Path(n).stem.isdigit()]
            if len(numeric)!=len(chunks) or numeric!=list(range(len(chunks))):
                raise ExportError('Full transcript chunk numbering has gaps or unexpected names')
            path=src.stage/('reassembled-'+session['id']+'.jsonl')
            constituent=[]
            with path.open('xb') as out:
                for part in chunks:
                    piece,pinfo=src.snapshot(part)
                    constituent.append(pinfo)
                    with piece.open('rb') as incoming:
                        for b in iter(lambda:incoming.read(CHUNK),b''): out.write(b)
            info={'relative_source':base+'chunks/transcript_full/*','sha256':hash_file(path),
                  'bytes':path.stat().st_size,'capture_method':'ordered-byte-concatenation',
                  'fidelity':'full-chunks-reassembled','constituents':constituent,
                  'atomic_multifile_snapshot':False}
        elif allow_reduced and src.exists(base+'transcript.jsonl'):
            name=base+'transcript.jsonl'; path,info=src.snapshot(name,growth=True)
            info={**info,'fidelity':'REDUCED_LOG_EXPLICIT_OPT_IN'}
        else:
            raise ExportError('No full transcript/chunks available. --allow-reduced is explicit loss acceptance; opaque protobuf is not guessed.')
    if verify_chunks and info['fidelity']=='full-recorded-log':
        chunks=[n for n in src.files(base+'chunks/transcript_full/') if n.endswith('.jsonl')]
        nums=[int(Path(n).stem) for n in chunks if Path(n).stem.isdigit()]
        h=hashlib.sha256(); size=0
        with (src.progress.group('verify-transcript-chunks',total=len(chunks),unit='files') if src.progress else nullcontext()):
            for number,n in enumerate(chunks,1):
                p,_=src.snapshot(n)
                with p.open('rb') as f:
                    for b in iter(lambda:f.read(CHUNK),b''): h.update(b); size+=len(b)
                if src.progress: src.progress.update('verify-transcript-chunks',number,len(chunks),unit='files',detail=n)
        info['chunk_audit']={'files':len(chunks),'contiguous':nums==list(range(len(chunks))) and len(nums)==len(chunks),
                             'bytes':size,'sha256':h.hexdigest(),
                             'equals_monolithic':bool(chunks) and size==info['bytes'] and h.hexdigest()==info['sha256']}
    return path,info

class EventIndex:
    """Metadata in SQLite; at most one decoded event is kept in Python memory."""
    def __init__(self, source:Path, path:Path, *, max_record_bytes=256*1024*1024,
                 strict_events=False, skip_invalid=False, progress=None):
        self.source=source; self.path=path; self.con=sqlite3.connect(path)
        try:
            self.con.execute('CREATE TABLE events (ord INTEGER PRIMARY KEY, step INTEGER UNIQUE, offset INTEGER, size INTEGER, '
                             'turn INTEGER, kind TEXT, source TEXT, status TEXT, timestamp TEXT, content_chars INTEGER, '
                             'thinking_chars INTEGER, tool_count INTEGER, assistant_text INTEGER, truncated INTEGER, sha256 TEXT)')
            self.counts=Counter(); self.unknown=Counter(); self.issues=[]; self.partial_tail=None
            self.turns=[]; current=None; turn=0; ordinal=0; laststep=-1; gap_ranges=[]
            with source.open('rb') as f:
                while True:
                    offset=f.tell(); line=f.readline(max_record_bytes+1)
                    if not line: break
                    if len(line)>max_record_bytes: raise ExportError('Individual JSONL record exceeds --max-record-bytes')
                    if not line.strip(): continue
                    try:
                        record=json.loads(line)
                        if not isinstance(record,dict) or type(record.get('step_index')) is not int or record['step_index']<0:
                            raise ValueError('Expected object with nonnegative integer step_index')
                    except (ValueError,UnicodeError) as exc:
                        issue={'offset':offset,'bytes':len(line),'sha256':hashlib.sha256(line).hexdigest(),'error':str(exc)}
                        if not line.endswith(b'\n') and f.tell()==source.stat().st_size:
                            self.partial_tail=issue; break
                        if not skip_invalid: raise ExportError(f'Completed malformed JSONL record at byte {offset}: {exc}') from exc
                        self.issues.append(issue); continue
                    step=record['step_index']
                    if step<=laststep: raise ExportError(f'Duplicate/out-of-order step {step}; no silent deduplication')
                    if step>laststep+1: gap_ranges.append([laststep+1,step-1])
                    laststep=step; ordinal+=1
                    typ=record.get('type','UNKNOWN'); role=record.get('source','UNKNOWN'); status=record.get('status','UNKNOWN')
                    if not all(isinstance(x,str) for x in (typ,role,status)): raise ExportError('Invalid type/source/status field')
                    if typ not in KNOWN_TYPES:
                        self.unknown[typ]+=1
                        if strict_events: raise ExportError(f'Unrecognized event type: {typ}')
                    if typ=='USER_INPUT' and role=='USER_EXPLICIT':
                        turn+=1; current={'turn':turn,'first_step':step,'last_step':step,'records':0,'tool_calls':0,
                                         'assistant_text_messages':0,'first_ordinal':ordinal,'last_ordinal':ordinal,
                                         'start_utc':record.get('created_at'),'execution_complete':'not_established_by_transcript'}
                        self.turns.append(current)
                    content=record.get('content',''); thinking=record.get('thinking',''); tools=record.get('tool_calls',[])
                    content_len=len(content) if isinstance(content,str) else 0
                    thinking_len=len(thinking) if isinstance(thinking,str) else 0
                    tool_count=len(tools) if isinstance(tools,list) else 0
                    assistant=int(role=='MODEL' and typ=='PLANNER_RESPONSE' and bool(content_len))
                    trunc=int(bool(TRUNCATED.search(content)) if isinstance(content,str) else False)
                    if record.get('truncated_fields'): trunc=1
                    self.con.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                     (ordinal,step,offset,len(line),turn,typ,role,status,record.get('created_at'),
                                      content_len,thinking_len,tool_count,assistant,trunc,hashlib.sha256(line).hexdigest()))
                    self.counts[f'{role}/{typ}/{status}']+=1
                    if current:
                        current.update(last_step=step,last_ordinal=ordinal)
                        current['records']+=1; current['tool_calls']+=tool_count; current['assistant_text_messages']+=assistant
                        current['last_event_is_assistant_text']=bool(assistant and not tool_count)
                    if ordinal%1000==0: self.con.commit()
                    if progress: progress.update('index-records',f.tell(),source.stat().st_size,detail=f'{ordinal} records')
            self.con.commit()
            if not ordinal: raise ExportError('No valid transcript records')
            self.count=ordinal
            first,last=self.con.execute('SELECT MIN(step),MAX(step) FROM events').fetchone()
            self.audit={'records':ordinal,'first_step':first,'last_step':last,'missing_step_ranges':gap_ranges,
                        'partial_tail_deferred':self.partial_tail,'invalid_complete_records':self.issues,
                        'event_types':dict(self.counts),'unknown_types':dict(self.unknown),
                        'user_turns':turn,'tool_calls':self.con.execute('SELECT SUM(tool_count) FROM events').fetchone()[0],
                        'recorded_thinking_chars':self.con.execute('SELECT SUM(thinking_chars) FROM events').fetchone()[0],
                        'native_copy_contains_entire_captured_prefix':True}
        except BaseException:
            self.con.close()
            raise
    def close(self): self.con.close()
    def selection(self, args):
        clauses=[]; params=[]
        mode=args.mode
        if getattr(args,'turn',None): clauses.append('turn=?'); params.append(args.turn)
        if getattr(args,'last_n_turns',None):
            if not self.turns: raise ExportError('No actual user turns recorded')
            clauses.append('turn>=?'); params.append(max(1,len(self.turns)-args.last_n_turns+1))
        if mode in ('turn','response','response-block'):
            if not getattr(args,'turn',None): raise ExportError(f'--mode {mode} requires --turn N')
            if mode=='response': clauses.append('assistant_text=1')
            elif mode=='response-block': clauses.append("NOT (source='USER_EXPLICIT' AND kind='USER_INPUT')")
        if mode=='chat': clauses.append("(assistant_text=1 OR (source='USER_EXPLICIT' AND kind='USER_INPUT'))")
        if mode=='chat-actions': clauses.append("(assistant_text=1 OR source='USER_EXPLICIT' OR tool_count>0 OR kind='GENERIC')")
        if mode=='actions': clauses.append("(tool_count>0 OR kind='GENERIC')")
        if mode in ('last-assistant','last-substantial','last-response'):
            minimum=args.min_chars if mode=='last-substantial' else 1
            row=self.con.execute('SELECT ord FROM events WHERE assistant_text=1 AND content_chars>=? ORDER BY ord DESC LIMIT 1',(minimum,)).fetchone()
            if not row: raise ExportError('No matching recorded assistant text')
            clauses.append('ord=?'); params.append(row[0])
        if mode=='checkpoint-context':
            row=self.con.execute("SELECT MAX(ord) FROM events WHERE kind='CHECKPOINT'").fetchone()
            if not row[0]: raise ExportError('No recorded checkpoint; cannot reconstruct context')
            clauses.append('ord>=?'); params.append(row[0])
        for option,column,op in [('message','ord','='),('from_message','ord','>='),('to_message','ord','<='),
                                 ('from_step','step','>='),('to_step','step','<='),('step','step','=')]:
            value=getattr(args,option,None)
            if value is not None: clauses.append(f'{column}{op}?'); params.append(value)
        if mode=='message' and getattr(args,'message',None) is None: raise ExportError('--mode message requires --message N (1-based ordinal)')
        self.con.execute('DROP TABLE IF EXISTS selected')
        self.con.execute('CREATE TEMP TABLE selected AS SELECT * FROM events'+(' WHERE '+' AND '.join(clauses) if clauses else '')+' ORDER BY ord',params)
        if getattr(args,'last_n_steps',None):
            self.con.execute('DELETE FROM selected WHERE ord NOT IN (SELECT ord FROM selected ORDER BY ord DESC LIMIT ?)',(args.last_n_steps,))
        n,low,high=self.con.execute('SELECT COUNT(*),MIN(step),MAX(step) FROM selected').fetchone()
        if not n: raise ExportError('Selection matched no records')
        return {'records':n,'first_step':low,'last_step':high,'mode':mode,
                'native_jsonl_scope':'entire captured source, including records outside this rendered selection',
                'context_reconstruction':'checkpoint plus suffix only; not actual server context' if mode=='checkpoint-context' else None}
    def records(self, selected=True):
        table='selected' if selected else 'events'
        with self.source.open('rb') as f:
            for ordinal,offset,size in self.con.execute(f'SELECT ord,offset,size FROM {table} ORDER BY ord'):
                f.seek(offset); line=f.read(size)
                yield ordinal,json.loads(line)
    def listing(self, responses=False):
        where=' WHERE assistant_text=1' if responses else ''
        for row in self.con.execute('SELECT ord,step,turn,kind,status,timestamp,content_chars,tool_count FROM events'+where+' ORDER BY ord'):
            yield dict(zip(('message','step','turn','type','status','time_utc','content_chars','tool_calls'),row))

# Small, schema-independent protobuf wire reader. Text leaves have field paths, not
# guessed application field names. No arbitrary binary decompression or execution.
def protobuf_strings(data: bytes, *, depth=0, path=(), budget=None):
    if budget is None: budget=[20000]
    if depth>12 or len(data)>16*1024*1024 or budget[0]<=0: return []
    def varint(pos):
        value=0
        for shift in range(0,70,7):
            if pos>=len(data): raise ValueError('short varint')
            b=data[pos]; pos+=1; value|=(b&127)<<shift
            if b<128: return value,pos
        raise ValueError('long varint')
    pos=0; fields=[]; counts=Counter()
    try:
        while pos<len(data):
            budget[0]-=1
            if budget[0]<0: raise ValueError('budget')
            tag,pos=varint(pos); field,wire=tag>>3,tag&7
            if field<1 or field>536870911: raise ValueError('field')
            counts[field]+=1; fieldpath=path+(f'{field}[{counts[field]}]',)
            if wire==0: _,pos=varint(pos)
            elif wire==1: pos+=8
            elif wire==5: pos+=4
            elif wire==2:
                size,pos=varint(pos)
                if size>len(data)-pos: raise ValueError('short length')
                value=data[pos:pos+size]; pos+=size
                try:
                    text=value.decode('utf-8')
                    printable=all(c.isprintable() or c in '\r\n\t' for c in text)
                    if text and printable:
                        fields.append({'field_path':'.'.join(fieldpath),'text':text})
                    else: fields.extend(protobuf_strings(value,depth=depth+1,path=fieldpath,budget=budget))
                except UnicodeError:
                    fields.extend(protobuf_strings(value,depth=depth+1,path=fieldpath,budget=budget))
            else: raise ValueError('unsupported wire type')
            if pos>len(data): raise ValueError('short fixed width')
        return fields
    except ValueError:
        return []

def audit_database(src:Source, session, index:EventIndex):
    name=f"{session['variant']}/conversations/{session['id']}.db"
    if not src.exists(name): return {'available':False,'coverage':'unverified'}, []
    db,receipt=src.snapshot(name,database=True)
    con=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)
    con.row_factory=sqlite3.Row
    supplements=[]
    try:
        tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'steps','trajectory_meta'}<=tables:
            return {'available':True,'coverage':'unsupported_schema','snapshot':receipt}, []
        cols={r[1] for r in con.execute('PRAGMA table_info(steps)')}
        if not {'idx','step_type','status'}<=cols: raise ExportError('Unsupported SQLite steps schema')
        check=con.execute('PRAGMA quick_check').fetchall()
        if [r[0] for r in check]!=['ok']: raise ExportError('SQLite quick_check failed')
        identity=con.execute('SELECT cascade_id FROM trajectory_meta').fetchall()
        matches=any(r[0].lower()==session['id'].lower() for r in identity)
        if not matches: raise ExportError('SQLite cascade ID does not match selected session')
        # The temporary index performs the join; only small identifiers enter memory.
        index.con.execute('DROP TABLE IF EXISTS db_steps')
        index.con.execute('CREATE TEMP TABLE db_steps (step INTEGER PRIMARY KEY, kind INTEGER, status INTEGER)')
        index.con.executemany('INSERT INTO db_steps VALUES (?,?,?)',con.execute('SELECT idx,step_type,status FROM steps ORDER BY idx'))
        missing=[r[0] for r in index.con.execute('SELECT step FROM db_steps EXCEPT SELECT step FROM events ORDER BY step')]
        logonly=[r[0] for r in index.con.execute('SELECT step FROM events EXCEPT SELECT step FROM db_steps ORDER BY step')]
        n,lo,hi=index.con.execute('SELECT COUNT(*),MIN(step),MAX(step) FROM db_steps').fetchone()
        for step in missing:
            row=dict(con.execute('SELECT * FROM steps WHERE idx=?',(step,)).fetchone())
            blobs={k:v for k,v in row.items() if isinstance(v,bytes)}
            leaves=[]
            for key in ('error_details','step_payload'):
                if isinstance(row.get(key),bytes):
                    leaves.extend({'column':key,**v} for v in protobuf_strings(row[key]))
            # Exact byte evidence, not a claim of fully decoded error-message schema.
            joined=b'\n'.join(blobs.values())
            quota=bool(re.search(rb'(?i)(quota|resource_exhausted|rate.?limit)',joined))
            http429=bool(re.search(rb'(?<!\d)429(?!\d)',joined))
            times=[]
            for leaf in leaves:
                times.extend(re.findall(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z',leaf['text']))
            supplements.append({'step_index':step,'step_type':row['step_type'],'status_code':row['status'],
                                'provenance':'SQLite-only row; NOT an original JSONL event',
                                'quota_bytes_observed':quota,'http_429_bytes_observed':http429,
                                'timestamp_strings_observed':sorted(set(times)),
                                'text_leaves':leaves,'blob_hashes':{k:{'bytes':len(v),'sha256':hashlib.sha256(v).hexdigest()} for k,v in blobs.items()},
                                'semantic_schema_decoded':False})
        result={'available':True,'quick_check':'ok','cascade_id_matches':matches,'sqlite_records':n,
                'sqlite_range':[lo,hi],'db_only_steps':missing,'log_only_steps':logonly,
                'matched_exactly':not missing and not logonly,'snapshot':receipt,
                'opaque_or_wire_text_diagnostics_count':len(supplements),
                'atomic_with_log':False}
        return result,supplements
    finally: con.close()

def interruption_summary(index, supplements):
    quota=[x for x in supplements if x['quota_bytes_observed'] or x['http_429_bytes_observed']]
    latest=max(quota,key=lambda x:x['step_index']) if quota else None
    out={'completion':'not established; assistant text is not a completion signal',
         'database_only_quota_steps':[x['step_index'] for x in quota],
         'latest_database_only_quota_step':latest['step_index'] if latest else None,
         'timestamp_strings_in_latest_quota_step':latest['timestamp_strings_observed'] if latest else [],
         'timestamps_are_historical_observations_not_live_service_predictions':True}
    for turn in index.turns:
        next_turn=next((t['first_step'] for t in index.turns if t['turn']==turn['turn']+1),None)
        in_turn=[q['step_index'] for q in quota if q['step_index']>=turn['first_step'] and (next_turn is None or q['step_index']<next_turn)]
        turn['database_only_quota_steps']=in_turn
        if in_turn: turn['execution_complete']='interrupted_by_recorded_quota_event'
    return out
