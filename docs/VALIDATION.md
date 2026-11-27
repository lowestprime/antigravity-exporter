# Validation history and release boundary

## v0.4.2 project exports and source order — 2026-11-27

A supplied console log showed two project exports stopping during indexing after
successful source capture. The v0.4.1 strict native-step check rejected a repeated
or backward identifier. The current failing transcript itself was not supplied,
so its exact repeated payload, final record count and remaining project members
are not represented as tested. The console log alone does not identify replay,
revision, corruption or application chronology.

Synthetic regressions reproduce the rejected pattern and now verify:

- Every source occurrence and exact native bytes, including changed/identical
  repeated lines, backward unique IDs, CRLF, Unicode, NUL gaps and captured tails.
- Accurate distinct-step gaps and bounded diagnostic examples; source-ordinal
  selections and ambiguous since anchors; explicit strict rejection with context.
- Distinct database coverage versus one-to-one identifier correspondence, and no
  inferred quota-turn or latest-time placement for ambiguous native coordinates.
- Full-source diagnostic/sidecar appendices, partial-view omission of payloads with
  unknown association, unchanged previews and ordinary unique-step recovery.
- A two-session project with repeated titles and nonmonotonic records, verified
  grouped archives, byte-exact raw/structured record order, native capture and
  review reconstruction, with original source hashes unchanged.
- Deferred loose publication, no earlier output after a later malformed source,
  retained unrelated preexisting files, and successful separate loose directories.
- Repeated real bounded exports under rename, plus skip/error, ordinary multipart
  names, digest-shortened long ASCII/Unicode names and literal template characters.
- Bounded regex scans matching the reference regex across Unicode word boundaries,
  punctuation, combining characters, CRLF, empty inputs and very long lines.

A local Linux/CPython 3.12.14 benchmark compared the old and new counting passes
on the same previously exported Markdown: 17,718,275 UTF-8 bytes, 238,974 LF-based
lines and exactly 4,406,997 regex-estimate tokens. Three-run median counting time
with plain progress fell from 2.676s to 0.729s (3.67x). This measures the counting
function, not total export speed, compression throughput or Windows performance.
On a separate 18,559,795-byte single-line Unicode fixture, peak Python-traced
allocation fell from 145,817,121 to 2,102,959 bytes; these are `tracemalloc` values,
not process RSS. Timing is evidence, not a flaky automated pass/fail threshold.

The published version uses the existing six Linux/macOS/Windows Python 3.10/3.13
validation legs, actual Windows PowerShell checks, real ZIP/LZMA tests and an
installed-wheel check outside the source checkout. New installed-wheel checks
exercise a source-order project export and explicit strict rejection. Publication
waits for the exact candidate's gates. Available older source captures provide
additional compatibility evidence; they cannot prove the outcome on a newer,
unavailable raw transcript or claim complete application history.

## v0.4.1 diagnostics and session tables — 2026-11-27

A reported failure reproduced in the unmodified v0.4.0 native CLI: `doctor`
raised `UnboundLocalError` because the separate tokenizer branch bound a local
`importlib` name. A successful installation and `--version` check did not cover
that command. The fix uses a distinct metadata import and preserves doctor JSON.

The previous helper tests discarded human listing output and did not execute
`agy-doctor`. New regressions execute doctor as a real subprocess, parse its JSON,
check the active interpreter and codec fields, and verify saved config bytes are
unchanged. Additional cases cover reported archive-preflight errors, optional
codec absence, saved/explicit tokenizer encoding, required-tokenizer failure,
requirement precedence and missing distribution metadata. PowerShell now asserts
actual doctor, tokenizer and human/JSON session/project/turn/record output.

The installed-wheel gate runs outside the checkout with `PYTHONPATH` removed,
checks the installed module origin and console entry point, and exercises doctor,
tokenizer, version and synthetic human/JSON session listings. This replaces the
former version-only package check.

Actual PowerShell 7 over a pseudo-terminal confirmed that the session pager
shows rows and restores the previous screen after `q`. Redirected output is
complete. The pager implementation and defaults are unchanged; `--no-pager`
retains the listing in scrollback. This is a display choice, not source loss.
The session view now adds descriptive headers, JSONL sizes and recorded catalog
modification times; raw JSON metadata remains available for exact parsing.

