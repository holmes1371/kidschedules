"""Pytest suite for agent.py.

Covers the validation layer that sits between the LLM's JSON response
and downstream Python — specifically `_filter_events_by_source_id`,
which enforces that each extracted event carries a `source_message_id`
that maps back to one of the emails in the current batch.

That mapping is what lets main.py look up the original sender domain
for the Ignore-sender feature. If the LLM omits the field or invents an
ID, the event is dropped with a warning (tolerant-parse posture — see
design/failure-notifications.md).
"""
from __future__ import annotations

import sys
from pathlib import Path

# agent.py lives at the repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import agent  # noqa: E402


def _event(name: str, sid: str | None) -> dict:
    """Minimal event dict for filter-path tests.

    Only the source_message_id key matters for these tests; name is a
    human-readable label so failure output is legible. If sid is None
    the key is omitted entirely (models that ignore the schema).
    """
    ev: dict = {"name": name, "date": "2026-05-01"}
    if sid is not None:
        ev["source_message_id"] = sid
    return ev


# ─── valid source_message_id ──────────────────────────────────────────────


def test_keeps_events_whose_source_id_is_in_batch():
    batch_ids = {"1111aaaa2222bbbb", "3333cccc4444dddd"}
    events = [
        _event("A", "1111aaaa2222bbbb"),
        _event("B", "3333cccc4444dddd"),
    ]
    kept = agent._filter_events_by_source_id(events, batch_ids)
    assert [e["name"] for e in kept] == ["A", "B"]


def test_preserves_event_order_and_extra_keys():
    batch_ids = {"1111aaaa2222bbbb"}
    events = [
        {
            "name": "E1",
            "date": "2026-05-01",
            "time": "6:30 PM",
            "source_message_id": "1111aaaa2222bbbb",
        }
    ]
    kept = agent._filter_events_by_source_id(events, batch_ids)
    assert kept == events


# ─── missing source_message_id ────────────────────────────────────────────


def test_drops_event_with_missing_source_id(capsys):
    batch_ids = {"1111aaaa2222bbbb"}
    events = [
        _event("keep", "1111aaaa2222bbbb"),
        _event("missing", None),
    ]
    kept = agent._filter_events_by_source_id(events, batch_ids)
    assert [e["name"] for e in kept] == ["keep"]
    out = capsys.readouterr().out
    assert "missing source_message_id" in out
    assert "dropped 1 event" in out


def test_drops_event_with_empty_source_id(capsys):
    batch_ids = {"1111aaaa2222bbbb"}
    events = [_event("empty", "")]
    kept = agent._filter_events_by_source_id(events, batch_ids)
    assert kept == []
    assert "missing source_message_id" in capsys.readouterr().out


def test_drops_event_with_non_string_source_id(capsys):
    batch_ids = {"1111aaaa2222bbbb"}
    events = [_event("wrong-type", 12345)]  # type: ignore[arg-type]
    kept = agent._filter_events_by_source_id(events, batch_ids)
    assert kept == []
    assert "missing source_message_id" in capsys.readouterr().out


# ─── hallucinated / unknown source_message_id ─────────────────────────────


def test_drops_event_whose_source_id_is_not_in_batch(capsys):
    batch_ids = {"1111aaaa2222bbbb"}
    events = [
        _event("keep", "1111aaaa2222bbbb"),
        _event("hallucinated", "9999ffff0000eeee"),
    ]
    kept = agent._filter_events_by_source_id(events, batch_ids)
    assert [e["name"] for e in kept] == ["keep"]
    out = capsys.readouterr().out
    assert "did not match any email in the batch" in out
    assert "dropped 1 event" in out


def test_warning_counts_are_separate_for_missing_and_unknown(capsys):
    batch_ids = {"1111aaaa2222bbbb"}
    events = [
        _event("missing", None),
        _event("hallucinated", "deadbeefdeadbeef"),
        _event("keep", "1111aaaa2222bbbb"),
    ]
    kept = agent._filter_events_by_source_id(events, batch_ids)
    assert [e["name"] for e in kept] == ["keep"]
    out = capsys.readouterr().out
    # Both warning branches fire independently so a batch can surface
    # a mix of schema violations and hallucinations in one pass.
    assert "missing source_message_id" in out
    assert "did not match any email in the batch" in out


# ─── empty / no-op cases ──────────────────────────────────────────────────


def test_empty_events_list_is_noop():
    assert agent._filter_events_by_source_id([], {"1111aaaa2222bbbb"}) == []


def test_no_warnings_when_nothing_is_dropped(capsys):
    batch_ids = {"1111aaaa2222bbbb"}
    events = [_event("ok", "1111aaaa2222bbbb")]
    agent._filter_events_by_source_id(events, batch_ids)
    assert capsys.readouterr().out == ""
