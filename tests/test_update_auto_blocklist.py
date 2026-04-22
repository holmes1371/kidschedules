"""Pytest suite for scripts/update_auto_blocklist.py.

The script auto-mutates blocklist_auto.txt using agent suggestions.
Its three private helpers — _domain_of, _is_protected, _parse_block_file
— gate which addresses can be auto-added; a regression in any one
of them risks polluting a tracked file with bad entries (or missing
real ones). `main()` threads those helpers together and also writes
the auto-header, the `# auto YYYY-MM-DD: reason` trailers, and the
audit-log JSONL line.

Coverage layers:
1. Helpers (_domain_of, _is_protected, _parse_block_file) — pinned
   individually with tolerated/rejected shapes.
2. `main()` — driven through argv against a tmp_path, with
   `dt.date.today()` monkeypatched for stable trailer text. Each
   guardrail branch (wrong confidence, invalid address, protected
   domain, already in auto, already in main, non-dict entry) is
   exercised, plus the error-exit branches for missing / unparseable
   / non-list suggestions, the auto-header creation, and the audit
   JSONL append.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import sys
from pathlib import Path

import pytest

# scripts/ is added to sys.path by tests/conftest.py
import update_auto_blocklist as uab  # noqa: E402


# ─── _domain_of ───────────────────────────────────────────────────────────

def test_domain_of_simple_address():
    assert uab._domain_of("tom@example.com") == "example.com"


def test_domain_of_multi_level_tld():
    assert uab._domain_of("tom@sub.example.co.uk") == "sub.example.co.uk"


def test_domain_of_lowercases_mixed_case_input():
    assert uab._domain_of("Tom@Example.COM") == "example.com"


def test_domain_of_strips_surrounding_whitespace():
    assert uab._domain_of("  tom@example.com  ") == "example.com"


def test_domain_of_named_form_returns_none():
    """Regex is anchored — "Tom Holmes <tom@example.com>" doesn't match.
    Callers must pass a bare address; main() does this via .strip()."""
    assert uab._domain_of("Tom Holmes <tom@example.com>") is None


def test_domain_of_no_at_sign_returns_none():
    assert uab._domain_of("not-an-email") is None


def test_domain_of_no_local_part_returns_none():
    assert uab._domain_of("@example.com") is None


def test_domain_of_no_tld_returns_none():
    """example with no dot-suffix isn't a routable address."""
    assert uab._domain_of("tom@example") is None


def test_domain_of_single_char_tld_returns_none():
    """TLD must be 2+ chars; tom@x.y is rejected."""
    assert uab._domain_of("tom@example.x") is None


def test_domain_of_empty_string_returns_none():
    assert uab._domain_of("") is None


def test_domain_of_whitespace_only_returns_none():
    assert uab._domain_of("   ") is None


# ─── _is_protected ────────────────────────────────────────────────────────

def test_is_protected_exact_match():
    assert uab._is_protected("fcps.edu") is True


def test_is_protected_subdomain_match():
    """A subdomain of a protected suffix is also protected — covers
    "schoolname.fcps.edu" senders that share the umbrella domain."""
    assert uab._is_protected("elementary.fcps.edu") is True


def test_is_protected_deep_subdomain_match():
    assert uab._is_protected("a.b.c.pta.org") is True


def test_is_protected_unrelated_domain_not_protected():
    assert uab._is_protected("randomspam.com") is False


def test_is_protected_case_insensitive():
    assert uab._is_protected("FCPS.EDU") is True
    assert uab._is_protected("Elementary.FCPS.Edu") is True


def test_is_protected_substring_but_not_suffix_not_protected():
    """"notpta.org" contains the chars "pta.org" but is not a subdomain
    of "pta.org" — must not be protected. This is the classic substring-
    vs-suffix confusion guard."""
    assert uab._is_protected("notpta.org") is False


def test_is_protected_suffix_chars_in_middle_not_protected():
    """"pta.org.example.com" ends in ".example.com", not ".pta.org"."""
    assert uab._is_protected("pta.org.example.com") is False


# ─── _parse_block_file ────────────────────────────────────────────────────

def test_parse_block_file_missing_file_returns_empty_set(tmp_path):
    missing = tmp_path / "does-not-exist.txt"
    assert uab._parse_block_file(str(missing)) == set()


