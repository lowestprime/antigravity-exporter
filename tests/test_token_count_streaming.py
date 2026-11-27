"""Preserve lexical counts while bounding Unicode reads and progress callbacks."""
import math
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from antigravity_exporter.render import count_text
from antigravity_exporter.storage import CHUNK, ExportError


class RecordedProgress:
    def __init__(self):
        self.events = []

    def update(self, stage, done=None, total=None, **options):
        self.events.append((stage, done, total, options))


class BoundedReader:
    """Reject an unbounded read or line iteration instead of relying on RSS timing."""
    def __init__(self, stream, limit, requests):
        self.stream = stream
        self.limit = limit
        self.requests = requests

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stream.close()

    def read(self, size=-1):
        if not 0 < size <= self.limit:
            raise AssertionError(f'Unbounded or oversized read: {size}')
        self.requests.append(size)
        return self.stream.read(size)

    def __iter__(self):
        raise AssertionError('Line iteration can allocate an unbounded source line')


class BoundedPath:
    def __init__(self, path):
        self.path = path
        self.binary_reads = []
        self.text_reads = []

    def stat(self):
        return self.path.stat()

    def open(self, mode, **options):
        if mode == 'rb':
            return BoundedReader(self.path.open(mode, **options), CHUNK, self.binary_reads)
        if mode != 'r' or options != {'encoding': 'utf-8', 'newline': ''}:
            raise AssertionError(f'Unexpected text opening policy: {mode}, {options}')
        return BoundedReader(self.path.open(mode, **options), 65536, self.text_reads)

    def read_text(self, **options):
        raise AssertionError('Regex estimation must not read the complete file')


class TokenCountStreamingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'fixture.md'

    def write(self, text):
        self.path.write_bytes(text.encode('utf-8'))
        return self.path

    @staticmethod
    def base_result(text):
        raw = text.encode('utf-8')
        return {
            'lines': raw.count(b'\n') + int(bool(raw) and not raw.endswith(b'\n')),
            'utf8_bytes': len(raw),
            'tokenizer': 'none',
            'tokens': None,
        }

    @classmethod
    def expected_regex(cls, text):
        # Independent, unchunked reference checks word continuity at every boundary.
        return {
            **cls.base_result(text),
            'tokenizer': 'regex',
            'tokens': len(re.findall(r'\w+|[^\w\s]', text)),
            'exact_for_selected_encoding': False,
            'note': 'Regex token estimate, not Gemini/Claude/GPT model tokens',
        }

    def test_empty_final_newline_and_unicode_whitespace_preserve_all_fields(self):
        cases = [
            '', '\n', 'word', 'word\n', 'one\ntwo', 'one\r\ntwo\rthree\n',
            '\ufeffé漢٣_Ⅷ² 👩🏽\u200d💻 e\u0301\x7f!',
            'one\t two\vthree\ffour\u0085five\u2028six\u2029seven\u200b!',
        ]
        for text in cases:
            with self.subTest(text=repr(text)):
                self.assertEqual(count_text(self.write(text), 'regex'), self.expected_regex(text))

    def test_word_punctuation_combining_and_crlf_chunk_boundaries(self):
        block = 65536
        cases = [
            'a' * (3 * block + 7),
            '漢' * (2 * block + 1),
            '!' * (2 * block + 1),
            'x' * (block - 1) + 'é漢_٣Ⅷ²',
            'x' * (block - 1) + '!word',
            'x' * block + '!word',
            'x' * (block - 1) + '\x7fword',
            'x' * block + '\x7fword',
            'é' * (block - 1) + '\u0301word',
            'é' * block + '\u0301word',
            'x' * (block - 1) + '\r\nword',
            'x' * block + '\r\nword\n',
            'x' * (block - 1) + '👩🏽\u200d💻word',
        ]
        for index, text in enumerate(cases):
            with self.subTest(case=index):
                self.assertEqual(count_text(self.write(text), 'regex'), self.expected_regex(text))

    def test_reads_and_callbacks_are_bounded_with_exact_utf8_progress(self):
        for label, text in (
            ('many_short_lines', '漢字!\r\n' * 50000 + 'final'),
            ('single_long_line', '漢_é' * 100000 + '\x7f' + 'z' * 100000),
        ):
            with self.subTest(input=label):
                path = BoundedPath(self.write(text))
                progress = RecordedProgress()
                self.assertEqual(count_text(path, 'regex', progress=progress), self.expected_regex(text))
                self.assertTrue(path.binary_reads)
                self.assertTrue(path.text_reads)
                events = [item for item in progress.events if item[0] == 'tokenize-regex']
                self.assertEqual(events[0][1], 0)
                self.assertTrue(events[0][3]['force'])
                self.assertEqual(events[-1][1], len(text.encode('utf-8')))
                self.assertEqual([item[1] for item in events], sorted(item[1] for item in events))
                self.assertTrue(all(item[2] == len(text.encode('utf-8')) for item in events))
                self.assertLessEqual(len(events), math.ceil(len(text) / 4096) + 2)

    def test_disabled_tokenizer_preserves_original_dictionary_without_decoding(self):
        text = 'é\r\nlast'
        self.assertEqual(count_text(self.write(text), 'none'), self.base_result(text))
        self.path.write_bytes(b'\xff\n')
        self.assertEqual(count_text(self.path, 'none'), {
            'lines': 1, 'utf8_bytes': 2, 'tokenizer': 'none', 'tokens': None,
        })

    def test_regex_rejects_invalid_and_incomplete_utf8(self):
        for raw in (b'\xff\n', b'a' * 65535 + b'\xc3(', b'valid\n\xf0\x9f'):
            with self.subTest(ending=repr(raw[-10:])):
                self.path.write_bytes(raw)
                with self.assertRaises(UnicodeDecodeError):
                    count_text(self.path, 'regex')

    def test_exact_tokenizer_result_and_newline_policy_remain_unchanged(self):
        text = 'alpha\r\nβ\r!\n'
        self.write(text)
        encoder = Mock()
        encoder.encode.return_value = [11, 22, 33]
        module = SimpleNamespace(get_encoding=Mock(return_value=encoder))
        progress = RecordedProgress()
        with patch.dict(sys.modules, {'tiktoken': module}):
            result = count_text(self.path, 'tiktoken', encoding='fixture_encoding', progress=progress)
        self.assertEqual(result, {
            **self.base_result(text), 'tokenizer': 'tiktoken', 'encoding': 'fixture_encoding',
            'tokens': 3, 'exact_for_selected_encoding': True, 'is_provider_billing_token_count': False,
        })
        module.get_encoding.assert_called_once_with('fixture_encoding')
        encoder.encode.assert_called_once_with('alpha\nβ\n!\n', disallowed_special=())
        self.assertIn('tokenize-exact', [item[0] for item in progress.events])
        self.assertNotIn('tokenize-regex', [item[0] for item in progress.events])

    def test_auto_above_exact_cap_uses_regex_without_calling_encoder(self):
        text = '漢字_abc\r\nnext!'
        module = SimpleNamespace(get_encoding=Mock())
        with patch.dict(sys.modules, {'tiktoken': module}):
            result = count_text(self.write(text), 'auto', exact_max_bytes=1)
        self.assertEqual(result, self.expected_regex(text))
        module.get_encoding.assert_not_called()

    def test_required_exact_above_cap_still_fails(self):
        module = SimpleNamespace(get_encoding=Mock())
        with patch.dict(sys.modules, {'tiktoken': module}):
            with self.assertRaisesRegex(ExportError, 'Exact token count exceeds --exact-max-bytes'):
                count_text(self.write('abc!'), 'tiktoken', exact_max_bytes=1)
        module.get_encoding.assert_not_called()

    def test_missing_exact_package_preserves_auto_fallback_and_required_error(self):
        text = 'é漢字!\nlast'
        self.write(text)
        with patch.dict(sys.modules, {'tiktoken': None}):
            self.assertEqual(count_text(self.path, 'auto'), self.expected_regex(text))
            with self.assertRaisesRegex(ExportError, 'tiktoken is not installed'):
                count_text(self.path, 'tiktoken')


if __name__ == '__main__':
    unittest.main()
