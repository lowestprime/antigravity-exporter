# Antigravity Exporter

Read-only exports of local Antigravity conversations: Markdown, original JSONL,
SQLite coverage diagnostics, verified ZIP archives, and PowerShell helpers.

**Version: 0.3.0rc3 (prerelease).** This is an independent tool, not a Google
product. Supported storage layouts have been observed in Antigravity 2.24–2.29;
that is not a guarantee for future proprietary storage changes. It is not an API
client and does not submit prompts, consume model quota, or require account tokens.

The rc2 release passed the public Windows/macOS/Linux validation matrix and
real Windows LZMA integration. See [validation history](docs/VALIDATION.md).
The rc3 maintenance release repairs workflow/manifest drift and adds gated
publication after a reviewed version bump reaches `main`; it does not change
export formats, compression defaults, or user configuration.

## Install or use the portable source

Python 3.10+ is required. Runtime dependencies are optional. Full default ZIP/LZMA
compression requires an existing compatible 7-Zip CLI. DEFLATE is available with
Python's standard library, explicitly selected per export or in saved settings.

Extract the portable source ZIP to a clean directory. In PowerShell 7:

```powershell
Import-Module .\AntigravityExporter.psm1 -Force -WarningAction Stop
agy-export --version
agy-doctor
agy-sessions
```

The module prefers an already installed uv-managed Python 3.13, then the Windows
Python launcher, then `python`. It does not install or upgrade interpreters or
packages. To choose explicitly:

```powershell
Set-AntigravityPython -Path 'C:\path\to\python.exe'
```

For an installed Python CLI, use `python -m pip install .` from this repository
(or `uv tool install .` for an isolated tool environment). The console command is
`antigravity-export`. PowerShell helpers remain supplied in the portable source.
The Python distribution is named `antigravity-thread-exporter` for compatibility
with the original candidate; the GitHub repository is `antigravity-exporter`.

## Everyday use

```powershell
agy-sessions
agy-turns UUID
agy-audit UUID -VerifyChunks
agy-thread UUID
agy-last UUID 2
agy-turn UUID 2
agy-response UUID 2
agy-response-block UUID 2
agy-msg UUID 15
agy-range UUID 20 100
agy-chat UUID
agy-actions UUID
agy-project project-name
agy-batch @('UUID1', 'UUID2')
```

`UUID` placeholders must be replaced with a session identifier from the listing.
A unique UUID prefix of at least eight characters is supported. A turn begins
with a recorded user input. Message selectors use 1-based record ordinals; step
ranges retain original, possibly non-contiguous Antigravity step indices.

The ordinary default creates **one private ZIP containing exactly three files**:
Markdown, native captured JSONL, and a manifest. An ordinary non-JSON export prints
the resulting path; progress goes to stderr. Add `-Json` for structured results.

Native JSONL is the entire captured prefix even for a selected Markdown view.
Use `-NoSourceCopy -RecordsJsonl` for selected-only structured records.

## Archival versus shareable outputs

```powershell
# Complete accessible per-conversation native archive plus review text.
agy-thread UUID -IncludeNative -IncludeArtifacts -Review -Handoff -VerifyChunks

# First local acceptance check: retain loose files after archive verification.
agy-thread UUID -NoArchiveCleanup -Json

# Credential-redacted views, without original database/binary/native copies.
agy-thread UUID -Share -Review -Handoff -RecordsJsonl `
    -ArchiveMethod Deflate -ArchiveBackend python -ArchiveLevel 6
