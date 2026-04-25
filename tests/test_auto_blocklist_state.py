"""Pytest suite for scripts/auto_blocklist_state.py (#27 v1).

Pure helpers (``add_or_promote``, ``tick_ttl``,
``seed_active_from_legacy``, ``_parse_iso``) get unit tests with
in-memory state dicts; ``load_state`` / ``save_state`` get round-trip
and corruption tests using ``tmp_path``. The schema is the contract —
a regression here would silently corrupt the auto-blocklist state on
the state branch and cascade through the gating layer.

The five ``add_or_promote`` outcome labels each get at least one pin:

  - ``"resolved_by_main_blocklist"``
  - ``"active_refreshed"``
  - ``"duplicate_flag"``
  - ``"pending_promoted"``
  - ``"pending_added"``
"""
from __future__ import annotations

import datetime as dt
import json

# scripts/ is added to sys.path by tests/conftest.py
import auto_blocklist_state as als  # noqa: E402


_TODAY = dt.date(2026, 4, 25)


# ─── load_state / save_state ─────────────────────────────────────────────

def test_load_state_missing_file_returns_empty(tmp_path):
    state = als.load_state(str(tmp_path / "does-not-exist.json"))
    assert state == {
        "schema_version": 1,
        "last_updated_iso": "",
        "pending": {},
        "active": {},
    }


def test_load_state_corrupt_json_returns_empty_with_warning(tmp_path, capsys):
    p = tmp_path / "state.json"
    p.write_text("{ not json")
    state = als.load_state(str(p))
    assert state["pending"] == {}
    assert state["active"] == {}
    assert "unreadable" in capsys.readouterr().err


def test_load_state_non_dict_returns_empty_with_warning(tmp_path, capsys):
    """A JSON list (or any non-object) is rejected — the schema is
    a top-level dict and we never want to mis-merge a list into a
    dict-typed handler."""
    p = tmp_path / "state.json"
    p.write_text(json.dumps(["not", "a", "dict"]))
    state = als.load_state(str(p))
    assert state["pending"] == {}
    assert state["active"] == {}
    assert "not a JSON object" in capsys.readouterr().err


def test_load_state_schema_version_mismatch_returns_empty_with_warning(
    tmp_path, capsys,
):
    """Schema-version drift is the load-bearing safety net. A future
    schema bump that adds/renames a field MUST trip this branch so the
    load returns empty rather than silently mis-parsing."""
    p = tmp_path / "state.json"
    p.write_text(json.dumps({
        "schema_version": 99,
        "pending": {"x": {}},
        "active": {"y": {}},
    }))
    state = als.load_state(str(p))
    assert state["pending"] == {}
    assert state["active"] == {}
    assert "schema version mismatch" in capsys.readouterr().err


def test_load_state_round_trips_pending_and_active(tmp_path):
    """Faithful round trip: write a fully-populated payload, read it
    back, both sections preserved verbatim."""
    payload = {
        "schema_version": 1,
        "last_updated_iso": "2026-04-25T12:00:00",
        "pending": {
            "spam@example.com": {
                "first_flagged_iso": "2026-04-25",
                "last_flagged_iso": "2026-04-25",
                "flagged_message_ids": ["msgid1"],
                "reason_samples": ["weekly deals"],
            },
        },
        "active": {
            "ads@example.org": {
                "added_iso": "2026-01-01",
                "last_flagged_iso": "2026-04-20",
                "reason": "newsletter",
            },
        },
    }
    p = tmp_path / "state.json"
    p.write_text(json.dumps(payload))
    state = als.load_state(str(p))
    assert state["pending"] == payload["pending"]
    assert state["active"] == payload["active"]
    assert state["last_updated_iso"] == "2026-04-25T12:00:00"


