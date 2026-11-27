# Codex Exporter parity and source audit

Comparison date: **2026-11-26**. This is a pinned comparison, not an assertion that
later upstream commits are already included.

| Repository | Reference | Tracked files | Bytes | UTF-8 lines |
|---|---|---:|---:|---:|
| Codex Exporter v0.5.2 | `acd322d1987f1078b6f79dcba62c898704dcc5ba` | 144 | 2,544,397 | 51,077 |
| Antigravity Exporter baseline v0.3.0rc3 | `11598ac1534f8892baf2cf5d60230593e6e51924` | 44 | 266,816 | 4,617 |

`REFERENCE_INVENTORY.json` inventories every tracked Git blob at both references:
path, byte length, SHA-256, text line count, Python AST function/class counts, and
literal argparse option declarations. Codex's separately distributed skill scripts
are included, even where they duplicate package code. Reproduce this read-only
inventory with `tools/audit_reference.py`. Hash and lexical coverage is not a
semantic proof that every possible behavior has been tested.

A mechanical line-to-line correspondence between independent source formats is
not useful: Antigravity stores trajectories/steps, native SQLite/protobuf records,
annotations, reduced text and generated brain artifacts, while Codex stores its
own rollout event schemas. The comparison below maps observable behavior,
configuration defaults and safety contracts, and identifies differences explicitly.

## Shared archive and progress implementation

The core archive/progress implementation is adapted directly from the pinned
Codex v0.5.2 modules, under their MIT license. `THIRD_PARTY_NOTICES.md` retains the
license and names the source. Vendored `PROVENANCE.json` records original and
adapted hashes for every retained module. Antigravity-specific changes permit
safe scoped native paths, change product-specific metadata/repair scope, preserve
its existing progress callback interface, and propagate its timeout setting.
Codex's transcript-specific packaging wrapper is excluded.

| Capability | Antigravity behavior | Status / relevant code |
|---|---|---|
| LZMA defaults | 9 / 256m / word size 64 / 19 requested threads / external auto | Aligned; `archive_set.py`, vendored `archive_pipeline.py` |
| Explicit Deflate | Python or external backend, caller-selected level | Aligned; no implicit codec downgrade |
| Final ZIP size cap | 512,000,000 bytes; configurable smaller bound >=16,384 | Added; `archive_set.py`, vendored `bounded_archive.py` |
| One ZIP when fitting | Ordinary members in one ZIP | Aligned |
| Independent ZIP parts | Exact original offsets/hashes, `.bytes` fragments when unavoidable | Added; no conventional split volumes |
| Measured compression planning | Stratified probes and adaptation using completed ranges | Added; vendored `compression_plan.py` |
| Bounded temporary compression | Stream source ranges; reuse compressed streams during packing | Added; vendored `archive_stream.py` |
| Batch/project grouping | One set with `BATCH_INDEX.json`; explicit separate-archive override | Added; `cli.publish_prepared` |
| Whole-set verification | Full decode, CRC, SHA, contiguous coverage and inventory hash | Added; `verify-set` also verifies already published sets |
| Existing output collision | Rename/skip/error; never overwrite archive payloads | Aligned safety contract; deliberately no archive overwrite |
| Publication rollback | Remove only this attempt's unchanged outputs on partial failure | Shared bounded publication engine |
| Windows ACL inheritance | Create fresh destination files; checksum/copy/readback | Shared `file_access.py` |
| Access diagnostics | Actual readability; Windows owner and parent/file DACL information | Added rich diagnostics |
| Scoped access repair | Explicit recognized `antigravity_` artifacts; no source/parent ACL edits | Added; adapted from Codex restrictions |
| Work volume | `--work-dir`; final output retains `--out-dir` | Added |
| Disk-space errors | Known allocation/free-space checks and volume-specific diagnostics | Added |
| Native 7-Zip byte telemetry | `M` counters mean encoder bytes at MiB resolution | Shared v0.5.2 progress parser |
| ETA and stalls | Recent measured rates; unknown/stalled/finalizing states | Shared v0.5.2 stage renderer |
| Recoverable range overflow | Retry state; recovered retries do not mark overall failure | Shared v0.5.2 outcome semantics |
| Process lifecycle | Bounded output drain, input errors, cancellation, child reaping, timeout | Shared runner; Antigravity timeout retained |
| Progress output | stderr; auto/rich/plain/json/off, width/color/Unicode handling | Aligned; absolute callbacks bridged by `progress.py` |
| Overall progress | Stage counts only; no guessed overall ETA/percentage | Aligned |

