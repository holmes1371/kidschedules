"""Pytest suite for events_state.py.

Covers the cache module that sits between Gmail reads and agent
extraction: load/save round-trip, corruption fallbacks, message-ID
filtering, event merge semantics, and garbage collection.

Includes a parity test against process_events._event_id so the two
hashing functions can't silently diverge.

All tests pin today = 2026-04-14 so GC boundary dates resolve
deterministically.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

# events_state.py lives at the repo root (imported in-process by main.py),
# so add the repo root to sys.path alongside the scripts/ dir that
# conftest.py already installs.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import events_state as es  # noqa: E402
import process_events as pe  # noqa: E402


TODAY = dt.date(2026, 4, 14)
NOW_ISO = "2026-04-14T06:30:00-04:00"


# ─── _event_id parity with process_events ────────────────────────────────


@pytest.mark.parametrize(
    "name,date,child",
    [
        ("Spring Concert", "2026-04-23", "Isla"),
        ("  spring   CONCERT  ", "2026-04-23", "ISLA"),
        ("PTA Meeting", "", ""),
        ("Field Trip", "2026-05-15", "Rowan"),
        ("", "2026-06-01", ""),
    ],
)
def test_event_id_parity_with_process_events(name, date, child):
    """If these drift the cache merge silently corrupts."""
    assert es._event_id(name, date, child) == pe._event_id(name, date, child)


def test_event_id_is_12_chars_hex():
    eid = es._event_id("X", "2026-01-01", "Y")
    assert len(eid) == 12
    assert all(c in "0123456789abcdef" for c in eid)


# ─── load_state ───────────────────────────────────────────────────────────


def test_load_state_missing_file_returns_empty(tmp_path):
    state = es.load_state(str(tmp_path / "nonexistent.json"))
    assert state["schema_version"] == es.CURRENT_SCHEMA_VERSION
    assert state["processed_messages"] == {}
    assert state["events"] == {}


def test_load_state_valid_file_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    payload = {
        "schema_version": es.CURRENT_SCHEMA_VERSION,
        "last_updated_iso": NOW_ISO,
        "processed_messages": {"msg-1": NOW_ISO},
        "events": {"abc123": {"name": "X", "date": "2026-05-01", "child": "Isla"}},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    state = es.load_state(str(path))
    assert state["processed_messages"] == {"msg-1": NOW_ISO}
    assert state["events"]["abc123"]["name"] == "X"


def test_load_state_corrupt_json_falls_back(tmp_path, capsys):
    path = tmp_path / "state.json"
    path.write_text("{not valid json", encoding="utf-8")
    state = es.load_state(str(path))
    assert state["processed_messages"] == {}
    assert state["events"] == {}
    assert "WARNING" in capsys.readouterr().out


def test_load_state_non_dict_falls_back(tmp_path, capsys):
    path = tmp_path / "state.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    state = es.load_state(str(path))
    assert state["processed_messages"] == {}
    assert "WARNING" in capsys.readouterr().out


def test_load_state_schema_mismatch_falls_back(tmp_path, capsys):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"schema_version": 999, "events": {"x": {}}}),
        encoding="utf-8",
    )
    state = es.load_state(str(path))
    assert state["events"] == {}
    assert "WARNING" in capsys.readouterr().out


def test_load_state_tolerates_non_dict_inner_fields(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": es.CURRENT_SCHEMA_VERSION,
                "processed_messages": "oops",
                "events": ["oops"],
            }
        ),
        encoding="utf-8",
    )
    state = es.load_state(str(path))
    assert state["processed_messages"] == {}
    assert state["events"] == {}


# ─── save_state ───────────────────────────────────────────────────────────


def test_save_state_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    state = es._empty_state()
    state["processed_messages"]["m1"] = NOW_ISO
    state["events"]["eid1"] = {"name": "Concert", "date": "2026-05-01"}
    es.save_state(str(path), state, NOW_ISO)

    reloaded = es.load_state(str(path))
    assert reloaded["processed_messages"] == {"m1": NOW_ISO}
    assert reloaded["events"]["eid1"]["name"] == "Concert"
    assert reloaded["last_updated_iso"] == NOW_ISO


def test_save_state_stamps_schema_and_timestamp(tmp_path):
    path = tmp_path / "state.json"
    state = {"schema_version": 0, "processed_messages": {}, "events": {}}
    es.save_state(str(path), state, NOW_ISO)
    assert state["schema_version"] == es.CURRENT_SCHEMA_VERSION
    assert state["last_updated_iso"] == NOW_ISO


def test_save_state_uses_tempfile_then_replace(tmp_path):
    """The atomic-write pattern should leave no .tmp file after success."""
    path = tmp_path / "state.json"
    es.save_state(str(path), es._empty_state(), NOW_ISO)
    assert path.exists()
    assert not (tmp_path / "state.json.tmp").exists()


# ─── filter_unprocessed ──────────────────────────────────────────────────


def test_filter_unprocessed_all_new():
    state = es._empty_state()
    emails = [{"messageId": "a"}, {"messageId": "b"}]
    assert es.filter_unprocessed(emails, state) == emails


def test_filter_unprocessed_all_cached():
    state = es._empty_state()
    state["processed_messages"] = {"a": NOW_ISO, "b": NOW_ISO}
    emails = [{"messageId": "a"}, {"messageId": "b"}]
    assert es.filter_unprocessed(emails, state) == []


def test_filter_unprocessed_partial_hit():
    state = es._empty_state()
    state["processed_messages"] = {"a": NOW_ISO}
    emails = [{"messageId": "a"}, {"messageId": "b"}, {"messageId": "c"}]
    result = es.filter_unprocessed(emails, state)
    assert [e["messageId"] for e in result] == ["b", "c"]


# ─── stamp_event_ids ──────────────────────────────────────────────────────


def test_stamp_event_ids_adds_stable_ids():
    events = [
        {"name": "Concert", "date": "2026-05-01", "child": "Isla"},
        {"name": "Concert", "date": "2026-05-01", "child": "Isla"},
    ]
    stamped = es.stamp_event_ids(events)
    assert stamped[0]["id"] == stamped[1]["id"]
    assert len(stamped[0]["id"]) == 12


# ─── merge_events ─────────────────────────────────────────────────────────


def _mk_event(name="Concert", date="2026-05-01", child="Isla", **extras):
    ev = {"name": name, "date": date, "child": child}
    ev.update(extras)
    ev["id"] = es._event_id(name, date, child)
    return ev


def test_merge_events_inserts_new_and_stamps_first_seen():
    state = es._empty_state()
    ev = _mk_event(time="6:30 PM", location="LAES", source="LAES PTA")
    es.merge_events(state, [ev], NOW_ISO)
    cached = state["events"][ev["id"]]
    assert cached["first_seen_iso"] == NOW_ISO
    assert cached["time"] == "6:30 PM"


def test_merge_events_higher_completeness_wins():
    state = es._empty_state()
    sparse = _mk_event(time="", location="", source="")
    complete = _mk_event(time="6:30 PM", location="LAES", source="PTA email")
    es.merge_events(state, [sparse], NOW_ISO)
    later_iso = "2026-04-21T06:30:00-04:00"
    es.merge_events(state, [complete], later_iso)
    winner = state["events"][sparse["id"]]
    assert winner["time"] == "6:30 PM"
    # first_seen_iso preserved from original insertion
    assert winner["first_seen_iso"] == NOW_ISO


def test_merge_events_tie_keeps_cached():
    state = es._empty_state()
    first = _mk_event(time="6:30 PM", location="LAES", source="PTA")
    second = _mk_event(time="6:30 PM", location="LAES", source="PTA")
    second["marker"] = "second"
    es.merge_events(state, [first], NOW_ISO)
    es.merge_events(state, [second], NOW_ISO)
    assert "marker" not in state["events"][first["id"]]


def test_merge_events_less_complete_does_not_overwrite():
    state = es._empty_state()
    complete = _mk_event(time="6:30 PM", location="LAES", source="PTA")
    sparse = _mk_event(time="", location="", source="")
    sparse["marker"] = "sparse"
    es.merge_events(state, [complete], NOW_ISO)
    es.merge_events(state, [sparse], NOW_ISO)
    assert "marker" not in state["events"][complete["id"]]
    assert state["events"][complete["id"]]["time"] == "6:30 PM"


def test_merge_events_skips_events_without_id():
    state = es._empty_state()
    ev = {"name": "Concert", "date": "2026-05-01", "child": "Isla"}  # no 'id'
    es.merge_events(state, [ev], NOW_ISO)
    assert state["events"] == {}


def test_merge_events_treats_sentinel_strings_as_empty():
    """completeness should not reward "Time TBD" over a real time."""
    state = es._empty_state()
    sentinel = _mk_event(time="Time TBD", location="Location TBD", source="unknown source")
    real = _mk_event(time="6:30 PM", location="LAES", source="PTA")
    es.merge_events(state, [sentinel], NOW_ISO)
    es.merge_events(state, [real], NOW_ISO)
    assert state["events"][real["id"]]["time"] == "6:30 PM"


# ─── mark_processed ───────────────────────────────────────────────────────


def test_mark_processed_adds_entries():
    state = es._empty_state()
    es.mark_processed(state, ["m1", "m2"], NOW_ISO)
    assert state["processed_messages"] == {"m1": NOW_ISO, "m2": NOW_ISO}


def test_mark_processed_overwrites_existing_timestamp():
    state = es._empty_state()
    state["processed_messages"] = {"m1": "2026-01-01T00:00:00-04:00"}
    es.mark_processed(state, ["m1"], NOW_ISO)
    assert state["processed_messages"]["m1"] == NOW_ISO


# ─── gc_state ─────────────────────────────────────────────────────────────


def test_gc_drops_messages_past_window():
    state = es._empty_state()
    # 121 days old → drops; 119 days old → keeps.
    old_iso = (TODAY - dt.timedelta(days=121)).isoformat() + "T00:00:00-04:00"
    fresh_iso = (TODAY - dt.timedelta(days=119)).isoformat() + "T00:00:00-04:00"
    state["processed_messages"] = {"old": old_iso, "fresh": fresh_iso}
    counts = es.gc_state(state, TODAY)
    assert counts["messages_dropped"] == 1
    assert "old" not in state["processed_messages"]
    assert "fresh" in state["processed_messages"]


def test_gc_drops_messages_with_unparseable_timestamp():
    state = es._empty_state()
    state["processed_messages"] = {"bad": "not-a-date"}
    counts = es.gc_state(state, TODAY)
    assert counts["messages_dropped"] == 1
    assert state["processed_messages"] == {}


def test_gc_drops_past_dated_events():
    state = es._empty_state()
    past = _mk_event(date="2026-04-13")  # yesterday
    today_ev = _mk_event(name="Today", date="2026-04-14")
    future = _mk_event(name="Future", date="2026-05-01")
    for ev in (past, today_ev, future):
        ev["first_seen_iso"] = NOW_ISO
        state["events"][ev["id"]] = ev
    counts = es.gc_state(state, TODAY)
    assert counts["events_dropped"] == 1
    ids_left = set(state["events"].keys())
    assert past["id"] not in ids_left
    assert today_ev["id"] in ids_left
    assert future["id"] in ids_left


def test_gc_drops_stale_undated_events():
    state = es._empty_state()
    stale_iso = (TODAY - dt.timedelta(days=121)).isoformat() + "T00:00:00-04:00"
    fresh_iso = (TODAY - dt.timedelta(days=30)).isoformat() + "T00:00:00-04:00"
    stale = _mk_event(name="Stale", date="")
    stale["first_seen_iso"] = stale_iso
    fresh = _mk_event(name="Fresh", date="")
    fresh["first_seen_iso"] = fresh_iso
    state["events"] = {stale["id"]: stale, fresh["id"]: fresh}
    es.gc_state(state, TODAY)
    assert stale["id"] not in state["events"]
    assert fresh["id"] in state["events"]


def test_gc_keeps_undated_event_missing_first_seen():
    """Defensive: undated event with no first_seen should linger, not drop."""
    state = es._empty_state()
    ev = _mk_event(name="Orphan", date="")
    # no first_seen_iso
    state["events"] = {ev["id"]: ev}
    es.gc_state(state, TODAY)
    assert ev["id"] in state["events"]


def test_gc_returns_counts_dict_shape():
    state = es._empty_state()
    counts = es.gc_state(state, TODAY)
    assert set(counts.keys()) == {"messages_dropped", "events_dropped"}
    assert counts == {"messages_dropped": 0, "events_dropped": 0}