def test_save_state_stamps_last_updated_iso(tmp_path):
    p = tmp_path / "state.json"
    state = als._empty_state()
    als.save_state(str(p), state, "2026-04-25T12:00:00")
    on_disk = json.loads(p.read_text())
    assert on_disk["last_updated_iso"] == "2026-04-25T12:00:00"
    assert on_disk["schema_version"] == 1


def test_save_state_atomic_no_tempfile_left_behind(tmp_path):
    """Atomic write should rename the tempfile into place; nothing
    transient should remain after a successful save."""
    p = tmp_path / "state.json"
    als.save_state(str(p), als._empty_state(), "2026-04-25T12:00:00")
    assert not (tmp_path / "state.json.tmp").exists()
    assert p.exists()


# ─── add_or_promote: pending_added ───────────────────────────────────────

def test_add_or_promote_first_flag_lands_in_pending():
    state = als._empty_state()
    label = als.add_or_promote(
        state, "spam@example.com", "msgid1", "weekly deals", _TODAY,
        already_active=False, already_in_main_blocklist=False,
    )
    assert label == "pending_added"
    assert "spam@example.com" in state["pending"]
    assert "spam@example.com" not in state["active"]
    entry = state["pending"]["spam@example.com"]
    assert entry["flagged_message_ids"] == ["msgid1"]
    assert entry["first_flagged_iso"] == "2026-04-25"
    assert entry["last_flagged_iso"] == "2026-04-25"
    assert entry["reason_samples"] == ["weekly deals"]


def test_add_or_promote_pending_added_lowercases_address():
    """Address is lowercased before storage so case-variant flags on
    the same mailbox don't create duplicate pending entries."""
    state = als._empty_state()
    als.add_or_promote(
        state, "Spam@Example.COM", "msgid1", "r1", _TODAY,
        already_active=False, already_in_main_blocklist=False,
    )
    assert "spam@example.com" in state["pending"]
    assert "Spam@Example.COM" not in state["pending"]


# ─── add_or_promote: pending_promoted ────────────────────────────────────

def test_add_or_promote_second_flag_distinct_message_promotes():
    state = als._empty_state()
    als.add_or_promote(
        state, "spam@example.com", "msgid1", "r1", _TODAY,
        already_active=False, already_in_main_blocklist=False,
    )
    label = als.add_or_promote(
        state, "spam@example.com", "msgid2", "r2", _TODAY,
        already_active=False, already_in_main_blocklist=False,
    )
    assert label == "pending_promoted"
    assert "spam@example.com" in state["active"]
    assert "spam@example.com" not in state["pending"]
    entry = state["active"]["spam@example.com"]
    assert entry["added_iso"] == "2026-04-25"
    assert entry["last_flagged_iso"] == "2026-04-25"
    # Most-recent flag's reason wins — the audit log preserves history.
    assert entry["reason"] == "r2"


def test_add_or_promote_promotion_preserves_pending_iso_dates_in_active():
    """The promotion records today's date as added_iso, not the pending
    entry's first_flagged_iso. Active TTL therefore counts from the
    promotion moment, which matches the operator-visible behavior of
    blocklist_auto.txt's `# auto YYYY-MM-DD: ...` trailer."""
    state = als._empty_state()
    als.add_or_promote(
        state, "spam@example.com", "msgid1", "r1", dt.date(2026, 4, 18),
        already_active=False, already_in_main_blocklist=False,
    )
    als.add_or_promote(
        state, "spam@example.com", "msgid2", "r2", dt.date(2026, 4, 25),
        already_active=False, already_in_main_blocklist=False,
    )
    assert state["active"]["spam@example.com"]["added_iso"] == "2026-04-25"


# ─── add_or_promote: duplicate_flag ──────────────────────────────────────

