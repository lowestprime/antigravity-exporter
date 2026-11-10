# Initialize and maintain the public repository

The requested remote is `lowestprime/antigravity-exporter`. Repository creation
requires the owner's authenticated GitHub CLI. The bootstrap deliberately creates
a fresh local folder from PUBLIC_FILES.json, not from an arbitrary Downloads or
exports directory. It refuses an existing destination and never force-pushes.

## First publication

From the reviewed portable source directory, after local tests:

```powershell
.\Publish-AntigravityRepository.ps1 -Destination C:\projects\antigravity-exporter -WhatIf
.\Publish-AntigravityRepository.ps1 -Destination C:\projects\antigravity-exporter -Publish
```

The script verifies the public manifest, reruns the local PowerShell/Python checks,
and verifies existing Git author settings and the authenticated owner. It then initializes `main`, commits under your configured identity,
creates the public remote, pushes, and checks remote visibility and SHA readback.
Without `-Publish`, it initializes local Git only and configures origin without
claiming a server-side repository exists. It does not configure account tokens,
change global Git settings, or install tools.

An already-existing remote is not overwritten. A failed publish can leave a valid
local commit or an empty server-side repository. Inspect `git status`, `git remote
-v`, and `gh repo view` before resuming manually; do not delete or force-reset an
unfamiliar existing repository.

## First prerelease

```powershell
Set-Location C:\projects\antigravity-exporter
.\New-AntigravityRelease.ps1
```

The script requires a clean main worktree, remote main equal to local HEAD, and a
successful `ci.yml` run for that exact SHA. It creates an annotated `vVERSION`
tag. The tag-triggered release workflow reruns the six OS/Python test jobs and
package validation; publication has `needs: validate`. It then builds a wheel,
source distribution, portable ZIP and SHA256SUMS. Release candidates are marked
prerelease and `latest=false`. An existing tag/release is never overwritten.

The scripts/workflows are provided, not evidence of a completed GitHub run.
Inspect the actual Actions result and release assets. Do not label rc2 stable
until its Windows regression and real encoder smoke test have passed.

## Ongoing maintenance

```powershell
git pull --ff-only
git switch -c fix/descriptive-topic
# Make and review the changes.
python -m unittest discover -s tests -v
python tools/public_release.py --refresh
git add -- .
python tools/public_release.py --tracked
git diff --cached --check
git commit -m 'fix: describe the correction'
git push -u origin HEAD
gh pr create --base main
```

Review the staged content before committing. Merge only after CI passes. Bump
both package version declarations, update CHANGELOG.md, refresh the public
allowlist, commit and push main, then use the release script. Never force-push
published tags or upload private archives as debugging fixtures.

After repository creation, the owner should enable a main-branch ruleset requiring
pull requests and the actual discovered CI checks, block force pushes/deletions,
and enable secret scanning/push protection and private vulnerability reporting
where offered. These server settings require appropriate administrative authority
and are **not** silently enabled by these scripts. Dependabot configuration is
included, but any proposed update needs review and a refreshed allowlist.

## Git bundle supplied with the preparation kit

The separate optional bundle contains sanitized development history authored by
`Antigravity Exporter automation`, not an impersonated maintainer. It has never
contained the private transcripts. Verify with `git bundle verify FILE` and clone
to a fresh folder to inspect it. The default bootstrap instead creates a fresh
initial commit using your configured local Git identity.

Official command references:
- https://cli.github.com/manual/gh_repo_create
- https://cli.github.com/manual/gh_run_watch
- https://cli.github.com/manual/gh_release_create
- https://docs.github.com/en/actions/reference/security/secure-use
