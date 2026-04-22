"""Tests for scripts/build_queries.py.

Covers: the `ignored_senders.json` → Gmail exclusion-clause wiring added
alongside the Ignore-sender UI, and `load_audit_state` — the function
that decides whether the weekly run triggers the loose-vs-tight filter
audit (step1b) or skips it. A bug in `load_audit_state` either spams
the run log with unnecessary audits or silently lets the blocklist go
stale for months, so the date-math, threshold-defaulting, and
malformed-JSON branches are pinned explicitly.

The hand-curated/auto blocklist loaders already have coverage via the
pipeline's smoke tests.
"""
from __future__ import annotations

import datetime as dt
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


# ── load_audit_state ────────────────────────────────────────────────────


_TODAY = dt.date(2026, 4, 22)


def test_load_audit_state_missing_file_is_due_with_default_threshold(tmp_path):
    result = bq.load_audit_state(str(tmp_path / "nope.json"), _TODAY)
    assert result == {
        "last_verified_iso": None,
        "threshold_days": 30,
        "days_since": None,
        "due": True,
        "reason": "no audit state file found",
    }


def test_load_audit_state_malformed_json_is_due_with_reason(tmp_path):
    """A corrupt audit state file must not mask the audit schedule —
    the wrapper returns due=True so step1b still runs, and the reason
    carries the parse error forward for the operator."""
    path = tmp_path / ".filter_audit.json"
    path.write_text("{ not json ")
    result = bq.load_audit_state(str(path), _TODAY)
    assert result["due"] is True
    assert result["last_verified_iso"] is None
    assert result["threshold_days"] == 30
    assert result["reason"].startswith("audit state unreadable:")


def test_load_audit_state_missing_last_verified_is_due(tmp_path):
    """File exists but `last_verified_iso` is absent → treat as never
    verified. Threshold from the file is preserved so an intentional
    override doesn't get clobbered by this branch."""
    path = tmp_path / ".filter_audit.json"
    path.write_text(json.dumps({"threshold_days": 45}))
    result = bq.load_audit_state(str(path), _TODAY)
    assert result["due"] is True
    assert result["threshold_days"] == 45
    assert result["reason"] == "last_verified_iso missing"
    assert result["last_verified_iso"] is None


def test_load_audit_state_invalid_iso_string_is_due(tmp_path):
    """Unparseable date string → due=True, reason includes the
    offending value so the operator can see what to fix."""
    path = tmp_path / ".filter_audit.json"
    path.write_text(json.dumps({
        "last_verified_iso": "not-a-date",
        "threshold_days": 30,
    }))
    result = bq.load_audit_state(str(path), _TODAY)
    assert result["due"] is True
    assert "invalid last_verified_iso" in result["reason"]
    assert "'not-a-date'" in result["reason"]


def test_load_audit_state_fresh_under_threshold(tmp_path):
    """days_since < threshold_days → due=False, reason='fresh'."""
    path = tmp_path / ".filter_audit.json"
    path.write_text(json.dumps({
        "last_verified_iso": (_TODAY - dt.timedelta(days=10)).isoformat(),
        "threshold_days": 30,
    }))
    result = bq.load_audit_state(str(path), _TODAY)
    assert result["due"] is False
    assert result["days_since"] == 10
    assert result["threshold_days"] == 30
    assert result["reason"] == "fresh"


def test_load_audit_state_exactly_at_threshold_is_due(tmp_path):
    """Boundary: days_since == threshold → due=True (>= in the check).
    Pins the inclusive boundary — drift to a strict `>` would silently
    delay the audit by a day."""
    path = tmp_path / ".filter_audit.json"
    path.write_text(json.dumps({
        "last_verified_iso": (_TODAY - dt.timedelta(days=30)).isoformat(),
        "threshold_days": 30,
    }))
    result = bq.load_audit_state(str(path), _TODAY)
    assert result["due"] is True
    assert result["days_since"] == 30
    assert result["reason"] == "stale: 30 days since last verification"


def test_load_audit_state_past_threshold_is_due(tmp_path):
    path = tmp_path / ".filter_audit.json"
    path.write_text(json.dumps({
        "last_verified_iso": (_TODAY - dt.timedelta(days=45)).isoformat(),
        "threshold_days": 30,
    }))
    result = bq.load_audit_state(str(path), _TODAY)
    assert result["due"] is True
    assert result["days_since"] == 45
    assert result["reason"] == "stale: 45 days since last verification"


def test_load_audit_state_custom_threshold_honored(tmp_path):
    """Threshold defaults to 30 but can be overridden via the file.
    The comparison must use the per-file threshold, not the default."""
    path = tmp_path / ".filter_audit.json"
    path.write_text(json.dumps({
        "last_verified_iso": (_TODAY - dt.timedelta(days=40)).isoformat(),
        "threshold_days": 60,
    }))
    result = bq.load_audit_state(str(path), _TODAY)
    assert result["due"] is False
    assert result["threshold_days"] == 60
    assert result["days_since"] == 40


def test_load_audit_state_missing_threshold_defaults_to_30(tmp_path):
    """`threshold_days` key absent → coerce to 30. Pins the default
    against a silent drift to some other number."""
    path = tmp_path / ".filter_audit.json"
    path.write_text(json.dumps({
        "last_verified_iso": (_TODAY - dt.timedelta(days=20)).isoformat(),
    }))
    result = bq.load_audit_state(str(path), _TODAY)
    assert result["threshold_days"] == 30
    assert result["due"] is False
