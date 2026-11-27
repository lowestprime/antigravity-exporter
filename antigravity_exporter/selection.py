"""Inclusive user-turn selection from the captured Antigravity event index.

Selectors inspect original user events, never preview strings or invented model
context. Ambiguous text requires a 1-based occurrence. The same anchor table is
used by listings, selection and the recorded provenance.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
import hashlib
import json
import re
import unicodedata

from .storage import ExportError
from .timestamps import parse_source_timestamp


def preview(text: str, limit: int = 240) -> str:
    safe = ''.join(' ' if ch.isspace() else ch for ch in text
                   if ch.isspace() or unicodedata.category(ch) not in ('Cc', 'Cf', 'Cs'))
    safe = ' '.join(safe.split())
    return safe if len(safe) <= limit else safe[:max(0, limit - 1)] + '…'


def _normal(text: str) -> str:
    return ' '.join(text.split()).casefold()


def parse_since_time(value: str) -> datetime:
    """A date is local midnight; a clock time must specify its UTC offset."""
    value = str(value).strip()
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return datetime.combine(date.fromisoformat(value), time.min).astimezone()
        parsed = datetime.fromisoformat(value[:-1] + '+00:00' if value.endswith(('Z', 'z')) else value)
    except ValueError as exc:
        raise ExportError('Use an ISO date (YYYY-MM-DD) or timestamp with Z or a UTC offset.') from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExportError('A --since-time clock value requires Z or an explicit UTC offset; a date means local midnight.')
    return parsed


def _anchors(index):
    """Yield one original user record at a time; memory is bounded by one record."""
    turns = {row['first_ordinal']: row for row in index.turns}
    with index.source.open('rb') as stream:
        for ordinal, step, offset, size, digest in index.con.execute(
                "SELECT ord,step,offset,size,sha256 FROM events WHERE source='USER_EXPLICIT' AND kind='USER_INPUT' ORDER BY ord"):
            stream.seek(offset)
            raw = stream.read(size)
            if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
                raise ExportError('Captured user record changed after indexing; selection stopped.')
            record = json.loads(raw)
            content = record.get('content', '')
            text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, sort_keys=True)
            native_ids = []
            for key in ('id', 'message_id', 'turn_id', 'user_message_id', 'client_message_id'):
                value = record.get(key)
                if isinstance(value, str) and value and value not in native_ids:
                    native_ids.append(value)
            turn = turns.get(ordinal)
            if turn is None:
                raise ExportError('User-turn metadata disagrees with the indexed event coordinates.')
            yield {**turn, 'anchor_id': f'step:{step}', 'native_ids': native_ids,
                   'preview': preview(text), 'chars': len(text),
                   'text_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                   'source_sha256': digest, 'source_offset': offset, 'source_bytes': size}, text


def turn_listing(index):
    """Public turn metadata, retaining native ordinals even when display is sorted."""
    return [anchor for anchor, _ in _anchors(index)]


def resolve_since(index, args):
    """Return selection audit and set args.from_message to the inclusive user anchor.

    Only one of --since, --since-turn, --since-id, --since-text and --since-time
    may be specified. Existing upper bounds may narrow the suffix; competing
    lower-bound or per-message selectors are rejected, not silently combined.
    """
    selectors = [(name, getattr(args, name, None)) for name in
                 ('since', 'since_turn', 'since_id', 'since_text', 'since_time')]
    supplied = [(name, value) for name, value in selectors if value is not None]
    occurrence = getattr(args, 'since_occurrence', None)
    if not supplied:
        if occurrence is not None:
            raise ExportError('--since-occurrence requires a --since selector.')
        return None
    if len(supplied) != 1:
        raise ExportError('Specify exactly one --since selector.')
    if occurrence is not None and (type(occurrence) is not int or occurrence < 1):
        raise ExportError('--since-occurrence must be a positive 1-based integer.')
    if getattr(args, 'mode', 'thread') not in ('thread', 'chat', 'chat-actions', 'actions'):
        raise ExportError('--since selectors require thread, chat, chat-actions or actions mode.')
    for name in ('turn', 'message', 'from_message', 'from_step', 'step', 'last_n_turns', 'last_n_steps'):
        if getattr(args, name, None) is not None:
            raise ExportError('--since cannot be combined with --' + name.replace('_', '-') + '.')
    option, value = supplied[0]
    if option == 'since':
        raw = str(value).strip()
        prefix, separator, body = raw.partition(':')
        kind = prefix.casefold() if separator and prefix.casefold() in ('turn', 'id', 'text', 'time', 'step') else 'text'
        query = body.strip() if separator and kind == prefix.casefold() else raw
        if kind == 'step':
            kind, query = 'id', 'step:' + query
    else:
        kind, query = option.removeprefix('since_'), str(value).strip()
    if not query:
        raise ExportError('The --since selector must not be empty.')
    if kind == 'turn' and (not query.isdecimal() or int(query) < 1):
        raise ExportError('--since-turn must be a positive user-turn number.')
    threshold = parse_since_time(query).astimezone(timezone.utc) if kind == 'time' else None
    needle = _normal(query) if kind == 'text' else ''
    matches, prefix_matches = [], []
    for anchor, text in _anchors(index):
        matched = False
        if kind == 'turn':
            matched = anchor['turn'] == int(query)
        elif kind == 'id':
            identities = [anchor['anchor_id'], *anchor['native_ids']]
            matched = query in identities
            # Recorded step coordinates are exact; numeric-prefix collisions are
            # not treated as a matching native identifier.
            if not matched and not query.startswith('step:') and any(value.startswith(query) for value in anchor['native_ids']):
                prefix_matches.append(anchor)
        elif kind == 'time':
            stamp = parse_source_timestamp(anchor.get('start_utc'))
            matched = (stamp is not None and stamp.tzinfo is not None and stamp.utcoffset() is not None
                       and stamp.astimezone(timezone.utc) >= threshold)
        elif kind == 'text':
            matched = needle in _normal(text)
        if matched:
            matches.append(anchor)
            if kind == 'time':
                break  # First recorded user occurrence at/after the instant in source order.
    if not matches and kind == 'id':
        matches = prefix_matches
    if not matches:
        raise ExportError('No recorded user turn matches the selector; inspect agy-turns UUID first.')
    if occurrence is not None and occurrence > len(matches):
        raise ExportError(f'Occurrence {occurrence} exceeds {len(matches)} matching user turns.')
    if len(matches) > 1 and occurrence is None:
        candidates = '\n'.join(f"  turn:{m['turn']} | id:{m['anchor_id']} | {m.get('start_utc') or 'no timestamp'} | {m['preview']}" for m in matches[:20])
        raise ExportError(f'Selector matches {len(matches)} user turns. Use --since-occurrence N or an exact turn number.\n{candidates}')
    anchor = matches[(occurrence or 1) - 1]
    args.from_message = anchor['first_ordinal']
    return {'kind': kind, 'query_sha256': hashlib.sha256(query.encode('utf-8')).hexdigest(),
            'occurrence': occurrence, 'matching_turns': len(matches), 'inclusive': True,
            'turn': anchor['turn'], 'first_step': anchor['first_step'],
            'first_ordinal': anchor['first_ordinal'], 'selection_coordinate': 'source_ordinal',
            'anchor_id': anchor['anchor_id'], 'source_offset': anchor['source_offset'],
            'source_sha256': anchor['source_sha256'], 'text_sha256': anchor['text_sha256']}
