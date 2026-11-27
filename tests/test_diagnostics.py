"""Exercise diagnostic commands through the public CLI, including stdout JSON."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from antigravity_exporter import __version__
from antigravity_exporter.cli import main
from antigravity_exporter.storage import ExportError

ROOT = Path(__file__).resolve().parents[1]


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.config = self.directory / 'config.json'
        self.config.write_text(json.dumps({'method': 'Deflate', 'backend': 'python'}), encoding='utf-8')
        env = patch.dict(os.environ, {'AGY_EXPORT_CONFIG': str(self.config)})
        env.start()
        self.addCleanup(env.stop)

    def invoke(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = main(list(arguments))
        self.assertEqual(result, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), '')
        return json.loads(stdout.getvalue())

    def test_doctor_cli_emits_json_without_changing_config(self):
        before = self.config.read_bytes()
        result = subprocess.run(
            [sys.executable, str(ROOT / 'agy_export.py'), 'doctor'],
            cwd=self.directory, capture_output=True, text=True, encoding='utf-8',
            check=False, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        report = json.loads(result.stdout)
        self.assertEqual(report['version'], __version__)
        self.assertTrue(Path(report['executable']).is_file())
        self.assertIsNone(report['archive_preflight_error'])
        self.assertEqual(set(report['codecs']), {'lzma', 'zlib', 'tiktoken', 'zstandard'})
        self.assertTrue(all(type(value) is bool for value in report['codecs'].values()))
        self.assertTrue(report['codecs']['zlib'])
        self.assertTrue(report['no_network_requests_or_package_installation_performed'])
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()), ['config.json'])

    def test_doctor_reports_absent_optional_codecs(self):
        available = lambda name: object() if name in ('lzma', 'zlib') else None
        with patch('antigravity_exporter.cli.importlib.util.find_spec', side_effect=available):
            report = self.invoke('doctor')
        self.assertEqual(report['codecs'], {'lzma': True, 'zlib': True, 'tiktoken': False, 'zstandard': False})

    def test_doctor_reports_preflight_failure_without_traceback(self):
        with patch('antigravity_exporter.cli.preflight', side_effect=ExportError('Fixture archiver unavailable')):
            report = self.invoke('doctor', '--archiver-path', str(self.directory / 'missing'))
        self.assertIsNone(report['archiver'])
        self.assertEqual(report['archive_preflight_error'], 'Fixture archiver unavailable')
        self.assertIn('zlib', report['codecs'])

    def test_tokenizer_metadata_and_doctor_have_independent_imports(self):
        with patch('antigravity_exporter.cli.importlib.util.find_spec', return_value=object()), \
                patch('importlib.metadata.version', return_value='fixture-version') as version:
            report = self.invoke('tokenizer', '--tokenizer', 'regex')
        self.assertEqual(report['configured'], 'regex')
        self.assertTrue(report['tiktoken_installed'])
        self.assertEqual(report['tiktoken_version'], 'fixture-version')
        version.assert_called_once_with('tiktoken')
        self.assertEqual(self.invoke('doctor')['version'], __version__)

    def test_tokenizer_without_optional_package_does_not_read_metadata(self):
        with patch('antigravity_exporter.cli.importlib.util.find_spec', return_value=None), \
                patch('importlib.metadata.version') as version:
            report = self.invoke('tokenizer')
        self.assertFalse(report['tiktoken_installed'])
        self.assertIsNone(report['tiktoken_version'])
        version.assert_not_called()


    def test_tokenizer_uses_saved_encoding_and_explicit_override(self):
        self.config.write_text(json.dumps({'tokenizer': 'regex', 'token_encoding': 'o200k_base'}), encoding='utf-8')
        with patch('antigravity_exporter.cli.importlib.util.find_spec', return_value=None):
            saved = self.invoke('tokenizer')
            explicit = self.invoke('tokenizer', '--token-encoding', 'cl100k_base', '--tokenizer', 'none')
        self.assertEqual(saved['configured'], 'regex')
        self.assertEqual(saved['encoding'], 'o200k_base')
        self.assertEqual(explicit['configured'], 'none')
        self.assertEqual(explicit['encoding'], 'cl100k_base')

    def test_tokenizer_enforces_required_package_without_installing(self):
        for flags in (['--require-tiktoken'], ['--tokenizer', 'tiktoken']):
            with self.subTest(flags=flags):
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch('antigravity_exporter.cli.importlib.util.find_spec', return_value=None), \
                        contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    result = main(['tokenizer', *flags])
                self.assertEqual(result, 2)
                self.assertEqual(stdout.getvalue(), '')
                self.assertIn('tiktoken is not installed', stderr.getvalue())
                self.assertNotIn('Traceback', stderr.getvalue())

    def test_tokenizer_requirement_overrides_preferred_fallback(self):
        with patch('antigravity_exporter.cli.importlib.util.find_spec', return_value=object()), \
                patch('importlib.metadata.version', return_value='fixture-version'):
            report = self.invoke('tokenizer', '--tokenizer', 'regex', '--require-tiktoken')
        self.assertEqual(report['configured'], 'tiktoken')
        self.assertTrue(report['tiktoken_required'])
        self.assertTrue(report['availability_probe_only'])

    def test_importable_tokenizer_without_distribution_metadata_is_reported(self):
        from importlib.metadata import PackageNotFoundError
        with patch('antigravity_exporter.cli.importlib.util.find_spec', return_value=object()), \
                patch('importlib.metadata.version', side_effect=PackageNotFoundError('tiktoken')):
            report = self.invoke('tokenizer')
        self.assertTrue(report['tiktoken_installed'])
        self.assertIsNone(report['tiktoken_version'])
        self.assertFalse(report['tiktoken_metadata_found'])


if __name__ == '__main__':
    unittest.main()