```

`-IncludeNative` adds the conversation SQLite snapshot and annotation;
`-IncludeArtifacts` adds accessible files under that conversation's brain folder.
Neither follows arbitrary project paths mentioned inside a transcript. Current
workspace files must be collected separately. `-Archive` means bundle generated
outputs; it does not imply inclusion of every native artifact.

**Ordinary archives are PRIVATE even when the filename has no privacy suffix.**
Redaction is best effort and is not anonymization. Never commit conversation
exports, account directories, raw databases, private fixtures, or credentials.
Read [SECURITY.md](SECURITY.md).

## Codex-style progress and filenames

The progress adapter follows the style of Codex Exporter 0.3.12: two-line TTY
panel, stage counters, measured throughput/ETA, elapsed time, color/Unicode
fallbacks, and a bounded continuously drained 7-Zip output reader. It is an
adaptation, not the same byte-identical engine. Antigravity-specific stages differ.
Nested chunk checks, sidecar reads, and native artifact capture use aggregate
progress rather than hundreds of alternating capture/verify lines.

```powershell
agy-thread UUID -ProgressStyle rich
agy-thread UUID -ProgressStyle plain
agy-thread UUID -ProgressStyle json 2> progress.jsonl
agy-thread UUID -NoProgress
```

`auto` uses a capable terminal, otherwise sparse plain logs; explicitly requesting
`rich` still falls back when redirected or unsupported. `NO_COLOR` suppresses
color. Heartbeats do not invent completed bytes. Compression percentages are
those reported by the encoder, not a fabricated whole-job percent or byte rate.
Publication separately reports source checksum, destination copy, and readback.

New configurations use this template:

```text
antigravity_{title}_{mode}_{stamp}{session_short_part}{counts}.md
```

`{stamp}` has the Codex shape `MON_11092026_053752_PM-PST`. Windows long timezone
names are abbreviated, title slugs are lowercase, and a short session suffix uses
the **last eight hexadecimal UUID characters**, matching Codex rather than rc1's
first-eight `{short_id}`. The suffix is present for batches, stable names, or
`-IncludeSessionShortId`. Counts are `_Nlines_approxMtokens`, or
`_Nlines_Mcl100k_base_tokens` when that encoding was actually counted. `-Fast`
uses no tokenization and does not mislabel an estimate as an exact count.

Saved rc1 templates and other preferences are preserved. To deliberately adopt the
new naming/count defaults without erasing output or archiver settings:

```powershell
agy-config --set 'template=antigravity_{title}_{mode}_{stamp}{session_short_part}{counts}.md' `
    --set tokenizer=auto --set filename_counts=true --set include_session_short_id=false
```

Legacy `{timestamp}`, `{short_id}`, `{privacy}`, `{first_step}`, and `{last_step}`
remain supported. `AGY_EXPORT_TZ` selects an IANA zone when zone data is available;
otherwise the system timezone is used. Configurations never read or rewrite Codex
preferences. See [parity and defaults](docs/PARITY.md).

## Compression and integrity

Default policy: ZIP/LZMA, level 9, `256m`, word size 64, 19 requested threads,
external auto backend, original-source copy on, cleanup on, review off. The worker
setting is not measured worker utilization. LZMA is less universally supported
than DEFLATE. A readable archive is not proof a downstream model ingested it.

```powershell
agy-config --set 'archiver=C:\Program Files\7-Zip-Zstandard\7z.exe'
agy-doctor
agy-verify 'C:\Exports\example.zip'
agy-verify 'C:\Exports\example.zip' -Metadata
agy-review 'C:\Exports\example.zip'
```

Use an existing executable identified on your system, not an assumed installation
path. The input member list is passed through a UTF-8 response file to avoid
Windows command-line limits. Full verification decodes every member and checks
CRC, length, SHA-256 and the embedded manifest before publication/cleanup.
Metadata-only diagnostics never claim decoded-content verification.

## Live-source and completion semantics

The exporter captures a fixed readable source prefix and verifies it with a second
pass. Observed replacement, truncation, or prefix rewriting is rejected. Windows
read handles permit the owning process to rotate files using read/write/delete
sharing; the exporter itself requests **read access only**. It then detects the
changed pathname identity. SQLite backup includes committed WAL data. These
operations are not an atomic snapshot across unrelated files.

A record present only in SQLite is separately attributed; it is not inserted into
native JSONL as an invented assistant message. Stored `thinking` is optionally
rendered; no server-only reasoning is retrieved. Tool-sidecar recovery only reads
an explicitly corresponding step in the same conversation, retaining the original
preview. Assistant narration or a DONE tool status does not prove completion.

## Public repository and releases

[Publishing guide](docs/PUBLISHING.md) · [Contributing](CONTRIBUTING.md) ·
[Validation](docs/VALIDATION.md) · [Changelog](CHANGELOG.md)

The public-source allowlist intentionally excludes every private fixture and
export. CI targets Windows/macOS/Linux, Python 3.10/3.13, PowerShell validation,
real Windows LZMA smoke tests, and distribution hygiene. Tag publication repeats
the checks; release-candidate versions are published as prereleases, not latest
stable. The presence of workflow files is not evidence those remote runs passed.

## Limits

No claim of feature-for-feature parity with Codex Exporter: Codex rollback and
cloud-export schemas are different. Full proprietary protobuf-only decoding,
Zstandard direct inputs, general Windows ACL repair, and identical rich-renderer
internals remain outside this release. The checkpoint view is only an attributed
checkpoint-plus-suffix view, not guaranteed model-context reconstruction. Individual
large JSON objects can still consume memory even though whole-history indexing is
incremental. Exact token counting has an explicit size guard.

MIT licensed. Adapted upstream formatting ideas/code are credited with their
original notice in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