def test_add_or_promote_second_flag_same_message_is_duplicate_flag():
    """``--reextract`` re-fires the agent on a previously-processed
    message. The second flag for the same message_id must NOT count
    toward the strike count — otherwise re-extraction would double-
    count strikes and falsely promote."""
    state = als._empty_state()
    als.add_or_promote(
        state, "spam@example.com", "msgid1", "r1", _TODAY,
        already_active=False, already_in_main_blocklist=False,
    )
    label = als.add_or_promote(
        state, "spam@example.com", "msgid1", "r1-redux",
        dt.date(2026, 4, 26),
        already_active=False, already_in_main_blocklist=False,
    )
    assert label == "duplicate_flag"
    # Still pending, single message ID. last_flagged_iso bumped to
    # today (the new flag is a fresh observation, even though the
    # message is the same — relevant for TTL aging).
    entry = state["pending"]["spam@example.com"]
    assert entry["flagged_message_ids"] == ["msgid1"]
    assert entry["last_flagged_iso"] == "2026-04-26"
    assert "spam@example.com" not in state["active"]


# ─── add_or_promote: active_refreshed ────────────────────────────────────

def test_add_or_promote_active_refreshed_via_already_active_flag():
    """Caller signals via ``already_active=True``. Bumps
    last_flagged_iso to today; preserves added_iso (TTL counts from
    when the block was originally created, not from the latest flag)."""
    state = als._empty_state()
    state["active"]["spam@example.com"] = {
        "added_iso": "2026-01-01",
        "last_flagged_iso": "2026-03-01",
        "reason": "weekly deals",
    }
    label = als.add_or_promote(
        state, "spam@example.com", "msgid_new", "fresh deal", _TODAY,
        already_active=True, already_in_main_blocklist=False,
    )
    assert label == "active_refreshed"
    assert state["active"]["spam@example.com"]["last_flagged_iso"] == "2026-04-25"
    assert state["active"]["spam@example.com"]["added_iso"] == "2026-01-01"


def test_add_or_promote_active_refreshed_drops_stale_pending_entry():
    """Defense against a corrupt state where the same address exists
    in BOTH pending and active. Once active wins, drop the pending
    entry so the next tick_ttl doesn't surface it as aged_out."""
    state = als._empty_state()
    state["pending"]["spam@example.com"] = {
        "first_flagged_iso": "2026-04-01",
        "last_flagged_iso": "2026-04-01",
        "flagged_message_ids": ["old_msg"],
        "reason_samples": ["old"],
    }
    state["active"]["spam@example.com"] = {
        "added_iso": "2026-04-15",
        "last_flagged_iso": "2026-04-15",
        "reason": "weekly deals",
    }
    als.add_or_promote(
        state, "spam@example.com", "msgid_new", "fresh", _TODAY,
        already_active=True, already_in_main_blocklist=False,
    )
    assert "spam@example.com" not in state["pending"]
    assert "spam@example.com" in state["active"]


# ─── add_or_promote: resolved_by_main_blocklist ──────────────────────────

def test_add_or_promote_resolved_by_main_blocklist_drops_pending():
    """If Tom adds an address to the hand-curated blocklist.txt while
    it's pending, the next cron's load step finds it in the union and
    clears the pending entry as resolved. The bot defers to the
    operator's hand decision."""
    state = als._empty_state()
    state["pending"]["spam@example.com"] = {
        "first_flagged_iso": "2026-04-20",
        "last_flagged_iso": "2026-04-20",
        "flagged_message_ids": ["msgid1"],
        "reason_samples": ["r1"],
    }
    label = als.add_or_promote(
        state, "spam@example.com", "msgid_doesnt_matter", "anything",
        _TODAY,
        already_active=False, already_in_main_blocklist=True,
    )
    assert label == "resolved_by_main_blocklist"
    assert "spam@example.com" not in state["pending"]
    assert "spam@example.com" not in state["active"]