def test_parse_block_file_strips_comment_only_lines(tmp_path):
    p = tmp_path / "block.txt"
    p.write_text(
        "# header comment\n"
        "spam@example.com\n"
        "# another comment\n",
        encoding="utf-8",
    )
    assert uab._parse_block_file(str(p)) == {"spam@example.com"}


def test_parse_block_file_strips_inline_comments(tmp_path):
    """Auto-added entries carry "# auto YYYY-MM-DD: reason" trailers;
    the parse must drop the trailer when comparing to existing entries."""
    p = tmp_path / "block.txt"
    p.write_text("spam@example.com  # auto 2026-01-01: weekly digest\n", encoding="utf-8")
    assert uab._parse_block_file(str(p)) == {"spam@example.com"}


def test_parse_block_file_strips_blank_lines(tmp_path):
    p = tmp_path / "block.txt"
    p.write_text("\nspam@example.com\n\n   \n", encoding="utf-8")
    assert uab._parse_block_file(str(p)) == {"spam@example.com"}


def test_parse_block_file_lowercases_entries(tmp_path):
    """Comparison set must be lowercased so dedup catches case-variant
    duplicates between auto and main blocklists."""
    p = tmp_path / "block.txt"
    p.write_text("Spam@Example.COM\n", encoding="utf-8")
    assert uab._parse_block_file(str(p)) == {"spam@example.com"}


def test_parse_block_file_dedupes_repeated_entries(tmp_path):
    p = tmp_path / "block.txt"
    p.write_text(
        "spam@example.com\n"
        "spam@example.com\n"
        "Spam@Example.com\n",
        encoding="utf-8",
    )
    assert uab._parse_block_file(str(p)) == {"spam@example.com"}


def test_parse_block_file_inline_comment_only_line_yields_no_entry(tmp_path):
    """A line that's just whitespace before '#' has no entry to keep."""
    p = tmp_path / "block.txt"
    p.write_text("  # just a comment after some whitespace\n", encoding="utf-8")
    assert uab._parse_block_file(str(p)) == set()


# ─── main() — end-to-end with stubbed date and file paths ────────────────
#
# Helper that writes suggestions, runs main, returns (rc, stdout,
# stderr, files-on-disk). Uses monkeypatch to freeze dt.date.today so
# the `# auto YYYY-MM-DD: ...` trailer is deterministic.


_FROZEN_DATE = dt.date(2026, 4, 22)


class _FrozenDate(dt.date):
    @classmethod
    def today(cls):
        return _FROZEN_DATE


def _run_main(monkeypatch, tmp_path, suggestions, *, main_block=None,
              auto_block_existing=None, audit_log=True):
    sug_path = tmp_path / "suggestions.json"
    sug_path.write_text(json.dumps(suggestions), encoding="utf-8")

    auto_path = tmp_path / "blocklist_auto.txt"
    if auto_block_existing is not None:
        auto_path.write_text(auto_block_existing, encoding="utf-8")

    main_path = tmp_path / "blocklist.txt"
    main_path.write_text(main_block or "", encoding="utf-8")

    audit_path = tmp_path / "audit.jsonl" if audit_log else None

    monkeypatch.setattr(uab.dt, "date", _FrozenDate)

    argv = [
        "update_auto_blocklist.py",
        "--suggestions", str(sug_path),
        "--auto-blocklist", str(auto_path),
        "--main-blocklist", str(main_path),
    ]
    if audit_path is not None:
        argv += ["--audit-log", str(audit_path)]
    monkeypatch.setattr(sys, "argv", argv)

    out_buf = io.StringIO()
    err_buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out_buf)
    monkeypatch.setattr(sys, "stderr", err_buf)

    rc = uab.main()
    return (
        rc,
        out_buf.getvalue(),
        err_buf.getvalue(),
        auto_path,
        audit_path,
    )


def test_main_missing_suggestions_file_exits_1(monkeypatch, tmp_path):
    """Unreadable suggestions file → exit 1, stderr error. No
    blocklist_auto.txt write."""
    auto_path = tmp_path / "blocklist_auto.txt"
    main_path = tmp_path / "blocklist.txt"
    main_path.write_text("")

    monkeypatch.setattr(sys, "argv", [
        "update_auto_blocklist.py",
        "--suggestions", str(tmp_path / "nope.json"),
        "--auto-blocklist", str(auto_path),
        "--main-blocklist", str(main_path),
    ])
    err_buf = io.StringIO()
    out_buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", err_buf)
    monkeypatch.setattr(sys, "stdout", out_buf)

    assert uab.main() == 1
    assert "could not read" in err_buf.getvalue()
    assert not auto_path.exists()


