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
ORDER_EXAMPLE_LIMIT = 32
TRUNCATED = re.compile(r'(?i)(?:output (?:was|is) truncated|content truncated|<truncated \d+|partial read \(lines|\btruncated_fields\b)')

def annotation_title(data):
    if data is None: return None
    text=data.decode('utf-8', errors='strict')
    m=re.search(r'\btitle\s*:\s*("(?:[^"\\]|\\.)*")',text)
    if not m: return None
    try: return json.loads(m.group(1))
    except ValueError: return None

def sessions(src: Source) -> list[dict]:
    from .catalog import discover_sessions
    return discover_sessions(src)


def resolve(all_sessions, ids=None, project=None, variant=None, latest=False, all_=False):
    pool = [s for s in all_sessions if (not variant or s['variant'] == variant) and
            (not project or s['project'].casefold() == project.casefold() or s['project_id'] == project)]
    if ids:
        selected = []
        selected_keys = set()
        for value in ids:
            term = value.strip().lower()
            qualifier = None
            if ':' in term:
                qualifier, term = term.split(':', 1)
                if qualifier not in VARIANTS:
                    raise ExportError(f'Unknown session storage variant: {qualifier!r}')
            if len(term) < 8 or not re.fullmatch('[a-f0-9-]+', term):
                raise ExportError('Use an exact UUID or unique hexadecimal UUID prefix of at least 8 characters; variant:UUID is also supported')
            matches = [s for s in pool if s['id'].startswith(term) and
                       (qualifier is None or s['variant'] == qualifier)]
            if len(matches) != 1:
                locations = ', '.join(s.get('selector', s['variant'] + ':' + s['id']) for s in matches)
                detail = f'; candidates: {locations}' if locations else ''
                raise ExportError(f'Session selector {value!r} matched {len(matches)} storage identities{detail}. Use --variant or variant:UUID for distinct sources.')
            key = (matches[0]['variant'], matches[0]['id'])
            if key not in selected_keys:
                selected.append(matches[0]); selected_keys.add(key)
        return selected
    if latest and pool:
        return [pool[0]]
    if all_ or project:
        return pool
    raise ExportError('Specify --id UUID, --project NAME, --latest-session, or --all')

def capture_transcript(src: Source, session, *, allow_reduced=False, verify_chunks=False):
    base=f"{session['variant']}/brain/{session['id']}/.system_generated/logs/"
    name=base+'transcript_full.jsonl'
    chunks=None
    if src.exists(name):
        path,info=src.snapshot(name,growth=True)
        info={**info,'fidelity':'full-recorded-log'}
    else:
        from .catalog import ordered_chunks
        chunks,_=ordered_chunks(src,base+'chunks/transcript_full/')
        if chunks:
            import uuid
            path=src.stage/('reassembled-'+session['variant']+'-'+session['id']+'-'+uuid.uuid4().hex+'.jsonl')
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
        from .catalog import ordered_chunks
        chunks,contiguous=ordered_chunks(src,base+'chunks/transcript_full/',validate=False)
        h=hashlib.sha256(); size=0
        with (src.progress.group('verify-transcript-chunks',total=len(chunks),unit='files') if src.progress else nullcontext()):
            for number,n in enumerate(chunks,1):
                p,_=src.snapshot(n)
                with p.open('rb') as f:
                    for b in iter(lambda:f.read(CHUNK),b''): h.update(b); size+=len(b)
                if src.progress: src.progress.update('verify-transcript-chunks',number,len(chunks),unit='files',detail=n)
        info['chunk_audit']={'files':len(chunks),'contiguous':contiguous,
                             'bytes':size,'sha256':h.hexdigest(),
                             'equals_monolithic':bool(chunks) and size==info['bytes'] and h.hexdigest()==info['sha256']}
    return path,info

