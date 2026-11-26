"""Codex v0.5.2 measured progress with Antigravity callback compatibility.

The renderer, recent-window ETA, subprocess telemetry and retry semantics are
shared with the pinned attributed implementation. Absolute callbacks remain
available for Antigravity's source and SQLite capture operations.
"""
from __future__ import annotations
from contextlib import contextmanager
import re
import time
from ._vendor.codex_archive.progress import (
    ProgressReporter, StageProgress, PROGRESS_STYLES, _ANSI, _cells, _clip,
    _windows_vt, _clean, _duration, _amount, _finite_count,
    SevenZipProgressParser, run_seven_zip, SEVEN_ZIP_PROGRESS_ARGS,
)

STYLES = PROGRESS_STYLES
ANSI = _ANSI
clean = _clean
cells = _cells
clip = _clip
duration = _duration
amount = _amount
windows_vt = _windows_vt


class Progress(ProgressReporter):
    """One reporter for both explicit stages and legacy absolute callbacks."""
    def __init__(self, style='auto', *, stream=None, clock=time.monotonic,
                 width=None, interval=0.2, plain_interval=5.0, heartbeat=True):
        self._legacy_heartbeat = heartbeat
        self._implicit = None
        self._groups = []
        self._initial_done = None
        super().__init__(style=style, stream=stream, clock=clock, width=width,
                         interval=interval, plain_interval=plain_interval)

    @staticmethod
    def _terminal_vt(stream):
        return windows_vt(stream)

    @property
    def thread(self):
        return next((task._heartbeat_thread for task in reversed(self._stages)
                     if task._heartbeat_thread is not None), None)

    def _emit(self, task, event, *, force=False):
        if event == 'stage_start' and self._initial_done is not None:
            task.completed = self._initial_done
        return super()._emit(task, event, force=force)

    def _settle_implicit(self, success=True):
        task = self._implicit
        self._implicit = None
        if task is not None and task.status == 'running':
            task.complete() if success else task.fail()

    def stage(self, name, **kwargs):
        self._settle_implicit()
        return super().stage(name, **kwargs)

    def update(self, stage, done=None, total=None, *, unit='bytes', detail='', force=False):
        if self._finished:
            return
        for label, value in (('done', done), ('total', total)):
            if value is not None:
                _finite_count(value, label)
        if self._groups:
            name, task = self._groups[-1]
            if stage != name:
                return
        else:
            task = self._implicit
            if (task is None or task.status != 'running' or task.name != clean(stage, 120)
                    or (done is not None and done < task.completed)):
                self._settle_implicit()
                self._initial_done = 0 if done is None else done
                try:
                    task = super().stage(stage, total=total, unit=unit, detail=detail)
                finally:
                    self._initial_done = None
                self._implicit = task
        task.update(done, total=total, detail=detail)
        if force:
            self._emit(task, 'stage_progress', force=True)

    @contextmanager
    def group(self, name, *, total=None, unit='files', detail=''):
        self._settle_implicit()
        task = super().stage(name, total=total, unit=unit, detail=detail)
        self._groups.append((name, task))
        try:
            with task:
                yield self
        finally:
            self._groups.pop()

    def finish(self, success=True, *, detail=None):
        self._settle_implicit(success)
        return super().finish(success=success, detail=detail)

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
