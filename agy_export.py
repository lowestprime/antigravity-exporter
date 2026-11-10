#!/usr/bin/env python3
"""Portable entry point; does not require a package installation."""
from antigravity_exporter.cli import main
if __name__ == '__main__':
    raise SystemExit(main())
