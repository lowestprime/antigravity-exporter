"""Real Bash-to-Python forwarding without profile edits or source export."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(shutil.which('bash'), 'Bash unavailable')
class BashHelpers(unittest.TestCase):
    def test_values_batch_and_selectors_survive_argument_boundary(self):
        helper=Path(__file__).resolve().parents[1]/'bash'/'antigravity-exporter.sh'
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);probe=root/'probe.py';out=root/'seen.json'
            probe.write_text('import json,os,sys; open(os.environ["AGY_TEST_ARGUMENTS"],"w").write(json.dumps(sys.argv[1:]))')
            env={**os.environ,'AGY_EXPORT_PYTHON':sys.executable,'AGY_EXPORT_SCRIPT':str(probe),'AGY_TEST_ARGUMENTS':str(out)}
            script='source "$1"; shift; agy-since-text "$1" "$2" --since-occurrence 2 --out-dir "$3"'
            sid='11111111-2222-4333-8444-555555555555';text='-Turn; $(literal) "quotes" 🧠';directory='/a path/with spaces'
            subprocess.run(['bash','-c',script,'test',str(helper),sid,text,directory],env=env,check=True)
            self.assertEqual(json.loads(out.read_text()),['export','--mode','thread','--id',sid,'--since-text='+text,'--since-occurrence','2','--out-dir',directory])
            script='source "$1"; agy-batch "$2" "$3" --separate-archives'
            subprocess.run(['bash','-c',script,'test',str(helper),sid,sid],env=env,check=True)
            self.assertEqual(json.loads(out.read_text()),['export','--mode','thread','--id',sid,'--id',sid,'--separate-archives'])

    def test_missing_project_rejected_before_python_execution(self):
        helper=Path(__file__).resolve().parents[1]/'bash'/'antigravity-exporter.sh'
        result=subprocess.run(['bash','-c','source "$1"; agy-project','test',str(helper)],capture_output=True,text=True)
        self.assertEqual(result.returncode,2)
        self.assertIn('Usage: agy-project',result.stderr)