def test_add_or_promote_resolved_by_main_blocklist_does_not_touch_active():
    """Operator-blocked address must NOT land in active state — the
    operator owns blocklist.txt; the bot owns blocklist_auto.txt and
    this state file. They don't share entries."""
    state = als._empty_state()
    label = als.add_or_promote(
        state, "spam@example.com", "msgid1", "r1", _TODAY,
        already_active=False, already_in_main_blocklist=True,
    )
    assert label == "resolved_by_main_blocklist"
    assert "spam@example.com" not in state["active"]
    assert "spam@example.com" not in state["pending"]


# ─── tick_ttl ────────────────────────────────────────────────────────────

def test_tick_ttl_expires_active_after_active_ttl_days():
    """Active entry whose last_flagged_iso is older than 90 days is
    pruned and surfaced in the ``expired`` list."""
    state = als._empty_state()
    state["active"]["spam@example.com"] = {
        "added_iso": "2026-01-01",
        "last_flagged_iso": "2026-01-01",
        "reason": "old",
    }
    # 2026-04-25 - 2026-01-01 = 114 days (> 90)
    result = als.tick_ttl(state, _TODAY)
    assert result["expired"] == ["spam@example.com"]
    assert "spam@example.com" not in state["active"]


def test_tick_ttl_does_not_expire_active_inside_window():
    """Active entry whose last_flagged_iso is exactly TTL days old (or
    fewer) is kept. The boundary is "older than" — 90 days is fine, 91
    days expires."""
    state = als._empty_state()
    state["active"]["spam@example.com"] = {
        "added_iso": "2026-01-25",
        "last_flagged_iso": "2026-01-25",  # 90 days before _TODAY
        "reason": "borderline",
    }
    result = als.tick_ttl(state, _TODAY)
    assert result["expired"] == []
    assert "spam@example.com" in state["active"]


def test_tick_ttl_ages_out_pending_after_pending_ttl_days():
    """Pending entry whose last_flagged_iso is older than 30 days is
    pruned and surfaced in ``aged_out``."""
    state = als._empty_state()
    state["pending"]["spam@example.com"] = {
        "first_flagged_iso": "2026-03-01",
        "last_flagged_iso": "2026-03-01",  # 55 days before _TODAY
        "flagged_message_ids": ["msgid1"],
        "reason_samples": ["r1"],
    }
    result = als.tick_ttl(state, _TODAY)
    assert result["aged_out"] == ["spam@example.com"]
    assert "spam@example.com" not in state["pending"]


def test_tick_ttl_does_not_age_out_pending_inside_window():
    state = als._empty_state()
    state["pending"]["spam@example.com"] = {
        "first_flagged_iso": "2026-03-26",
        "last_flagged_iso": "2026-03-26",  # 30 days before _TODAY
        "flagged_message_ids": ["msgid1"],
        "reason_samples": ["r1"],
    }
    result = als.tick_ttl(state, _TODAY)
    assert result["aged_out"] == []
    assert "spam@example.com" in state["pending"]


def test_tick_ttl_returns_both_lists_when_both_categories_age():
    """Mixed run: active expires AND pending ages out in the same
    tick. Both lists are returned in a single result dict so the
    audit log can record both actions."""
    state = als._empty_state()
    state["active"]["old-active@example.com"] = {
        "added_iso": "2025-12-01",
        "last_flagged_iso": "2025-12-01",
        "reason": "expired",
    }
    state["pending"]["old-pending@example.com"] = {
        "first_flagged_iso": "2026-01-01",
        "last_flagged_iso": "2026-01-01",
        "flagged_message_ids": ["msgid1"],
        "reason_samples": ["aged"],
    }
    result = als.tick_ttl(state, _TODAY)
    assert result["expired"] == ["old-active@example.com"]
    assert result["aged_out"] == ["old-pending@example.com"]


