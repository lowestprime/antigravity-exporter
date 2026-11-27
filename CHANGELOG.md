# Changelog

## 0.4.1 — diagnostics and readable session listings

- Fix the `doctor` command's `UnboundLocalError`: a conditional tokenizer import
  no longer creates an uninitialized function-local `importlib` name.
- Report the effective saved tokenizer encoding, preserve explicit overrides,
  and fail clearly when `--require-tiktoken` or the selected exact tokenizer
  requires a package absent from the active interpreter. No package is installed.
- Distinguish an importable tokenizer from absent distribution-version metadata;
  diagnostics remain an availability probe and do not download encoding data.
- Execute real doctor and tokenizer commands, assert human and JSON listing
  content, and smoke-test the installed wheel outside the source checkout.
- Add descriptive session-table headers, readable JSONL sizes, recorded last-modified
  dates, explicit transcript availability and SQLite presence. Retain exact bytes
  and original metadata in JSON output.
- Document why the interactive pager restores the prior screen on `q`, plus
  per-command and saved no-pager settings. Keep paging and export defaults.
- Publish a separate versioned PowerShell upgrade; preserve existing profiles,
  configuration, prior module versions, and previously published releases.


## 0.4.0 — source identity repair and Codex 0.5.2 archive parity

- Resolve recorded catalog app_data_dir pointers before merging identities; add
  the validated hub summary map adapter, qualified selectors and metadata provenance.
- Restore project/agent/parent/title metadata, numeric chunk handling and early
  missing-source diagnostics. Do not invent opaque historical conversation text.
- Share the attributed pinned Codex 0.5.2 bounded archive/progress engine: measured
  range ETA, retries, shared batch sets, complete reconstruction, source checks,
  final 512000000-byte ZIP cap and verified fresh-file publication.
- Match selected native JSONL to turn/since/message/range views with original byte
  ranges and NUL-gap provenance. Add compressed direct JSONL input.
- Add since/typed helpers, sort/paging/browser controls, timestamp preferences,
  frontmatter/map controls, recorded edit cards, unknown sidecars and scoped ACL repair.
- Install verified versioned PowerShell modules with static exports and automatic
  discovery; preserve profiles, settings, prior versions and interpreter choices.
- Require observed error-row evidence for quota-interruption labels; retain all
  other database diagnostics and warn about real readable-history coverage gaps.
- Keep selected-view database diagnostic text within the selected span; redact
  credential-bearing titles in filenames, handoff indexes and grouped metadata.
- Verify selected native bytes against the indexed record hashes before packaging;
  correctly preflight chunks-only sessions and preserve whole-session coverage counts.
- Add public snapshot inventories, synthetic regression coverage, real codec and
  PowerShell installation gates, detailed parity/usage/installation documentation.


## 0.3.0rc3 — repository and release maintenance

- Repair PUBLIC_FILES.json after the reviewed checkout/setup-python updates were
  merged; retain pinned action SHAs and the fail-closed public-file guard.
- Identify changed/added/removed public paths in manifest-mismatch diagnostics.
- Publish a new version automatically when its reviewed version bump reaches
  main, only after the complete validation matrix and distribution checks pass.
- Create an annotated version tag without moving existing tags; verify uploaded
  asset size/digests before publishing the draft. Preserve published assets on
  retries. Existing rc2 artifacts remain unchanged.
- Use version-specific release notes, update post-publication validation evidence,
  and document branch-aware dependency maintenance and safe retry behavior.
- Export rendering, compression defaults, source preservation and saved settings
  are unchanged apart from reporting the new package version.

## 0.3.0rc2 — public-release preparation

- Fix Windows source capture to permit owner-driven rotation without granting the
  exporter write/delete access; retain replacement-detection regression coverage.
- Replace one-line stage-switch spam with a measured two-line terminal adapter,
  sparse logs, structured events, bounded heartbeats and grouped chunk/artifact
  work. Publication exposes checksum, copy and readback stages separately.
- Parse external 7-Zip CR/backspace/member progress while retaining bounded output,
  interruption cleanup, and independent archive verification.
- Add Codex-compatible filename fields, timestamp/timezone abbreviations, title
  slugs, optional last-eight UUID suffix, mode descriptors and count suffixes.
  Preserve saved rc1 templates; migration is explicit.
- Ordinary export stdout is the final path; JSON results remain opt-in.
- Add public-file allowlisting, safe repository bootstrap, cross-platform CI,
  gated prerelease workflow, source/wheel distribution checks and maintainer docs.
- Do not claim complete Codex feature parity or an already published remote.

## 0.3.0rc1 — private development baseline

Initial portable streaming/indexed Antigravity exporter with native-source
preservation, SQLite reconciliation, verified archives and PowerShell helpers.
Public history contains a sanitized code baseline only, not private fixtures or
case-specific documentation. Earlier development archive checks are not current
cross-platform release certification.