class EventIndex:
    """Metadata in SQLite; at most one decoded event is kept in Python memory."""
    def __init__(self, source:Path, path:Path, *, max_record_bytes=256*1024*1024,
                 strict_events=False, skip_invalid=False, strict_step_order=False, progress=None):
        self.source=source; self.path=path; self.con=sqlite3.connect(path)
        try:
            self.con.execute('CREATE TABLE events (ord INTEGER PRIMARY KEY, step INTEGER, offset INTEGER, size INTEGER, '
                             'turn INTEGER, kind TEXT, source TEXT, status TEXT, timestamp TEXT, content_chars INTEGER, '
                             'thinking_chars INTEGER, tool_count INTEGER, assistant_text INTEGER, truncated INTEGER, sha256 TEXT)')
            self.counts=Counter(); self.unknown=Counter(); self.issues=[]; self.partial_tail=None
            self.turns=[]; current=None; turn=0; ordinal=0; laststep=-1
            backward_transitions=0; backward_examples=[]
            self.source_gaps=[]; source_size=source.stat().st_size
            with source.open('rb') as f:
                while True:
                    offset=f.tell(); line=f.readline(max_record_bytes+1)
                    if not line: break
                    if len(line)>max_record_bytes: raise ExportError('Individual JSONL record exceeds --max-record-bytes')
                    if not line.strip(): continue
                    body=line[:-2] if line.endswith(b'\r\n') else line[:-1]
                    if line.endswith(b'\n') and body and body.count(b'\x00')==len(body):
                        self.source_gaps.append({'offset':offset,'bytes':len(line),'sha256':hashlib.sha256(line).hexdigest(),
                                                 'kind':'nul_only_record','content_reconstructed':False})
                        continue
                    try:
                        record=json.loads(line)
                        if not isinstance(record,dict) or type(record.get('step_index')) is not int or record['step_index']<0:
                            raise ValueError('Expected object with nonnegative integer step_index')
                    except (ValueError,UnicodeError) as exc:
                        issue={'offset':offset,'bytes':len(line),'sha256':hashlib.sha256(line).hexdigest(),'error':str(exc)}
                        if not line.endswith(b'\n') and f.tell()==source_size:
                            self.partial_tail=issue; break
                        if not skip_invalid: raise ExportError(f'Completed malformed JSONL record at byte {offset}: {exc}') from exc
                        self.issues.append(issue); continue
                    step=record['step_index']
                    if strict_step_order and step<=laststep:
                        raise ExportError(f'Duplicate/out-of-order step {step} at source record {ordinal+1}, '
                                          f'byte {offset}; --strict-step-order requires increasing unique native steps')
                    if step<laststep:
                        backward_transitions+=1
                        if len(backward_examples)<ORDER_EXAMPLE_LIMIT:
                            backward_examples.append({'source_ordinal':ordinal+1,'step_index':step,'source_offset':offset,
                                                      'previous_source_ordinal':ordinal,'previous_step_index':laststep})
                    laststep=step; ordinal+=1
                    typ=record.get('type','UNKNOWN'); role=record.get('source','UNKNOWN'); status=record.get('status','UNKNOWN')
                    if not all(isinstance(x,str) for x in (typ,role,status)): raise ExportError('Invalid type/source/status field')
                    if typ not in KNOWN_TYPES:
                        self.unknown[typ]+=1
                        if strict_events: raise ExportError(f'Unrecognized event type: {typ}')
                    if typ=='USER_INPUT' and role=='USER_EXPLICIT':
                        turn+=1; current={'turn':turn,'first_step':step,'last_step':step,'min_step':step,'max_step':step,'records':0,'tool_calls':0,
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
                        current.update(last_step=step,last_ordinal=ordinal,min_step=min(current['min_step'],step),max_step=max(current['max_step'],step))
                        current['records']+=1; current['tool_calls']+=tool_count; current['assistant_text_messages']+=assistant
                        current['last_event_is_assistant_text']=bool(assistant and not tool_count)
                    if ordinal%1000==0: self.con.commit()
                    if progress: progress.update('index-records',f.tell(),source_size,detail=f'{ordinal} records')
            self.con.commit()
            if not ordinal: raise ExportError('No valid transcript records')
            self.count=ordinal
            self.con.execute('CREATE INDEX events_by_step ON events(step,sha256)')
            step_order,gap_ranges=self._audit_step_order(strict_step_order,backward_transitions,backward_examples)
            first,last=self.con.execute('SELECT MIN(step),MAX(step) FROM events').fetchone()
            self.audit={'records':ordinal,'first_step':first,'last_step':last,'missing_step_ranges':gap_ranges,
                        'first_source_step':self.con.execute('SELECT step FROM events WHERE ord=1').fetchone()[0],
                        'last_source_step':laststep,'record_identity':'source_ordinal',
                        'record_order_policy':'captured_source_order','step_order':step_order,
                        'user_turn_definition':'Each recorded USER_EXPLICIT/USER_INPUT occurrence in source order; replay semantics are not inferred',
                        'partial_tail_deferred':self.partial_tail,'invalid_complete_records':self.issues,
                        'event_types':dict(self.counts),'unknown_types':dict(self.unknown),
                        'user_turns':turn,'tool_calls':self.con.execute('SELECT SUM(tool_count) FROM events').fetchone()[0],
                        'recorded_thinking_chars':self.con.execute('SELECT SUM(thinking_chars) FROM events').fetchone()[0],
                        'native_copy_contains_entire_captured_prefix':True,'source_gaps':self.source_gaps,
                        'source_gap_policy':'Only complete newline-terminated NUL-only records; original bytes preserved, content not reconstructed'}
        except BaseException:
            self.con.close()
            raise
    def _audit_step_order(self,strict_requested,backward_transitions,backward_examples):
        # Native step IDs are observed coordinates, not a unique event key. Keep
        # all bytes/occurrences and use SQLite for counts rather than a Python
        # dictionary of the transcript. Later arrivals may close earlier gaps.
        distinct_steps=0; gap_ranges=[]; previous=-1
        for step, in self.con.execute('SELECT step FROM events GROUP BY step ORDER BY step'):
            if step>previous+1:gap_ranges.append([previous+1,step-1])
            previous=step;distinct_steps+=1
        duplicate_occurrences=self.count-distinct_steps
        distinct_source_lines=self.con.execute(
            'SELECT COUNT(*) FROM (SELECT 1 FROM events GROUP BY step,sha256)').fetchone()[0]
        duplicate_step_ids=self.con.execute(
            'SELECT COUNT(*) FROM (SELECT 1 FROM events GROUP BY step HAVING COUNT(*)>1)').fetchone()[0]
        examples=[]
        if duplicate_occurrences:
            rows=self.con.execute(
                'SELECT e.ord,e.step,e.offset,e.size,e.sha256,repeated.first_ordinal FROM events e '
                'JOIN (SELECT step,MIN(ord) AS first_ordinal FROM events GROUP BY step HAVING COUNT(*)>1) repeated '
                'ON e.step=repeated.step WHERE e.ord>repeated.first_ordinal ORDER BY e.ord LIMIT ?',
                (ORDER_EXAMPLE_LIMIT,))
            for ordinal,step,offset,size,digest,first_ordinal in rows:
                identical=self.con.execute(
                    'SELECT MIN(ord) FROM events WHERE step=? AND sha256=? AND ord<?',
                    (step,digest,ordinal)).fetchone()[0]
                examples.append({'source_ordinal':ordinal,'step_index':step,'source_offset':offset,
                                 'source_bytes':size,'source_sha256':digest,'first_occurrence_ordinal':first_ordinal,
                                 'identical_prior_ordinal':identical,
                                 'classification':'identical_source_bytes' if identical is not None else 'different_source_bytes'})
        return {'strict_requested':bool(strict_requested),
                'monotonic_unique':not duplicate_occurrences and not backward_transitions,
                'distinct_steps':distinct_steps,'duplicate_step_ids':duplicate_step_ids,
                'duplicate_occurrences':duplicate_occurrences,
                'duplicate_identical_source_bytes':self.count-distinct_source_lines,
                'duplicate_different_source_bytes':distinct_source_lines-distinct_steps,
                'backward_transitions':backward_transitions,
                'duplicate_comparison':'SHA-256 of entire original JSONL line, including whitespace and line ending; semantic revision or replay is not inferred',
                'examples_limit_per_kind':ORDER_EXAMPLE_LIMIT,
                'examples':{'duplicate_occurrences':examples,'backward_transitions':backward_examples},
                'examples_truncated':{'duplicate_occurrences':max(0,duplicate_occurrences-len(examples)),
                                      'backward_transitions':max(0,backward_transitions-len(backward_examples))}},gap_ranges

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
                'all_records_selected':n==self.count,'record_identity':'source_ordinal','source_order_preserved':True,
                'distinct_steps':self.con.execute('SELECT COUNT(DISTINCT step) FROM selected').fetchone()[0],
                'native_step_selector_policy':'All matching source occurrences, retained in captured source order; --message selects one ordinal',
                'native_jsonl_scope':'byte-exact selected source records and disclosed gaps within selected intervals',
                'first_ordinal':self.con.execute('SELECT MIN(ord) FROM selected').fetchone()[0],
                'last_ordinal':self.con.execute('SELECT MAX(ord) FROM selected').fetchone()[0],
                'context_reconstruction':'checkpoint plus suffix only; not actual server context' if mode=='checkpoint-context' else None}
    def selected_source(self, destination:Path, selection:dict, *, progress=None):
        """Borrow a full capture or copy original selected ranges without JSON rewriting.

        Contiguous event runs include intervening blank/NUL/skipped records; the
        manifest discloses these bytes. Disjoint display filters do not bring back
        unrelated events between runs. Complete exports retain the captured tail.
        """
        if selection['records']==self.count:
            return self.source, {'included':True,'scope':'entire captured transcript',
                'byte_exact_captured_prefix':True,'byte_exact_source_records':True,
                'bytes':self.source.stat().st_size,'sha256':hash_file(self.source),
                'ranges':[{'start':0,'end_exclusive':self.source.stat().st_size}],
                'source_gaps':self.source_gaps,'partial_tail_retained':self.partial_tail}
        ranges=[]; previous=None
        for ordinal,offset,size in self.con.execute('SELECT ord,offset,size FROM selected ORDER BY ord'):
            end=offset+size
            if previous is not None and ordinal==previous+1:
                ranges[-1]['end_exclusive']=end
            else:ranges.append({'start':offset,'end_exclusive':end})
            previous=ordinal
        total=sum(r['end_exclusive']-r['start'] for r in ranges)
        digest=hashlib.sha256(); done=0
        with self.source.open('rb') as source,destination.open('xb') as output:
            for item in ranges:
                source.seek(item['start']);remaining=item['end_exclusive']-item['start']
                while remaining:
                    block=source.read(min(CHUNK,remaining))
                    if not block:raise ExportError('Captured source truncated while extracting selected native bytes')
                    output.write(block);digest.update(block);remaining-=len(block);done+=len(block)
                    if progress:progress.update('select-native-records',done,total)
        # Verify copied event bytes against the immutable index, not against a
        # digest computed from the same possibly mutated source during copying.
        # Map original offsets to their positions in the concatenated output.
        range_number = 0; output_base = 0
        with destination.open('rb') as copied:
            for offset, size, expected in self.con.execute('SELECT offset,size,sha256 FROM selected ORDER BY ord'):
                while offset >= ranges[range_number]['end_exclusive']:
                    output_base += ranges[range_number]['end_exclusive'] - ranges[range_number]['start']
                    range_number += 1
                copied.seek(output_base + offset - ranges[range_number]['start'])
                record_hash = hashlib.sha256(); remaining = size
                while remaining:
                    block = copied.read(min(CHUNK, remaining))
                    if not block: raise ExportError('Selected native record was truncated during copying')
                    record_hash.update(block); remaining -= len(block)
                if record_hash.hexdigest() != expected:
                    raise ExportError('Captured event changed after indexing while copying selected native bytes')
        if hash_file(destination)!=digest.hexdigest():raise ExportError('Selected native source verification failed')
        retained=[g for g in self.source_gaps if any(r['start']<=g['offset'] and g['offset']+g['bytes']<=r['end_exclusive'] for r in ranges)]
        return destination, {'included':True,'scope':'selected original source ranges',
            'byte_exact_captured_prefix':False,'byte_exact_source_records':True,
            'bytes':done,'sha256':digest.hexdigest(),'ranges':ranges,'source_gaps':retained,
            'excluded_records':self.count-selection['records'],'partial_tail_retained':None,
            'embedded_history_policy':'Selected checkpoint or context content may quote prior history; retained without rewriting.'}

    def records(self, selected=True):
        table='selected' if selected else 'events'
        with self.source.open('rb') as f:
            for ordinal,offset,size,expected in self.con.execute(f'SELECT ord,offset,size,sha256 FROM {table} ORDER BY ord'):
                f.seek(offset); line=f.read(size)
                if len(line)!=size or hashlib.sha256(line).hexdigest()!=expected:
                    raise ExportError('Captured event changed after indexing; export stopped')
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
                                'quota_error_step':row['step_type']==17 and (quota or http429),
                                'evidence_class':('quota-error-step' if row['step_type']==17 and (quota or http429) else
                                                  'error-step-without-quota-marker' if row['step_type']==17 else
                                                  'text-mention-in-opaque-row' if quota or http429 else 'opaque-row'),
                                'timestamp_strings_observed':sorted(set(times)),
                                'text_leaves':leaves,'blob_hashes':{k:{'bytes':len(v),'sha256':hashlib.sha256(v).hexdigest()} for k,v in blobs.items()},
                                'semantic_schema_decoded':False})
        step_sets_match=not missing and not logonly
        one_to_one_step_match=step_sets_match and n==index.count
        result={'available':True,'quick_check':'ok','cascade_id_matches':matches,'sqlite_records':n,
                'jsonl_records':index.count,'jsonl_unique_steps':index.audit['step_order']['distinct_steps'],
                'jsonl_duplicate_occurrences':index.audit['step_order']['duplicate_occurrences'],
                'step_sets_match':step_sets_match,'one_to_one_step_match':one_to_one_step_match,
                'comparison_scope':'Native step identifiers and multiplicity only; payload content and chronology are not compared',
                'payloads_compared':False,
                'sqlite_range':[lo,hi],'db_only_steps':missing,'log_only_steps':logonly,
                'matched_exactly':one_to_one_step_match,'snapshot':receipt,
                'opaque_or_wire_text_diagnostics_count':len(supplements),
                'atomic_with_log':False}
        return result,supplements
    finally: con.close()