## Discovery, selection, and source fidelity

| Capability | Antigravity behavior | Difference or guarantee |
|---|---|---|
| Session/catalog discovery | Reconciles native variant catalogs and accessible source paths | Antigravity-specific adapter |
| Duplicate UUID resolution | Groups catalog/native replicas using actual source evidence | Does not confuse a catalog-only row with an independent transcript |
| Explicit variant | Selects the requested variant | Explicit choice remains authoritative |
| Ambiguous distinct captures | Reports ambiguity with concrete candidates | No arbitrary preference between conflicting full sources |
| Project selection | Resolves logical sessions before export; records missing sources | Default fails early; omission requires explicit `--missing-source skip` |
| Missing source | Lists availability/capture diagnostics | SQLite/protobuf is not assumed to contain decoded full text |
| Reduced source | Explicit `--allow-reduced` remains lossy acceptance | Never presented as a complete transcript |
| Source capture | Verified bounded source prefix and read-only SQLite backup | Not a simultaneous snapshot across all application files |
| Repeated/backward native steps | Preserve every observation in source order; bounded audits; optional strict rejection | Antigravity source ordinal is the record identity, not a Codex event-schema transplant |
| Multipart name collisions | Detect normal and digest-shortened sets before publication | Adapted from pinned Codex `avoid_existing_archive` behavior |
| Loose project publication | Prepare all selected sessions before publishing directories | Validation boundary matches archived batch preparation; multi-directory atomicity is not claimed |
| Native excerpts | Selected raw source records/intervals retain original byte coordinates | Subset views no longer blindly reattach the entire native history |
| Turn listings | Recorded user anchors, timestamps and source identities | Application-native step/record numbers remain distinct |
| Incremental selection | Generic/typed since selectors, occurrence disambiguation, last-N | Includes the selected observed user turn through the captured boundary |
| Exact turn/message/range | Uses one shared index for listing and selection | Numeric identity is not changed by listing sort order |
| Native compressed direct input | Plain JSONL and explicitly supported compressed input | Decoder dependencies/format errors are explicit |
| Corrupt/unknown records | Strict audit or explicit skip policy, native bytes retained when in scope | No guessed recovery of missing conversation content |
| Database-only steps | Provenance-labeled diagnostics and optional native DB capture | Antigravity-specific functionality beyond Codex's JSONL source |
| Tool-output sidecars | Recover explicitly referenced recorded full output, preserving original preview | Additional evidence is separately labeled, never silently substituted |
| Brain/artifact capture | Explicit scoped native artifacts, no automatic unrelated project crawl | Antigravity-specific |
| Checkpoint view | Recorded checkpoint context, labeled with limits | Not equivalent to Codex rollback/compaction reconstruction |
| Read-only application policy | No original source writes/deletes | Preserved |

## Presentation, navigation, and shell workflows

