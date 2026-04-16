"""Tests for scripts/build_queries.py — focused on the ignored_senders.json
→ Gmail exclusion-clause wiring added alongside the Ignore-sender UI.

The hand-curated/auto blocklist loaders already have coverage via the
pipeline's smoke tests; this file just exercises the new surface area.
"""
from __future__ import annotations

import json

import pytest

import build_queries as bq


def test_load_ignored_senders_missing_file_returns_empty(tmp_path):
    assert bq.load_ignored_senders(str(tmp_path / "nope.json")) == []


def test_load_ignored_senders_valid_payload(tmp_path):
    path = tmp_path / "ignored_senders.json"
    path.write_text(json.dumps([
        {"domain": "example.com", "source": "ui", "timestamp": "t1"},
        {"domain": "foo.org", "source": "ui", "timestamp": "t2"},
    ]))
    assert bq.load_ignored_senders(str(path)) == ["example.com", "foo.org"]


def test_load_ignored_senders_malformed_json_returns_empty(tmp_path):
    path = tmp_path / "ignored_senders.json"
    path.write_text("not json at all")
    assert bq.load_ignored_senders(str(path)) == []


def test_load_ignored_senders_non_list_payload_returns_empty(tmp_path):
    path = tmp_path / "ignored_senders.json"
    path.write_text(json.dumps({"domain": "example.com"}))
    assert bq.load_ignored_senders(str(path)) == []


def test_load_ignored_senders_skips_rows_without_domain_string(tmp_path):
    path = tmp_path / "ignored_senders.json"
    path.write_text(json.dumps([
        {"domain": "good.com"},
        {"source": "ui"},              # no domain key
        {"domain": 123},               # wrong type
        "not-a-dict",                  # wrong row type
        {"domain": "   "},             # whitespace-only
        {"domain": " spaced.com "},    # trimmed
    ]))
    assert bq.load_ignored_senders(str(path)) == ["good.com", "spaced.com"]


def test_cli_unions_ignored_senders_into_exclusion(tmp_path, monkeypatch):
    # Disable the main + auto blocklists so the CLI surface is isolated to
    # ignored_senders.
    ignored = tmp_path / "ignored_senders.json"
    ignored.write_text(json.dumps([
        {"domain": "spammer.com"},
        {"domain": "other.example"},
    ]))
    import io
    import sys

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    monkeypatch.setattr(sys, "argv", [
        "build_queries.py",
        "--blocklist", "",
        "--auto-blocklist", "",
        "--ignored-senders", str(ignored),
        "--today", "2026-04-15",
    ])
    assert bq.main() == 0
    out = json.loads(buf.getvalue())
    excl = out["exclusions"]
    assert excl["blocklist_size_ignored_senders"] == 2
    assert excl["blocklist_size"] == 2
    # Queries include both domains as -from:... tokens.
    sample = out["queries"]["school_activities"]
    assert "-from:spammer.com" in sample
    assert "-from:other.example" in sample


def test_cli_dedupes_ignored_senders_against_main_blocklist(tmp_path, monkeypatch):
    main_bl = tmp_path / "blocklist.txt"
    main_bl.write_text("Spammer.com\n")  # different casing to exercise dedupe
    ignored = tmp_path / "ignored_senders.json"
    ignored.write_text(json.dumps([
        {"domain": "spammer.com"},     # dup of main (case-insensitive)
        {"domain": "new.example"},
    ]))
    import io
    import sys

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    monkeypatch.setattr(sys, "argv", [
        "build_queries.py",
        "--blocklist", str(main_bl),
        "--auto-blocklist", "",
        "--ignored-senders", str(ignored),
        "--today", "2026-04-15",
    ])
    assert bq.main() == 0
    out = json.loads(buf.getvalue())
    excl = out["exclusions"]
    assert excl["blocklist_size_main"] == 1
    assert excl["blocklist_size_ignored_senders"] == 2
    # The duplicate is counted in the source list but does NOT double up
    # in the unioned total.
    assert excl["blocklist_size"] == 2


def test_cli_empty_ignored_senders_path_skips_loader(tmp_path, monkeypatch):
    import io
    import sys

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    monkeypatch.setattr(sys, "argv", [
        "build_queries.py",
        "--blocklist", "",
        "--auto-blocklist", "",
        "--ignored-senders", "",
        "--today", "2026-04-15",
    ])
    assert bq.main() == 0
    out = json.loads(buf.getvalue())
    excl = out["exclusions"]
    assert excl["ignored_senders_path"] is None
    assert excl["blocklist_size_ignored_senders"] == 0
