"""Pytest suite for scripts/sync_ignored_senders.py.

Covers the two pure functions (normalize_rows, write_if_changed).
The fetch/CLI layer is thin stdlib plumbing and is smoke-tested by
the workflow step, not here.
"""
from __future__ import annotations

import json

import pytest

import sync_ignored_senders as sis


# ─── normalize_rows ──────────────────────────────────────────────────────


def test_normalize_lowercases_and_trims_domain():
    rows = [{"domain": "  Foo.COM  ", "source": "manual", "timestamp": "t0"}]
    out = sis.normalize_rows(rows)
    assert out == [{"domain": "foo.com", "source": "manual", "timestamp": "t0"}]


@pytest.mark.parametrize("bad", [
    "",               # empty
    "foo",            # no TLD
    "-foo.com",       # leading hyphen
    ".com",           # no label before dot
    "not a domain",   # spaces
    "foo.c",          # TLD too short
])
def test_normalize_drops_invalid_domain(bad):
    out = sis.normalize_rows([{"domain": bad}])
    assert out == []


def test_normalize_drops_rows_missing_domain_key():
    rows = [
        {"source": "manual"},                       # no domain at all
        {"domain": None},                           # domain is None
        {"domain": 42},                             # non-string domain
        {"domain": "laes.org", "source": "manual"}, # good row
    ]
    out = sis.normalize_rows(rows)
    assert [r["domain"] for r in out] == ["laes.org"]


def test_normalize_dedups_first_wins_on_same_domain_after_lowercase():
    rows = [
        {"domain": "Laes.org", "source": "auto-button", "timestamp": "t1"},
        {"domain": "laes.org", "source": "manual",      "timestamp": "t2"},
        {"domain": "LAES.ORG", "source": "other",       "timestamp": "t3"},
    ]
    out = sis.normalize_rows(rows)
    assert len(out) == 1
    assert out[0] == {"domain": "laes.org", "source": "auto-button", "timestamp": "t1"}


def test_normalize_sorts_alphabetically():
    rows = [
        {"domain": "zebra.io"},
        {"domain": "apple.com"},
        {"domain": "middle.org"},
    ]
    out = sis.normalize_rows(rows)
    assert [r["domain"] for r in out] == ["apple.com", "middle.org", "zebra.io"]


def test_normalize_passthrough_and_default_missing_fields():
    rows = [
        {"domain": "full.org", "source": "manual", "timestamp": "2026-04-15T00:00:00Z"},
        {"domain": "bare.org"},  # no timestamp, no source
    ]
    out = sis.normalize_rows(rows)
    by_d = {r["domain"]: r for r in out}
    assert by_d["full.org"] == {
        "domain": "full.org", "source": "manual",
        "timestamp": "2026-04-15T00:00:00Z",
    }
    assert by_d["bare.org"] == {"domain": "bare.org", "source": "", "timestamp": ""}


def test_normalize_ignores_non_dict_items():
    # Defensive — a malformed Apps Script response shouldn't crash us.
    rows = ["just a string", 42, None, {"domain": "ok.com"}]
    out = sis.normalize_rows(rows)
    assert [r["domain"] for r in out] == ["ok.com"]


# ─── write_if_changed ────────────────────────────────────────────────────


def test_write_if_changed_writes_when_file_absent(tmp_path):
    path = tmp_path / "ignored_senders.json"
    rows = [{"domain": "ok.com", "source": "manual", "timestamp": "t0"}]
    wrote = sis.write_if_changed(str(path), rows)
    assert wrote is True
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == rows


def test_write_if_changed_writes_when_content_differs(tmp_path):
    path = tmp_path / "ignored_senders.json"
    path.write_text('[{"domain":"old.com"}]\n', encoding="utf-8")
    new_rows = [{"domain": "new.com", "source": "", "timestamp": ""}]
    wrote = sis.write_if_changed(str(path), new_rows)
    assert wrote is True
    assert json.loads(path.read_text(encoding="utf-8")) == new_rows


def test_write_if_changed_returns_false_when_identical(tmp_path):
    path = tmp_path / "ignored_senders.json"
    rows = [{"domain": "ok.com", "source": "manual", "timestamp": "t0"}]
    assert sis.write_if_changed(str(path), rows) is True
    first_bytes = path.read_bytes()
    assert sis.write_if_changed(str(path), rows) is False
    # File bytes must be untouched so git sees no diff.
    assert path.read_bytes() == first_bytes


def test_write_if_changed_uses_2_space_indent_and_trailing_newline(tmp_path):
    path = tmp_path / "ignored_senders.json"
    rows = [{"domain": "ok.com", "source": "manual", "timestamp": "t0"}]
    sis.write_if_changed(str(path), rows)
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    # indent=2 → list opens at col 0, list items at col 2, dict keys at
    # col 4. So object keys show up preceded by exactly four spaces.
    assert '\n    "domain"' in text
