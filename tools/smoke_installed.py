"""Exercise the installed wheel's CLI outside the checkout with synthetic data.

Run with the same Python environment in which the wheel was installed:
    python tools/smoke_installed.py --expected-version 0.4.2
No repository package or test fixture is imported by this script.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import sysconfig
import tempfile
import zipfile


CHECKOUT = Path(__file__).resolve().parents[1]
SESSION = '11111111-2222-4333-8444-555555555555'
PROJECT = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
TITLE = 'Installed wheel fixture — Unicode ✓'
PROJECT_NAME = 'Synthetic installation project'
MODIFIED = '2026-01-01T00:00:00Z'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def snapshot(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def fixture(root):
    project = root / 'config' / 'projects' / (PROJECT + '.json')
    project.parent.mkdir(parents=True)
    project.write_text(json.dumps({'id': PROJECT, 'name': PROJECT_NAME}), encoding='utf-8')
    transcript = root / 'antigravity' / 'brain' / SESSION / '.system_generated' / 'logs' / 'transcript_full.jsonl'
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({'step_index': 0, 'source': 'USER_EXPLICIT',
                                     'type': 'USER_INPUT', 'status': 'DONE',
                                     'content': 'Synthetic installed-package smoke test.'}) + '\n', encoding='utf-8')
    database = root / 'antigravity' / 'conversation_summaries.db'
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute('CREATE TABLE conversation_summaries '
                           '(conversation_id TEXT, title TEXT, project_id TEXT, last_modified_time TEXT)')
        connection.execute('INSERT INTO conversation_summaries VALUES (?, ?, ?, ?)',
                           (SESSION, TITLE, PROJECT, MODIFIED))
    return transcript.stat().st_size


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-version', help='Fail if the installed version differs.')
    parser.add_argument('--executable', help='Explicit installed console entry point.')
    args = parser.parse_args()
    scripts = Path(sysconfig.get_path('scripts'))
    candidate = scripts / ('antigravity-export.exe' if os.name == 'nt' else 'antigravity-export')
    selected = args.executable or (str(candidate) if candidate.is_file() else shutil.which('antigravity-export'))
    require(selected is not None, 'No installed antigravity-export console entry point was found.')
    executable = Path(selected).resolve()
    require(executable.is_file(), 'Installed console entry point does not exist: ' + str(executable))
    require(not executable.is_relative_to(CHECKOUT), 'Console entry point is inside the checkout.')

    with tempfile.TemporaryDirectory(prefix='agy-installed-smoke-') as temporary:
        work = Path(temporary).resolve()
        require(not work.is_relative_to(CHECKOUT), 'Smoke-test working directory must be outside the checkout.')
        config = work / 'config.json'
        config.write_text(json.dumps({'method': 'Deflate', 'backend': 'python',
                                      'tokenizer': 'regex', 'token_encoding': 'o200k_base',
                                      'pager': 'never', 'progress_style': 'off',
                                      'timestamp_timezone': 'UTC'}), encoding='utf-8')
        config_before = config.read_bytes()
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        env.pop('PYTHONHOME', None)
        env.update(AGY_EXPORT_CONFIG=str(config), PYTHONUTF8='1')
        commands = 0

        def invoke(arguments, *, expected_code=0, command=None):
            nonlocal commands
            commands += 1
            result = subprocess.run([str(executable) if command is None else command, *arguments],
                                    cwd=work, env=env, capture_output=True, text=True,
                                    encoding='utf-8', timeout=60)
            require(result.returncode == expected_code,
                    f'Command {arguments!r} exited {result.returncode}, expected {expected_code}: '
                    + (result.stderr or result.stdout)[-4000:])
            require('Traceback (most recent call last)' not in result.stderr,
                    'Unexpected traceback: ' + result.stderr[-4000:])
            return result

        # -I rejects checkout/user-site/PYTHONPATH shadowing for the origin probe.
        probe = invoke(['-I', '-c',
                        'import importlib.metadata,json,sys,antigravity_exporter as p; '
                        'print(json.dumps({"file":p.__file__,"version":p.__version__, '
                        '"distribution_version":importlib.metadata.version("antigravity-thread-exporter"), '
                        '"python":sys.executable}))'], command=sys.executable)
        installed = json.loads(probe.stdout)
        require(not Path(installed['file']).resolve().is_relative_to(CHECKOUT),
                'Isolated import unexpectedly resolved to the repository package.')
        version = installed['version']
        require(version == installed['distribution_version'], 'Package/distribution versions differ.')
        if args.expected_version:
            require(version == args.expected_version, 'Unexpected installed version: ' + version)
        require(invoke(['--version']).stdout.strip() == version, 'Console entry-point version mismatch.')

        doctor = json.loads(invoke(['doctor']).stdout)
        require(doctor['version'] == version, 'Doctor reported the wrong package version.')
        require(Path(doctor['executable']).samefile(sys.executable), 'Doctor used a different Python interpreter.')
        require(doctor['archive_preflight_error'] is None and doctor['archiver'] is None,
                'Deflate/Python diagnostic unexpectedly required an external archiver.')
        require(all(type(doctor['codecs'].get(name)) is bool for name in ('lzma', 'zlib', 'tiktoken', 'zstandard')),
                'Doctor codec fields are missing or not booleans.')
        require(doctor['no_network_requests_or_package_installation_performed'] is True,
                'Doctor did not report its read-only probe boundary.')

        tokenizer = json.loads(invoke(['tokenizer']).stdout)
        require(tokenizer['version'] == version, 'Tokenizer reported the wrong package version.')
        require(Path(tokenizer['python']).samefile(sys.executable), 'Tokenizer used a different Python interpreter.')
        require(tokenizer['configured'] == 'regex' and tokenizer['encoding'] == 'o200k_base',
                'Tokenizer diagnostics ignored saved preferences.')
        require(tokenizer['no_network_or_installation_performed'] is True,
                'Tokenizer did not report its read-only probe boundary.')
        override = json.loads(invoke(['tokenizer', '--token-encoding', 'cl100k_base']).stdout)
        require(override['encoding'] == 'cl100k_base', 'Explicit tokenizer encoding did not override preferences.')
        required = invoke(['tokenizer', '--require-tiktoken'],
                          expected_code=0 if tokenizer['tiktoken_installed'] else 2)
        if tokenizer['tiktoken_installed']:
            require(json.loads(required.stdout)['configured'] == 'tiktoken',
                    'Required tokenizer was not selected.')
        else:
            require('tiktoken is not installed' in required.stderr,
                    'Missing required optional tokenizer was not explained.')

        source = work / 'source'
        source_bytes = fixture(source)
        before = snapshot(source)
        common = ['sessions', '--root', str(source), '--no-progress']
        rows = json.loads(invoke([*common, '--json']).stdout)
        require(len(rows) == 1 and rows[0]['id'] == SESSION, 'Installed session discovery returned the wrong identity.')
        require(rows[0]['title'] == TITLE and rows[0]['project'] == PROJECT_NAME,
                'Installed session discovery lost source metadata.')
        require(rows[0]['size'] == source_bytes and rows[0]['modified'] == MODIFIED,
                'Session JSON changed the source byte count or recorded modification timestamp.')
        human = invoke([*common, '--no-pager']).stdout
        headers = ('Session selector', 'Conversation title', 'JSONL size', 'Last modified',
                   'Transcript source', 'SQLite database', 'Project')
        require(any(all(header in line for header in headers) for line in human.splitlines()),
                'Human listing omitted its aligned column headers.')
        expected_row = ('antigravity:' + SESSION, TITLE, PROJECT_NAME, 'Full log', 'Missing',
                        f'{source_bytes} B', 'January 1, 2026 at 12:00:00 AM UTC')
        require(any(all(value in line for value in expected_row) for line in human.splitlines()),
                'Human listing omitted the session identity, source size, recorded date or readable labels.')
        require('\x1b[' not in human, 'Redirected human listing contains terminal control sequences.')
        require(snapshot(source) == before, 'Read-only listings modified their synthetic source store.')

        # Exercise a project export from the installed wheel, not only imports.
        transcript=source/'antigravity'/'brain'/SESSION/'.system_generated'/'logs'/'transcript_full.jsonl'
        rows=[{'step_index':0,'type':'USER_INPUT','source':'USER_EXPLICIT','status':'DONE','content':'Synthetic prompt'},
              {'step_index':7,'type':'PLANNER_RESPONSE','source':'MODEL','status':'DONE','content':'First observation ✓'},
              {'step_index':8,'type':'GENERIC','source':'TOOL','status':'DONE','content':'Synthetic output'},
              {'step_index':7,'type':'PLANNER_RESPONSE','source':'MODEL','status':'DONE','content':'Different recorded observation ✓'}]
        raw=b''.join((json.dumps(row,ensure_ascii=False)+'\r\n').encode('utf-8') for row in rows)
        transcript.write_bytes(raw);before=snapshot(source)
        output=work/'exports'
        export_args=['export','--root',str(source),'--project',PROJECT_NAME,'--out-dir',str(output),
                     '--no-progress','--json','--raw-jsonl','--review']
        exported=json.loads(invoke(export_args).stdout)
        require(exported['selected_records']==len(rows), 'Installed project export lost repeated source observations.')
        require(exported['source_step_order']['duplicate_occurrences']==1 and
                exported['source_step_order']['backward_transitions']==1,
                'Installed project export omitted source-order diagnostics.')
        with zipfile.ZipFile(exported['archive']) as archive:
            native=next(name for name in archive.namelist() if name.endswith('.jsonl') and not name.endswith('.records.jsonl'))
            structured=next(name for name in archive.namelist() if name.endswith('.records.jsonl'))
            require(archive.read(native)==raw, 'Installed project export changed original JSONL bytes.')
            require([json.loads(line) for line in archive.read(structured).splitlines()]==rows,
                    'Installed structured JSONL changed source order or content.')
        verification=json.loads(invoke(['verify',exported['archive']]).stdout)
        require(verification['embedded_manifest_hashes_matched'] and verification['complete_set_verified'],
                'Installed project archive did not pass member/manifest verification.')
        before_outputs=snapshot(output)
        strict=invoke([*export_args,'--strict-step-order'],expected_code=2)
        require('source record 4' in strict.stderr and 'byte ' in strict.stderr and SESSION in strict.stderr,
                'Installed strict-order rejection omitted source context.')
        require(snapshot(output)==before_outputs, 'Strict failure changed existing outputs or published partial output.')
        require(snapshot(source)==before, 'Project export modified its synthetic application-owned source.')
        require(config.read_bytes() == config_before, 'Diagnostics/listings/exports modified saved configuration.')

        print(f'PASS: {commands} installed-package subprocess checks; version {version}; '
              'isolated import, doctor, tokenizer, JSON/human sessions, project source-order preservation, '
              'archive verification, strict rejection and source integrity.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print('FAIL: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
