# Adapted from lowestprime/codex-exporter 0.5.2; MIT copyright (c) 2026 Cooper Beaman.
# See THIRD_PARTY_NOTICES.md for upstream commit and full license.
"""Stable listing order and bounded, dependency-free terminal navigation.

These helpers only arrange presentation.  They never renumber native turn/message
IDs or mutate source rows, and redirected or JSON output is always complete.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import math
import os
import re
import select
import shutil
import sys
from typing import Any, TextIO
import unicodedata

from .progress import _ANSI, _cells, _clip, _windows_vt
from .timestamps import TimestampFormatter, parse_source_timestamp


@dataclass(frozen=True)
class DisplaySettings:
    sort: str = ''
    sort_order: str = 'auto'
    pager: str = 'auto'
    page_size: int = 0

    def __post_init__(self) -> None:
        if self.sort_order not in ('auto', 'asc', 'desc'):
            raise ValueError('Sort order must be auto, asc, or desc.')
        if self.pager not in ('auto', 'always', 'never'):
            raise ValueError('Pager must be auto, always, or never.')
        if isinstance(self.page_size, bool) or not isinstance(self.page_size, int) or self.page_size < 0:
            raise ValueError('Page size must be zero (terminal height) or a positive integer.')


def _get(row: Any, *names: str) -> Any:
    for name in names:
        value = row.get(name) if isinstance(row, Mapping) else getattr(row, name, None)
        if value is not None and value != '':
            return value
    return None


def _length(value: Any) -> int | None:
    return len(value) if value is not None else None


def _status(row: Any) -> str:
    explicit = _get(row, 'status', 'state')
    if explicit is not None:
        return str(explicit)
    if _get(row, 'missing_rollout'):
        return 'missing'
    return 'archived' if _get(row, 'archived') else 'live'


def _number(value: Any) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) else None
    try:
        result = float(str(value).replace(',', ''))
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _time(value: Any) -> Decimal | None:
    number = _number(value)
    if number is not None:
        return Decimal(int(value)) if isinstance(value, bool) else Decimal(str(value).replace(',', ''))
    stamp = parse_source_timestamp(value)
    if stamp is None:
        return None
    try:
        # Keep offset-free wall-clock ordering deterministic without presenting
        # it to the user as an asserted UTC instant.
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        delta = stamp.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
        result = Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds).scaleb(-6)
        # Retain observed catalog nanosecond ordering beyond datetime precision.
        fraction = re.match(r'^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}[.,](\d+)', str(value).strip())
        if fraction and len(fraction.group(1)) > 6:
            result += Decimal('0.000000' + fraction.group(1)[6:])
        return result
    except (ValueError, OverflowError, OSError):
        return None


def _text(value: Any) -> str | None:
    return None if value is None else str(value).casefold()


def _numeric(*names: str) -> Callable[[Any], Any]:
    return lambda row: _number(_get(row, *names))


def _string(*names: str) -> Callable[[Any], Any]:
    return lambda row: _text(_get(row, *names))


# Aliases name actual columns. Numeric columns stay numeric, even for JSON
# receipts containing commas, and absent values sort last in both directions.
_COMMON = {
    'id': _string('session_id', 'turn_id', 'message_id', 'id'),
    'title': _string('title', 'name'),
    'project': _string('project', 'project_name', 'project_id'),
    'workspace': _string('cwd', 'workspace', 'project_path', 'path'),
    'path': _string('path', 'source', 'db_rollout_path'),
    'size': _numeric('size', 'bytes', 'file_size'),
    'modified': lambda r: _time(_get(r, 'mtime', 'modified', 'updated_at', 'timestamp')),
    'timestamp': lambda r: _time(_get(r, 'timestamp', 'created_at', 'time_utc', 'start_utc', 'time', 'modified')),
    'status': _status,
    'archived': lambda r: _number(_get(r, 'archived')),
    'missing': lambda r: _number(_get(r, 'missing_rollout', 'missing')),
    'compression': _string('compression'),
    'title_source': _string('title_source'),
    'source': _string('source', 'path'),
}
_FIELDS = {
    'sessions': {**_COMMON, 'variant': _string('variant'), 'steps': _numeric('summary_step_count'), 'full': _numeric('full'), 'db': _numeric('db')},
    'turns': {
        'turn': _numeric('turn', 'index', 'number'),
        'id': _string('turn_id', 'message_id', 'id'),
        'timestamp': lambda r: _time(_get(r, 'start_utc', 'timestamp', 'created_at')),
        'step': _numeric('first_step', 'step'),
        'preview': _string('preview', 'text', 'prompt'),
        'chars': lambda r: _number(_get(r, 'chars', 'content_chars')) if _get(r, 'chars', 'content_chars') is not None else _length(_get(r, 'text', 'prompt', 'preview')),
        'line': _numeric('source_line', 'line', 'start_line', 'line_start'),
        'records': _numeric('records', 'record_count'),
        'source': _COMMON['source'],
    },
    'messages': {
        'index': _numeric('index', 'message', 'number'),
        'step': _numeric('step'),
        'turn': _numeric('turn'),
        'role': _string('role'),
        'timestamp': _COMMON['timestamp'],
        'chars': lambda r: _number(_get(r, 'chars', 'content_chars')) if _get(r, 'chars', 'content_chars') is not None else _length(_get(r, 'text')),
        'preview': _string('preview', 'text'),
        'id': _string('message_id', 'id'),
        'kind': _string('kind'),
        'line': _numeric('source_line', 'line'),
        'source': _COMMON['source'],
    },
    'responses': {
        'index': _numeric('index', 'response', 'message'),
        'step': _numeric('step'),
        'turn': _numeric('turn'),
        'user_message': _numeric('user_message', 'user_message_index', 'index'),
        'records': lambda r: _number(_get(r, 'records', 'record_count')) if _get(r, 'records', 'record_count') is not None else _length(_get(r, 'response')),
        'assistant_chars': _numeric('assistant_chars', 'a_chars', 'chars', 'content_chars'),
        'lines': _numeric('lines', 'response_lines'),
        'tokens': _numeric('tokens', 'response_tokens'),
        'actions': _numeric('actions', 'action_records', 'action_count'),
        'edited': _numeric('edited'),
        'created': _numeric('created'),
        'preview': _string('preview'),
        'timestamp': _COMMON['timestamp'],
        'source': _COMMON['source'],
    },
    'events': {
        'event': _string('event', 'type', 'schema', 'name', 'key'),
        'count': _numeric('count', 'total'),
        'status': _string('status', 'coverage'),
        'source': _COMMON['source'],
        'timestamp': _COMMON['timestamp'],
    },
    'audit': {**_COMMON, 'issue': _string('issue', 'reason'), 'count': _numeric('count')},
    'projects': {
        **_COMMON,
        'project': _string('project', 'name', 'title'),
        'count': _numeric('count', 'sessions', 'session_count'),
        'missing': _numeric('missing', 'missing_count'),
        'archived': _numeric('archived', 'archived_count'),
    },
}
_KIND_ALIASES = {'session': 'sessions', 'turn': 'turns', 'message': 'messages',
                 'response': 'responses', 'records': 'messages', 'record': 'messages', 'event': 'events', 'project': 'projects', 'audits': 'audit'}
_ALIASES = {'session_id': 'id', 'turn_id': 'id', 'message_id': 'id', 'cwd': 'workspace',
            'mtime': 'modified', 'bytes': 'size', 'date': 'timestamp', 'time': 'timestamp',
            'text': 'preview', 'prompt': 'preview', 'number': 'index', 'response': 'index',
            'message': 'index', 'length': 'chars', 'characters': 'chars',
            'type': 'event', 'schema': 'event', 'name': 'title', 'sessions': 'count',
            'action_records': 'actions', 'response_lines': 'lines', 'response_tokens': 'tokens'}
_DEFAULTS = {'sessions': 'recent', 'turns': 'turn', 'messages': 'index',
             'responses': 'index', 'events': 'event', 'audit': 'source', 'projects': 'recent'}
_DESCENDING = {'modified', 'size', 'count', 'records', 'assistant_chars', 'chars', 'lines',
               'tokens', 'actions', 'edited', 'created', 'missing', 'archived'}


def sort_fields(kind: str) -> tuple[str, ...]:
    """Return documented columns accepted by a listing kind."""
    key = _KIND_ALIASES.get(kind, kind)
    if key not in _FIELDS:
        raise ValueError(f'Unknown listing kind: {kind}')
    return tuple(_FIELDS[key])


def _sort_terms(kind: str, spec: str | None, order: str | None) -> list[tuple[str, bool]]:
    key = _KIND_ALIASES.get(kind, kind)
    fields = sort_fields(key)
    if order not in (None, '', 'auto', 'asc', 'desc'):
        raise ValueError('Sort order must be auto, asc, or desc.')
    raw = (spec or _DEFAULTS[key]).strip().casefold()
    if raw in ('native', 'original', 'source-order', 'none'):
        return []
    terms = []
    for part in raw.split(','):
        token = part.strip()
        if not token:
            raise ValueError('Sort fields cannot be empty. Use, for example, modified:desc,title:asc.')
        prefix = token[0] if token[0] in '+-' else ''
        token = token[1:] if prefix else token
        name, separator, direction = token.partition(':')
        if separator and direction not in ('asc', 'desc'):
            raise ValueError(f'Invalid sort direction {direction!r}; use asc or desc.')
        if prefix and separator:
            raise ValueError('Use either a +/- prefix or :asc/:desc for each sort field, not both.')
        alias_direction = None
        if name in ('recent', 'oldest'):
            name, alias_direction = 'modified' if key in ('sessions', 'projects', 'audit') else 'timestamp', 'desc' if name == 'recent' else 'asc'
        else:
            name = _ALIASES.get(name, name)
        if key == 'turns' and name == 'index':
            name = 'turn'
        if key == 'projects' and name == 'title':
            name = 'project'
        if name not in fields:
            raise ValueError(f'Unknown {kind} sort field {name!r}. Available: {", ".join(fields)}; recent, oldest, native. Use field:asc or field:desc, separated by commas.')
        selected = direction if separator else ('desc' if prefix == '-' else 'asc') if prefix else order if order in ('asc', 'desc') else alias_direction
        descending = (selected == 'desc') if selected else name in _DESCENDING
        terms.append((name, descending))
    return terms


def validate_sort(spec: str, *, kind: str = 'sessions', order: str | None = None) -> None:
    _sort_terms(kind, spec, order)


def sort_rows(rows: Iterable[Any], *, kind: str, sort: str | None = None,
              order: str | None = None) -> list[Any]:
    """Sort a copy, with stable ties and nulls last, retaining original objects/IDs.

    ``native`` retains input order.  ``recent``/``oldest`` are time aliases;
    comma-separated fields accept ``:asc``/``:desc`` or ``+``/``-`` prefixes.
    Explicit directions beat ``order``; ``auto`` uses sensible field defaults.
    """
    key = _KIND_ALIASES.get(kind, kind)
    terms = _sort_terms(key, sort, order)
    result = list(rows)
    for field, descending in reversed(terms):
        accessor = _FIELDS[key][field]
        present, missing = [], []
        for row in result:
            value = accessor(row)
            (missing if value is None else present).append(row if value is None else (value, row))
        present.sort(key=lambda item: item[0], reverse=descending)
        result = [item[1] for item in present] + missing
    return result


def _isatty(stream: Any) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def _terminal_size(stream: TextIO) -> os.terminal_size:
    try:
        size = os.get_terminal_size(stream.fileno())
    except (AttributeError, OSError, ValueError):
        size = shutil.get_terminal_size(fallback=(100, 30))
    return os.terminal_size((max(12, size.columns), max(6, size.lines)))


def _safe_line(value: str) -> str:
    # Unlike the progress preview helper, this preserves all printable content
    # and spacing. Stripping control sequences cannot truncate a source preview.
    return ''.join(ch if ch != '\t' else '    ' for ch in _ANSI.sub('', value)
                   if ch == '\t' or unicodedata.category(ch) not in ('Cc', 'Cf', 'Cs'))


def _wrap_lines(lines: Sequence[str], width: int) -> list[str]:
    """Wrap in terminal cells, with linear cost and no dropped characters."""
    result = []
    for line in lines:
        value = _safe_line(line)
        segment, cells = [], 0
        for ch in value:
            count = 0 if unicodedata.combining(ch) else 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
            if cells + count > width and segment:
                result.append(''.join(segment))
                segment, cells = [], 0
            segment.append(ch)
            cells += count
        result.append(''.join(segment))
    return result


SESSION_HEADERS = ('Session selector', 'Conversation title', 'JSONL size', 'Last modified',
                   'Transcript source', 'SQLite database', 'Project')


def _human_jsonl_size(value: Any) -> str:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return 'N/A'
    if value < 1024:
        return f'{value} B'
    amount = float(value)
    for unit in ('KiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB'):
        amount /= 1024
        if amount < 1024 or unit == 'EiB':
            return f'{amount:.1f} {unit}'
    raise AssertionError('Unreachable byte-size unit')


def _session_source(row: Mapping[str, Any]) -> str:
    if row.get('full'):
        return 'Full log'
    if row.get('chunked_full') and row.get('chunks_contiguous'):
        return 'Full chunks'
    if row.get('reduced'):
        return 'Reduced'
    return 'Missing (invalid chunks)' if row.get('chunked_full') else 'Missing'


def session_listing_lines(rows: Iterable[Mapping[str, Any]], *, full_titles: bool = False,
                          timezone_name: str = 'local', settings: DisplaySettings | None = None,
                          input_stream: TextIO | None = None,
                          output_stream: TextIO | None = None) -> list[str]:
    """Present source catalog metadata with readable units and labeled columns.

    Selectors are never abbreviated; JSON values are neither changed nor added.
    Pipes and --no-pager always receive the complete table. A narrow interactive
    pager uses labeled records when its terminal cannot accommodate all headers.
    """
    settings = settings or DisplaySettings()
    source = input_stream if input_stream is not None else sys.stdin
    target = output_stream if output_stream is not None else sys.stdout
    formatter = TimestampFormatter(True, timezone_name)
    values = []
    titles_clipped = False
    def text(value: Any, fallback: str = 'Not recorded') -> str:
        clean = _safe_line(' '.join(str(value).split())) if value is not None else ''
        return clean or fallback
    for row in rows:
        title = text(row.get('title'))
        if not full_titles and len(title) > 140:
            title = title[:139] + '…'
            titles_clipped = True
        modified = row.get('modified')
        missing_time = modified is None or (isinstance(modified, str) and not modified.strip())
        date = 'Not recorded' if missing_time else formatter.format(modified) or 'Unknown'
        database = 'Present' if row.get('db') is True else 'Missing' if row.get('db') is False else 'Unknown'
        values.append((text(row.get('selector') or row.get('id'), 'Unknown'), title,
                       _human_jsonl_size(row.get('size')), text(date), _session_source(row),
                       database, text(row.get('project') or row.get('project_id'))))
    widths = [max([_cells(header)] + [_cells(row[i]) for row in values])
              for i, header in enumerate(SESSION_HEADERS)]
    # Wrap verbose titles and project names without abbreviating identities.
    for index, limit in ((1, 60), (6, 40)):
        widths[index] = min(widths[index], limit)
    interactive = settings.pager != 'never' and _isatty(source) and _isatty(target)
    narrow = False
    if interactive:
        available = _terminal_size(target).columns - 1
        while sum(widths) + 2 * (len(widths) - 1) > available:
            flexible = [i for i in (1, 6) if widths[i] > _cells(SESSION_HEADERS[i])]
            if not flexible:
                narrow = True
                break
            widest = max(flexible, key=lambda i: widths[i] - _cells(SESSION_HEADERS[i]))
            widths[widest] -= 1
    if narrow and values:
        lines = ['Session catalog (narrow terminal: every column is labeled below)', '']
        for row in values:
            lines.extend(f'{header}: {cell}' for header, cell in zip(SESSION_HEADERS, row))
            lines.append('')
    else:
        def aligned(row: Sequence[str]) -> str:
            return '  '.join(cell + ' ' * max(0, width - _cells(cell))
                             for cell, width in zip(row, widths)).rstrip()
        lines = [aligned(SESSION_HEADERS), aligned(tuple('-' * width for width in widths))]
        for row in values:
            wrapped = [_wrap_lines([cell], width) for cell, width in zip(row, widths)]
            for index in range(max(len(cell) for cell in wrapped)):
                lines.append(aligned(tuple(cell[index] if index < len(cell) else '' for cell in wrapped)))
        if not values:
            lines.append('No sessions matched.')
        lines.append('')
    lines.extend((
        'JSONL size: available full log, otherwise contiguous full chunks (sum), otherwise reduced log; binary units (KiB/MiB/GiB).',
        'Last modified: recorded conversation catalog time, displayed in the configured timezone; not filesystem modification time.',
        'Transcript source and SQLite database describe file availability; history completeness is checked during audit/export.',
        'Use --json for exact byte counts, original timestamps and source metadata.'))
    if titles_clipped:
        lines.append('Title previews end with … when shortened; --full-session-titles displays the complete title.')
    return lines


_WINDOWS_KEYS = {'H': 'up', 'P': 'down', 'K': 'left', 'M': 'right',
                 'I': 'pageup', 'Q': 'pagedown', 'G': 'home', 'O': 'end', 'S': 'delete'}
_ESCAPE_KEYS = {'[A': 'up', '[B': 'down', '[C': 'right', '[D': 'left',
                '[5~': 'pageup', '[6~': 'pagedown', '[H': 'home', '[F': 'end',
                '[1~': 'home', '[4~': 'end', '[7~': 'home', '[8~': 'end',
                'OH': 'home', 'OF': 'end', '[3~': 'delete'}


class TerminalKeys:
    """Read console keys without altering shell state after exit or interruption."""

    def __init__(self, stream: TextIO):
        self.stream = stream
        self.fd: int | None = None
        self.previous: Any = None
        self.windows = os.name == 'nt'
        self._bytes = bytearray()

    def __enter__(self) -> 'TerminalKeys':
        if self.windows:
            import msvcrt
            self.console = msvcrt
        else:
            import termios
            import tty
            self.fd = self.stream.fileno()
            self.previous = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        return self

    def __exit__(self, *_: Any) -> None:
        if self.previous is not None and self.fd is not None:
            import termios
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.previous)
            self.previous = None

    def _character(self) -> str:
        if self.windows:
            char = self.console.getwch()
            if char in ('\x00', '\xe0'):
                return _WINDOWS_KEYS.get(self.console.getwch(), 'unknown')
            return char
        assert self.fd is not None
        first = os.read(self.fd, 1)
        if not first:
            return ''
        # os.read avoids TextIO buffering hiding bytes from select during escape
        # decoding. Decode a complete UTF-8 key so browser search accepts Unicode.
        count = 1 if first[0] < 0x80 else 2 if first[0] < 0xe0 else 3 if first[0] < 0xf0 else 4
        raw = bytearray(first)
        while len(raw) < count:
            next_byte = os.read(self.fd, 1)
            if not next_byte:
                break
            raw.extend(next_byte)
        return raw.decode(getattr(self.stream, 'encoding', None) or 'utf-8', errors='replace')

    def read(self) -> str:
        char = self._character()
        if char == '\x03':
            raise KeyboardInterrupt
        if char in ('', '\x04', '\x1a'):
            return 'eof'
        if char in ('\r', '\n'):
            return 'enter'
        if char in ('\x08', '\x7f'):
            return 'backspace'
        if char != '\x1b' or self.windows:
            return char
        assert self.fd is not None
        tail = ''
        for _ in range(8):
            if not select.select([self.fd], [], [], 0.04)[0]:
                break
            tail += self._character()
            if tail in _ESCAPE_KEYS:
                return _ESCAPE_KEYS[tail]
            if len(tail) > 1 and tail[-1].isalpha():
                break
        return 'escape'


def read_command(input_stream: TextIO, output_stream: TextIO, *, navigation: bool = True) -> str:
    """Line commands with instant page keys on consoles; ordinary lines in pipes.

    Arrow keys navigate only an empty prompt, so text selection commands remain
    exact. The caller prints/flushed its prompt before entering this function.
    """
    if not (_isatty(input_stream) and _isatty(output_stream)):
        line = input_stream.readline()
        if not line:
            raise EOFError
        return line.rstrip('\r\n')
    buffer: list[str] = []
    with TerminalKeys(input_stream) as keys:
        while True:
            key = keys.read()
            if key == 'eof':
                raise EOFError
            if key == 'enter':
                output_stream.write('\n')
                output_stream.flush()
                return ''.join(buffer)
            if not buffer and navigation and key in ('up', 'pageup', 'left', 'down', 'pagedown', 'right', 'home', 'end'):
                output_stream.write('\n')
                output_stream.flush()
                return {'up': 'p', 'pageup': 'p', 'left': 'p', 'down': 'n', 'pagedown': 'n',
                        'right': 'n', 'home': 'first', 'end': 'last'}[key]
            if key == 'backspace' and buffer:
                removed = buffer.pop()
                output_stream.write('\b \b' * max(1, _cells(removed)))
            elif len(key) == 1 and unicodedata.category(key) not in ('Cc', 'Cs'):
                buffer.append(key)
                output_stream.write(key)
            output_stream.flush()


def print_listing(lines: Iterable[str] | str, *, settings: DisplaySettings | None = None,
                  title: str = '', input_stream: TextIO | None = None,
                  output_stream: TextIO | None = None, json_output: bool = False,
                  key_reader: Callable[[], str] | None = None) -> None:
    """Print all rows to files/pipes; interactively page long terminal listings.

    ``always`` enters the pager even for short listings, but still requires both
    streams to be terminals. No input is consumed in scripts, JSON mode, or
    ``never`` mode. ``page_size`` is a maximum, bounded by current terminal height.
    The optional key_reader is a deterministic test seam, not a CLI dependency.
    """
    settings = settings or DisplaySettings()
    source = input_stream if input_stream is not None else sys.stdin
    target = output_stream if output_stream is not None else sys.stdout
    original = lines.splitlines() if isinstance(lines, str) else [str(line).rstrip('\r\n') for line in lines]
    terminal = _isatty(source) and _isatty(target)
    if json_output or settings.pager == 'never' or not terminal:
        try:
            for line in original:
                target.write(line + '\n')
            target.flush()
        except BrokenPipeError:
            return
        return
    size = _terminal_size(target)
    width = max(1, size.columns - 1)
    body = min(settings.page_size or max(1, size.lines - 4), max(1, size.lines - 4))
    wrapped = _wrap_lines(original, width)
    if len(wrapped) <= body and settings.pager != 'always':
        for line in wrapped:
            target.write(line + '\n')
        target.flush()
        return
    capable = os.environ.get('TERM', '').casefold() != 'dumb' and _windows_vt(target)
    offset = 0
    keys: TerminalKeys | None = None
    try:
        if key_reader is None:
            keys = TerminalKeys(source).__enter__()
            key_reader = keys.read
        if capable:
            target.write('\x1b[?1049h\x1b[?25l')
        while True:
            new_size = _terminal_size(target)
            if new_size != size:
                old_length = max(1, len(wrapped))
                fraction = offset / old_length
                size, width = new_size, max(1, new_size.columns - 1)
                wrapped = _wrap_lines(original, width)
                offset = int(fraction * len(wrapped))
            body = min(settings.page_size or max(1, size.lines - 4), max(1, size.lines - 4))
            offset = min(max(0, offset), max(0, len(wrapped) - body))
            if capable:
                target.write('\x1b[H\x1b[2J')
            label = f'{_safe_line(title) or "Antigravity listing"} | lines {offset + 1}-{min(offset + body, len(wrapped))}/{len(wrapped)}'
            target.write(_clip(label, width, True) + '\n')
            for line in wrapped[offset:offset + body]:
                target.write(line + '\n')
            if capable:
                for _ in range(max(0, body - len(wrapped[offset:offset + body]))):
                    target.write('\n')
            # Keep controls visible without relying on terminal-specific glyphs.
            footer = 'Enter/Space/PgDn next | PgUp previous | Up/Down line | Home/End | q quit'
            if width < len(footer):
                footer = 'Enter:more PgUp:back arrows Home/End q:quit'
            target.write(_clip(footer, width, True))
            target.flush()
            key = key_reader()
            if key in ('q', 'Q', 'escape', 'eof', '\x03'):
                break
            if key in ('enter', ' ', 'pagedown', 'right', 'n'):
                if offset + body >= len(wrapped):
                    break
                offset += body
            elif key in ('pageup', 'left', 'p', 'b'):
                offset -= body
            elif key in ('up', 'k'):
                offset -= 1
            elif key in ('down', 'j'):
                offset += 1
            elif key in ('home', 'g'):
                offset = 0
            elif key in ('end', 'G'):
                offset = max(0, len(wrapped) - body)
            if not capable:
                target.write('\n')
    except (KeyboardInterrupt, EOFError, BrokenPipeError):
        pass
    finally:
        if keys is not None:
            keys.__exit__(None, None, None)
        try:
            target.write('\x1b[?25h\x1b[?1049l' if capable else '\n')
            target.flush()
        except (OSError, ValueError):
            pass
