# Contributing

Keep all conversation fixtures synthetic and generated at test time. Do not commit
application profiles, personal logs, session identifiers, credentials, screenshots,
or generated exports. Use feature branches and pull requests.

```sh
python -m unittest discover -s tests -v
python tools/public_release.py --refresh
python tools/public_release.py
```

Refresh the public allowlist only after reviewing your changed files. The refresh
is an explicit acceptance of the public source set, not a substitute for review.
Stage the complete reviewed change, including PUBLIC_FILES.json, then run:

```sh
python tools/public_release.py --tracked
git diff --cached --check
git diff --cached --stat
```

Windows contributors should run Test-AntigravityExporter.ps1 with their selected
Python and tools/smoke_external.py with an existing compatible 7-Zip executable.
Do not skip a Windows failure solely because the same test passes on POSIX.

Preserve source immutability, explicit incompleteness reporting, unredacted-source
separation, independent archive verification, and recoverable failure output.
Never call assistant text proof of execution completion. Do not force-push release
tags. A new build with changed content requires a new version and tag.
