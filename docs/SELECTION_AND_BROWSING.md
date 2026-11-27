# Inclusive selections, browsing and recorded timestamps

## Select where a continuation starts

The same observed Antigravity user events supply `agy-turns` and the inclusive
`agy-since-*` selectors. Turn numbers remain 1-based and step coordinates remain
native, including step zero. Display sorting never renumbers them.

```powershell
agy-turns '11111111-2222-4333-8444-555555555555' -Sort turn:desc
agy-since-turn '11111111-2222-4333-8444-555555555555' 2
agy-since-text '11111111-2222-4333-8444-555555555555' 'distinctive prompt text'
agy-since-id '11111111-2222-4333-8444-555555555555' 'step:0'
agy-since-time '11111111-2222-4333-8444-555555555555' '2026-01-01T09:00:00-08:00'
```

The generic equivalent is `agy-since UUID 'turn:2'`. Recognized prefixes are
`turn:`, `id:`, `text:`, `time:` and the Antigravity-specific `step:` shorthand.
An unprefixed value is literal prompt text; an unprefixed integer is not
silently converted into a turn number. Typed `agy-since-text` always treats the
text literally, even if it starts with `turn:`. Text matching inspects the full
recorded prompt with normalized whitespace and Unicode case folding, not a
truncated preview or a regular expression.

When text or a native-ID prefix matches several distinct user turns, inspect
the candidates and supply `--since-occurrence N` / `-Occurrence N`. Occurrences
are 1-based in recorded chronology. Repeated prompts are not deduplicated just
because their strings match. Exact IDs take priority over native-ID prefixes.
Recorded `step:N` anchors require an exact step number; they are identified as
coordinates, not fabricated native message UUIDs.

An ISO date means midnight in the machine's local timezone. A clock timestamp
requires `Z` or an explicit offset. Missing/invalid/naive recorded timestamps do
not participate in instant comparison. The first user turn at or after the
threshold is selected; the exporter does not invent a timestamp for missing
records. Selections include the chosen user turn and its chronological suffix.
Mutually incompatible lower bounds or per-message selectors are rejected.

The selection manifest records the resolved turn/step, captured source offset,
source-record SHA-256, prompt-text SHA-256, match count and occurrence. It records
the query's hash instead of copying a potentially sensitive search phrase into
extra provenance. Selected native JSONL is drawn from the captured source, with
its scope documented separately from rendered-mode filtering.

## List and browse

`agy-sessions` displays descriptive headers for the session selector, conversation
name, JSONL size, last-modified time, transcript source, SQLite database and
project. A full variant-qualified selector remains available for exact selection.
`--no-pager` and redirected output always use an aligned table; a narrow
interactive pager can use labeled records so that no column or identity disappears.

JSONL sizes use binary units (KiB, MiB, GiB) in the human view. They describe the
full monolithic log when present, otherwise the sum of contiguous full-log
chunks, otherwise an available reduced log. A missing usable source has no
invented size; zero bytes is a real empty source. For ZIP inputs, the value is
the member's uncompressed size, not its compressed storage size. Discovery
reads filesystem/ZIP metadata rather than scanning every transcript. JSON
output retains the exact integer `size` and its `size_basis`.

Last modified is the **recorded conversation-catalog timestamp**, not the
filesystem modification time of an export. Dates and times use the readable export timestamp format, for example
`November 27, 2026 at 11:09:16 AM PST`, and honor the saved `timestamp_timezone`
preference (local time by default). A timestamp with no recorded offset is
labeled as such. Missing and invalid dates are explicit, and the original
`modified` value remains in JSON. A full-log source means that file/chunk representation exists; it does
not certify that every SQLite step is present in readable JSONL.


```powershell
agy-sessions -Sort 'project:asc,modified:desc'
agy-turns UUID -Sort turn:desc -PageSize 20
agy-list UUID -Sort 'turn:asc,step:asc' -Pager never
agy-browse
```

Sort accepts comma-separated fields with `:asc`/`:desc` or `+`/`-` prefixes.
`recent`, `oldest`, and `native` are available aliases. Numeric fields sort as
numbers; absent values sort last in either direction. Fields are validated for
the listing kind. Interactive paging uses terminal height and restores terminal
state when finished; redirected/JSON output remains complete and consumes no
pager input.


On capable terminals, a long listing opens an alternate screen. Pressing `q`
closes that screen and restores the terminal contents from before the listing;
the rows are therefore absent from ordinary scrollback. This does not mean
session discovery returned an empty catalog. To retain all rows in scrollback:

```powershell
agy-sessions --no-pager
agy-projects --no-pager
```

To make ordinary scrollback listings the saved default, use
`agy-config --set pager=never`. Restore automatic paging with
`agy-config --set pager=auto`; override either preference for one invocation
with `--pager auto` or `--no-pager`. JSON remains complete independently:

```powershell
agy-sessions --json --no-progress | ConvertFrom-Json
```

The session browser supports search, page-relative rows/ranges, project views,
selection baskets, sorting, refresh and inspection. It renders to stderr so
stdout remains usable for final archive paths or JSON. Variant-qualified
selectors preserve distinct source identities. A catalog-only row cannot be
selected for export through the browser by pretending that a full transcript
exists. Browser status `available` means a complete recorded transcript source
is present, not that the Antigravity application is currently running.

## Timestamps

Recorded event timestamps are displayed by default. `--no-timestamps` disables
the human-readable labels while retaining original native fields. Use
`--timestamp-timezone local`, `UTC`, `source`, a fixed offset, or an available
IANA zone such as `America/Los_Angeles`. IANA zones may require an installed
`tzdata` database on Windows; local timezone conversion uses the operating
system and does not require an extra dependency.

Local conversion uses the offset at the recorded event instant, including DST,
not the current offset. Epoch zero remains valid. A timestamp without an offset
is labeled “timezone unspecified”; a date without a time is identified as such.
Missing or invalid timestamps are omitted, and export creation time is never
substituted for recorded event time.
