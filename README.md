# Antigravity Exporter 0.4.0

Export recorded Google Antigravity conversations to Markdown and verified ZIP
archives. Resolve real storage identities across desktop/IDE/CLI catalogs,
select complete threads or precise user-turn ranges, preserve native JSONL, and
inspect source gaps rather than silently inventing missing history.

This independent MIT-licensed tool is not a Google product. The application data
store is read-only. Python 3.10+ is required; the base package has no third-party
Python dependencies. Default LZMA encoding requires an existing compatible
7-Zip CLI. Windows PowerShell helpers target PowerShell 7.

## What changed

- Respect the catalog's `app_data_dir` pointer. A CLI catalog row pointing into
  the desktop store is one storage identity, not a second fake conversation.
- Read the observed `agyhub_summaries_proto.pb`/`jetbox_summaries_proto.pb`
  summary map with explicit schema/provenance checks. Recover recorded project,
  title, agent and parent metadata without interpreting opaque trajectory bodies.
- Add inclusive `since` selectors, matching native excerpts, sorting, paging,
  timestamps, interactive browsing, scoped Windows access repair and compressed
  direct JSONL input.
- Share the pinned Codex Exporter **0.5.2** archive/progress implementation under
  its MIT license: one archive set per batch, a hard **512,000,000-byte maximum**
  for every final ZIP, exact multipart reconstruction, measured compression ETA,
  explicit retry state and verified publication before cleanup.
- Install a versioned PowerShell module with static exports. New sessions
  discover `agy-*` automatically, with no profile edits or PATH changes.
- Stop classifying quoted quota wording in non-error SQLite rows as an actual
  interruption. Warn about non-error database rows absent from readable JSONL.

The [parity matrix](docs/PARITY.md) maps every audited capability category to the
pinned Codex source. Source-specific Codex rollback, ChatGPT account archives and
Codex agent skill integration are distinct product adapters, not fabricated
Antigravity features. The [reference inventory](docs/REFERENCE_INVENTORY.json)
records every tracked file's hash and line count for both starting repositories.

## Permanent Windows installation or upgrade

