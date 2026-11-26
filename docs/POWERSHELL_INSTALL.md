# Permanent PowerShell installation

Antigravity Exporter 0.4.0 includes a PowerShell module manifest with explicit
function and alias exports. Installing it in a standard module directory makes
`agy-*` commands discoverable in new PowerShell 7 sessions. The installer does
not need to add a loader to any profile.

## Install or upgrade for the current user

Use the reviewed source from the matching release, or a verified checkout. In
PowerShell 7, from that source directory:

```powershell
$ErrorActionPreference = 'Stop'
.\Install-AntigravityExporter.ps1 -Scope CurrentUser
agy-export --version
Get-Module AntigravityExporter | Select-Object Name, Version, Path
agy-doctor
```

The default Windows destination is the active PowerShell 7 user module root,
usually `Documents\PowerShell\Modules\AntigravityExporter\0.4.0`. The actual root
is derived from the current-user all-hosts profile directory and checked against
`PSModulePath`; a redirected Documents folder is supported. On Linux and macOS,
the user module root is under `XDG_DATA_HOME` or `~/.local/share/powershell`.
No administrator session is needed.

The installer verifies every public source file against `PUBLIC_FILES.json`,
stages a separate copy, verifies the copied hashes and lengths, unblocks only
verified copied PowerShell code on Windows/macOS, and then
publishes the version directory. The module version and Python package version
must agree. A second installation of identical bytes is a no-op. Other version
directories and an older unversioned module remain available. Replacing a
modified copy of the same version requires `-Force` and first moves it to an
`AntigravityExporter-install-backups` directory outside module discovery.

By default the verified module is imported with `-Global`, making the commands
available in the caller after the installer returns. `-NoImport` installs only;
`-WhatIf` validates and previews without creating the installation. An optional
`-PythonPath` chooses an existing Python 3.10+ executable for the current session.
It does not install packages, change interpreter defaults or persist a new
interpreter setting.

Original download markers remain untouched. Only copied `.ps1`, `.psm1` and
`.psd1` files in the verified installation have their download-block markers
removed; `-WhatIf` performs no unblocking. The installer leaves PowerShell
profiles, execution policies, PATH, Python
packages, Codex helpers and Antigravity exporter settings unchanged. If execution
policy blocks a downloaded script, review the verified file first and use
`Unblock-File -LiteralPath .\Install-AntigravityExporter.ps1` for that file when
appropriate; the installer never changes execution policy.

## Existing profile loaders

A previous loader that uses `Import-Module AntigravityExporter` discovers the
new version. A profile that imports an explicit older `.psm1` path continues to
request that file. Change only that import to `Import-Module AntigravityExporter`
if that is the intended upgrade. The installer does not rewrite arbitrary
profile text or dot-source the whole profile into the running session.

A new session normally needs no explicit import:

```powershell
Get-Command agy-since-text
agy-export --version
```

If you intentionally disabled automatic module loading, use the existing
name-based profile loader or run `Import-Module AntigravityExporter`. Restoring
unqualified automatic discovery would require changing your chosen
`PSModuleAutoLoadingPreference`; the installer does not do that.

## Portable installation and rollback

For an isolated copy outside module discovery:

```powershell
.\Install-AntigravityExporter.ps1 -Scope Portable -Destination 'D:\Tools\AntigravityExporter\0.4.0'
Import-Module 'D:\Tools\AntigravityExporter\0.4.0\AntigravityExporter.psd1'
```

For backward compatibility, an explicit `-Destination` without `-Scope` implies
`Portable`. To install a custom current-user module root, explicitly supply both
`-Scope CurrentUser` and `-Destination`; its parent module root must already be
in `PSModulePath`, and the destination must end in `AntigravityExporter/0.4.0`.

Previous versions are retained. To activate an earlier manifest-bearing version
in a session, remove the loaded module and import its exact manifest path. An
older unversioned installation can likewise be imported by its preserved `.psm1`
path. Source archives, saved exporter settings and exported conversations are
not changed by switching the module in memory.

## Helper additions

| Helper | Purpose |
| --- | --- |
| `agy-help`, `agy-exporter`, `antigravity-exporter` | Versioned CLI help and forwarding |
| `agy-latest [MODE]` | Export the latest resolved session |
| `agy-tokenizer` | Diagnose the installed tokenizer/runtime without installing anything |
| `agy-since UUID SELECTOR` | Inclusive suffix from `turn:N`, `id:step:N`, `text:WORDS` or `time:ISO` |
| `agy-since-text UUID TEXT [-Occurrence N]` | Full prompt text search; ambiguity is explicit |
| `agy-since-turn UUID N` | Inclusive observed user-turn boundary |
| `agy-since-id UUID 'step:N'` | Exact recorded step coordinate, or a real recorded native ID |
| `agy-since-time UUID ISO_TIME` | First recorded user turn at or after an offset-qualified instant |
| `agy-turns UUID -Sort turn:desc -Pager auto` | Sort/paginate without changing turn IDs |
| `agy-archive-config` | View saved and effective archive preferences |

Additional GNU-style CLI options pass through. Familiar PowerShell-style aliases
include `-ArchiveMaxBytes`, `-WorkDir`, `-SeparateArchives`, `-SinceText`,
`-SinceOccurrence`, `-Sort`, `-SortOrder`, `-Pager`, `-PageSize`, `-NoTimestamps`
and `-TimestampTimezone`. Option values retain their boundaries, quotes and
Unicode; leading-dash literal values are encoded as `--option=value` for argparse.
Only `agy-batch` accepts an array of session IDs as one positional argument.
It also accepts piped ID strings or session objects, preferring a recorded
variant-qualified `selector` over an unqualified `id`:

```powershell
$catalog = agy-sessions -Json | ConvertFrom-Json
$catalog | Where-Object project -eq 'Example Project' | agy-batch -NoProgress
```

## Validation

`Test-AntigravityShell.ps1 -PythonPath <existing-python>` exercises actual
PowerShell-to-Python argument forwarding and a temporary installation. It tests
`-WhatIf`, activation after script return, fresh-process module auto-loading,
name-based selection over an older unversioned module, idempotence, refusal to
replace modified files, and byte-preserving replacement backups. It uses a
synthetic module root and does not read or edit a real profile. The Windows CI
entry point invokes this suite as part of `Test-AntigravityExporter.ps1`.

## PowerShell documentation

- [Module authoring and explicit export metadata](https://learn.microsoft.com/en-us/powershell/scripting/dev-cross-plat/performance/module-authoring-considerations?view=powershell-7.6)
- [Importing modules and automatic discovery](https://learn.microsoft.com/en-us/powershell/scripting/developer/module/importing-a-powershell-module?view=powershell-7.6)
- [Unblock-File and download markers](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.utility/unblock-file?view=powershell-7.6)
