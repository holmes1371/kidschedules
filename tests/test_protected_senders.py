"""Tests for scripts/protected_senders.py — the shared loader and matcher
used by both the HTML render pass and the Gmail-query build pass.
"""
from __future__ import annotations

import json

import pytest

import build_queries as bq
import process_events as pe
from protected_senders import is_protected, load_protected_senders


def test_load_protected_senders_missing_file_returns_empty(tmp_path):
    assert load_protected_senders(str(tmp_path / "nope.txt")) == []


def test_load_protected_senders_strips_comments_and_blanks(tmp_path):
    path = tmp_path / "protected.txt"
    path.write_text(
        "# top-of-file comment\n"
        "\n"
        "fcps.edu  # trailing comment\n"
        "\n"
        "*pta.org\n"
        "# another comment\n"
    )
    assert load_protected_senders(str(path)) == ["fcps.edu", "*pta.org"]


def test_load_protected_senders_lowercases_and_dedupes(tmp_path):
    path = tmp_path / "protected.txt"
    path.write_text("FCPS.edu\nfcps.edu\n*PTA.ORG\n")
    assert load_protected_senders(str(path)) == ["fcps.edu", "*pta.org"]


def test_is_protected_exact_match():
    assert is_protected("fcps.edu", ["fcps.edu"]) is True
    assert is_protected("FCPS.EDU", ["fcps.edu"]) is True
    assert is_protected("other.edu", ["fcps.edu"]) is False


def test_is_protected_suffix_wildcard():
    patterns = ["*pta.org"]
    assert is_protected("louisearcherpta.org", patterns) is True
    assert is_protected("canterburypta.org", patterns) is True
    assert is_protected("pta.org", patterns) is True
    assert is_protected("pta.org.evil.com", patterns) is False


def test_is_protected_empty_domain_is_never_protected():
    assert is_protected("", ["fcps.edu"]) is False
    assert is_protected("   ", ["fcps.edu"]) is False


def test_is_protected_with_empty_pattern_list():
    assert is_protected("fcps.edu", []) is False


# --- Build-queries integration: protected domains filtered out of the union ---

def test_build_queries_drops_protected_from_ignored_senders(tmp_path, monkeypatch):
    ignored = tmp_path / "ignored_senders.json"
    ignored.write_text(json.dumps([
        {"domain": "louisearcherpta.org"},  # protected via *pta.org
        {"domain": "teamsnap.com"},         # protected exact
        {"domain": "spammer.com"},          # not protected
    ]))
    protected = tmp_path / "protected.txt"
    protected.write_text("teamsnap.com\n*pta.org\n")

    import io
    import sys

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    monkeypatch.setattr(sys, "argv", [
        "build_queries.py",
        "--blocklist", "",
        "--auto-blocklist", "",
        "--ignored-senders", str(ignored),
        "--protected-senders", str(protected),
        "--today", "2026-04-15",
    ])
    assert bq.main() == 0
    out = json.loads(buf.getvalue())
    excl = out["exclusions"]
    assert excl["protected_senders_size"] == 2
    assert excl["ignored_senders_dropped_protected"] == 2
    assert excl["blocklist_size_ignored_senders"] == 1
    # Only the non-protected domain ends up in the Gmail clause.
    sample = out["queries"]["school_activities"]
    assert "-from:spammer.com" in sample
    assert "-from:teamsnap.com" not in sample
    assert "-from:louisearcherpta.org" not in sample


# --- Render integration: Ignore-sender button suppressed for protected senders ---

def test_render_html_omits_ignore_sender_button_for_protected_sender():
    import datetime as dt

    event = {
        "id": "evt1",
        "name": "Field trip",
        "date": "2026-04-20",
        "_date_obj": dt.date(2026, 4, 20),
        "time": "9:00 AM",
        "location": "School",
        "category": "School",
        "child": "Kid",
        "source": "test@fcps.edu",
        "sender_domain": "fcps.edu",
    }
    weeks = [(dt.date(2026, 4, 20), [event])]
    html = pe.render_html(
        today=dt.date(2026, 4, 15),
        weeks=weeks,
        undated=[],
        total_future=1,
        lookback_days=60,
        webhook_url="https://example.com/hook",
        pages_url="",
        protected_senders=["fcps.edu"],
    )
    assert 'data-sender="fcps.edu"' in html  # the card attribute remains
    # The CSS rule for .ignore-sender-btn still exists (harmless), but no
    # actual <button class="ignore-sender-btn"> element is rendered.
    assert 'button class="ignore-sender-btn"' not in html


def test_render_html_keeps_ignore_sender_button_for_unprotected_sender():
    import datetime as dt

    event = {
        "id": "evt2",
        "name": "Field trip",
        "date": "2026-04-20",
        "_date_obj": dt.date(2026, 4, 20),
        "time": "9:00 AM",
        "location": "School",
        "category": "School",
        "child": "Kid",
        "source": "test@spammer.com",
        "sender_domain": "spammer.com",
    }
    weeks = [(dt.date(2026, 4, 20), [event])]
    html = pe.render_html(
        today=dt.date(2026, 4, 15),
        weeks=weeks,
        undated=[],
        total_future=1,
        lookback_days=60,
        webhook_url="https://example.com/hook",
        pages_url="",
        protected_senders=["fcps.edu"],
    )
    assert 'button class="ignore-sender-btn"' in html
    assert 'data-sender="spammer.com"' in html
