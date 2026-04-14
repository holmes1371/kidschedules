"""Path helpers + fixture/snapshot loaders shared by the test suite."""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
FIXTURES_DIR = REPO_ROOT / "fixtures" / "test"
SNAPSHOTS_DIR = Path(__file__).resolve().parent / "snapshots"


def load_fixture(name: str) -> list[dict]:
    """Load a candidate-events fixture from fixtures/test/ by stem."""
    path = FIXTURES_DIR / f"{name}.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def read_snapshot(name: str) -> str:
    """Read a pinned text snapshot from tests/snapshots/ by stem."""
    path = SNAPSHOTS_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8")
