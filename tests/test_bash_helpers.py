"""Real Bash-to-Python forwarding without profile edits or source export.

On Windows use a native Git/MSYS Bash, not the unrelated System32 WSL launcher.
Only Bash-owned paths are converted with that runtime's cygpath. Probe arguments
are delivered to native Python unchanged so this tests the helper's boundaries,
not MSYS's optional path rewriting heuristics.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


def _find_bash():
    if os.name != 'nt':
        return shutil.which('bash')
    candidates = []
    git = shutil.which('git')
    if git:
        # Git for Windows exposes git.exe in either its cmd or bin directory.
        git_root = Path(git).resolve().parent.parent
        candidates.extend(git_root / part for part in ('bin/bash.exe', 'usr/bin/bash.exe'))
    for key in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
        base = os.environ.get(key)
        if base:
            root = Path(base) / ('Programs/Git' if key == 'LOCALAPPDATA' else 'Git')
            candidates.extend(root / part for part in ('bin/bash.exe', 'usr/bin/bash.exe'))
    discovered = shutil.which('bash')
    if discovered:
        candidates.append(Path(discovered))
    seen = set()
    for candidate in candidates:
        identity = str(candidate).replace('\\', '/').casefold()
        if identity in seen or not candidate.is_file():
            continue
        seen.add(identity)
        # Do not launch WSL first-run/install machinery while discovering a test shell.
        if '/windows/system32/' in identity or '/windows/sysnative/' in identity:
            continue
        try:
            result = subprocess.run([str(candidate), '--noprofile', '--norc', '-c', '/usr/bin/uname -s'],
                                    capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and result.stdout.strip().upper().startswith(('MINGW', 'MSYS', 'CYGWIN')):
            return str(candidate)
    return None


BASH = _find_bash()


def _environment(**values):
    env = {**os.environ, **values}
    if os.name == 'nt':
        # The test's '/a path/...' must not become a Git installation pathname.
        # Native Python still receives ordinary Windows paths for its own files.
        env['MSYS2_ARG_CONV_EXCL'] = '*'
        env['MSYS_NO_PATHCONV'] = '1'
    return env


def _bash_path(path, env):
    if os.name != 'nt':
        return str(path)
    result = subprocess.run([BASH, '--noprofile', '--norc', '-c', '/usr/bin/cygpath -u "$1"',
                             'agy-test-path', str(path)], env=env, check=True,
                            capture_output=True, encoding='utf-8', timeout=10)
    return result.stdout.strip()


def _run(script, *arguments, env=None, **options):
    # Non-login Git Bash need not inherit /usr/bin in Windows PATH.
    script = 'PATH="/usr/bin:/bin:$PATH"; export PATH\n' + script
    return subprocess.run([BASH, '--noprofile', '--norc', '-c', script, 'agy-test',
                           *arguments], env=env or _environment(), **options)


@unittest.skipUnless(BASH, 'Native Bash unavailable (Windows requires Git/MSYS Bash, not a WSL launcher)')
class BashHelpers(unittest.TestCase):
    def test_values_batch_and_selectors_survive_argument_boundary(self):
        helper = Path(__file__).resolve().parents[1] / 'bash' / 'antigravity-exporter.sh'
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); probe = root / 'probe.py'; out = root / 'seen.json'
            probe.write_text('import json,os,sys; open(os.environ["AGY_TEST_ARGUMENTS"],"w").write(json.dumps(sys.argv[1:]))')
            env = _environment(AGY_EXPORT_SCRIPT=str(probe), AGY_TEST_ARGUMENTS=str(out))
            env['AGY_EXPORT_PYTHON'] = _bash_path(sys.executable, env)
            helper_path = _bash_path(helper, env)
            script = 'source "$1"; shift; agy-since-text "$1" "$2" --since-occurrence 2 --out-dir "$3"'
            sid = '11111111-2222-4333-8444-555555555555'
            text = '-Turn; $(literal) "quotes" 🧠'; directory = '/a path/with spaces'
            _run(script, helper_path, sid, text, directory, env=env, check=True)
            self.assertEqual(json.loads(out.read_text()), ['export', '--mode', 'thread', '--id', sid,
                             '--since-text='+text, '--since-occurrence', '2', '--out-dir', directory])
            script = 'source "$1"; agy-batch "$2" "$3" --separate-archives'
            _run(script, helper_path, sid, sid, env=env, check=True)
            self.assertEqual(json.loads(out.read_text()), ['export', '--mode', 'thread', '--id', sid,
                             '--id', sid, '--separate-archives'])

    def test_missing_project_rejected_before_python_execution(self):
        helper = Path(__file__).resolve().parents[1] / 'bash' / 'antigravity-exporter.sh'
        env = _environment()
        result = _run('source "$1"; agy-project', _bash_path(helper, env), env=env,
                      capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Usage: agy-project', result.stderr)
