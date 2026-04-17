"""Pytest suite for newsletter_stats.py.

Covers the per-sender learned-stats module that powers newsletter
classification, per-run outlier alerts, and newsletter-isolated
batching. See design/newsletter-robustness.md for the full design.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import newsletter_stats as ns  # noqa: E402


NOW_ISO = "2026-04-17T10:15:00-04:00"
LATER_ISO = "2026-04-24T10:15:00-04:00"


# ─── load_stats ───────────────────────────────────────────────────────────


def test_load_stats_missing_file_returns_empty(tmp_path):
    stats = ns.load_stats(str(tmp_path / "nonexistent.json"))
    assert stats["schema_version"] == ns.CURRENT_SCHEMA_VERSION
    assert stats["senders"] == {}


def test_load_stats_empty_senders_dict(tmp_path):
    path = tmp_path / "stats.json"
    payload = {
        "schema_version": ns.CURRENT_SCHEMA_VERSION,
        "last_updated_iso": NOW_ISO,
        "senders": {},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    stats = ns.load_stats(str(path))
    assert stats["senders"] == {}


def test_load_stats_roundtrip(tmp_path):
    path = tmp_path / "stats.json"
    payload = {
        "schema_version": ns.CURRENT_SCHEMA_VERSION,
        "last_updated_iso": NOW_ISO,
        "senders": {
            "sunbeam@laespta.org": {
                "messages_seen": 4,
                "total_events": 47,
                "per_message_counts": [12, 11, 13, 11],
                "first_seen_iso": NOW_ISO,
                "last_seen_iso": NOW_ISO,
                "is_newsletter": True,
            }
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    stats = ns.load_stats(str(path))
    assert stats["senders"]["sunbeam@laespta.org"]["messages_seen"] == 4
    assert stats["senders"]["sunbeam@laespta.org"]["is_newsletter"] is True


def test_load_stats_malformed_json_returns_empty(tmp_path, capsys):
    path = tmp_path / "stats.json"
    path.write_text("{not valid json", encoding="utf-8")
    stats = ns.load_stats(str(path))
    assert stats["senders"] == {}
    out = capsys.readouterr().out
    assert "unreadable" in out.lower()


def test_load_stats_not_a_dict_returns_empty(tmp_path, capsys):
    path = tmp_path / "stats.json"
    path.write_text(json.dumps(["wrong", "shape"]), encoding="utf-8")
    stats = ns.load_stats(str(path))
    assert stats["senders"] == {}
    out = capsys.readouterr().out
    assert "not a json object" in out.lower()


def test_load_stats_wrong_schema_version_returns_empty(tmp_path, capsys):
    path = tmp_path / "stats.json"
    payload = {
        "schema_version": 99,
        "last_updated_iso": NOW_ISO,
        "senders": {"a@b.com": {"messages_seen": 5}},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    stats = ns.load_stats(str(path))
    assert stats["senders"] == {}
    out = capsys.readouterr().out
    assert "schema version mismatch" in out.lower()


def test_load_stats_senders_not_a_dict_coerces_empty(tmp_path):
    path = tmp_path / "stats.json"
    payload = {
        "schema_version": ns.CURRENT_SCHEMA_VERSION,
        "last_updated_iso": NOW_ISO,
        "senders": ["wrong"],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    stats = ns.load_stats(str(path))
    assert stats["senders"] == {}


# ─── save_stats ───────────────────────────────────────────────────────────


def test_save_stats_roundtrip(tmp_path):
    path = str(tmp_path / "stats.json")
    stats = {
        "schema_version": ns.CURRENT_SCHEMA_VERSION,
        "last_updated_iso": "",
        "senders": {
            "a@b.com": {
                "messages_seen": 2,
                "total_events": 7,
                "per_message_counts": [3, 4],
                "first_seen_iso": NOW_ISO,
                "last_seen_iso": NOW_ISO,
                "is_newsletter": False,
            }
        },
    }
    ns.save_stats(path, stats, NOW_ISO)
    loaded = ns.load_stats(path)
    assert loaded["senders"]["a@b.com"]["per_message_counts"] == [3, 4]
    assert loaded["last_updated_iso"] == NOW_ISO


def test_save_stats_atomic_tempfile_then_rename(tmp_path):
    path = str(tmp_path / "stats.json")
    stats = ns._empty_stats()
    ns.save_stats(path, stats, NOW_ISO)
    # The tempfile name is path + ".tmp"; it should not remain on disk
    # after a successful save.
    assert not (tmp_path / "stats.json.tmp").exists()
    assert (tmp_path / "stats.json").exists()


def test_save_stats_stamps_schema_and_timestamp(tmp_path):
    path = str(tmp_path / "stats.json")
    stats = {"senders": {}}  # no version, no timestamp
    ns.save_stats(path, stats, NOW_ISO)
    loaded = ns.load_stats(path)
    assert loaded["schema_version"] == ns.CURRENT_SCHEMA_VERSION
    assert loaded["last_updated_iso"] == NOW_ISO


# ─── update_sender_counts ────────────────────────────────────────────────


def test_update_new_sender_seeds_entry():
    stats = ns._empty_stats()
    ns.update_sender_counts(
        stats,
        [("a@b.com", "msg-1", 5)],
        NOW_ISO,
    )
    entry = stats["senders"]["a@b.com"]
    assert entry["messages_seen"] == 1
    assert entry["total_events"] == 5
    assert entry["per_message_counts"] == [5]
    assert entry["first_seen_iso"] == NOW_ISO
    assert entry["last_seen_iso"] == NOW_ISO
    assert entry["is_newsletter"] is False


def test_update_existing_sender_appends_and_preserves_first_seen():
    stats = ns._empty_stats()
    ns.update_sender_counts(stats, [("a@b.com", "m-1", 5)], NOW_ISO)
    ns.update_sender_counts(stats, [("a@b.com", "m-2", 8)], LATER_ISO)
    entry = stats["senders"]["a@b.com"]
    assert entry["messages_seen"] == 2
    assert entry["total_events"] == 13
    assert entry["per_message_counts"] == [5, 8]
    assert entry["first_seen_iso"] == NOW_ISO  # preserved
    assert entry["last_seen_iso"] == LATER_ISO  # advances


def test_update_rolls_window_at_11th_entry():
    stats = ns._empty_stats()
    per_message_counts = [
        ("a@b.com", f"m-{i}", i) for i in range(1, 12)  # 11 messages
    ]
    ns.update_sender_counts(stats, per_message_counts, NOW_ISO)
    entry = stats["senders"]["a@b.com"]
    # Window keeps most recent 10; first entry (value=1) falls off.
    assert entry["per_message_counts"] == [2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    # Lifetime counters do not reset when the window rolls.
    assert entry["messages_seen"] == 11
    assert entry["total_events"] == sum(range(1, 12))


def test_update_zero_count_still_recorded():
    """A newsletter sender who produced zero events this run must
    contribute a 0 to per_message_counts so the rolling median stays
    honest across quiet weeks."""
    stats = ns._empty_stats()
    ns.update_sender_counts(
        stats,
        [("a@b.com", "msg-quiet", 0)],
        NOW_ISO,
    )
    assert stats["senders"]["a@b.com"]["per_message_counts"] == [0]
    assert stats["senders"]["a@b.com"]["messages_seen"] == 1


def test_update_empty_sender_key_skipped():
    """Defensive: a message whose From header was unparseable upstream
    arrives with sender_key == '' and must not create a bogus entry."""
    stats = ns._empty_stats()
    ns.update_sender_counts(stats, [("", "msg-1", 5)], NOW_ISO)
    assert stats["senders"] == {}


def test_update_multiple_senders_same_run():
    stats = ns._empty_stats()
    ns.update_sender_counts(
        stats,
        [
            ("a@b.com", "m-1", 3),
            ("c@d.com", "m-2", 7),
            ("a@b.com", "m-3", 9),
        ],
        NOW_ISO,
    )
    assert stats["senders"]["a@b.com"]["per_message_counts"] == [3, 9]
    assert stats["senders"]["c@d.com"]["per_message_counts"] == [7]


# ─── classify_senders ────────────────────────────────────────────────────


def test_classify_promotes_when_both_thresholds_met():
    stats = ns._empty_stats()
    stats["senders"]["a@b.com"] = {
        "messages_seen": 3,
        "total_events": 18,
        "per_message_counts": [5, 7, 6],
        "first_seen_iso": NOW_ISO,
        "last_seen_iso": NOW_ISO,
        "is_newsletter": False,
    }
    ns.classify_senders(stats)
    assert stats["senders"]["a@b.com"]["is_newsletter"] is True


def test_classify_no_promote_below_message_threshold():
    stats = ns._empty_stats()
    stats["senders"]["a@b.com"] = {
        "messages_seen": 2,  # below PROMOTION_MIN_MESSAGES (3)
        "total_events": 24,
        "per_message_counts": [12, 12],
        "first_seen_iso": NOW_ISO,
        "last_seen_iso": NOW_ISO,
        "is_newsletter": False,
    }
    ns.classify_senders(stats)
    assert stats["senders"]["a@b.com"]["is_newsletter"] is False


def test_classify_no_promote_below_median_threshold():
    stats = ns._empty_stats()
    stats["senders"]["a@b.com"] = {
        "messages_seen": 5,
        "total_events": 20,
        "per_message_counts": [4, 4, 4, 4, 4],  # median 4, below 5
        "first_seen_iso": NOW_ISO,
        "last_seen_iso": NOW_ISO,
        "is_newsletter": False,
    }
    ns.classify_senders(stats)
    assert stats["senders"]["a@b.com"]["is_newsletter"] is False


def test_classify_is_sticky_once_promoted():
    """A promoted sender stays promoted even if counts drop later.

    Prevents flappy batching when a newsletter has a quiet issue."""
    stats = ns._empty_stats()
    stats["senders"]["a@b.com"] = {
        "messages_seen": 5,
        "total_events": 5,
        "per_message_counts": [1, 1, 1, 1, 1],  # median 1, below 5
        "first_seen_iso": NOW_ISO,
        "last_seen_iso": NOW_ISO,
        "is_newsletter": True,  # already promoted
    }
    ns.classify_senders(stats)
    assert stats["senders"]["a@b.com"]["is_newsletter"] is True


def test_classify_empty_window_does_not_promote():
    """Defensive: a sender with messages_seen counter but no window
    (manual-edit corruption) must not promote."""
    stats = ns._empty_stats()
    stats["senders"]["a@b.com"] = {
        "messages_seen": 10,
        "total_events": 100,
        "per_message_counts": [],
        "first_seen_iso": NOW_ISO,
        "last_seen_iso": NOW_ISO,
        "is_newsletter": False,
    }
    ns.classify_senders(stats)
    assert stats["senders"]["a@b.com"]["is_newsletter"] is False


# ─── newsletter_senders ──────────────────────────────────────────────────


def test_newsletter_senders_returns_promoted_set():
    stats = ns._empty_stats()
    stats["senders"]["n1@x.com"] = {"is_newsletter": True}
    stats["senders"]["n2@x.com"] = {"is_newsletter": True}
    stats["senders"]["r@x.com"] = {"is_newsletter": False}
    result = ns.newsletter_senders(stats)
    assert result == {"n1@x.com", "n2@x.com"}


def test_newsletter_senders_empty_when_none_promoted():
    stats = ns._empty_stats()
    stats["senders"]["r@x.com"] = {"is_newsletter": False}
    assert ns.newsletter_senders(stats) == set()


# ─── outlier_alerts ──────────────────────────────────────────────────────


def _make_newsletter_entry(counts: list[int]) -> dict:
    return {
        "messages_seen": len(counts),
        "total_events": sum(counts),
        "per_message_counts": list(counts),
        "first_seen_iso": NOW_ISO,
        "last_seen_iso": NOW_ISO,
        "is_newsletter": True,
    }


def test_outlier_below_half_median_flags():
    stats = ns._empty_stats()
    stats["senders"]["n@x.com"] = _make_newsletter_entry(
        [12, 11, 13, 10, 12]  # median 12
    )
    alerts = ns.outlier_alerts(
        stats, [("n@x.com", "msg-short", 3)]
    )
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["sender"] == "n@x.com"
    assert alert["message_id"] == "msg-short"
    assert alert["prior_median"] == 12
    assert alert["current_count"] == 3
    assert alert["threshold"] == 6  # round(12 * 0.5) = 6


def test_outlier_at_floor_flags():
    """A newsletter with a very low prior median still flags at
    current=1 because the floor is 2."""
    stats = ns._empty_stats()
    stats["senders"]["n@x.com"] = _make_newsletter_entry(
        [2, 2, 2]  # median 2; half = 1 → threshold = max(2, 1) = 2
    )
    alerts = ns.outlier_alerts(
        stats, [("n@x.com", "msg-1", 1)]
    )
    assert len(alerts) == 1
    assert alerts[0]["threshold"] == 2
    assert alerts[0]["current_count"] == 1


def test_outlier_at_threshold_no_flag():
    """current == threshold must NOT flag (strict < check)."""
    stats = ns._empty_stats()
    stats["senders"]["n@x.com"] = _make_newsletter_entry(
        [12, 11, 13, 10, 12]  # threshold = 6
    )
    alerts = ns.outlier_alerts(
        stats, [("n@x.com", "msg-1", 6)]
    )
    assert alerts == []


def test_outlier_above_threshold_no_flag():
    stats = ns._empty_stats()
    stats["senders"]["n@x.com"] = _make_newsletter_entry(
        [12, 11, 13, 10, 12]
    )
    alerts = ns.outlier_alerts(
        stats, [("n@x.com", "msg-1", 9)]
    )
    assert alerts == []


def test_outlier_non_newsletter_never_flags():
    """A non-promoted sender produces no alerts regardless of delta."""
    stats = ns._empty_stats()
    stats["senders"]["r@x.com"] = {
        "messages_seen": 5,
        "total_events": 60,
        "per_message_counts": [12, 12, 12, 12, 12],
        "first_seen_iso": NOW_ISO,
        "last_seen_iso": NOW_ISO,
        "is_newsletter": False,
    }
    alerts = ns.outlier_alerts(
        stats, [("r@x.com", "msg-1", 0)]
    )
    assert alerts == []


def test_outlier_unknown_sender_no_flag():
    """A sender that has never been seen before can't possibly be a
    newsletter — no alert even at count=0."""
    stats = ns._empty_stats()
    alerts = ns.outlier_alerts(
        stats, [("new@x.com", "msg-1", 0)]
    )
    assert alerts == []


def test_outlier_multiple_messages_one_sender():
    """One newsletter sender sending two messages this run: each
    message is evaluated against the same prior median."""
    stats = ns._empty_stats()
    stats["senders"]["n@x.com"] = _make_newsletter_entry(
        [12, 11, 13, 10, 12]  # threshold = 6
    )
    alerts = ns.outlier_alerts(
        stats,
        [
            ("n@x.com", "msg-short", 2),
            ("n@x.com", "msg-full", 11),
        ],
    )
    assert len(alerts) == 1
    assert alerts[0]["message_id"] == "msg-short"


def test_outlier_uses_prior_stats_only():
    """Caller must invoke outlier_alerts BEFORE update_sender_counts
    so the rolling median reflects prior runs. This test pins the
    contract: if the caller violates it, behavior changes (not a
    correctness guarantee of the function itself, but a documented
    ordering constraint)."""
    stats = ns._empty_stats()
    stats["senders"]["n@x.com"] = _make_newsletter_entry(
        [12, 11, 13]  # prior median 12; threshold 6
    )
    # Correct order: alerts BEFORE update.
    alerts_before = ns.outlier_alerts(
        stats, [("n@x.com", "msg-1", 2)]
    )
    assert len(alerts_before) == 1

    # If the caller folded this run's count in FIRST, the median would
    # shift down (median of [12,11,13,2] = 11.5, threshold round(5.75)=6),
    # so this particular case still flags. The ordering matters in
    # borderline cases — this assertion documents the contract.
    ns.update_sender_counts(stats, [("n@x.com", "msg-1", 2)], NOW_ISO)
    alerts_after = ns.outlier_alerts(
        stats, [("n@x.com", "msg-1", 2)]
    )
    # Still flagged at this specific input because threshold is still 6;
    # what we're really asserting is that outlier_alerts consults the
    # passed stats and nothing else (no hidden prior-state tracking).
    assert len(alerts_after) == 1