def test_main_malformed_suggestions_exits_1(monkeypatch, tmp_path):
    sug_path = tmp_path / "suggestions.json"
    sug_path.write_text("{ not json")
    auto_path = tmp_path / "blocklist_auto.txt"
    main_path = tmp_path / "blocklist.txt"
    main_path.write_text("")

    monkeypatch.setattr(sys, "argv", [
        "update_auto_blocklist.py",
        "--suggestions", str(sug_path),
        "--auto-blocklist", str(auto_path),
        "--main-blocklist", str(main_path),
    ])
    err_buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", err_buf)
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    assert uab.main() == 1
    assert "could not read" in err_buf.getvalue()


def test_main_non_list_suggestions_exits_1(monkeypatch, tmp_path):
    sug_path = tmp_path / "suggestions.json"
    sug_path.write_text(json.dumps({"from": "spam@x.com"}))
    auto_path = tmp_path / "blocklist_auto.txt"
    main_path = tmp_path / "blocklist.txt"
    main_path.write_text("")

    monkeypatch.setattr(sys, "argv", [
        "update_auto_blocklist.py",
        "--suggestions", str(sug_path),
        "--auto-blocklist", str(auto_path),
        "--main-blocklist", str(main_path),
    ])
    err_buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", err_buf)
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    assert uab.main() == 1
    assert "must be a JSON list" in err_buf.getvalue()


def test_main_happy_path_writes_entry_and_header(monkeypatch, tmp_path):
    """High-confidence, valid, non-protected, new → appended to
    blocklist_auto.txt with the auto-header (since the file was
    created fresh) and a `# auto YYYY-MM-DD: reason` trailer."""
    rc, stdout, _err, auto_path, audit_path = _run_main(
        monkeypatch, tmp_path,
        [{"from": "spam@example.com", "reason": "weekly deals",
          "confidence": "high"}],
    )
    assert rc == 0
    text = auto_path.read_text(encoding="utf-8")
    assert text.startswith("# Auto-populated blocklist")
    assert (
        "spam@example.com  # auto 2026-04-22: weekly deals\n" in text
    )
    assert "AUTO-BLOCK: spam@example.com" in stdout

    # Audit log carries one JSONL entry with the summary.
    audit_text = audit_path.read_text(encoding="utf-8")
    entry = json.loads(audit_text.strip())
    assert entry["run_iso"] == "2026-04-22"
    assert entry["suggestion_count"] == 1
    assert entry["added"] == [
        {"from": "spam@example.com", "reason": "weekly deals",
         "confidence": "high"}
    ]
    assert entry["rejected"] == []


def test_main_low_confidence_suggestion_rejected(monkeypatch, tmp_path):
    rc, stdout, _err, auto_path, audit_path = _run_main(
        monkeypatch, tmp_path,
        [{"from": "spam@example.com", "reason": "weekly deals",
          "confidence": "medium"}],
    )
    assert rc == 0
    assert not auto_path.exists()  # nothing added → never opened
    assert "rejected:" in stdout
    entry = json.loads(audit_path.read_text().strip())
    assert entry["added"] == []
    assert len(entry["rejected"]) == 1
    assert "confidence=" in entry["rejected"][0]["why"]


def test_main_invalid_address_rejected(monkeypatch, tmp_path):
    rc, stdout, _err, _ap, audit_path = _run_main(
        monkeypatch, tmp_path,
        [{"from": "not-an-email", "reason": "foo", "confidence": "high"}],
    )
    assert rc == 0
    entry = json.loads(audit_path.read_text().strip())
    assert entry["added"] == []
    assert entry["rejected"][0]["why"] == "not a valid email address"


def test_main_protected_domain_rejected(monkeypatch, tmp_path):
    """fcps.edu is a protected suffix; even with high confidence it
    never lands in the blocklist."""
    rc, stdout, _err, auto_path, audit_path = _run_main(
        monkeypatch, tmp_path,
        [{"from": "staff@school.fcps.edu", "reason": "foo",
          "confidence": "high"}],
    )
    assert rc == 0
    assert not auto_path.exists()
    entry = json.loads(audit_path.read_text().strip())
    assert entry["added"] == []
    assert "protected domain" in entry["rejected"][0]["why"]