| Capability | Antigravity behavior | Status |
|---|---|---|
| Markdown modes | Thread, turn, response, response-block, message, range, chat/actions/final variants | Existing modes retained and selection extended |
| Frontmatter and export map | Default structured metadata and navigable turn map; `--no-frontmatter`, `--no-map`, `--plain` | Added |
| File-operation cards | Recorded path, action, arguments and status | Added; does not infer successful filesystem changes |
| Tool formatting | Full/summary/tail/none and explicit limits; optional collapsible UI | Adapted; full output is preserved by default |
| Timestamps | Human-readable recorded times and configurable timezones | Added; underlying timestamp text remains source evidence |
| Listing order | Multi-column sorting, direction and stable selection identifiers | Added |
| Listing paging | Terminal-aware paging and explicit noninteractive mode | Added |
| Interactive browser | Paged search, project selection and multi-session basket | Added; selection precedes active export work |
| CLI help | Versioned command surface and effective configuration inspection | Updated |
| PowerShell helpers | Approved-verb functions with `agy-*` aliases; typed selectors and archive controls | Expanded; no unapproved-verb import warning |
| Current-user installation | Module discovery/autoload and explicit version verification | Updated; preserve unrelated profile/configuration |
| CLI stdout | Final path(s) once; structured JSON when requested | Aligned |
| Clipboard/open/dialogs | Explicit clipboard, open-after, directory and Save As helpers | Existing functionality retained |
| Privacy | Credential redaction; share mode omits private native copy/artifacts | Preserved; redaction is not anonymization |
| Unknown-event sidecar | Explicit separate native-record sidecar, with share-mode redaction | Added |
| Review text | Reversible UTF-8 parts, per-part hashes, bounded bytes; standalone conversion also obeys ZIP cap | Existing format retained; cap added |
| Batch review selection | Exact Markdown member or unambiguous variant-qualified session ID | Added; only selected text/manifests decoded, no claim of entire input-ZIP verification |
| Tokenizer | none/regex/auto/exact encoding counts and diagnostics; bounded Unicode regex scan | No count is called a Gemini billing count; fewer callbacks preserve the same estimate |
| Filename syntax | Product prefix; same timestamp/template/count vocabulary | Aligned where source semantics match |
| Persistent settings | Existing output/template/archive preferences retained | Added fields receive defaults; explicit preferences survive |
| Public source packaging | Allowlisted files, per-file hashes, no private transcripts | Preserved and expanded |
| Release validation | Python matrix, package/import, PowerShell and hash-pinned real 7-Zip tests | Expanded |

## Deliberate differences and remaining limits

The following are not represented as implemented Antigravity capabilities merely
because Codex exposes a similarly named switch.

| Codex-specific behavior | Antigravity decision |
|---|---|
| Codex rollout rollback/compaction live-context reconstruction | Keep Antigravity checkpoint context separate; do not invent a shared native history model |
| ChatGPT account-export import and branch reconstruction | Remains a separate source adapter in Codex; an Antigravity store exporter does not claim to decode account exports |
| Codex message mirror IDs, command event unions and rollback discriminators | Apply Antigravity's observed event semantics; no direct transplant of Codex schema predicates |
| `$codex-thread-export` agent skill | Product-specific Codex integration; not an Antigravity IDE installation or extension |
| Codex parser's historical Windows-escape/mojibake repair | Do not silently rewrite Antigravity source bytes using unrelated heuristics |
| Codex render cleanup options for repeated lines/large text | Antigravity preserves full text unless explicit tool-output policy selects a limit; no automatic loss added for cosmetic parity |
| Every identically spelled CLI flag | Compatibility is documented by behavior, not a claim of drop-in parser compatibility |
| Review filenames and guide layout | Antigravity retains its `review/PART_*.txt`, `INDEX.json`, `READ_FIRST.txt` schema with exact reconstruction metadata |
| `--fast` | Antigravity already uses its disk-backed index; this flag skips token counting rather than selecting Codex's second renderer |
| Displayed stored thinking | Export of recorded Antigravity data is not equivalent to Codex's explicit reasoning-summary field |
| Project file contents/current machine state | An archived conversation does not prove current code, installed programs, or command outcomes |
| Receiving-chat completeness | ZIP byte integrity and upload success do not prove complete model ingestion |

Strict equivalence to every Codex implementation detail is neither claimed nor
used to weaken Antigravity source integrity. Follow the runtime manifest and
actual test/CI results for the exact build being used.

## Reproducible evidence

- `docs/REFERENCE_INVENTORY.json`: every tracked reference file and digest.
- `antigravity_exporter/_vendor/codex_archive/PROVENANCE.json`: shared-engine origins.
- `tests/test_archive_sets.py`: actual size limits, reconstruction, nested native
  members, missing/tampered sets, publication races, real LZMA, ETA, retries and
  subprocess failures.
- `tests/test_source_resolution.py`: synthetic source/catalog regressions.
- `tests/test_selection_listing.py`: selectors and presentation behavior.
- `tools/ci_7zip.py`: pinned official codec acquisition for CI.
- `docs/ARCHIVE_SETS.md`: exact archive/progress/verification and access semantics.
