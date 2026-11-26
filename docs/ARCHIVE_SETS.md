# Bounded archive sets, verification, progress, and access

This implementation adapts the archive engine from Codex Exporter v0.5.2 at
commit `acd322d1987f1078b6f79dcba62c898704dcc5ba`. It is bundled with the exporter;
exports do not fetch code or install dependencies. See `THIRD_PARTY_NOTICES.md`
and the per-file upstream/adaptation hashes in the vendored `PROVENANCE.json`.

## Archive contract

Every successfully published ordinary export ZIP is at most **512,000,000 bytes**,
including its filenames, ZIP headers, central directory, and metadata. The
`--archive-max-bytes` option can reduce this cap to 16,384 bytes. A cap outside
16,384–512,000,000 is rejected. An extremely small cap may be unable to hold the
required metadata for a large member inventory; that produces an explicit error,
never missing records or a ZIP exceeding its declared bound.

A fitting single-session export retains its ordinary Markdown, manifest and
matching native JSONL. Optional review text, unknown-event records, and explicitly
requested native/artifact captures join that same archive. Multiple selected
sessions share one bounded archive set by default, with a `BATCH_INDEX.json`.
Use `--separate-archives` for separate per-session sets.

The archive policy remains LZMA, level 9, dictionary ceiling 256m, word size 64,
19 requested threads, external compatible 7-Zip, and verification before cleanup.
The requested thread count is an encoder limit, not measured utilization. Source
content and hardware determine memory use, ratio and speed; no universal optimum
or fixed speedup is asserted. Python's ZIP_LZMA does not honor a compression level,
so it is not substituted for this configured external policy. An explicit Python
Deflate backend remains available.

The engine uses bounded stratified compression samples when useful and adjusts
later ranges using completed measurements. Estimates affect planning only. Actual
finished ZIP sizes and complete decoded hashes decide whether output is accepted.
Large members are streamed through the encoder; raw segment copies are not staged.
Small private inputs may be hard-linked inside the private working tree. A
cross-volume or link-restricted case uses streamed input instead.

### Independent parts and exact reconstruction

Oversized exports become `.part0001-of-000N.zip` files. Each file is an independent
ZIP, not a conventional split `.zip.001` volume. Every part carries
`ARCHIVE_PART.json` containing the set identity, part number/count, original
inventory hash, original file sizes/hashes, and each segment's original offset,
size, SHA-256 and line-boundary flags.

Fitting compressed streams are reused without recompression when packing parts.
The packing algorithm is a bounded practical search over measured piece sizes;
its diagnostics distinguish cases where a fixed-piece minimum was proven from
cases where only a valid packing was found. It does not claim global optimality
across all codecs, dictionary choices, segment boundaries or arbitrary inputs.

A whole JSONL record may itself be larger than the cap. Such input requires
explicit `.bytes` fragments. Reconstruct the original by sorting its segments by
original offset, requiring contiguous coverage, concatenating exact bytes, and
checking the original size and SHA-256. A fragment can split a UTF-8 character
or JSON object and must not be parsed as an independent document. Ordinary
newline-aligned JSONL segments retain the `.jsonl` suffix.

All parts are decoded, CRC-checked, SHA-256-checked and reconciled to every original
member before any final path is published. Publication is exclusive; collisions
never overwrite existing files. If publication of a later part fails, rollback
removes only earlier files from this attempted publication whose filesystem
identity and signature still match. App-owned files are never part of this cleanup.

## Verify a set later

```powershell
$parts = Get-ChildItem -LiteralPath 'C:\Exports' -Filter 'antigravity_example.part*-of-*.zip' |
    Sort-Object Name
antigravity-export verify-set @($parts.FullName)
```

Use exact paths for one particular export set. `verify-set` rejects mixed sets,
duplicate/missing parts, unsafe inventory, unexpected codecs, invalid metadata,
coverage gaps/overlaps and hash/size mismatch. Verification streams the decoded
bytes without extracting members. A single ordinary ZIP also receives a full
CRC/SHA inspection; the `verify` command additionally checks embedded per-session
manifests. Neither inspection mutates an existing export.

The decoder dictionary guard defaults to 512 MiB. A policy-limit rejection is
not evidence of corruption. Dictionary size, fixed I/O buffers, and total process
memory are different quantities. Archives record actual encoded dictionaries
separately from the requested encoder ceiling.

