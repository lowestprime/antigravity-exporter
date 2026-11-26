"""Selected native bytes must match indexed records, including after rendering."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from antigravity_exporter.model import EventIndex
from antigravity_exporter.storage import ExportError


class SelectedNativeIntegrityTests(unittest.TestCase):
    def fixture(self, folder):
        path = Path(folder) / 'events.jsonl'
        rows = [
            {'step_index': number, 'source': 'USER_EXPLICIT' if number % 2 == 0 else 'MODEL',
             'type': 'USER_INPUT' if number % 2 == 0 else 'GENERIC',
             'status': 'DONE', 'content': 'original recorded text'} for number in range(4)
        ]
        raw = [json.dumps(row).encode() + b'\n' for row in rows]
        path.write_bytes(b''.join(raw))
        index = EventIndex(path, Path(folder) / 'index.db')
        return path, index, raw

    def test_mutation_after_render_before_selected_native_copy_is_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            path, index, rows = self.fixture(folder)
            try:
                selection = index.selection(SimpleNamespace(mode='thread', from_step=2))
                list(index.records())  # Rendering saw the unchanged indexed bytes.
                path.write_bytes(path.read_bytes().replace(b'original recorded text', b'MODIFIED recorded text'))
                with self.assertRaisesRegex(ExportError, 'changed after indexing'):
                    index.selected_source(Path(folder) / 'selected.jsonl', selection)
            finally:
                index.close()

    def test_disjoint_filtered_native_records_keep_their_original_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            _, index, rows = self.fixture(folder)
            try:
                selection = index.selection(SimpleNamespace(mode='chat'))
                path, info = index.selected_source(Path(folder) / 'selected.jsonl', selection)
                self.assertEqual(path.read_bytes(), rows[0] + rows[2])
                self.assertTrue(info['byte_exact_source_records'])
                self.assertFalse(info['byte_exact_captured_prefix'])
            finally:
                index.close()


if __name__ == '__main__':
    unittest.main()