The same six OS/Python validation legs and Windows PowerShell gates apply to
this patch release. Publication waits for those gates; the release page links
the exact workflow result. Synthetic tests do not prove access to a particular
user's Windows terminal or data store.


## v0.4.0 preparation — 2026-11-26

This section records the v0.4.0 work and its observed validation. The older
sections below remain historical records; their test counts and limitations do
not describe the current implementation.

### Local regression coverage

Local validation used Linux, CPython 3.12.14, official 7-Zip 26.06, and PowerShell
7.7.0. The Python regression suite passed with the real 7-Zip executable available.
PowerShell parsing/import checks, approved exported verbs, isolated deployment
and module autoload, interpreter selection, and the 36-helper CLI exercise also
passed locally. These runs do not substitute for Windows execution.

The current synthetic tests cover:

- Canonical session identity, catalog entries that point to another Antigravity
  storage variant, genuine cross-variant ambiguity, explicit variant filtering,
  current hub metadata, project and child relationships, and unchanged source
  snapshots after metadata discovery. Unsupported protobuf fields remain
  diagnostic data rather than invented transcript content.
- Numbered chunk ordering, chunks-only sessions, empty or conflicting chunk
  sources, source identity changes between indexing and rendering, exact selected
  JSONL ranges, retained intervening NUL padding, and rejection of mixed corrupt
  text. Reduced-source export remains an explicit choice.
- Turns, responses, response blocks, actions, chat, checkpoint context, since
  selectors, deduplicated batches, latest-session choices, and failure before
  publication when a requested batch member cannot be exported.
- Raw, gzip and optional Zstandard inputs; filename byte limits; timezone and
  offset handling; browser sorting/filtering/pagination; shell argument forwarding;
  render frontmatter/maps, recorded file-operation cards and unknown-field
  sidecars; redacted share output versus exact private source bytes.
- Quota/interruption evidence scoped to actual error records. An ordinary payload
  mentioning a quota, a timestamp or a past failure does not become a claim that
  the current turn failed or that the account has a particular reset time.
- Grouped and separate archive outputs; strict ZIP size caps; oversized members
  and JSONL records; nested native paths; empty members; dictionary memory guards;
  full multipart reconstruction; missing, repeated and tampered parts; publication
  races and rollback; exclusive publication; and unchanged input sources.
- Real external ZIP/LZMA encoding, safe UTF-8 member lists, subprocess timeout
  cleanup, progress counter interpretation, recent-rate estimates, quiet/JSON
  output, recovered compression retries and final stage completion.
- Review conversion of an explicitly selected Markdown member in a grouped
  archive, exact UTF-8 reconstruction, capped standalone review ZIPs and rejection
  of incomplete multipart fragments. A deliberately corrupt native JSONL member
  confirms that Markdown-only review does not decode it or claim it was verified.
- Access-repair scope restrictions, including the shallow standalone review
  directory; failed-work cleanup and explicit recovery retention; public-file
  allowlisting; release context restrictions; all three package/module version
  declarations; checked release inventories; and preservation of existing tags
  and published assets.

Tests use synthetic public fixtures. Private archives used for the additional
runs below remain outside the source tree and distributions. Test totals can
change as regressions are added; the workflow log for the exact published commit
is authoritative for its count, skips and platform results.

### Additional private-source integration runs

A project export exercised **28 sessions and 7,707 selected records** using a
single grouped archive, full Markdown, selected native JSONL and per-session
manifests. This run requested external ZIP/LZMA level 9 with a 256 MiB dictionary
ceiling, 64 fast bytes and 19 threads, disabled tokenization, and omitted extra
native artifacts. The resulting archive contained **85 members** and occupied
**17,410,625 bytes**. Independent verification decoded every member, checked CRCs,
matched all 28 embedded manifests, and compared all 28 native JSONL members to
the original uploaded source ZIP: **44,361,393 bytes matched exactly**.