## Convert one transcript from an existing batch

```powershell
antigravity-export review-from 'C:\Exports\antigravity_batch.zip' --id 'SESSION-UUID'
antigravity-export review-from 'C:\Exports\antigravity_batch.zip' --member 'exact_transcript_member.md'
```

The ID accepts an unambiguous UUID prefix and optional `variant:UUID` qualifier.
The exact member selector must name an existing Markdown member. Ambiguous input
is rejected instead of picking a transcript. The converter reads the selected
Markdown and any manifests needed to resolve its identity; it does not open native
JSONL payloads. Its receipt explicitly distinguishes these CRC/hash checks from
full input-archive verification. The original input is unchanged.

Standalone conversion retains its review text directory and, by default, creates
a bounded Deflate archive set. `--no-archive` keeps only the text directory.
`--archive-max-bytes` and `--work-dir` apply to this conversion as well. Multipart
fragments are not treated as complete transcripts: request `--review` when making
the original export, or reconstruct and verify the complete Markdown before
converting it. `verify` reports one part's integrity without claiming the entire
set was reconstructed; `verify-set` checks all parts together.

## Work directory and publication

`--work-dir` chooses an exporter-owned temporary-work parent on another volume.
Final files still go to `--out-dir`. Space checks use known allocations and observed
free space; they do not promise a compression ratio or reserve free space against
other applications. Disk-full diagnostics identify the work and output volumes.

On Windows, final publication creates new destination files, flushes them and
checks their bytes. Newly created files inherit the output directory's ACL. The
exporter does not move a private staging DACL into the output directory. It does
not change source ACLs, take ownership, elevate itself, or grant broad access.
On POSIX, verified hard-link publication can avoid another full copy.

`diagnose-access` inspects actual read access and, on Windows, owner/file/parent
security information. `repair-access` is an explicit operation limited to
recognized `antigravity_` export files and validated review directories. It uses
the Windows system `icacls` executable, resets only the selected artifact to its
parent inheritance, and rejects app data paths, links, reparse points, multiply
linked files, wildcards and unrelated directory trees. Existing rights must
already permit the repair; it does not bypass access controls.

## Measured progress

The public stage display now shares the Codex v0.5.2 implementation. Antigravity's
SQLite and source-capture callbacks are bridged into that reporter. Progress stays
on stderr; result JSON and final output paths stay on stdout.

| Observation | Reported meaning |
|---|---|
| Source/read/copy/verify callbacks | Observed bytes against a known stage size |
| Native 7-Zip `M` frames | Encoder-processed bytes at 1,048,576-byte resolution |
| Native 7-Zip percentage | Backend percentage; no invented byte throughput |
| No usable native encoder telemetry | Explicitly labeled stdin-feed counters and input ETA |
| Recent measured work | Throughput and ETA from a bounded recent window |
| No advance for ten seconds | Waiting-for-progress state; stale rate/ETA withheld |
| All input consumed but close/check pending | Finalizing/checking; no premature successful 100% |
| Oversized candidate retried | Retry status; a recovered budget attempt does not poison success |
| Encoder, input, verification, publication or cleanup failure | Failed outcome |

Stage counts are discovered dynamically, so they are not converted into an
invented overall percentage or ETA. Terminal color, width, Unicode/ASCII fallback,
`NO_COLOR`, plain logs, JSON events and disabled progress remain supported.

## Receiving applications

These guarantees concern the output produced and verified by this exporter.
They do not prove a receiving chat has materialized an upload, supports ZIP-LZMA,
read every member, or placed all text in its active context. The optional review
parts preserve rendered text and can be attached separately when appropriate;
their byte bounds are not model-token bounds. Native source, rendering omissions,
and private content still require the intended recipient's review.

## Primary implementation references

- [Codex Exporter v0.5.2 archive implementation](https://github.com/lowestprime/codex-exporter/tree/acd322d1987f1078b6f79dcba62c898704dcc5ba/codex_exporter)
- [Python ZIP reader and compression semantics](https://docs.python.org/3.13/library/zipfile.html)
- [Microsoft file security and creation inheritance](https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights)
- [Official 7-Zip console progress implementation](https://github.com/ip7z/7zip/blob/26.06/CPP/7zip/UI/Console/PercentPrinter.cpp)
