"""Round-trip tests for `.filter_audit.json` — writer vs reader.

`scripts/mark_filter_audit.py` writes `.filter_audit.json` after a
successful loose-vs-tight filter audit. `scripts/build_queries.py`
(`load_audit_state`) reads that same file on every scheduled run to
decide whether step1b fires again. There is no shared schema module
between them — a rename like `last_verified_iso` → `last_audit_iso`,
or a type drift from int to str on `threshold_days`, would silently
break the audit cadence: the reader's missing-field branch would mark
every run due, wasting tokens AND desensitizing the operator to the
"due" signal.

These tests drive the writer via its CLI, then feed the on-disk file
to the reader and assert the reader's interpretation matches the
writer's intent.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import sys

import build_queries as bq
import mark_filter_audit as mfa


def _run_writer(monkeypatch, audit_state_path, argv_extras=None):
    """Invoke mark_filter_audit.main() with its stdout captured."""
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    monkeypatch.setattr(sys, "argv", [
        "mark_filter_audit.py",
        "--audit-state", str(audit_state_path),
        *(argv_extras or []),
    ])
    rc = mfa.main()
    return rc, buf.getvalue()


def test_writer_produces_keys_reader_consumes(tmp_path, monkeypatch):
    """The fields the reader inspects (`last_verified_iso`,
    `threshold_days`) must exist and be the right shape after a
    writer run."""
    state_path = tmp_path / ".filter_audit.json"
    rc, _ = _run_writer(monkeypatch, state_path,
                        ["--today", "2026-04-22"])
    assert rc == 0
    assert state_path.exists()

    raw = json.loads(state_path.read_text(encoding="utf-8"))
    assert isinstance(raw["last_verified_iso"], str)
    assert raw["last_verified_iso"] == "2026-04-22"
    assert isinstance(raw["threshold_days"], int)
    assert raw["threshold_days"] == 30  # writer default


def test_writer_output_parses_as_fresh_when_read_same_day(
    tmp_path, monkeypatch,
):
    """Same-day audit → reader says not due, days_since=0, reason='fresh'.
    A schema break here would flip `due` to True by falling into the
    missing-field branch."""
    state_path = tmp_path / ".filter_audit.json"
    _run_writer(monkeypatch, state_path, ["--today", "2026-04-22"])

    result = bq.load_audit_state(str(state_path), dt.date(2026, 4, 22))
    assert result["due"] is False
    assert result["days_since"] == 0
    assert result["last_verified_iso"] == "2026-04-22"
    assert result["threshold_days"] == 30
    assert result["reason"] == "fresh"


def test_writer_threshold_override_propagates_to_reader(
    tmp_path, monkeypatch,
):
    """`--threshold-days 60` on the writer → reader uses 60, not 30."""
    state_path = tmp_path / ".filter_audit.json"
    _run_writer(monkeypatch, state_path, [
        "--today", "2026-04-22",
        "--threshold-days", "60",
    ])

    # Read on a day 45 days later: with threshold=60 this should still
    # be fresh; with the default 30 it would be due.
    result = bq.load_audit_state(str(state_path), dt.date(2026, 6, 6))
    assert result["threshold_days"] == 60
    assert result["due"] is False
    assert result["days_since"] == 45


def test_writer_preserves_existing_threshold_on_rewrite(
    tmp_path, monkeypatch,
):
    """Rewriting the file without `--threshold-days` keeps the prior
    value — the reader must see the stored threshold, not 30."""
    state_path = tmp_path / ".filter_audit.json"
    state_path.write_text(json.dumps({
        "last_verified_iso": "2026-01-01",
        "threshold_days": 90,
        "notes": "seed",
    }))

    _run_writer(monkeypatch, state_path, ["--today", "2026-04-22"])

    raw = json.loads(state_path.read_text(encoding="utf-8"))
    assert raw["threshold_days"] == 90
    assert raw["last_verified_iso"] == "2026-04-22"

    result = bq.load_audit_state(str(state_path), dt.date(2026, 4, 22))
    assert result["threshold_days"] == 90
    assert result["due"] is False


def test_writer_output_after_threshold_elapsed_reads_as_due(
    tmp_path, monkeypatch,
):
    """End-to-end: writer stamps today, reader sees it as stale after
    threshold_days elapse. Reason prefix is pinned so a renaming to
    'overdue' / 'stale' wouldn't slip past the existing reader tests."""
    state_path = tmp_path / ".filter_audit.json"
    _run_writer(monkeypatch, state_path, ["--today", "2026-01-01"])

    result = bq.load_audit_state(str(state_path), dt.date(2026, 4, 22))
    assert result["due"] is True
    assert result["days_since"] == 111
    assert result["reason"].startswith("stale:")


def test_writer_unreadable_file_does_not_crash_writer(
    tmp_path, monkeypatch,
):
    """If `.filter_audit.json` is corrupted, the writer treats it as
    empty and stamps a fresh one. The reader tests already cover the
    corrupt-read case on the load side; this pins the write side so
    an operator can recover from a broken file just by running the
    writer."""
    state_path = tmp_path / ".filter_audit.json"
    state_path.write_text("{ not valid json")

    rc, _ = _run_writer(monkeypatch, state_path, ["--today", "2026-04-22"])
    assert rc == 0

    raw = json.loads(state_path.read_text(encoding="utf-8"))
    assert raw["last_verified_iso"] == "2026-04-22"
    assert raw["threshold_days"] == 30
