"""Inventory every tracked file at pinned reference commits, without execution."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess


def inventory(checkout, reference='HEAD'):
    checkout = Path(checkout).resolve()
    commit = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', reference], text=True).strip()
    paths = subprocess.check_output(['git', '-C', str(checkout), 'ls-tree', '-r', '--name-only', '-z', commit]).decode().split('\0')
    rows = []
    options = set()
    for name in filter(None, paths):
        raw = subprocess.check_output(['git', '-C', str(checkout), 'show', commit + ':' + name])
        row = {'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        try:
            text = raw.decode('utf-8')
        except UnicodeError:
            row['format'] = 'binary_or_non_utf8'
        else:
            row.update(format='utf8', lines=len(text.splitlines()))
            if name.endswith('.py'):
                tree = ast.parse(text)
                row['functions'] = sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree))
                row['classes'] = sum(isinstance(node, ast.ClassDef) for node in ast.walk(tree))
                declared = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'add_argument':
                        declared.update(value.value for value in node.args
                                        if isinstance(value, ast.Constant) and isinstance(value.value, str)
                                        and value.value.startswith('--'))
                row['declared_cli_flags'] = sorted(declared)
                options.update(declared)
        rows.append(row)
    return {'commit': commit, 'tracked_files': len(rows), 'total_bytes': sum(row['bytes'] for row in rows),
            'utf8_lines': sum(row.get('lines', 0) for row in rows), 'files': rows,
            'declared_cli_flags': sorted(options)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex-checkout', required=True, type=Path)
    parser.add_argument('--antigravity-checkout', default=Path(__file__).resolve().parents[1], type=Path)
    parser.add_argument('--codex-ref', default='acd322d1987f1078b6f79dcba62c898704dcc5ba')
    parser.add_argument('--antigravity-ref', default='11598ac1534f8892baf2cf5d60230593e6e51924')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    values = {
        'schema': 'exporter-reference-inventory/1',
        'scope': 'Every tracked Git blob at the pinned comparison commits; generated output and private sources excluded',
        'limitations': 'Hashes and lexical/AST inventories prove inspected byte coverage, not a semantic proof or complete behavioral equivalence. Feature decisions are documented in PARITY.md.',
        'codex_exporter': inventory(args.codex_checkout, args.codex_ref),
        'antigravity_exporter_baseline': inventory(args.antigravity_checkout, args.antigravity_ref),
    }
    args.output.write_text(json.dumps(values, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({name: {key: values[name][key] for key in ('commit', 'tracked_files', 'total_bytes', 'utf8_lines')}
                      for name in ('codex_exporter', 'antigravity_exporter_baseline')}, indent=2))


if __name__ == '__main__': main()
