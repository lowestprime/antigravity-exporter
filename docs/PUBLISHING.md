# Initialize and maintain the public repository

The maintained remote is `lowestprime/antigravity-exporter`. For this existing
repository, use the maintenance and release process below. The bootstrap section
applies only when initializing a genuinely new repository; it does not update or
replace the existing remote.

## Bootstrap of a new repository

Repository creation requires the owner's authenticated GitHub CLI. The bootstrap
creates a fresh local folder from PUBLIC_FILES.json. It refuses an existing
destination and never force-pushes.

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

Keep all three release declarations aligned:

- `antigravity_exporter/__init__.py`: `__version__`.
- `pyproject.toml`: project `version`.
- `AntigravityExporter.psd1`: `ModuleVersion` and, for a release candidate,
  `PrivateData.PSData.Prerelease`.

For v0.4.0, all three version values are `0.4.0`, with no prerelease suffix.
PowerShell requires a numeric `ModuleVersion`; a future Python version such as
`0.4.1rc1` corresponds to `ModuleVersion = '0.4.1'` and
`Prerelease = 'rc1'` inside `PrivateData.PSData`. Do not put `rc1` in ModuleVersion
itself. The publication guard checks the normalized Python/PowerShell versions
and rejects a disagreement. Keep the module GUID unchanged across upgrades.
See Microsoft's [module manifest documentation](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_module_manifests)
and [prerelease metadata documentation](https://learn.microsoft.com/en-us/powershell/gallery/concepts/module-prerelease-support).

Add a version-specific CHANGELOG.md section, review/refresh PUBLIC_FILES.json,
and merge only after CI passes. A push to main changing either Python version
file runs release.yml; changing only the PowerShell manifest does not trigger a
new release. Publication has `needs: validate`: it waits for Python 3.10 and 3.13
on Linux, macOS and Windows, real ZIP/LZMA checks in all six jobs, Windows
PowerShell validation, and distribution validation. It creates an annotated tag
for that exact commit, uploads four checked assets to a draft, verifies their
reported digests/sizes, and publishes. RC versions remain prereleases; a stable
version such as v0.4.0 is published as the latest release only after those gates.

The four v0.4.0 assets are `antigravity-exporter-0.4.0.zip`,
`antigravity_thread_exporter-0.4.0-py3-none-any.whl`,
`antigravity_thread_exporter-0.4.0.tar.gz`, and `SHA256SUMS.txt`. The portable ZIP
includes the PowerShell module, manifest, installer, tests and documentation. A
Python wheel installation alone does not install the PowerShell module into a
module discovery directory. See [POWERSHELL_INSTALL.md](POWERSHELL_INSTALL.md)
for the persistent per-user installation.

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

Review the staged content before committing. Merge only after CI passes. For a
release, align all three version declarations, update CHANGELOG.md, refresh the
public allowlist, commit on a PR branch and merge after CI; the reviewed Python
version-file change starts automatic release validation. Do not put private
source catalogs, conversation archives, database snapshots, benchmark receipts or
local export paths into public tests, documentation, release assets or Git
history. Keep public fixtures synthetic. Never force-push published tags.

After repository creation, the owner should enable a main-branch ruleset requiring
pull requests and the actual discovered CI checks, block force pushes/deletions,
and enable secret scanning/push protection and private vulnerability reporting
where offered. These server settings require appropriate administrative authority
and are **not** silently enabled by these scripts. Dependabot configuration is
included, but any proposed update needs review and a refreshed allowlist.

## Historical optional preparation bundle

The earlier optional bundle is separate from the four current release assets.
That preparation bundle contains sanitized development history authored by
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