Download and verify the portable source ZIP and checksum file from the
[release page](https://github.com/lowestprime/antigravity-exporter/releases).
Extract it to a new directory. From that directory in PowerShell 7:

```powershell
$ErrorActionPreference = 'Stop'
.\Install-AntigravityExporter.ps1 -Scope CurrentUser
agy-export --version
Get-Module AntigravityExporter | Select-Object Name, Version, Path
agy-doctor
```

The installer checks `PUBLIC_FILES.json`, copies and verifies the complete public
source into the current user's standard module directory, and activates it with
`Import-Module -Global`. Versioned upgrades retain previous versions. Repeating
an identical installation is safe. A modified copy of the same version is not
replaced unless `-Force` is supplied; its bytes are backed up first.

Open a new PowerShell 7 session and run `agy-export --version` without manually
importing a file. No administrator session, PowerShell profile rewrite, execution
policy change, Python package upgrade or Codex helper change is needed. Read the
[installation guide](docs/POWERSHELL_INSTALL.md) for custom paths, existing
explicit-path profile loaders, rollback, `-WhatIf` and downloaded-file handling.

Python selection retains the existing order: an explicitly selected executable,
`AGY_EXPORT_PYTHON`, an existing uv-managed Python 3.13 found offline, the Windows
Python launcher, then a suitable `python` executable. No interpreter is downloaded.

For portable use:

```powershell
Import-Module .\AntigravityExporter.psd1 -Force -WarningAction Stop
agy-help
```

Direct Python usage also works without installation:

```text
python agy_export.py --help
python agy_export.py sessions
python agy_export.py export --id 11111111-2222-4333-8444-555555555555
```

The optional wheel supplies `antigravity-export` and `antigravity-exporter`.
It does not install the PowerShell helper files; use the portable release for
that. Optional extras are `exact` (tiktoken), `timezones` (IANA data on platforms
without it) and `zstd` (direct `.jsonl.zst` input).

## Everyday commands

```powershell
agy-sessions
agy-projects
agy-sessions -Sort 'project:asc,modified:desc'
agy-browse
agy-thread '11111111-2222-4333-8444-555555555555'
agy-project 'example-project'
agy-batch @('11111111-2222-4333-8444-555555555555', '22222222-2222-4333-8444-555555555555')
agy-turns '11111111-2222-4333-8444-555555555555'
agy-since-turn '11111111-2222-4333-8444-555555555555' 2
agy-since-text '11111111-2222-4333-8444-555555555555' 'distinct starting prompt'
agy-last '11111111-2222-4333-8444-555555555555' 3
agy-audit '11111111-2222-4333-8444-555555555555'
```

Use `--json` for machine-readable listings/results. Ordinary exports print each
final archive path once on stdout. Progress and interactive UI use stderr.
Terminal listings page automatically; redirected or JSON listings remain complete.
The [selection and browsing guide](docs/SELECTION_AND_BROWSING.md) describes
text ambiguity, recorded IDs, time offsets, stable turn numbers and browser keys.

A full UUID is sufficient when it resolves to one storage identity. When genuinely
different variant stores contain that UUID, use `antigravity:UUID`,
`antigravity-ide:UUID`, `antigravity-cli:UUID` or `--variant`. The exporter never
arbitrarily merges distinct stored transcripts. A unique hexadecimal prefix of
at least eight characters is accepted.

### Complete archival preservation of the available session files

```powershell
agy-thread '11111111-2222-4333-8444-555555555555' `
    --include-native --include-artifacts --verify-chunks --handoff
```

This adds the selected conversation's SQLite/annotation snapshots and accessible
brain files, including recorded uploads and tool output sidecars. It can include
private credentials or unrelated historical material referenced inside that
session. Shared account configuration and arbitrary external paths are never
followed. Native artifact flags include the **entire selected session store**,
including when the rendered view is a narrower turn selection.

A file named `transcript_full.jsonl` means the application's recorded full-detail
log. It does **not** prove that every historical SQLite row is represented there.
The manifest records step gaps, database-only rows and partial tails. Database
wire-text diagnostics have separate provenance; opaque payloads are not promoted
into invented user or assistant messages. See [source resolution](docs/SOURCE_RESOLUTION.md).

### Selected exports

Modes are `thread`, `turn`, `response`, `response-block`, `message`, `range`,
`chat`, `chat-actions`, `actions`, `last-response`, `last-assistant`,
`last-substantial`, and `checkpoint-context`. The checkpoint view is explicitly
approximate; it is not a reconstruction of a server's actual model context.

`--since`, `--since-turn`, `--since-text`, `--since-id` and `--since-time` are
inclusive of the matched recorded human turn. Ambiguous prompt text requires
`--since-occurrence N`. `--last-n-turns`, message ordinals and exact step bounds
remain available. Indices retain their original meaning after sorting.

Database diagnostic text follows the rendered step span, including terminal
rows when the final recorded event is selected. Whole-session coverage counts
remain visible; earlier diagnostic text is omitted from a later-turn view.
`--include-native` and `--include-artifacts` explicitly capture whole-session
native stores and artifacts, even when the readable view is a subset.

The default native JSONL contains the selected original byte ranges, not the
entire earlier conversation. Full selections borrow the verified capture without
another raw copy. Disjoint display filters retain exact selected source records;
contiguous runs retain intervening disclosed source gaps. Original whitespace,
Unicode, tool payloads and recorded step numbers are unchanged. A selected
checkpoint may itself quote older material; its content is not rewritten.

## Defaults and configuration

| Setting | Default |
| --- | --- |
| Output | `~/Documents/Antigravity Exports` unless previously configured |
| Archive / native JSONL / cleanup | `true / true / true` |
| Batch packaging | One shared bounded archive set; `--separate-archives` opts out |
| ZIP limit | `512000000` bytes; smaller persisted caps are respected |
| Compression | LZMA, level 9, dictionary `256m`, word size 64, requested threads 19 |
| Backend | `auto`, requiring external 7-Zip for LZMA |
| Review text | Off; explicitly requested review goes inside the same archive |
| Tool output / recorded thinking | Full / included |
| Redaction | Off; exports are private by default |
| Timestamps | Enabled; local timezone at the recorded event instant |
| Tokenizer | `auto`; exact tiktoken when available/within limit, otherwise labeled regex estimate |
| Filename | `antigravity_{title}_{mode}_{stamp}{session_short_part}{counts}.md` |
| Collision | Rename; existing outputs are never overwritten |
| Failed temporary work | Removed; use `--keep-failed-work` for explicit private recovery |

`stamp` uses the Codex-compatible `THU_11262026_024500_PM-PST` syntax. Names use
Windows-safe slugs, optional last-eight session identifiers, mode descriptors and
honestly labeled line/token counts. Batch members automatically include the
session suffix. Filename components are also bounded in UTF-8 bytes for Unix
filesystems. All supported template substitutions are listed by the CLI help.

```powershell
agy-config
agy-config --set max_archive_bytes=500000000
agy-config --set 'work_dir=D:\AntigravityExportWork'
agy-config --set timestamps=false
agy-config --set timestamp_timezone=UTC
agy-config --set progress_style=rich
agy-config --set 'archiver=C:\Program Files\7-Zip-Zstandard\7z.exe'
```

Settings live in `%LOCALAPPDATA%\AntigravityExporter\config.json` on Windows,
with an `AGY_EXPORT_CONFIG` override for isolated use. Upgrades preserve them.
Bare invocation and help remain available when a configuration is invalid;
`config --reset` explicitly restores defaults.

One-run controls include `--work-dir`, `--max-archive-bytes`,
`--progress-style auto|rich|plain|json|off`, `--no-progress`, `--no-timestamps`,
`--timestamp-timezone`, `--no-frontmatter`, `--no-map`, `--ui-style`, `--literal`,
`--unknown-sidecar`, `--no-filename-counts`, `--stable-filenames`, `--stdout`,
`--no-file`, `--clipboard`, `--open-after` and native Save As/folder dialogs.
Use `agy-export export --help` for the complete parser-defined interface.

## Archive integrity, limits and review text

A fitting single-session export has three members: Markdown, provenance manifest
and native JSONL. Batches add `BATCH_INDEX.json`. Oversized outputs become
independently readable `.partNNNN-of-NNNN.zip` files, each within the cap. Keep all
parts together; **do not concatenate their ZIP bytes**. Reconstruction metadata
records original member hashes, byte ranges and exact order.

```powershell
agy-verify 'C:\Exports\antigravity_example.zip'
agy-verify-set @('C:\Exports\antigravity_example.part0001-of-0002.zip',
                 'C:\Exports\antigravity_example.part0002-of-0002.zip')
agy-review 'C:\Exports\antigravity_example.zip'
agy-access 'C:\Exports\antigravity_example.zip'
```

The package verifies decompression/CRC, SHA-256, sizes, reconstruction, final caps
and destination readback. Only then does it clean owned temporary exports.
Source files, databases and previous exports remain unchanged. Windows output
files inherit the destination directory's ACL through fresh-file publication.
Explicit `agy-repair-access PATH` is narrowly restricted to recognized generated
artifacts; it never takes ownership or changes broader permissions.

Use `--review` during export to include reversible UTF-8 text parts and an index
inside the ordinary archive. The default per-part cap is 1,000,000 bytes including
headers. Standalone `review-from` converts an existing Markdown file or a selected
Markdown member from an ordinary ZIP. Multipart histories should request review
text at export time. These byte bounds do not guarantee that a receiving model
has ingested the complete record.

LZMA is ZIP method 14; not every consumer supports it. A verified local archive
and remote ingestion are different checks. Deflate remains an explicit option,
not a silent downgrade of saved settings. See the [archive guide](docs/ARCHIVE_SETS.md).

## Audit boundaries and privacy

The observed Antigravity metadata adapter is based on validated source examples;
it is not an official promise of schema stability. Unsupported layouts fail with
a diagnostic instead of guessing. `.pb` trajectory decoding and missing history
reconstruction are not claimed. All original fields of readable events remain
available in native JSONL; unknown fields are also rendered structurally.

`--share` creates credential-filtered views, disables raw native source copies,
and rejects binary/native artifact flags. Filtering is best effort and does not
anonymize names, paths or private case facts. `--redact` with a native source copy
is rejected. Review sensitive exports before forwarding them.

The test suite uses synthetic data. Private validation inputs and user exports
are not included in Git, release distributions or CI fixtures.

## Development and releases

```text
python -m unittest discover -s tests -v
python tools/public_release.py --refresh
python tools/public_release.py --tracked
python -m build
python tools/public_release.py --build
```

Refresh the public manifest only after reviewing intended changes. Release
publication requires the exact source's validation and package checks. Immutable
version tags and already published assets are never replaced on retry.

[Validation](docs/VALIDATION.md) · [Publishing](docs/PUBLISHING.md) ·
[Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) ·
[Changelog](CHANGELOG.md) · [Third-party provenance](THIRD_PARTY_NOTICES.md)
