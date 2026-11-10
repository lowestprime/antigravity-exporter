#!/usr/bin/env python3
"""Publish validated distributions without moving tags or replacing released assets.

Invoked by the release workflow only after the reusable validation job succeeds.
Authentication is provided to gh through GH_TOKEN, never a command argument.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:rc\d+)?\Z")
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")


def checked(args: list[str], *, stdin: str | None = None) -> str:
    result = subprocess.run(args, input=stdin, text=True, encoding='utf-8',
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise RuntimeError(f'{args[0]} failed: {result.stderr[-4000:]}')
    return result.stdout


def api(endpoint: str, payload: dict | None = None, *, missing_ok: bool = False):
    args = ['gh', 'api', endpoint, '--method', 'POST' if payload is not None else 'GET']
    if payload is not None:
        args += ['--input', '-']
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                            text=True, encoding='utf-8', stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        if missing_ok and '(HTTP 404)' in result.stderr:
            return None
        raise RuntimeError(f'GitHub request failed for {endpoint}: {result.stderr[-4000:]}')
    return json.loads(result.stdout) if result.stdout.strip() else None


def package_version(root: Path) -> str:
    module = (root / 'antigravity_exporter/__init__.py').read_text(encoding='utf-8')
    project = (root / 'pyproject.toml').read_text(encoding='utf-8')
    a = re.search(r'^__version__\s*=\s*"([^"]+)"', module, re.M)
    b = re.search(r'^version\s*=\s*"([^"]+)"', project, re.M)
    if not a or not b or a[1] != b[1] or not VERSION_RE.fullmatch(a[1]):
        raise ValueError('Package version declarations disagree or are unsupported')
    return a[1]


def validate_context(event: str, ref: str, sha: str, version: str) -> str:
    if not SHA_RE.fullmatch(sha):
        raise ValueError('Expected an exact commit SHA')
    if not VERSION_RE.fullmatch(version):
        raise ValueError('Unsupported release version')
    tag = 'v' + version
    if event not in ('push', 'workflow_dispatch'):
        raise ValueError('Publication is not allowed from this event')
    if event == 'workflow_dispatch' and ref != 'refs/heads/main':
        raise ValueError('Manual publication is restricted to main')
    if ref != 'refs/heads/main' and not (event == 'push' and ref == 'refs/tags/' + tag):
        raise ValueError('Publication requires main or the exact package-version tag')
    return tag


def expected_names(version: str) -> set[str]:
    return {'antigravity-exporter-' + version + '.zip',
            'antigravity_thread_exporter-' + version + '-py3-none-any.whl',
            'antigravity_thread_exporter-' + version + '.tar.gz', 'SHA256SUMS.txt'}


def validated_assets(directory: Path, version: str) -> dict[str, dict]:
    expected = expected_names(version)
    paths = list(directory.iterdir())
    if {p.name for p in paths} != expected or any(not p.is_file() or p.is_symlink() for p in paths):
        raise ValueError('Distribution inventory is not exactly the four release assets')
    checksums = {}
    for line in (directory / 'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  ([^/\\]+)', line)
        if not match or match[2] in checksums:
            raise ValueError('Malformed or duplicate distribution checksum')
        checksums[match[2]] = match[1]
    if set(checksums) != expected - {'SHA256SUMS.txt'}:
        raise ValueError('Checksum inventory mismatch')
    result = {}
    for path in paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.name in checksums and checksums[path.name] != digest:
            raise ValueError('Distribution checksum mismatch: ' + path.name)
        result[path.name] = {'size': path.stat().st_size, 'digest': 'sha256:' + digest}
    return result


def notes_for_version(changelog: str, version: str, repo: str, sha: str, run_id: str) -> str:
    headings = list(re.finditer(r'^## ([^\n]+)\n', changelog, re.M))
    for index, heading in enumerate(headings):
        if heading[1].split()[0] == version:
            end = headings[index + 1].start() if index + 1 < len(headings) else len(changelog)
            body = changelog[heading.start():end].strip()
            break
    else:
        raise ValueError('No changelog section for the package version')
    return (body + '\n\nValidated commit: `' + sha + '`.\n\n'
            + 'Validation and publication: https://github.com/' + repo
            + '/actions/runs/' + run_id + '\n\n'
            + 'Existing release tags and assets are never overwritten. '
            + 'Release candidates remain prereleases.\n')


def tag_commit(repo: str, tag: str) -> str | None:
    item = api(f'repos/{repo}/git/ref/tags/{tag}', missing_ok=True)
    if item is None:
        return None
    obj = item['object']
    for _ in range(8):
        if obj['type'] == 'commit':
            return obj['sha']
        if obj['type'] != 'tag':
            break
        obj = api(f'repos/{repo}/git/tags/{obj["sha"]}')['object']
    raise ValueError('Tag does not resolve to a commit')


def verify_uploaded(release: dict, expected: dict[str, dict]) -> None:
    assets = release.get('assets', [])
    if len(assets) != len(expected) or {a['name'] for a in assets} != set(expected):
        raise ValueError('Uploaded release asset inventory mismatch')
    for asset in assets:
        wanted = expected[asset['name']]
        if (asset.get('state') != 'uploaded' or asset.get('size') != wanted['size']
                or asset.get('digest') != wanted['digest']):
            raise ValueError('Uploaded asset size/digest mismatch: ' + asset['name'])


def publish() -> dict:
    repo = os.environ['GH_REPO']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Invalid repository identifier')
    sha = os.environ['GITHUB_SHA']
    event, ref = os.environ['GITHUB_EVENT_NAME'], os.environ['GITHUB_REF']
    version = package_version(ROOT)
    tag = validate_context(event, ref, sha, version)
    if checked(['git', 'rev-parse', 'HEAD']).strip() != sha:
        raise ValueError('Checked-out commit differs from validated workflow commit')
    if ref == 'refs/heads/main':
        current = api(f'repos/{repo}/git/ref/heads/main')['object']['sha']
        if current != sha:
            raise ValueError('main advanced after validation; publish from the current reviewed commit')
    resolved = tag_commit(repo, tag)
    if resolved is not None and resolved != sha:
        raise ValueError('Existing tag points elsewhere; bump the version instead of moving it')
    release = api(f'repos/{repo}/releases/tags/{tag}', missing_ok=True)
    if release is not None and not release.get('draft', False):
        if resolved != sha:
            raise ValueError('Published release tag identity mismatch')
        assets = release.get('assets', [])
        if (len(assets) != 4 or {a['name'] for a in assets} != expected_names(version)
                or any(a.get('state') != 'uploaded' or not a.get('digest') for a in assets)):
            raise ValueError('Existing published release has unexpected assets; refusing to modify it')
        return {'status': 'existing-release-preserved', 'tag': tag, 'url': release['html_url']}
    expected = validated_assets(ROOT / 'dist', version)
    notes = notes_for_version((ROOT / 'CHANGELOG.md').read_text(encoding='utf-8'),
                              version, repo, sha, os.environ['GITHUB_RUN_ID'])
    if resolved is None:
        if ref != 'refs/heads/main':
            raise ValueError('Tag-triggered publication cannot create its missing tag')
        obj = api(f'repos/{repo}/git/tags', {'tag': tag, 'message': 'Antigravity Exporter ' + version,
                                           'object': sha, 'type': 'commit'})
        api(f'repos/{repo}/git/refs', {'ref': 'refs/tags/' + tag, 'sha': obj['sha']})
    if release is None:
        release = api(f'repos/{repo}/releases', {'tag_name': tag, 'target_commitish': sha,
                      'name': 'Antigravity Exporter ' + version, 'body': notes,
                      'draft': True, 'prerelease': 'rc' in version, 'make_latest': 'false'})
    existing = {a['name']: a for a in release.get('assets', [])}
    if set(existing) - set(expected):
        raise ValueError('Draft contains unexpected assets; refusing to remove or replace them')
    for name, wanted in sorted(expected.items()):
        if name in existing:
            a = existing[name]
            if any(a.get(k) != v for k, v in wanted.items()) or a.get('state') != 'uploaded':
                raise ValueError('Draft asset differs; refusing to clobber ' + name)
        else:
            checked(['gh', 'release', 'upload', tag, str(ROOT / 'dist' / name), '--repo', repo])
    release = api(f'repos/{repo}/releases/{release["id"]}')
    verify_uploaded(release, expected)
    with tempfile.TemporaryDirectory(prefix='agy-release-notes-') as directory:
        path = Path(directory) / 'notes.md'
        path.write_text(notes, encoding='utf-8')
        checked(['gh', 'release', 'edit', tag, '--repo', repo, '--draft=false',
                 '--latest=false' if 'rc' in version else '--latest=true',
                 '--notes-file', str(path)])
    final = api(f'repos/{repo}/releases/{release["id"]}')
    if final.get('draft') or final.get('prerelease') != ('rc' in version):
        raise ValueError('Release publication state mismatch')
    verify_uploaded(final, expected)
    return {'status': 'published', 'tag': tag, 'commit': sha, 'url': final['html_url'],
            'prerelease': final['prerelease'], 'assets': expected}


if __name__ == '__main__':
    print(json.dumps(publish(), indent=2))