def interruption_summary(index, supplements):
    # A quota phrase quoted in a tool result or earlier prompt is not a new
    # interruption. Only the observed error-step type contributes to this index.
    from bisect import bisect_left
    quota = [row for row in supplements if row.get('step_type') == 17 and
             (row.get('quota_bytes_observed') or row.get('http_429_bytes_observed'))]
    quota.sort(key=lambda row: row['step_index'])
    positions = [row['step_index'] for row in quota]
    ordered_unique = getattr(index,'audit',{}).get('step_order',{}).get('monotonic_unique',True)
    highest = quota[-1] if quota else None
    latest = highest if ordered_unique else None
    out = {
        'completion': 'not established; assistant text is not a completion signal',
        'database_only_quota_steps': positions,
        'latest_database_only_quota_step': latest['step_index'] if latest else None,
        'highest_database_only_quota_step': highest['step_index'] if highest else None,
        'quota_order_basis': 'increasing unique native JSONL steps' if ordered_unique else 'native step identifiers only; temporal order is not established',
        'database_only_quota_turn_attribution': 'native step intervals' if ordered_unique else 'unassigned because JSONL native steps repeat or move backward',
        'unassigned_database_only_quota_steps': [],
        'timestamp_strings_in_latest_quota_step': latest['timestamp_strings_observed'] if latest else [],
        'timestamps_are_historical_observations_not_live_service_predictions': True,
        'quota_detection_policy': 'observed SQLite error step_type 17 plus quota or HTTP 429 byte evidence',
        'non_error_quota_mentions_are_interruptions': False,
    }
    if not ordered_unique:
        # A missing database row has no captured JSONL ordinal. Native numeric
        # intervals cannot identify which repeated/source-ordered user event owns it.
        out['unassigned_database_only_quota_steps']=positions
        for turn in index.turns:
            turn['database_only_quota_steps']=[]
            turn['database_only_quota_attribution']='unassigned; ambiguous native step ordering'
            if turn.get('execution_complete')=='interrupted_by_recorded_quota_event':
                turn['execution_complete']='not_established_by_transcript'
        return out
    out['unassigned_database_only_quota_steps']=positions[:bisect_left(positions,index.turns[0]['first_step'])] if index.turns else positions
    for number, turn in enumerate(index.turns):
        start = bisect_left(positions, turn['first_step'])
        end = (bisect_left(positions, index.turns[number + 1]['first_step'])
               if number + 1 < len(index.turns) else len(positions))
        in_turn = positions[start:end]
        turn['database_only_quota_steps'] = in_turn
        turn['database_only_quota_attribution']='native step interval'
        if turn.get('execution_complete')=='interrupted_by_recorded_quota_event':
            turn['execution_complete']='not_established_by_transcript'
        if in_turn:
            turn['execution_complete'] = 'interrupted_by_recorded_quota_event'
    return out
