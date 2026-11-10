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

## Automatic releases from reviewed version bumps

Bump both `antigravity_exporter/__init__.py` and `pyproject.toml`, add a
version-specific CHANGELOG.md section, review/refresh PUBLIC_FILES.json, and merge
only after CI passes. A push to main changing a version file runs release.yml.
Publication has `needs: validate`: it waits for six OS/Python jobs, Windows
PowerShell/real-LZMA checks and distribution validation. It creates an annotated
tag for that exact commit, uploads four checked assets to a draft, verifies their
reported digests/sizes, and publishes. RC versions remain prereleases.

The same job creates the tag and publishes; it does not rely on a tag pushed with
GITHUB_TOKEN triggering another workflow. Existing published assets and tags are
never overwritten. A different commit using an existing version is rejected.

`New-AntigravityRelease.ps1` remains an optional tag-based route for older setups.
Do not race it against automatic publication or recreate an existing version.
For a transient failure, inspect the run and rerun failed jobs. Manual dispatch
is restricted to main. If a draft already contains assets with different hashes,
publication stops for maintainer review instead of replacing them silently.
The workflow only has write access in the publish job, never in PR validation.

The published rc2 release and its validated assets remain unchanged; subsequent
maintenance is released under a new version. This is not a claim that server-side
branch protection or release immutability has been administratively configured.

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
allowlist, commit on a PR branch and merge after CI; the reviewed version-file change
starts automatic release validation. Never force-push
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

## Dependency PRs and the public manifest

Use a dedicated PR branch, not an unchanged main checkout. Check `git branch
--show-current` and the proposed files before refreshing. Review workflow action
pins and release notes, run `python tools/public_release.py --refresh`, commit
PUBLIC_FILES.json alongside the intended change, and run
`python tools/public_release.py --tracked`. Rebase/merge the latest main and
refresh again when another PR changed a covered file. Require fresh CI before
merging. Never refresh automatically inside an ordinary CI check: that would
accept content not represented by the reviewed manifest. Failed historical runs
are retained as an audit trail rather than deleted to conceal their results.
