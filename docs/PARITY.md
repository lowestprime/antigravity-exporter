# Codex Exporter comparison and provenance

Reference inspected: `lowestprime/codex-exporter`, commit
`8db0172cab2d3db9d257b252062ebc1f8fe0f3f0`, package version 0.3.12.
This is a pinned comparison target, not an automatic dependency or update promise.

| Surface | Alignment | Important boundary |
|---|---|---|
| Timestamp | Same `%a_%m%d%Y_%I%M%S_%p` uppercase head, timezone abbreviation | Product-specific `AGY_EXPORT_TZ` |
| Default template | Same field structure, `antigravity_` prefix | Saved preferences survive upgrade |
| Title and mode | Lowercase slugs and numbered/last-N descriptors | Steps and records are different quantities |
| UUID suffix | Last eight hex digits, opt-in/batch/stable | Legacy first-eight `{short_id}` remains |
| Counts | Approximate versus encoding-exact suffix vocabulary | `none` never invents a token total |
| Archive defaults | LZMA/9/256m/64/19/auto; source+cleanup on; review off | No speed or worker-use guarantee |
| Default output | One ZIP; three members | Raw source remains confidential |
| Progress | Same style names, two-line panel, stage metrics, no false job ETA | Adapted implementation, not byte-identical upstream engine |
| Output channels | stderr progress, path stdout, explicit JSON | Listings retain Antigravity metadata |
| Source capture | Verified fixed prefix; read-only SQLite backup | Not cross-file atomic |
| Native artifacts | Antigravity-only opt-in feature | No automatic project-directory crawl |
| Review | Optional reversible text parts and explicit conversion | Does not guarantee receiving-model ingestion |
| Windows test | Real rename under delete-sharing, identity check remains | Must be exercised on Windows CI |
| Not equivalent | Rollback reconstruction, cloud import, compressed direct source, ACL repair | Not implemented or not meaningful for this storage |

Archive/source safety semantics take precedence over copying Codex option names
whose meanings would be misleading. The stored thinking flag is not equivalent
to Codex's explicit reasoning-summary option. `agy-live` is not an exact context.

Upstream source references:
- https://github.com/lowestprime/codex-exporter/blob/8db0172cab2d3db9d257b252062ebc1f8fe0f3f0/codex_exporter/progress.py
- https://github.com/lowestprime/codex-exporter/blob/8db0172cab2d3db9d257b252062ebc1f8fe0f3f0/codex_exporter/cli.py
- https://github.com/lowestprime/codex-exporter/blob/8db0172cab2d3db9d257b252062ebc1f8fe0f3f0/.github/workflows/ci.yml
- https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew
