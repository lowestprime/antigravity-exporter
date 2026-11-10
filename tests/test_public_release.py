"""Synthetic release hygiene checks: no private user fixture is embedded here."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'tools' / 'public_release.py'
spec = importlib.util.spec_from_file_location('public_release', MODULE)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

class PublicReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(release, 'ROOT', self.root).start()
        patch.object(release, 'MANIFEST', self.root / 'PUBLIC_FILES.json').start()

    def write(self, name, text='public test\n'):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')
        return p

    def test_allowlist_hash_and_unreviewed_change(self):
        p = self.write('README.md')
        result = release.check(refresh=True)
        self.assertEqual(result['README.md']['sha256'], hashlib.sha256(p.read_bytes()).hexdigest())
        self.assertEqual(release.check(), result)
        p.write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'differs'): release.check()

    def test_private_archive_filename_rejected(self):
        self.write('private.zip')
        with self.assertRaisesRegex(ValueError, 'Unexpected public path'): release.check(refresh=True)

    def test_original_conversation_jsonl_not_allowed(self):
        self.write('tests/record.jsonl', '{}')
        with self.assertRaisesRegex(ValueError, 'Unexpected public path'): release.check(refresh=True)

    def test_database_not_allowed(self):
        self.write('docs/conversation.db')
        with self.assertRaisesRegex(ValueError, 'Unexpected public path'): release.check(refresh=True)

    def test_synthetic_uuid_allowed(self):
        self.write('README.md', '11111111-2222-4333-8444-555555555555')
        release.check(refresh=True)

    def test_non_synthetic_uuid_rejected(self):
        value = '-'.join(['12345678','1234','4234','8234','123456789012'])
        self.write('README.md', value)
        with self.assertRaisesRegex(ValueError, 'Non-synthetic UUID'): release.check(refresh=True)

    def test_private_username_path_rejected(self):
        self.write('README.md', 'C:' + chr(92) + 'Users' + chr(92) + 'PrivateAccount' + chr(92) + 'file.txt')
        with self.assertRaisesRegex(ValueError, 'Personal user path'): release.check(refresh=True)

    def test_example_path_allowed(self):
        self.write('README.md', r'C:\Users\example\file.txt')
        release.check(refresh=True)

    def test_credential_signature_rejected(self):
        self.write('README.md', 'g'+'hp_'+'x'*40)
        with self.assertRaisesRegex(ValueError, 'Potential credential'): release.check(refresh=True)

    def test_generated_bytecode_excluded(self):
        self.write('README.md')
        self.write('__pycache__/module.pyc')
        self.assertEqual(set(release.check(refresh=True)), {'README.md'})

    def test_embedded_image_payload_rejected(self):
        self.write('README.md', 'data:'+'image/png;base64,'+'a'*256)
        with self.assertRaisesRegex(ValueError, 'Embedded image data'): release.check(refresh=True)

    def test_unlisted_tracked_path_rejected(self):
        self.write('README.md')
        release.check(refresh=True)
        with patch.object(release.subprocess, 'check_output', return_value=b'README.md\x00PUBLIC_FILES.json\x00extra.txt\x00'):
            with self.assertRaisesRegex(ValueError, 'Tracked paths'): release.check(tracked=True)

class DistributionHygieneTests(unittest.TestCase):
    def test_wheel_rejects_bytecode(self):
        import zipfile
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'package.whl'
            with zipfile.ZipFile(p,'w') as z: z.writestr('pkg/__pycache__/file.pyc',b'not allowed')
            with self.assertRaisesRegex(ValueError,'distribution member'): release.audit_distribution(p)

    def test_wheel_rejects_private_database(self):
        import zipfile
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'package.whl'
            with zipfile.ZipFile(p,'w') as z: z.writestr('pkg/private.db',b'not allowed')
            with self.assertRaisesRegex(ValueError,'distribution member'): release.audit_distribution(p)

    def test_wheel_allows_code_and_metadata(self):
        import zipfile
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'package.whl'
            with zipfile.ZipFile(p,'w') as z:
                z.writestr('pkg/__init__.py',b'')
                z.writestr('pkg.dist-info/METADATA',b'Name: example\n')
            release.audit_distribution(p)
