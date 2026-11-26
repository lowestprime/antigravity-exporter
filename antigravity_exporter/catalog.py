"""Read recorded catalog identities without confusing catalog and storage locations.

The hub adapter is deliberately narrow: it reads the observed protobuf map layout
for catalog metadata, never decodes trajectory payloads or invents transcript text.
Every adapted record retains its entry and source checksums plus field inventory.
Original catalog bytes stay in the app data; they are not put in shared metadata. See docs/SOURCE_RESOLUTION.md.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .storage import ExportError, Source, UUID_RE, VARIANTS

CATALOG_LIMIT = 32 * 1024 * 1024
FIELD_LIMIT = 200000
HUB_SCHEMA = 'observed-antigravity-summary-map-v1'


def _fields(data: bytes):
    """Parse one bounded wire message, rejecting truncation and unsupported groups."""
    if len(data) > CATALOG_LIMIT:
        raise ExportError('Catalog protobuf exceeds the bounded metadata limit')
    pos = 0
    result = defaultdict(list)

    def varint():
        nonlocal pos
        value = 0
        for shift in range(0, 70, 7):
            if pos >= len(data):
                raise ExportError('Truncated catalog protobuf varint')
            byte = data[pos]
            pos += 1
            if shift == 63 and byte > 1:
                raise ExportError('Invalid catalog protobuf varint')
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ExportError('Oversized catalog protobuf varint')

    count = 0
    while pos < len(data):
        count += 1
        if count > FIELD_LIMIT:
            raise ExportError('Catalog protobuf field limit exceeded')
        tag = varint()
        number, wire = tag >> 3, tag & 7
        if not 1 <= number <= 536870911:
            raise ExportError('Invalid catalog protobuf field number')
        if wire == 0:
            value = varint()
        elif wire in (1, 2, 5):
            size = varint() if wire == 2 else (8 if wire == 1 else 4)
            if size > len(data) - pos:
                raise ExportError('Truncated catalog protobuf field')
            value = data[pos:pos + size]
            pos += size
        else:
            raise ExportError('Unsupported catalog protobuf wire type')
        result[number].append((wire, value))
    return result


def _one(fields, number, wire, default=None):
    values = fields.get(number, [])
    if not values:
        return default
    if len(values) != 1 or values[0][0] != wire:
        raise ExportError(f'Catalog field {number} does not match the supported summary layout')
    return values[0][1]


def _text(fields, number, default=''):
    raw = _one(fields, number, 2)
    if raw is None:
        return default
    try:
        value = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ExportError(f'Catalog text field {number} is not UTF-8') from exc
    if '\x00' in value:
        raise ExportError(f'Catalog text field {number} contains NUL')
    return value


def _timestamp(raw):
    if raw is None:
        return ''
    fields = _fields(raw)
    seconds = _one(fields, 1, 0, 0)
    nanos = _one(fields, 2, 0, 0)
    if not 0 <= seconds <= 253402300799 or not 0 <= nanos < 1000000000:
        raise ExportError('Catalog timestamp is outside the supported UTC range')
    stamp = datetime.fromtimestamp(seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    return stamp + (f'.{nanos:09d}'.rstrip('0') if nanos else '') + 'Z'


def modified_key(value):
    """Order SQLite and hub times consistently on Python 3.10 and newer.

    Python 3.10 rejects fractional widths other than three or six digits.
    Normalize the parser input to six digits and retain the sub-microsecond
    suffix separately. Removing trailing zeroes makes equal instants compare
    equally without losing recorded nanosecond ordering.
    """
    minimum = (datetime.min.replace(tzinfo=timezone.utc), '')
    if not value:
        return minimum
    text = str(value).strip()
    submicrosecond = ''
    fraction = re.fullmatch(r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})[.,](\d+)(Z|[+-]\d{2}:\d{2})?', text)
    if fraction:
        digits = fraction.group(2)
        submicrosecond = digits[6:].rstrip('0')
        text = fraction.group(1) + '.' + digits[:6].ljust(6, '0') + (fraction.group(3) or '')
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        utc = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        return utc, submicrosecond
    except (ValueError, OverflowError):
        return minimum


def read_hub_catalog(src: Source, variant: str):
    """Adapt observed map entries; UUID keys and known field types are mandatory."""
    records = []
    for filename in ('agyhub_summaries_proto.pb', 'jetbox_summaries_proto.pb'):
        relative = f'{variant}/{filename}'
        if not src.exists(relative):
            continue
        size = src.entries[relative].file_size if src.z else src.path(relative).stat().st_size
        if size > CATALOG_LIMIT:
            raise ExportError(f'Catalog exceeds {CATALOG_LIMIT} bytes: {relative}')
        path, capture = src.snapshot(relative)
        root = _fields(path.read_bytes())
        for wire, entry in root.get(1, []):
            if wire != 2:
                raise ExportError(f'Unsupported summary map entry in {relative}')
            fields = _fields(entry)
            ident = _text(fields, 1)
            value = _one(fields, 2, 2)
            if not UUID_RE.fullmatch(ident) or value is None:
                raise ExportError(f'Unsupported summary map identity in {relative}')
            summary = _fields(value)
            metadata_raw = _one(summary, 17, 2, b'')
            metadata = _fields(metadata_raw)
            parent = _text(metadata, 5)
            root_id = _text(metadata, 6)
            for name, uuid in (('parent', parent), ('root', root_id)):
                if uuid and not UUID_RE.fullmatch(uuid):
                    raise ExportError(f'Invalid {name} UUID in {relative}')
            agent = _fields(_one(metadata, 8, 2, b''))
            workspaces = []
            for workspace_wire, workspace in summary.get(9, []):
                if workspace_wire != 2:
                    raise ExportError(f'Unsupported workspace field in {relative}')
                folder = _text(_fields(workspace), 1)
                if folder:
                    workspaces.append(folder)
            project_id = _text(metadata, 18)
            records.append({
                'id': ident.lower(), 'variant': variant,
                'title': _text(summary, 1),
                'step_count': _one(summary, 2, 0),
                'last_modified_time': _timestamp(_one(summary, 3, 2)),
                'project_id': project_id,
                'workspace_uris': json.dumps(workspaces, ensure_ascii=False),
                'parent_conversation_id': parent.lower(),
                'root_conversation_id': root_id.lower(),
                'agent_name': _text(agent, 1),
                'agent_description': _text(agent, 2),
                'nesting_depth': _one(metadata, 17, 0),
                '_provenance': {
                    'source': relative, 'catalog_variant': variant,
                    'storage_variant': variant, 'adapter': HUB_SCHEMA,
                    'schema_status': 'observed-layout; not an official semantic schema',
                    'source_sha256': capture['sha256'], 'source_bytes': capture['bytes'],
                    'entry_sha256': hashlib.sha256(entry).hexdigest(),
                    'raw_catalog_embedded': False,
                    'summary_field_numbers': sorted(summary),
                    'metadata_field_numbers': sorted(metadata),
                    'transcript_content_decoded': False,
                },
            })
    return records


def read_sqlite_catalog(src: Source, catalog_variant: str):
    relative = f'{catalog_variant}/conversation_summaries.db'
    if not src.exists(relative):
        return []
    path, capture = src.snapshot(relative, database=True)
    con = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        con.execute('PRAGMA query_only=ON')
        con.execute('PRAGMA trusted_schema=OFF')
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'conversation_summaries' not in tables:
            return []
        columns = {row[1] for row in con.execute('PRAGMA table_info(conversation_summaries)')}
        wanted = [key for key in ('conversation_id', 'title', 'project_id', 'step_count',
                  'last_modified_time', 'workspace_uris', 'status', 'parent_conversation_id',
                  'not_fully_idle', 'app_data_dir', 'agent_name', 'nesting_depth') if key in columns]
        if 'conversation_id' not in wanted:
            raise ExportError('Summary DB has no conversation_id column')
        records = []
        for row in con.execute('SELECT ' + ','.join(wanted) + ' FROM conversation_summaries'):
            values = dict(row)
            ident = values.pop('conversation_id')
            if not isinstance(ident, str) or not UUID_RE.fullmatch(ident):
                raise ExportError(f'Invalid conversation UUID in {relative}')
            hint = values.get('app_data_dir') or catalog_variant
            if hint not in VARIANTS:
                raise ExportError(f'Unsupported app_data_dir {hint!r} in {relative}; no external path was followed')
            records.append({
                **values, 'id': ident.lower(), 'variant': hint,
                '_provenance': {
                    'source': relative, 'catalog_variant': catalog_variant,
                    'storage_variant': hint, 'adapter': 'sqlite-conversation-summaries',
                    'source_sha256': capture['sha256'], 'source_bytes': capture['bytes'],
                    'location_rule': 'recorded app_data_dir' if values.get('app_data_dir') else 'catalog location; app_data_dir absent',
                    'recorded_fields': values,
                },
            })
        return records
    finally:
        con.close()


def ordered_chunks(src: Source, prefix: str, *, validate=True):
    """Chunks are byte slices, not JSONL records; sort numerically before joining."""
    names = [name for name in src.files(prefix) if name.endswith('.jsonl')]
    pairs = []
    for name in names:
        suffix = name[len(prefix):]
        stem = Path(suffix).stem
        if '/' in suffix or not stem.isascii() or not stem.isdigit():
            if validate:
                raise ExportError('Full transcript chunks have unexpected names')
            return names, False
        pairs.append((int(stem), name))
    pairs.sort()
    contiguous = bool(pairs) and [number for number, _ in pairs] == list(range(len(pairs)))
    if validate and pairs and not contiguous:
        raise ExportError('Full transcript chunk numbering has gaps or duplicate numeric names')
    return [name for _, name in pairs], contiguous


def has_full_transcript(session):
    """Whether discovery found a monolith or valid contiguous full-log chunks."""
    return bool(session.get('full') or
                (session.get('chunked_full') and session.get('chunks_contiguous')))


def has_transcript(session, *, allow_reduced=False):
    """Shared availability policy for export preflight, audit and browser rows."""
    return has_full_transcript(session) or bool(allow_reduced and session.get('reduced'))


def transcript_availability(src: Source, variant: str, ident: str):
    prefix = f'{variant}/brain/{ident}/.system_generated/logs/'
    full = src.exists(prefix + 'transcript_full.jsonl')
    reduced = src.exists(prefix + 'transcript.jsonl')
    chunks, contiguous = ordered_chunks(src, prefix + 'chunks/transcript_full/', validate=False)
    if full:
        status = 'full'
    elif chunks and contiguous:
        status = 'full-chunks'
    elif chunks:
        status = 'invalid-full-chunks'
    elif reduced:
        status = 'reduced-only'
    else:
        status = 'catalog-only'
    def member_size(name):
        return src.entries[name].file_size if src.z else src.path(name).stat().st_size
    size = (member_size(prefix + 'transcript_full.jsonl') if full else
            sum(member_size(name) for name in chunks) if chunks and contiguous else
            member_size(prefix + 'transcript.jsonl') if reduced else None)
    return {'full': full, 'reduced': reduced, 'chunked_full': bool(chunks),
            'size': size, 'size_basis': ('recorded ZIP member size' if src.z else 'source file stat at discovery'),
            'chunks_contiguous': contiguous, 'source_availability': status,
            'db': src.exists(f'{variant}/conversations/{ident}.db')}


def discover_sessions(src: Source):
    projects = {}
    for name in src.files('config/projects/'):
        if not name.endswith('.json'):
            continue
        doc = json.loads(src.read_small(name))
        if isinstance(doc, dict):
            project_id, project_name = doc.get('id', Path(name).stem), doc.get('name', '')
            if isinstance(project_id, str) and isinstance(project_name, str):
                projects[project_id] = project_name
    candidates = defaultdict(list)
    variants = src.variants()
    for variant in variants:
        for row in read_sqlite_catalog(src, variant) + read_hub_catalog(src, variant):
            candidates[(row['variant'], row['id'])].append(row)
        if src.z:
            names = src.files(variant + '/conversations/') + src.files(variant + '/brain/')
            pattern = re.compile(re.escape(variant) + r'/(?:conversations/([a-fA-F0-9-]{36})\.(?:db|pb)$|brain/([a-fA-F0-9-]{36})/)')
            for name in names:
                match = pattern.match(name)
                if match:
                    ident = match.group(1) or match.group(2)
                    if UUID_RE.fullmatch(ident):
                        candidates.setdefault((variant, ident.lower()), [])
        else:
            for folder in ('brain', 'conversations'):
                base = src.path(f'{variant}/{folder}')
                if base.is_dir():
                    for entry in base.iterdir():
                        ident = entry.name if entry.is_dir() else entry.stem
                        if UUID_RE.fullmatch(ident):
                            candidates.setdefault((variant, ident.lower()), [])
    result = []
    for (variant, ident), records in candidates.items():
        if src.variant_hint and variant != src.variant_hint:
            continue
        # Prefer the most recent recorded metadata; equal timestamps prefer the
        # source's hub catalog, then stable source names. No directory priority.
        records.sort(key=lambda row: (modified_key(row.get('last_modified_time')),
                     row['_provenance']['adapter'] == HUB_SCHEMA,
                     row['_provenance']['source']), reverse=True)
        fields = {}
        conflicts = {}
        for key in ('title', 'project_id', 'step_count', 'last_modified_time', 'workspace_uris',
                    'status', 'parent_conversation_id', 'root_conversation_id', 'agent_name',
                    'agent_description', 'nesting_depth'):
            present = [row[key] for row in records if row.get(key) not in (None, '', '[]')]
            if present:
                fields[key] = present[0]
                distinct = list(dict.fromkeys(present))
                if len(distinct) > 1:
                    conflicts[key] = {'selected': present[0], 'recorded_values': distinct,
                                      'rule': 'most recent recorded metadata'}
        annotation = src.read_small(f'{variant}/annotations/{ident}.pbtxt')
        title = None
        if annotation is not None:
            text = annotation.decode('utf-8')
            match = re.search(r'\btitle\s*:\s*("(?:[^"\\]|\\.)*")', text)
            if match:
                try:
                    title = json.loads(match.group(1))
                except ValueError:
                    pass
        project_id = fields.get('project_id', '')
        row = {
            'id': ident, 'selector': f'{variant}:{ident}', 'variant': variant,
            'title': title or fields.get('title') or fields.get('agent_name') or ident,
            'project': projects.get(project_id, ''), 'project_id': project_id,
            'project_assignment': 'recorded catalog project_id' if project_id else 'unavailable',
            'summary_step_count': fields.get('step_count'),
            'modified': str(fields.get('last_modified_time', '')),
            'workspace_uris': fields.get('workspace_uris', ''),
            'catalog_status': fields.get('status'),
            'parent_conversation_id': fields.get('parent_conversation_id', ''),
            'root_conversation_id': fields.get('root_conversation_id', ''),
            'agent_name': fields.get('agent_name', ''),
            'agent_description': fields.get('agent_description', ''),
            'nesting_depth': fields.get('nesting_depth'),
            'catalog_sources': [r['_provenance']['source'] for r in records],
            'metadata_provenance': [r['_provenance'] for r in records],
            'metadata_conflicts': conflicts,
            **transcript_availability(src, variant, ident),
        }
        result.append(row)
    return sorted(result, key=lambda row: (modified_key(row['modified']), row['id'], row['variant']), reverse=True)
