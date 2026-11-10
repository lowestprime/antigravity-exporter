# Changelog

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
