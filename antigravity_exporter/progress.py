"""Measured two-line progress, adapted from lowestprime/codex-exporter 0.3.12.

Copyright (c) 2026 Cooper Beaman. MIT; see THIRD_PARTY_NOTICES.md.
The rendering vocabulary follows upstream; the adapter supports Antigravity's
absolute callbacks and explicitly grouped file operations. No whole-job ETA.
"""
from __future__ import annotations
from contextlib import contextmanager
import json
import math
import os
import re
import shutil
import sys
import threading
import time
import unicodedata

STYLES = ('auto', 'rich', 'plain', 'json', 'off')
ANSI = re.compile(r'\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|[@-_])')
SPINNERS = ('⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏')


def clean(value, limit=1024):
    value = ANSI.sub('', str(value))
    value = ''.join(' ' if ch in '\r\n\t' else ch for ch in value
                    if ch in '\r\n\t' or unicodedata.category(ch) not in ('Cc','Cf','Cs'))
    return ' '.join(value.split())[:limit]


def cells(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ('W','F') else 1 for c in text)


def clip(text, width, unicode=True):
    if cells(text) <= width: return text
    suffix = '…' if unicode else '...'
    if width <= len(suffix): return suffix[:max(0,width)]
    result = ''; remaining = width-cells(suffix)
    for ch in text:
        size = cells(ch)
        if size > remaining: break
        result += ch; remaining -= size
    return result+suffix


def duration(seconds):
    if seconds < 60: return f'{seconds:.1f}s'
    minutes, seconds = divmod(int(seconds),60)
    if minutes < 60: return f'{minutes}m {seconds:02}s'
    hours, minutes = divmod(minutes,60)
    return f'{hours}h {minutes:02}m {seconds:02}s'


def amount(value, unit):
    if unit != 'bytes': return f'{value:,.0f} {unit}'
    for power, label in reversed(tuple(enumerate(('B','KiB','MiB','GiB','TiB')))):
        scale=1024**power
        if value >= scale or power == 0:
            return f'{value/scale:,.1f} {label}' if power else f'{value:,.0f} B'


def windows_vt(stream):
    if os.name != 'nt': return True
    try:
        import ctypes
        from ctypes import wintypes
        import msvcrt
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetConsoleMode.argtypes=(wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD))
        kernel.GetConsoleMode.restype=wintypes.BOOL
        kernel.SetConsoleMode.argtypes=(wintypes.HANDLE,wintypes.DWORD)
        kernel.SetConsoleMode.restype=wintypes.BOOL
        handle=msvcrt.get_osfhandle(stream.fileno()); mode=wintypes.DWORD()
        return bool(kernel.GetConsoleMode(handle,ctypes.byref(mode)) and kernel.SetConsoleMode(handle,mode.value|4))
    except (ImportError,OSError,AttributeError,ValueError): return False