def test_tick_ttl_unparseable_iso_left_alone():
    """A corrupt or hand-edited entry with an unparseable
    last_flagged_iso must not crash tick_ttl; the entry is silently
    left alone. Defensive — the synthetic seed populates this field
    in normal operation."""
    state = als._empty_state()
    state["active"]["broken@example.com"] = {
        "added_iso": "not-a-date",
        "last_flagged_iso": "not-a-date",
        "reason": "broken",
    }
    state["pending"]["also-broken@example.com"] = {
        "first_flagged_iso": "still-not",
        "last_flagged_iso": "still-not",
        "flagged_message_ids": ["msgid"],
        "reason_samples": [],
    }
    result = als.tick_ttl(state, _TODAY)
    assert result == {"expired": [], "aged_out": []}
    assert "broken@example.com" in state["active"]
    assert "also-broken@example.com" in state["pending"]


def test_tick_ttl_custom_ttls_via_kwargs():
    """Test-friendliness: callers can override the TTLs per-call. The
    main loop in update_auto_blocklist exposes these as CLI flags so
    tests can compress the windows."""
    state = als._empty_state()
    state["active"]["recent@example.com"] = {
        "added_iso": "2026-04-20",
        "last_flagged_iso": "2026-04-20",  # 5 days before _TODAY
        "reason": "fresh",
    }
    # Default TTL=90 keeps it; override TTL=3 expires it.
    result = als.tick_ttl(state, _TODAY, active_ttl_days=3)
    assert result["expired"] == ["recent@example.com"]


# ─── seed_active_from_legacy ─────────────────────────────────────────────

def test_seed_active_from_legacy_seeds_missing_entries():
    """Pre-deploy entries in blocklist_auto.txt with no state-file
    counterpart get a synthetic state row with today's date. Reason is
    a placeholder string — the audit log captures the original
    add-time reason in its history if needed."""
    state = als._empty_state()
    seeded = als.seed_active_from_legacy(
        state,
        ["legacy1@example.com", "legacy2@example.com"],
        _TODAY,
    )
    assert seeded == 2
    for addr in ("legacy1@example.com", "legacy2@example.com"):
        entry = state["active"][addr]
        assert entry["added_iso"] == "2026-04-25"
        assert entry["last_flagged_iso"] == "2026-04-25"
        assert "legacy" in entry["reason"]


def test_seed_active_from_legacy_idempotent():
    """A second call with the same arguments seeds zero — existing
    entries are skipped. Lets the production cron call this on every
    run without churning the state file."""
    state = als._empty_state()
    als.seed_active_from_legacy(state, ["legacy@example.com"], _TODAY)
    second = als.seed_active_from_legacy(
        state, ["legacy@example.com"], _TODAY,
    )
    assert second == 0
    assert len(state["active"]) == 1


def test_seed_active_from_legacy_skips_addresses_already_in_active():
    """Addresses with existing state rows are NOT overwritten. Their
    real added_iso / last_flagged_iso / reason values must survive a
    seed pass that runs after they were already populated by a
    prior run."""
    state = als._empty_state()
    state["active"]["already-here@example.com"] = {
        "added_iso": "2026-02-01",
        "last_flagged_iso": "2026-04-15",
        "reason": "real reason",
    }
    seeded = als.seed_active_from_legacy(
        state,
        ["already-here@example.com", "new-legacy@example.com"],
        _TODAY,
    )
    assert seeded == 1
    # The pre-existing entry is untouched.
    assert state["active"]["already-here@example.com"] == {
        "added_iso": "2026-02-01",
        "last_flagged_iso": "2026-04-15",
        "reason": "real reason",
    }
    # The new legacy entry got the synthetic seed.
    assert state["active"]["new-legacy@example.com"]["added_iso"] == "2026-04-25"


def test_seed_active_from_legacy_lowercases_addresses():
    """Mixed-case input from the txt parser gets lowercased — keeps
    add_or_promote's lookup deterministic against the seeded state."""
    state = als._empty_state()
    seeded = als.seed_active_from_legacy(
        state, ["Legacy@Example.COM"], _TODAY,
    )
    assert seeded == 1
    assert "legacy@example.com" in state["active"]
    assert "Legacy@Example.COM" not in state["active"]
