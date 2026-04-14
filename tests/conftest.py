"""Pytest-local setup.

Runs once at collection time. Its only job is to put `scripts/` on
sys.path so test modules can `import process_events`. Everything else
lives in `tests/helpers.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Also expose tests/ on sys.path so `import helpers` works from test modules
# without needing relative imports.
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