The measured wall time was **27.23 seconds**. The peak sampled combined RSS of the
exporter and its descendants was **1,049,144 KiB**, sampled every 0.1 seconds. That
is a sampled measurement, not a proven memory ceiling. The encoder chose an
actual dictionary of 17,825,792 bytes for these members; the requested 256 MiB
value is not evidence that every member used that much memory. All 214 recorded
progress stages completed without a failed stage. This is one Linux workload,
not a Windows benchmark, a cross-tool speed comparison or a general throughput
promise.

A separate three-session archival batch included native artifacts and databases.
It produced **2,118 verified ZIP members**, including **2,105 native members**, in
**45,745,953 bytes**. All three native transcript JSONLs matched their original
source bytes. Of the additional native inventory, **2,103 members in total were
byte-identical to the source**; the two SQLite backups instead matched their
source schemas and rows. SQLite backup containers are not claimed byte-identical
because a consistent backup can have a different physical page representation.
All member CRCs, all three manifest inventories/hashes and the complete expected
native inventory were independently checked. Hashes taken before and after the
verification confirmed that the original input ZIP and resulting archive were
unchanged.

### Platform and completeness boundaries

The configured release gate runs Python 3.10 and 3.13 on Linux, macOS and Windows,
with hash-pinned official 7-Zip and real ZIP/LZMA checks in all six jobs. Windows
jobs also execute the complete PowerShell validation script. A separate job
builds and checks the wheel, source distribution and allowlisted portable ZIP,
and runs the installed CLI outside the checkout. The v0.4.0 release validation and publication subsequently completed in
[workflow 45909707305](https://github.com/lowestprime/antigravity-exporter/actions/runs/45909707305).
That passing result did not cover the doctor command defect documented above;
v0.4.1 adds direct diagnostic-command and installed-wheel coverage.

Local Linux PowerShell tests do not prove Windows ACL inheritance, file sharing,
clipboard/dialog behavior or execution in a particular customized Windows
profile. Verification establishes the integrity of captured, available records;
it cannot recover records that Antigravity never persisted, reconstruct an opaque
protobuf schema by assumption, or prove an atomic snapshot across independently
captured sessions. Neither redaction nor a successful integrity check guarantees
that arbitrary conversation text is safe to publish. Product-specific parity
boundaries are listed in [PARITY.md](PARITY.md).

### Reproduce the current checks

Use a disposable output directory and a reviewed 7-Zip executable. For example,
from PowerShell 7 in the source checkout, with `7z` already available on PATH:

```powershell
$ErrorActionPreference = 'Stop'
$python = (Get-Command python -CommandType Application).Source
$archiver = (Get-Command 7z -CommandType Application).Source
& $python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Python regressions failed.' }
& $python tools/smoke_external.py --archiver $archiver
if ($LASTEXITCODE -ne 0) { throw 'Real ZIP/LZMA smoke test failed.' }
.\Test-AntigravityExporter.ps1 -PythonPath $python
```

`tools/ci_7zip.py` is a CI helper that expects GitHub runner environment variables;
it verifies pinned upstream download hashes and supplies the executable to later
workflow steps. It is not a general-purpose local installer. The public release
checks and explicit manifest-refresh process are documented in
[PUBLISHING.md](PUBLISHING.md).

## Historical rc2 preparation: executed locally

Runtime: Linux, CPython 3.13.5. The full `unittest` suite collected **121 tests**:
**120 passed; one Windows-specific test was skipped**. The optional real-archive
integration test was enabled with two externally provided historical snapshots;
no real conversation fixture, identifier, transcript, database or test output is
included in this repository or its distributions. Without those external fixture
paths, that integration test also skips: 119 pass and two skip on this Linux host.

The original replacement-detection test remains enabled. The new Windows-only
handle-rotation test is additional; neither test has been deleted to conceal a
Windows incompatibility. Windows execution is a required CI validation step.

Coverage includes:

- Stable source identity, append-only prefix capture, rewrite/truncation rejection,
  read-only handles, SQLite WAL snapshots and log/database coverage differences.
- Turn/step selection, source-preserving rendering, recovery confined to the same
  conversation and step, redaction boundaries and review-text reconstruction.
- ZIP member safety, independent decoding, size/CRC/SHA-256 checks, failure and
  timeout handling, publication readback and cleanup gating.
- Aggregated chunk/artifact progress, bounded output frequency, real stage counters,
  no fabricated compression byte rates, heartbeat cancellation, Unicode widths,
  terminal-control filtering, redirected logs and distinct publication stages.
- Codex-shaped stamps, Windows timezone abbreviations, legacy-template compatibility,
  approximate versus encoding-exact filename labels and saved-setting preservation.
- Public source allowlisting, unreviewed changes, private-file and credential-pattern
  rejection, tracked-file mismatch and distribution hygiene.

The Python wheel and source distribution were built locally through the configured
setuptools backend. The wheel was installed without dependencies into an isolated
target directory; the CLI version and filename-template commands ran from outside
the source checkout. The portable source ZIP is constructed from `PUBLIC_FILES.json`,
with member CRC and SHA-256 checks. No unrelated files in the working directory are
accepted as public source. Distributions are checked for unsafe entries, databases,
transcripts and generated bytecode. Restricted-content detection is a safeguard,
not a mathematical guarantee that arbitrary text contains no personal information.

A separate full archival smoke test on one accessible historical snapshot exported
883 records into 467 verified ZIP members with native artifacts, review text and
handoff index. It used Python DEFLATE level 6, no tokenization, and completed in
2.214 seconds on this host. Redirected progress produced 43 lines across 21 stages.
This is not a Windows/LZMA benchmark or a speedup comparison against another tool.

## Historical rc2 preparation limitations (superseded where noted below)

- Successful execution of rc2 on the user's Windows machine or Windows CI.
- A real external 7-Zip encoder run in the Linux build environment.
- Successful creation of the GitHub remote, GitHub Actions runs or a public release.
- Optional tiktoken installation/tokenizer execution, desktop clipboard/dialogs,
  or exact parity with every Codex-specific subsystem.
- A byte-level verification of the most recently supplied private archive: that
  attachment was not readable as bytes in this build environment.

The source tree includes a six-job OS/Python matrix, a distribution-check job,
Windows PowerShell parsing/import checks and a real Windows LZMA smoke test.
These are **configured gates**, not claims that remote checks have already run.

## Reproduce

```powershell
python -m unittest discover -s tests -v
.\Test-AntigravityExporter.ps1 -PythonPath (Get-Command python).Source
python tools/public_release.py --tracked
```

`--tracked` is for a Git checkout, not a plain extracted ZIP. Before publishing an
intentional source edit, review it and regenerate the public allowlist with
`python tools/public_release.py --refresh`, then commit both the edit and allowlist.

Release candidates use `rc` versions and prereleases. A failed test is a release
blocker, not grounds for relabeling the package stable or overwriting an old tag.

## Post-publication evidence — 2026-11-10

The original rc2 release is associated with commit
`a78f5558a022d81c38bc6a1e8bf75fc003918387` and successful release run
[42777739450](https://github.com/lowestprime/antigravity-exporter/actions/runs/42777739450).
All six OS/Python jobs, package validation and publication succeeded. Windows
PowerShell validation passed; the Windows/Python 3.13 job also passed the real
7-Zip/LZMA integration step. The owner separately reported 121 local Windows
tests with one optional private-fixture skip and a verified real-conversation
archive. No private transcript or identifying fixture is published here.

PRs #1 and #2 subsequently updated pinned action revisions without refreshing
the checked public-source manifest. Main run
[42779764222](https://github.com/lowestprime/antigravity-exporter/actions/runs/42779764222)
failed at the public-file guard. Refreshing an unchanged local main checkout
cannot repair a different server-side revision. Rc3 reconciles the reviewed
remote files and manifest together; it does not disable the guard.

Rc3 adds synthetic tests for publication event/ref restrictions, exact asset
inventories/checksums, version consistency, version-specific notes and immutable
release handling. Consult the rc3 workflow linked from its release for the
actual remote results; the historical rc2 pass does not certify later commits.
Optional private-fixture, tokenizer and GUI tests retain their stated limits.