class Progress:
    """Stage counters; five-Hz TTY, sparse redirected logs, optional JSON events.

    Start/end of stages are explicit. `group()` suppresses nested per-file capture
    chatter, while the enclosing loop reports its measured aggregate counters.
    Background refresh never increments work and never changes completion status.
    """
    def __init__(self, style='auto', *, stream=None, clock=time.monotonic, width=None,
                 interval=0.2, plain_interval=5.0, heartbeat=True):
        if style not in STYLES: raise ValueError('Unknown progress style: '+str(style))
        self.stream=stream if stream is not None else sys.stderr
        self.clock=clock; self.width=width; self.interval=interval; self.plain_interval=plain_interval
        try: tty=bool(self.stream.isatty())
        except (AttributeError,OSError,ValueError): tty=False
        capable=tty and os.environ.get('TERM','').lower()!='dumb' and windows_vt(self.stream)
        self.style=('rich' if capable else 'plain') if style in ('auto','rich') else style
        self.color=self.style=='rich' and 'NO_COLOR' not in os.environ
        self.unicode=self.style=='rich'
        try: '⠋✓✕━░…'.encode(getattr(self.stream,'encoding',None) or 'utf-8')
        except (UnicodeError,LookupError): self.unicode=False
        self.started=clock(); self.task=None; self.history=[]; self.rows=0
        self.lock=threading.RLock(); self.closed=False; self.groups=[]
        self.stop=threading.Event(); self.thread=None
        if heartbeat and self.style!='off':
            self.thread=threading.Thread(target=self._beat,name='antigravity-progress',daemon=True)
            self.thread.start()

    def _beat(self):
        while not self.stop.wait(self.interval):
            with self.lock:
                if self.task and self.task['status']=='running': self._emit('stage_progress')

    def _write(self,text):
        try: self.stream.write(text); self.stream.flush()
        except UnicodeEncodeError:
            try:
                encoding=getattr(self.stream,'encoding',None) or 'ascii'
                self.stream.write(text.encode(encoding,'replace').decode(encoding)); self.stream.flush()
            except (OSError,ValueError,UnicodeError,LookupError): self.style='off'
        except (OSError,ValueError): self.style='off'

    def _metrics(self):
        t=self.task; elapsed=max(0.,(t.get('ended') or self.clock())-t['started'])
        done,total=t['done'],t['total']
        percent=100*done/total if done is not None and total else None
        rate=done/elapsed if done and elapsed>=0.1 and t['unit']!='percent' else None
        eta=(total-done)/rate if rate and total and total>done and t['status']=='running' else None
        return elapsed,percent,rate,eta

    def _emit(self,event,force=False):
        if self.style=='off' or self.task is None: return
        t=self.task; now=self.clock()
        interval=self.plain_interval if self.style=='plain' else self.interval
        if not force and now-t['last_emit']<interval: return
        elapsed,percent,rate,eta=self._metrics()
        if self.style=='json':
            self._write(json.dumps({'event':event,'stage':t['name'],'stage_index':t['index'],
                'status':t['status'],'unit':t['unit'],'completed':t['done'],'total':t['total'],
                'detail':t['detail'],'export_elapsed_seconds':max(0.,now-self.started),
                'elapsed_seconds':elapsed,'percent':percent,'rate_per_second':rate,'eta_seconds':eta},
                ensure_ascii=True,allow_nan=False)+'\n')
        else:
            complete=t['status']=='complete'; failed=t['status']=='failed'
            icon=('✕' if failed else '✓' if complete else SPINNERS[int(elapsed*5)%10]) if self.unicode else (
                '[failed]' if failed else '[done]' if complete else '[start]' if event=='stage_start' else '[working]')
            header=f"{icon} {t['index']:02d} {t['name']}"
            metrics=[]
            if percent is not None: metrics.append(f'{percent:.1f}%')
            if t['unit']!='percent':
                if t['total'] is not None: metrics.append(f"{amount(t['done'] or 0,t['unit'])}/{amount(t['total'],t['unit'])}")
                elif t['done']: metrics.append(amount(t['done'],t['unit']))
                if rate: metrics.append(amount(rate,t['unit'])+'/s')
            metrics.append('elapsed '+duration(elapsed))
            if eta is not None: metrics.append('ETA ~'+duration(eta))
            if self.style=='plain':
                self._write(' | '.join([header,*metrics,*([t['detail']] if t['detail'] else [])])+'\n')
            else:
                width=max(12,(self.width or shutil.get_terminal_size((120,24)).columns)-1)
                head_limit=min(width,max(36,width//2)); header=clip(header,head_limit,self.unicode)
                available=width-cells(header)-3
                if t['detail'] and available>4: header+=' | '+clip(t['detail'],available,self.unicode)
                body=' | '.join(metrics)
                bar_width=min(16,max(0,width-5-cells(body)))
                if percent is not None and bar_width>=6:
                    filled=min(bar_width,max(0,int(bar_width*percent/100)))
                    fill,empty=('━','░') if self.unicode else ('#','-')
                    body=fill*filled+empty*(bar_width-filled)+' '+body
                color='31' if failed else '32' if complete else '36'
                def paint(s): return f'\x1b[{color}m{s}\x1b[0m' if self.color else s
                if self.rows: self._write('\r\x1b[1A')
                self._write('\r\x1b[2K'+paint(clip(header,width,self.unicode))+'\n\r\x1b[2K'+paint('    '+clip(body,width-4,self.unicode)))
                self.rows=2
                if complete or failed: self._write('\n'); self.rows=0
        t['last_emit']=now

    def _end(self,success=True):
        if self.task is None or self.task['status']!='running': return
        self.task['status']='complete' if success else 'failed'; self.task['ended']=self.clock()
        self._emit('stage_complete' if success else 'stage_failed',force=True)
        self.history.append(dict(self.task))

    def update(self,stage,done=None,total=None,*,unit='bytes',detail='',force=False):
        # Never use percentages or counters in validation logic; they are observation only.
        for name,value in (('done',done),('total',total)):
            if value is not None and (not math.isfinite(value) or value<0): raise ValueError(name+' must be nonnegative and finite')
        with self.lock:
            if self.closed: return
            if self.groups and stage!=self.groups[-1]: return
            new=self.task is None or stage!=self.task['name']
            if not new and done is not None and self.task['done'] is not None and done<self.task['done']:
                new=True  # A new operation using the same stage name.
            if new:
                self._end(); now=self.clock()
                self.task={'name':clean(stage,120),'index':len(self.history)+1,'started':now,'last_emit':-math.inf,
                           'done':done,'total':total,'unit':clean(unit,32),'detail':clean(detail),'status':'running'}
                self._emit('stage_start',force=True)
            else:
                if done is not None: self.task['done']=done
                if total is not None: self.task['total']=total
                self.task['detail']=clean(detail)
                self._emit('stage_progress',force=force)

    @contextmanager
    def group(self,name,*,total=None,unit='files',detail=''):
        with self.lock:
            self.groups.append(name); self.update(name,0,total,unit=unit,detail=detail,force=True)
        try: yield self
        except BaseException:
            with self.lock: self._end(False)
            raise
        else:
            with self.lock: self._end(True)
        finally:
            with self.lock: self.groups.pop(); self.task=None

    def finish(self,success=True):
        with self.lock:
            if self.closed: return
            self._end(success); self.closed=True; self.stop.set()
            elapsed=max(0.,self.clock()-self.started)
            success=success and all(t['status']=='complete' for t in self.history)
            status='complete' if success else 'failed'
            if self.style=='json': self._write(json.dumps({'event':'export_'+status,'status':status,
                'elapsed_seconds':elapsed,'stages_completed':sum(t['status']=='complete' for t in self.history),
                'stages_total':len(self.history)})+'\n')
            elif self.style!='off': self._write(f"[{'done' if success else 'failed'}] Export {status} | {len(self.history)} stages | total {duration(elapsed)}\n")
        if self.thread and self.thread is not threading.current_thread(): self.thread.join(timeout=0.5)

class SevenZipFrames:
    """Bounded CR/backspace/newline parser; percent is never byte throughput."""
    def __init__(self, progress):
        self.progress=progress; self.pending=''; self.member=''; self.last=None
    def feed(self,text,final=False):
        frames=re.split(r'[\r\n\b]+',self.pending+text)
        self.pending=frames.pop()[-16384:]
        for frame in frames + ([self.pending] if final else []):
            value=clean(frame,16384)
            match=re.fullmatch(r'\s*(\d{1,3}(?:\.\d+)?)%(?:\s+(.*))?',value)
            if match:
                percent=float(match.group(1))
                if percent>100: continue
                inline=re.match(r'(?:\d+\s+)?[+UT-]\s+(.+)',match.group(2) or '')
                if inline: self.member=inline.group(1)
                self.last=percent
                if self.progress:self.progress.update('compress',percent,100,unit='percent',detail='7-Zip: '+(self.member or 'compressing members'))
            elif re.match(r'^[+UT-]\s+',value):
                self.member=value[1:].strip()
                if self.progress:self.progress.update('compress',self.last,100 if self.last is not None else None,unit='percent',detail='7-Zip: '+self.member)
        if final: self.pending=''
