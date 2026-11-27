# Project exports, source order and repeated native steps

## Why a project export could stop in 0.4.1

The v0.4.1 event index required `step_index` to increase strictly and stored it
under a SQLite `UNIQUE` constraint. A valid JSONL observation with a repeated or
lower native step therefore stopped the entire requested export. Source capture
or transcript-chunk checks could finish before that independent indexing error.
Removing native/artifact/review options could not repair the assumption.

Version 0.4.2 uses the captured source ordinal as the unique record identity.
Native step numbers remain exactly as recorded. This supports repeated and
backward identifiers without guessing whether their records are revisions,
replays, branches, retries or damaged source data.

## Preservation and diagnostics

- Every valid source observation is indexed and rendered in captured byte order.
  Full native JSONL retains the entire verified captured prefix, including original
  whitespace, line endings, disclosed NUL padding and an incomplete captured tail.
- Partial native JSONL contains the selected original byte ranges. No source record
  is silently deduplicated, rewritten, sorted or assigned a replacement native step.
- `source_coverage.step_order` records distinct steps, repeated identifiers, repeated
  occurrences, backward transitions, and examples with source ordinal/offset/hash.
  Examples are bounded to 32 per anomaly class; counters and omitted-example counts
  cover the full indexed input.
- Duplicate comparison uses the SHA-256 of the entire original line, including its
  JSON whitespace and line ending. Identical/different source bytes do not establish
  semantic equivalence, replay, revision or final application state.
- Missing native-step ranges are calculated from the distinct identifiers present
  anywhere in the capture. A later lower identifier can close an apparent gap.
- Each explicit recorded user-input occurrence starts an observed user turn.
  Repeated user input is not silently collapsed. Source order is established;
  actual event chronology and application replay semantics are not inferred.

Ordinary malformed complete JSONL still raises an error. This repair does not
require `--skip-invalid`, `--allow-reduced`, or `--missing-source skip`. An explicit
strict-order workflow can reject repeated/backward native steps:

```powershell
agy-project 'example-project' --strict-step-order
```

Errors include the qualified session, source path and applicable record/byte
coordinates. Add `--keep-failed-work` when a private staged capture is needed for
local diagnosis. Without it, only exporter-owned temporary staging is removed.
Application-owned files and existing exports are preserved.

## Selection semantics

| Selector | Meaning with repeated or backward native steps |
|---|---|
| `--step N` | Every recorded occurrence whose native step equals N, in source order |
| `--from-step A --to-step B` | Numeric native-step filter, inclusive; matching observations retain source order |
| `--message N` | Exactly one 1-based source message ordinal |
| `--from-message A --to-message B` | Inclusive source-ordinal interval |
| `--since-turn N` | Chosen observed user occurrence and its source-order suffix |
| `--since-id step:N` | Exact native user-step anchor; repeated matches require `--since-occurrence` |
| `--last-n-steps N` | Last N matching source observations, not the numerically largest native IDs |

The selection manifest records both native ranges and source ordinals. An
observed turn's `first_step` and `last_step` are source endpoints; `min_step` and
`max_step` give the numeric native range. A filtered view can have disjoint source
ordinals. The export map and per-record headings retain the original identities.

## Database coverage is separate from record identity

`database_audit.step_sets_match` means that the distinct JSONL and SQLite native
identifier sets agree. It does not prove one row per observation or matching
payloads. `one_to_one_step_match` additionally requires one JSONL occurrence for
each SQLite identifier. The compatibility field `matched_exactly` reflects this
identifier correspondence; `payloads_compared` is false.

For example, JSONL steps `[0, 7, 8, 7]` and SQLite IDs `[0, 7, 8]` have equal sets,
but four readable observations versus three native identifiers. The exporter
preserves all four and reports the distinction. A successful archive integrity
check does not imply that readable history covers every SQLite record.

Full-record exports retain all DB-only diagnostics, including historical indices
before the first readable source event. Those older quota rows are not assigned
to later recorded user turns.

When native IDs repeat or go backward, DB-only diagnostic rows cannot safely be
interleaved among readable occurrences or assigned to an observed turn:

- A full-record view appends all DB-only diagnostics as separate session evidence,
  sorted by native identifier for presentation, with no claimed temporal placement.
- A partial view omits those payloads because membership in the selected occurrences
  is unknown. Counts and the omission reason remain in the manifest.
- Quota error evidence remains available globally. The numerically highest ID is
  labeled as such, not asserted to be the latest event. No turn is declared
  interrupted solely by mapping an ambiguous native step interval.
- Explicit `--include-native` still preserves the whole session's database snapshot,
  even for a partial readable view. That broader scope is disclosed separately.

## Tool-output sidecars with repeated native steps

A recorded full-output path is keyed by native step. If that step has multiple
source occurrences, the path alone cannot identify which occurrence it belongs
to. Full-record exports preserve the sidecar once in an unassigned evidence
appendix, leaving every original preview unchanged. Partial views omit ambiguous
sidecar content and record why. Unique-step sidecars retain ordinary recovery.
Explicit `--include-artifacts` captures all accessible files in the chosen session
store and does not claim an occurrence-level association.

## Comprehensive project export

```powershell
agy-project 'example-project' `
    --mode thread `
    --tool-outputs full `
    --include-thinking `
    --archive `
    --copy-source `
    --include-native `
    --include-artifacts `
    --verify-db `
    --verify-chunks `
    --handoff `
    --review `
    --collision rename
```

The default groups selected sessions into one verified archive set. Every final
ZIP remains within the configured cap. Review parts preserve rendered Markdown
bytes and are not a guarantee that a receiving application ingests all content.
The handoff index describes source evidence; it is not a continuation prompt.

Both archived and `--no-archive` project/batch paths prepare every selected source
before publishing. Loose output remains one `.files` directory per session.
A later source-validation failure no longer publishes earlier loose sessions.
This preparation boundary is not a claim of atomic multi-directory publication
under an external filesystem failure or concurrent writer.

Repeated stable-name exports recognize existing ordinary and digest-shortened
multipart names. `--collision rename` chooses another stem; `skip` and `error`
respect the existing set. Existing archive parts are never replaced.

## Token-counting performance

The regex estimate now scans 65,536-character Unicode chunks and updates progress
once per chunk. A word crossing a chunk boundary is counted once. The line count,
UTF-8 byte count and regex estimate retain their previous definitions; this is
not an exact Gemini/GPT billing count. A very long line no longer dictates the
regex pass's allocation size. Exact `tiktoken` mode retains its explicit size limit
and separate encoding semantics.

See [validation evidence](VALIDATION.md) for measured local results, regression
coverage and the limits of testing without a user's current source files.
