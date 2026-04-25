"""Pytest suite for scripts/update_auto_blocklist.py.

The script auto-mutates blocklist_auto.txt using agent suggestions and
maintains the pending+active state ledger in blocklist_auto_state.json.
Its remaining private helpers — _domain_of, _parse_block_file — gate
which addresses can be auto-added; a regression in either risks
polluting a tracked file with bad entries (or missing real ones).
The protection check is delegated to the shared
`protected_senders.is_protected` matcher (#26 unification); the
pending/active routing is delegated to `auto_blocklist_state` (#27 v1).
Matcher semantics are pinned in `tests/test_protected_senders.py`;
state semantics are pinned in `tests/test_auto_blocklist_state.py`;
this file pins only the integration through `main()`.

Coverage layers:
1. Helpers (_domain_of, _parse_block_file) — pinned individually with
   tolerated/rejected shapes.
2. `main()` — driven through argv against a tmp_path, with
   `dt.date.today()` monkeypatched for stable timestamps. Each
   guardrail branch (wrong confidence, invalid address, missing
   source_message_id, protected domain/address, useful sender,
   non-dict entry, error-exit branches) is exercised, plus the new
   pending/promote/refresh outcome paths, state-file round-trip,
   legacy-seeding, and the audit JSONL shape with all new buckets.
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


# Note: the seven legacy `test_is_protected_*` cases (exact / subdomain /
# deep-subdomain / unrelated-domain / case-insensitive / substring-not-
# suffix / suffix-chars-in-middle) were removed when `_is_protected` and
# `PROTECTED_SUFFIXES` were unified into the shared
# `protected_senders.is_protected` matcher (#26). The same semantics —
# exact bare-domain match, subdomain match with `.` boundary, suffix
# wildcard, case-insensitivity, substring-confusion guard — are now
# pinned in `tests/test_protected_senders.py`. Keeping the integration
# pin (`test_main_protected_domain_rejected`) below so a regression in
# the wiring is still caught.


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


def _suggest(addr, *, source_message_id="msg1", reason="r", confidence="high"):
    """Build a {from, source_message_id, reason, confidence} suggestion
    dict. ``source_message_id`` defaults to ``"msg1"`` so single-flag
    tests don't need to specify; tests exercising the N-strikes flow
    (#27) pass distinct values per flag to drive promotion."""
    return {
        "from": addr,
        "source_message_id": source_message_id,
        "reason": reason,
        "confidence": confidence,
    }


def _run_main(monkeypatch, tmp_path, suggestions, *, main_block=None,
              auto_block_existing=None, audit_log=True,
              protected_patterns=None, sender_stats=None,
              state_existing=None):
    sug_path = tmp_path / "suggestions.json"
    sug_path.write_text(json.dumps(suggestions), encoding="utf-8")

    auto_path = tmp_path / "blocklist_auto.txt"
    if auto_block_existing is not None:
        auto_path.write_text(auto_block_existing, encoding="utf-8")

    main_path = tmp_path / "blocklist.txt"
    main_path.write_text(main_block or "", encoding="utf-8")

    # Empty list = no protection; None = use a fresh empty file (no
    # protected patterns). Pass a list of patterns to write a temp
    # protected_senders.txt and route it through --protected-senders.
    protected_path = tmp_path / "protected_senders.txt"
    protected_path.write_text(
        "\n".join(protected_patterns or []) + ("\n" if protected_patterns else "")
    )

    # Sender-stats sidecar (#27 v1). None = empty stats (gate falls
    # through for every suggestion); a dict overrides — typically with a
    # `senders: {addr: {messages_seen, total_events, ...}}` shape that
    # newsletter_stats.load_stats accepts. Always written to a tmp file
    # and routed through --sender-stats so existing tests are
    # deterministic against the project's real sender_stats.json (which
    # only exists on the state branch in production).
    stats_path = tmp_path / "sender_stats.json"
    stats_payload = sender_stats if sender_stats is not None else {
        "schema_version": 1,
        "last_updated_iso": "",
        "senders": {},
    }
    stats_path.write_text(json.dumps(stats_payload), encoding="utf-8")

    # Pending+active state file (#27 v1). None = file does not exist
    # pre-run; main() loads the empty state and saves a populated one.
    # A dict overrides — typically pre-seeds pending or active rows for
    # tests that exercise the promote / refresh paths.
    state_path = tmp_path / "blocklist_auto_state.json"
    if state_existing is not None:
        state_path.write_text(json.dumps(state_existing), encoding="utf-8")

    audit_path = tmp_path / "audit.jsonl" if audit_log else None

    monkeypatch.setattr(uab.dt, "date", _FrozenDate)

    argv = [
        "update_auto_blocklist.py",
        "--suggestions", str(sug_path),
        "--auto-blocklist", str(auto_path),
        "--main-blocklist", str(main_path),
        "--protected-senders", str(protected_path),
        "--sender-stats", str(stats_path),
        "--state-file", str(state_path),
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
        state_path,
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


def test_main_first_flag_lands_in_pending_not_txt(monkeypatch, tmp_path):
    """#27 lever 2 behavior switch: a first high-confidence flag for
    an unknown sender lands in the pending section of the state file
    — NOT in blocklist_auto.txt. Promotion to active (and the txt
    write) requires a second flag from a distinct message_id."""
    rc, stdout, _err, auto_path, audit_path, state_path = _run_main(
        monkeypatch, tmp_path,
        [_suggest("spam@example.com",
                  source_message_id="msg-first",
                  reason="weekly deals")],
    )
    assert rc == 0
    # txt is NOT created — single flag never reaches active.
    assert not auto_path.exists()
    # Stdout reports the pending outcome.
    assert "pending:    spam@example.com" in stdout
    # State file has the pending entry.
    state = json.loads(state_path.read_text())
    assert "spam@example.com" in state["pending"]
    assert "spam@example.com" not in state["active"]
    pending_entry = state["pending"]["spam@example.com"]
    assert pending_entry["flagged_message_ids"] == ["msg-first"]
    assert pending_entry["first_flagged_iso"] == "2026-04-22"
    # Audit log records under pending_added; promoted is empty.
    entry = json.loads(audit_path.read_text().strip())
    assert entry["added"] == []
    assert entry["promoted"] == []
    assert len(entry["pending_added"]) == 1
    assert entry["pending_added"][0]["from"] == "spam@example.com"
    assert entry["pending_added"][0]["source_message_id"] == "msg-first"


def test_main_second_flag_distinct_message_promotes(monkeypatch, tmp_path):
    """Two suggestions in one run for the same address with distinct
    source_message_ids → first-flag pending entry promotes on the
    second iteration; the txt is written with the # auto trailer."""
    rc, stdout, _err, auto_path, audit_path, state_path = _run_main(
        monkeypatch, tmp_path,
        [
            _suggest("spam@example.com",
                     source_message_id="msg-1", reason="r1"),
            _suggest("spam@example.com",
                     source_message_id="msg-2", reason="r2"),
        ],
    )
    assert rc == 0
    text = auto_path.read_text(encoding="utf-8")
    assert text.startswith("# Auto-populated blocklist")
    assert "spam@example.com  # auto 2026-04-22: r2\n" in text
    assert "PROMOTED: spam@example.com" in stdout
    state = json.loads(state_path.read_text())
    # After promotion: pending empty, active populated.
    assert "spam@example.com" not in state["pending"]
    assert state["active"]["spam@example.com"]["last_flagged_iso"] == "2026-04-22"
    # Audit log: promoted has one entry; pending_added empty (first
    # flag was promoted on the second iteration in the same loop).
    entry = json.loads(audit_path.read_text().strip())
    assert len(entry["promoted"]) == 1
    assert entry["promoted"][0]["source_message_id"] == "msg-2"
    # Legacy `added` field tracks the same set as promoted.
    assert entry["added"] == [
        {"from": "spam@example.com", "reason": "r2", "confidence": "high"}
    ]


def test_main_active_refreshed_extends_last_flagged_iso(monkeypatch, tmp_path):
    """Pre-seeded active entry. A repeat flag bumps last_flagged_iso
    to today and emits the active_refreshed outcome (NOT a fresh
    promotion or txt write — the entry is already in the txt)."""
    rc, stdout, _err, auto_path, audit_path, state_path = _run_main(
        monkeypatch, tmp_path,
        [_suggest("spam@example.com",
                  source_message_id="msg-new", reason="fresh deal")],
        auto_block_existing=(
            "# Auto-populated blocklist\n"
            "spam@example.com  # auto 2026-01-15: older\n"
        ),
        state_existing={
            "schema_version": 1,
            "last_updated_iso": "2026-01-15T00:00:00",
            "pending": {},
            "active": {
                "spam@example.com": {
                    "added_iso": "2026-01-15",
                    "last_flagged_iso": "2026-01-15",
                    "reason": "older",
                },
            },
        },
    )
    assert rc == 0
    # txt is unchanged — single line, no duplicate.
    text = auto_path.read_text(encoding="utf-8")
    assert text.count("spam@example.com") == 1
    assert "refreshed:  spam@example.com" in stdout
    # Active row's last_flagged_iso is bumped; added_iso is preserved.
    state = json.loads(state_path.read_text())
    active = state["active"]["spam@example.com"]
    assert active["last_flagged_iso"] == "2026-04-22"
    assert active["added_iso"] == "2026-01-15"
    entry = json.loads(audit_path.read_text().strip())
    assert len(entry["active_refreshed"]) == 1
    assert entry["active_refreshed"][0]["source_message_id"] == "msg-new"


def test_main_resolved_by_main_blocklist_clears_pending(monkeypatch, tmp_path):
    """If the operator hand-blocks an address that's currently in
    pending, the next agent flag triggers the resolved_by_main_blocklist
    outcome — pending entry dropped, no active write, no txt change."""
    rc, stdout, _err, auto_path, audit_path, state_path = _run_main(
        monkeypatch, tmp_path,
        [_suggest("spam@example.com", source_message_id="msg-2")],
        main_block="spam@example.com\n",
        state_existing={
            "schema_version": 1,
            "last_updated_iso": "2026-04-15T00:00:00",
            "pending": {
                "spam@example.com": {
                    "first_flagged_iso": "2026-04-15",
                    "last_flagged_iso": "2026-04-15",
                    "flagged_message_ids": ["msg-1"],
                    "reason_samples": ["older"],
                },
            },
            "active": {},
        },
    )
    assert rc == 0
    assert not auto_path.exists()
    assert "resolved:   spam@example.com" in stdout
    state = json.loads(state_path.read_text())
    assert "spam@example.com" not in state["pending"]
    assert "spam@example.com" not in state["active"]
    entry = json.loads(audit_path.read_text().strip())
    assert len(entry["resolved_by_main_blocklist"]) == 1


def test_main_synthetic_seed_for_legacy_active_entries(monkeypatch, tmp_path):
    """Pre-deploy txt entries with no state row get
    last_flagged_iso = today on first run via seed_active_from_legacy.
    The state file post-run has the entry under `active` with
    today's date."""
    rc, stdout, _err, _ap, _audit, state_path = _run_main(
        monkeypatch, tmp_path,
        [],  # no suggestions; seeding still runs
        auto_block_existing=(
            "# Auto-populated blocklist\n"
            "legacy1@example.com  # auto 2026-01-01: legacy\n"
            "legacy2@example.com  # auto 2026-02-15: also legacy\n"
        ),
        state_existing=None,  # no state file pre-run
    )
    assert rc == 0
    assert "Seeded 2 legacy" in stdout
    state = json.loads(state_path.read_text())
    for addr in ("legacy1@example.com", "legacy2@example.com"):
        entry = state["active"][addr]
        assert entry["added_iso"] == "2026-04-22"
        assert entry["last_flagged_iso"] == "2026-04-22"


def test_main_state_file_round_trips_through_main(monkeypatch, tmp_path):
    """Pre-existing state with one pending and one active entry;
    main runs with no suggestions (just persists the state). The
    on-disk file post-run contains exactly the same entries plus an
    updated last_updated_iso."""
    pre_state = {
        "schema_version": 1,
        "last_updated_iso": "2026-04-15T00:00:00",
        "pending": {
            "watch@example.com": {
                "first_flagged_iso": "2026-04-15",
                "last_flagged_iso": "2026-04-15",
                "flagged_message_ids": ["msg-1"],
                "reason_samples": ["watching"],
            },
        },
        "active": {
            "blocked@example.com": {
                "added_iso": "2026-03-01",
                "last_flagged_iso": "2026-04-10",
                "reason": "blocked",
            },
        },
    }
    rc, _stdout, _err, _ap, _audit, state_path = _run_main(
        monkeypatch, tmp_path, [], state_existing=pre_state,
    )
    assert rc == 0
    post = json.loads(state_path.read_text())
    assert post["pending"] == pre_state["pending"]
    assert post["active"] == pre_state["active"]
    # last_updated_iso bumps to today.
    assert post["last_updated_iso"] == "2026-04-22"


def test_main_low_confidence_suggestion_rejected(monkeypatch, tmp_path):
    rc, stdout, _err, auto_path, audit_path, _sp = _run_main(
        monkeypatch, tmp_path,
        [_suggest("spam@example.com",
                  reason="weekly deals", confidence="medium")],
    )
    assert rc == 0
    assert not auto_path.exists()
    assert "rejected:" in stdout
    entry = json.loads(audit_path.read_text().strip())
    assert entry["added"] == []
    assert len(entry["rejected"]) == 1
    assert "confidence=" in entry["rejected"][0]["why"]


def test_main_invalid_address_rejected(monkeypatch, tmp_path):
    rc, _stdout, _err, _ap, audit_path, _sp = _run_main(
        monkeypatch, tmp_path,
        [_suggest("not-an-email", reason="foo")],
    )
    assert rc == 0
    entry = json.loads(audit_path.read_text().strip())
    assert entry["added"] == []
    assert entry["rejected"][0]["why"] == "not a valid email address"


def test_main_missing_source_message_id_rejected(monkeypatch, tmp_path):
    """#27: a high-confidence flag without source_message_id is
    malformed at the gate. The agent's prompt asks for the field;
    flags missing it can't participate in N-strikes corroboration
    and would silently break the duplicate-flag defense if treated
    as empty-string ids."""
    rc, _stdout, _err, auto_path, audit_path, state_path = _run_main(
        monkeypatch, tmp_path,
        [{"from": "spam@example.com", "reason": "no msg id",
          "confidence": "high"}],  # raw dict, no source_message_id
    )
    assert rc == 0
    assert not auto_path.exists()
    state = json.loads(state_path.read_text())
    assert "spam@example.com" not in state["pending"]
    entry = json.loads(audit_path.read_text().strip())
    assert entry["added"] == []
    assert entry["rejected"][0]["why"] == "missing source_message_id"


def test_main_missing_source_message_id_empty_string_rejected(
    monkeypatch, tmp_path,
):
    """An explicit empty-string source_message_id is also rejected —
    the agent might emit `""` as a placeholder if it can't echo back
    the id; that case must NOT silently become a real id at the gate."""
    rc, _stdout, _err, _ap, audit_path, _sp = _run_main(
        monkeypatch, tmp_path,
        [_suggest("spam@example.com", source_message_id="")],
    )
    assert rc == 0
    entry = json.loads(audit_path.read_text().strip())
    assert entry["rejected"][0]["why"] == "missing source_message_id"


def test_main_protected_domain_rejected(monkeypatch, tmp_path):
    """A bare-domain protected pattern (fcps.edu) rejects subdomains too.

    Routed through `protected_senders.is_protected` which matches
    exact + `.suffix` (#26 unification). Pinned here as the integration
    check that the wiring loads protected_senders.txt and threads
    patterns through to the gate."""
    rc, _stdout, _err, auto_path, audit_path, _sp = _run_main(
        monkeypatch, tmp_path,
        [_suggest("staff@school.fcps.edu", reason="foo")],
        protected_patterns=["fcps.edu"],
    )
    assert rc == 0
    assert not auto_path.exists()
    entry = json.loads(audit_path.read_text().strip())
    assert entry["added"] == []
    assert "protected domain" in entry["rejected"][0]["why"]


def test_main_protected_address_form_rejected(monkeypatch, tmp_path):
    """Address-form protected pattern rejects exactly that address.

    Load-bearing #26 fix: ellen.n.holmes@gmail.com on the protected
    list MUST never land in blocklist_auto.txt no matter how
    confident the agent's adult-only judgment is on a one-shot email.
    Under #27, this also means the address never enters the pending
    ledger — protection wins before the pending check."""
    rc, _stdout, _err, auto_path, audit_path, state_path = _run_main(
        monkeypatch, tmp_path,
        [_suggest("ellen.n.holmes@gmail.com",
                  reason="adult personal email about tax return")],
        protected_patterns=["ellen.n.holmes@gmail.com"],
    )
    assert rc == 0
    assert not auto_path.exists()
    state = json.loads(state_path.read_text())
    assert "ellen.n.holmes@gmail.com" not in state["pending"]
    assert "ellen.n.holmes@gmail.com" not in state["active"]
    entry = json.loads(audit_path.read_text().strip())
    why = entry["rejected"][0]["why"]
    assert "protected sender" in why
    assert "ellen.n.holmes@gmail.com" in why


def test_main_address_form_pattern_does_not_protect_other_gmail_addresses(
    monkeypatch, tmp_path,
):
    """Address-form protection is per-address, not per-domain.

    Pin: protecting `ellen.n.holmes@gmail.com` does NOT protect every
    gmail.com sender. Otherwise we'd un-block every personal Gmail in
    the freemail-block universe (#20). Under #27, the unprotected
    address lands in pending (NOT in the txt — that's the second-flag
    promotion path)."""
    rc, _stdout, _err, auto_path, _audit, state_path = _run_main(
        monkeypatch, tmp_path,
        [_suggest("spammer.123@gmail.com", reason="junk")],
        protected_patterns=["ellen.n.holmes@gmail.com"],
    )
    assert rc == 0
    assert not auto_path.exists()  # pending, not active
    state = json.loads(state_path.read_text())
    assert "spammer.123@gmail.com" in state["pending"]


def test_main_non_dict_suggestion_rejected(monkeypatch, tmp_path):
    """Defensive: a suggestion that's a bare string or number must
    not crash main — it lands in the rejected bucket with 'not a dict'."""
    rc, _stdout, _err, auto_path, audit_path, _sp = _run_main(
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
    """Reason is truncated to 80 chars and any `#` inside is stripped
    so the trailer stays on one line and doesn't confuse the
    `#`-based comment parser on a later read.

    Under #27, the trailer is only written on PROMOTION — so this
    test feeds two distinct-message flags to trigger the txt write."""
    long_reason = "a" * 100 + " #stuff"
    rc, _stdout, _err, auto_path, _audit, _sp = _run_main(
        monkeypatch, tmp_path,
        [
            _suggest("spam@example.com", source_message_id="msg-1",
                     reason=long_reason),
            _suggest("spam@example.com", source_message_id="msg-2",
                     reason=long_reason),
        ],
    )
    assert rc == 0
    text = auto_path.read_text(encoding="utf-8")
    line = [ln for ln in text.splitlines() if ln.startswith("spam@")][0]
    _, _, trailer = line.partition("# auto 2026-04-22: ")
    assert "#" not in trailer
    assert len(trailer) <= 80


def test_main_summary_emitted_to_stderr(monkeypatch, tmp_path):
    """One-line JSON summary on stderr. With #27 the summary now also
    carries the new outcome counts (pending/promoted/refreshed/etc.)
    while preserving the legacy added_count / rejected_count fields."""
    rc, _stdout, stderr, _ap, _audit, _sp = _run_main(
        monkeypatch, tmp_path,
        [
            _suggest("spam@a.com", source_message_id="msg-a"),
            _suggest("bad", source_message_id="msg-b"),
        ],
    )
    assert rc == 0
    summary_line = [ln for ln in stderr.splitlines()
                    if ln.startswith("{")][-1]
    summary = json.loads(summary_line)
    # spam@a.com → pending_added (first flag); bad → rejected (invalid).
    assert summary["added_count"] == 0
    assert summary["promoted_count"] == 0
    assert summary["pending_added_count"] == 1
    assert summary["rejected_count"] == 1


def test_main_audit_log_optional(monkeypatch, tmp_path):
    """Without --audit-log, main still runs to 0 and the state file
    is still saved — only the audit JSONL is opt-in. The state file
    is the load-bearing persistence; the audit log is observability."""
    rc, _stdout, _err, _ap, audit_path, state_path = _run_main(
        monkeypatch, tmp_path,
        [_suggest("spam@example.com", source_message_id="msg-1")],
        audit_log=False,
    )
    assert rc == 0
    assert audit_path is None
    # State file is always saved.
    state = json.loads(state_path.read_text())
    assert "spam@example.com" in state["pending"]


def test_main_audit_log_records_all_outcome_buckets(monkeypatch, tmp_path):
    """A single run hitting four outcomes (pending, promoted, refreshed,
    resolved) produces an audit log entry with all four buckets
    populated, plus the legacy `added`/`rejected` shape for backward
    compat. Pins the audit-log schema as the integration contract."""
    rc, _stdout, _err, _ap, audit_path, _sp = _run_main(
        monkeypatch, tmp_path,
        [
            # pending_added: first flag for unknown sender
            _suggest("new-sender@a.com", source_message_id="msg-a1"),
            # promoted: second flag for sender already in pending
            _suggest("promoting@b.com", source_message_id="msg-b2"),
            # active_refreshed: flag for already-active sender
            _suggest("already-active@c.com", source_message_id="msg-c1"),
            # resolved_by_main_blocklist: flag for hand-blocked sender
            _suggest("hand-blocked@d.com", source_message_id="msg-d1"),
        ],
        main_block="hand-blocked@d.com\n",
        auto_block_existing=(
            "# Auto-populated blocklist\n"
            "already-active@c.com  # auto 2026-01-01: prior\n"
        ),
        state_existing={
            "schema_version": 1,
            "last_updated_iso": "2026-04-15T00:00:00",
            "pending": {
                "promoting@b.com": {
                    "first_flagged_iso": "2026-04-15",
                    "last_flagged_iso": "2026-04-15",
                    "flagged_message_ids": ["msg-b1"],
                    "reason_samples": ["earlier"],
                },
            },
            "active": {
                "already-active@c.com": {
                    "added_iso": "2026-01-01",
                    "last_flagged_iso": "2026-01-01",
                    "reason": "prior",
                },
            },
        },
    )
    assert rc == 0
    entry = json.loads(audit_path.read_text().strip())
    assert len(entry["pending_added"]) == 1
    assert entry["pending_added"][0]["from"] == "new-sender@a.com"
    assert len(entry["promoted"]) == 1
    assert entry["promoted"][0]["from"] == "promoting@b.com"
    assert len(entry["active_refreshed"]) == 1
    assert entry["active_refreshed"][0]["from"] == "already-active@c.com"
    assert len(entry["resolved_by_main_blocklist"]) == 1
    assert entry["resolved_by_main_blocklist"][0]["from"] == "hand-blocked@d.com"
    # Legacy `added` field still present, equals the promoted set.
    assert entry["added"] == [
        {"from": "promoting@b.com", "reason": "r", "confidence": "high"}
    ]


# ─── #27 v1 — sender-stats reject gate ────────────────────────────────────
#
# A high-confidence flag for a sender that has historically produced
# kid events must be rejected even when every other gate would pass it.
# The gate keys on the lowercased mailbox (matches `agent._sender_key`
# and the `newsletter_stats.update_sender_counts` write key). Empty
# stats / unknown sender / below-threshold all fall through to the
# pending/active routing.


def test_main_rejects_when_sender_stats_show_useful_sender(monkeypatch, tmp_path):
    """`messages_seen >= 3 AND total_events >= 1` → reject (NOT pending,
    NOT active). The sender-stats gate sits ahead of the pending/active
    routing so a useful sender never enters the watchlist either."""
    rc, _stdout, _err, auto_path, audit_path, state_path = _run_main(
        monkeypatch, tmp_path,
        [_suggest("newsletter@school.edu",
                  reason="no kid events this issue")],
        sender_stats={
            "schema_version": 1,
            "last_updated_iso": "2026-04-25T00:00:00",
            "senders": {
                "newsletter@school.edu": {
                    "messages_seen": 5,
                    "total_events": 8,
                    "per_message_counts": [1, 2, 1, 3, 1],
                    "first_seen_iso": "2026-01-01",
                    "last_seen_iso": "2026-04-20",
                    "is_newsletter": True,
                },
            },
        },
    )
    assert rc == 0
    assert not auto_path.exists()
    state = json.loads(state_path.read_text())
    assert "newsletter@school.edu" not in state["pending"]
    assert "newsletter@school.edu" not in state["active"]
    entry = json.loads(audit_path.read_text().strip())
    assert entry["added"] == []
    why = entry["rejected"][0]["why"]
    assert why.startswith("useful sender")
    assert "8" in why and "5" in why


def test_main_does_not_reject_when_sender_stats_below_message_threshold(
    monkeypatch, tmp_path,
):
    """Below `messages_seen >= 3` → fall through to pending/active
    routing. The first flag for a sub-threshold sender lands in
    pending, NOT in the txt — that's the N-strikes path, not the
    sender-stats rejection path."""
    rc, _stdout, _err, auto_path, _audit, state_path = _run_main(
        monkeypatch, tmp_path,
        [_suggest("newsletter@school.edu", reason="no kid events")],
        sender_stats={
            "schema_version": 1,
            "last_updated_iso": "2026-04-25T00:00:00",
            "senders": {
                "newsletter@school.edu": {
                    "messages_seen": 2,
                    "total_events": 5,
                    "per_message_counts": [3, 2],
                    "first_seen_iso": "2026-04-10",
                    "last_seen_iso": "2026-04-20",
                    "is_newsletter": False,
                },
            },
        },
    )
    assert rc == 0
    assert not auto_path.exists()  # pending, not active
    state = json.loads(state_path.read_text())
    assert "newsletter@school.edu" in state["pending"]


def test_main_does_not_reject_when_sender_stats_total_events_zero(
    monkeypatch, tmp_path,
):
    """`total_events == 0` → fall through to pending/active routing.
    A sender with three observed messages but zero extracted kid events
    looks exactly like the noisy sender we want to (eventually) block.
    Under #27 the first flag still goes to pending — N-strikes will
    block on the second corroborating flag from a distinct message."""
    rc, _stdout, _err, auto_path, _audit, state_path = _run_main(
        monkeypatch, tmp_path,
        [_suggest("marketing@deals.com", reason="weekly promotions")],
        sender_stats={
            "schema_version": 1,
            "last_updated_iso": "2026-04-25T00:00:00",
            "senders": {
                "marketing@deals.com": {
                    "messages_seen": 6,
                    "total_events": 0,
                    "per_message_counts": [0, 0, 0, 0, 0, 0],
                    "first_seen_iso": "2026-02-01",
                    "last_seen_iso": "2026-04-22",
                    "is_newsletter": False,
                },
            },
        },
    )
    assert rc == 0
    assert not auto_path.exists()
    state = json.loads(state_path.read_text())
    assert "marketing@deals.com" in state["pending"]
