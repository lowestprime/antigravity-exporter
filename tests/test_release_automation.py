"""Synthetic tests only: no credential or real conversation data."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('publish_github_release', ROOT/'tools/publish_github_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
VERSION = '0.3.0rc3'
SHA = 'a' * 40


class ReleaseAutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dist = self.root/'dist'
        self.dist.mkdir()
        for name in release.expected_names(VERSION) - {'SHA256SUMS.txt'}:
            (self.dist/name).write_bytes(('synthetic '+name).encode())
        self.write_sums()

    def write_sums(self):
        lines = [hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n'
                 for p in sorted(self.dist.iterdir()) if p.name!='SHA256SUMS.txt']
        (self.dist/'SHA256SUMS.txt').write_text(''.join(lines))

    def test_four_checked_assets(self):
        self.assertEqual(len(release.validated_assets(self.dist, VERSION)), 4)

    def test_stale_asset_refused(self):
        (self.dist/'old.zip').write_bytes(b'old')
        with self.assertRaisesRegex(ValueError, 'inventory'):
            release.validated_assets(self.dist, VERSION)

    def test_changed_asset_refused(self):
        next(self.dist.glob('*.whl')).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            release.validated_assets(self.dist, VERSION)

    def test_duplicate_checksum_refused(self):
        p = self.dist/'SHA256SUMS.txt'
        text=p.read_text();p.write_text(text+text.splitlines()[0]+'\n')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            release.validated_assets(self.dist, VERSION)

    def test_missing_checksum_refused(self):
        p=self.dist/'SHA256SUMS.txt';p.write_text(p.read_text().splitlines()[0]+'\n')
        with self.assertRaisesRegex(ValueError, 'inventory'):
            release.validated_assets(self.dist, VERSION)

    def test_main_and_matching_tag(self):
        for event, ref in [('push','refs/heads/main'),('push','refs/tags/v'+VERSION),
                           ('workflow_dispatch','refs/heads/main')]:
            self.assertEqual(release.validate_context(event,ref,SHA,VERSION),'v'+VERSION)

    def test_fork_pr_branch_and_wrong_tag_refused(self):
        for event,ref in [('pull_request','refs/pull/1/merge'),('push','refs/heads/topic'),
                          ('push','refs/tags/v1.2.3'),('workflow_dispatch','refs/tags/v'+VERSION)]:
            with self.assertRaises(ValueError):release.validate_context(event,ref,SHA,VERSION)

    def test_unvalidated_sha_refused(self):
        with self.assertRaisesRegex(ValueError,'SHA'):
            release.validate_context('push','refs/heads/main','main',VERSION)

    def test_injection_version_refused(self):
        with self.assertRaisesRegex(ValueError,'version'):
            release.validate_context('push','refs/heads/main',SHA,'1.0.0;echo unsafe')

    def test_version_pair_must_match(self):
        (self.root/'antigravity_exporter').mkdir()
        (self.root/'antigravity_exporter/__init__.py').write_text('__version__ = "0.3.0rc3"\n')
        p=self.root/'pyproject.toml';p.write_text('[project]\nversion = "0.3.0rc3"\n')
        self.assertEqual(release.package_version(self.root),VERSION)
        p.write_text('[project]\nversion = "0.3.0rc2"\n')
        with self.assertRaisesRegex(ValueError,'disagree'):release.package_version(self.root)

    def test_version_specific_notes(self):
        result=release.notes_for_version('# Changelog\n\n## 0.3.0rc3 — fix\nnew\n\n## 0.3.0rc2 — old\nold\n',
                                         VERSION,'example/project',SHA,'123')
        self.assertIn('new',result);self.assertNotIn('old',result)
        self.assertIn('/actions/runs/123',result)

    def test_missing_notes_refused(self):
        with self.assertRaisesRegex(ValueError,'changelog'):
            release.notes_for_version('# empty',VERSION,'example/project',SHA,'1')

    def test_uploaded_hashes_verified(self):
        wanted=release.validated_assets(self.dist,VERSION)
        assets=[dict(name=n,state='uploaded',**v) for n,v in wanted.items()]
        release.verify_uploaded({'assets':assets},wanted)
        assets[0]['digest']='sha256:'+'0'*64
        with self.assertRaisesRegex(ValueError,'digest'):release.verify_uploaded({'assets':assets},wanted)

    def test_uploaded_duplicate_refused(self):
        wanted=release.validated_assets(self.dist,VERSION)
        assets=[dict(name=n,state='uploaded',**v) for n,v in wanted.items()]
        assets.append(dict(assets[0]))
        with self.assertRaisesRegex(ValueError,'inventory'):release.verify_uploaded({'assets':assets},wanted)

    def test_annotation_resolves_to_commit(self):
        with patch.object(release,'api',side_effect=[{'object':{'type':'tag','sha':'b'*40}},
                                                    {'object':{'type':'commit','sha':SHA}}]):
            self.assertEqual(release.tag_commit('example/project','v'+VERSION),SHA)

    def test_unknown_tag_type_refused(self):
        with patch.object(release,'api',return_value={'object':{'type':'tree','sha':SHA}}):
            with self.assertRaisesRegex(ValueError,'commit'):release.tag_commit('example/project','v'+VERSION)

    def test_only_actual_404_is_absent(self):
        with patch.object(release.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','gh: Not Found (HTTP 404)')):
            self.assertIsNone(release.api('repos/example/project/thing',missing_ok=True))
        with patch.object(release.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','gh: Forbidden (HTTP 403)')):
            with self.assertRaises(RuntimeError):release.api('repos/example/project/thing',missing_ok=True)

    def test_existing_release_never_changed(self):
        env={'GH_REPO':'example/project','GITHUB_SHA':SHA,'GITHUB_EVENT_NAME':'push','GITHUB_REF':'refs/heads/main'}
        existing={'draft':False,'html_url':'https://github.com/example/project/releases/tag/v'+VERSION,
                  'assets':[{'name':n,'state':'uploaded','digest':'sha256:'+'0'*64} for n in release.expected_names(VERSION)]}
        with patch.dict(os.environ,env),patch.object(release,'package_version',return_value=VERSION), \
                patch.object(release,'checked',return_value=SHA) as calls, \
                patch.object(release,'tag_commit',return_value=SHA), \
                patch.object(release,'api',side_effect=[{'object':{'sha':SHA}},existing]) as api:
            self.assertEqual(release.publish()['status'],'existing-release-preserved')
            self.assertEqual(calls.call_count,1)
            self.assertEqual(api.call_count,2)

    def test_existing_tag_never_moved(self):
        env={'GH_REPO':'example/project','GITHUB_SHA':SHA,'GITHUB_EVENT_NAME':'push','GITHUB_REF':'refs/heads/main'}
        with patch.dict(os.environ,env),patch.object(release,'package_version',return_value=VERSION), \
                patch.object(release,'checked',return_value=SHA),patch.object(release,'tag_commit',return_value='b'*40), \
                patch.object(release,'api',return_value={'object':{'sha':SHA}}):
            with self.assertRaisesRegex(ValueError,'bump the version'):release.publish()

    def test_advanced_main_refused(self):
        env={'GH_REPO':'example/project','GITHUB_SHA':SHA,'GITHUB_EVENT_NAME':'push','GITHUB_REF':'refs/heads/main'}
        with patch.dict(os.environ,env),patch.object(release,'package_version',return_value=VERSION), \
                patch.object(release,'checked',return_value=SHA), \
                patch.object(release,'api',return_value={'object':{'sha':'b'*40}}):
            with self.assertRaisesRegex(ValueError,'advanced'):release.publish()


if __name__=='__main__':unittest.main()