def test_main_already_in_main_blocklist_rejected(monkeypatch, tmp_path):
    """Dedup source #1: main blocklist entries are read-only but
    block re-adding an auto entry that is already hand-curated."""
    rc, _stdout, _err, auto_path, audit_path = _run_main(
        monkeypatch, tmp_path,
        [{"from": "spam@example.com", "reason": "dup",
          "confidence": "high"}],
        main_block="spam@example.com\n",
    )
    assert rc == 0
    assert not auto_path.exists()
    entry = json.loads(audit_path.read_text().strip())
    assert entry["rejected"][0]["why"] == "already in blocklist"


def test_main_already_in_auto_blocklist_rejected(monkeypatch, tmp_path):
    """Dedup source #2: entries already in blocklist_auto.txt don't
    get re-appended with a fresh YYYY-MM-DD trailer."""
    rc, _stdout, _err, auto_path, audit_path = _run_main(
        monkeypatch, tmp_path,
        [{"from": "spam@example.com", "reason": "dup",
          "confidence": "high"}],
        auto_block_existing=(
            "# Auto-populated blocklist\n"
            "spam@example.com  # auto 2026-03-01: older\n"
        ),
    )
    assert rc == 0
    # File still exists but the entry count is unchanged.
    text = auto_path.read_text(encoding="utf-8")
    # The existing auto line is still there; no new line was appended.
    assert text.count("spam@example.com") == 1
    entry = json.loads(audit_path.read_text().strip())
    assert entry["rejected"][0]["why"] == "already in blocklist"


def test_main_non_dict_suggestion_rejected(monkeypatch, tmp_path):
    """Defensive: a suggestion that's a bare string or number must
    not crash main — it lands in the rejected bucket with 'not a dict'."""
    rc, _stdout, _err, auto_path, audit_path = _run_main(
        monkeypatch, tmp_path,
        ["not a dict", 42, None],
    )
    assert rc == 0
    assert not auto_path.exists()
    entry = json.loads(audit_path.read_text().strip())
    assert entry["added"] == []
    assert len(entry["rejected"]) == 3
    for row in entry["rejected"]:
        assert row["why"] == "not a dict"


def test_main_reason_truncated_and_hash_stripped(monkeypatch, tmp_path):
    """Reason is truncated to 80 chars and any `#` inside is
    stripped so the trailer stays on one line and doesn't confuse
    the `#`-based comment parser on a later read."""
    reason = "a" * 100 + " #stuff"
    rc, _stdout, _err, auto_path, _audit = _run_main(
        monkeypatch, tmp_path,
        [{"from": "spam@example.com", "reason": reason,
          "confidence": "high"}],
    )
    assert rc == 0
    text = auto_path.read_text(encoding="utf-8")
    # Find the added line and inspect only its trailer.
    line = [ln for ln in text.splitlines() if ln.startswith("spam@")][0]
    _, _, trailer = line.partition("# auto 2026-04-22: ")
    assert "#" not in trailer  # inline hash stripped
    assert len(trailer) <= 80


def test_main_summary_emitted_to_stderr(monkeypatch, tmp_path):
    """Regardless of audit-log flag, a one-line JSON summary with
    added_count / rejected_count lands on stderr."""
    rc, _stdout, stderr, _ap, _audit = _run_main(
        monkeypatch, tmp_path,
        [
            {"from": "spam@a.com", "reason": "r", "confidence": "high"},
            {"from": "bad", "reason": "r", "confidence": "high"},
        ],
    )
    assert rc == 0
    summary_line = [ln for ln in stderr.splitlines()
                    if ln.startswith("{")][-1]
    summary = json.loads(summary_line)
    assert summary["added_count"] == 1
    assert summary["rejected_count"] == 1


def test_main_audit_log_optional(monkeypatch, tmp_path):
    """Without --audit-log, main still runs to 0 and writes the
    auto-blocklist — the audit file is opt-in."""
    rc, _stdout, _err, auto_path, audit_path = _run_main(
        monkeypatch, tmp_path,
        [{"from": "spam@example.com", "reason": "r", "confidence": "high"}],
        audit_log=False,
    )
    assert rc == 0
    assert auto_path.exists()
    assert audit_path is None
