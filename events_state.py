"""Event cache keyed by Gmail message ID.

Persists two maps across pipeline runs so the Anthropic agent never
re-processes a message it has already seen:

    processed_messages: {<gmail_msg_id>: <processed_at_iso>}
    events:             {<12-char event_id>: {...event dict...}}

Main.py calls into this module between reading emails (step 2b) and
agent extraction (step 3): load the state, filter out already-seen
messages, run extraction on the remainder, merge the resulting events
back in, mark the new messages processed, save. See
design/incremental-extraction.md for the full design.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from typing import Any


CURRENT_SCHEMA_VERSION = 2

# Drop cache entries older than this many days. Default is 2x the 60-day
# Gmail search window so entries that can never come back into the
# search don't bloat the file.
DEFAULT_GC_DAYS = 120


# ── Event-ID hashing (MUST stay in sync with scripts/process_events.py) ──


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _event_id(name: str, date: str, child: str) -> str:
    """12-char sha1 of the canonical (name|date|child) key.

    MUST produce identical output to scripts/process_events.py::_event_id;
    the cache's correctness depends on both modules hashing identically.
    A parity test in tests/test_events_state.py enforces this.
    """
    key = "|".join([_norm(name), (date or "").strip(), _norm(child)])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


# ── State-file load / save ──────────────────────────────────────────────


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "last_updated_iso": "",
        "processed_messages": {},
        "events": {},
    }


def load_state(path: str) -> dict[str, Any]:
    """Read the state file. Return an empty state on missing/corrupt/wrong-version.

    Prints a warning to stdout on any recoverable issue so GitHub Actions
    surfaces it in the run log. Any failure mode falls back to empty state;
    the next run regenerates from live data.
    """
    if not os.path.exists(path):
        return _empty_state()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  WARNING: events_state.json unreadable ({e}); starting empty")
        return _empty_state()
    if not isinstance(data, dict):
        print("  WARNING: events_state.json not a JSON object; starting empty")
        return _empty_state()
    if data.get("schema_version") != CURRENT_SCHEMA_VERSION:
        print(
            f"  WARNING: events_state.json schema version mismatch "
            f"(expected {CURRENT_SCHEMA_VERSION}, "
            f"got {data.get('schema_version')!r}); starting empty"
        )
        return _empty_state()

    pm = data.get("processed_messages")
    if not isinstance(pm, dict):
        pm = {}
    ev = data.get("events")
    if not isinstance(ev, dict):
        ev = {}
    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "last_updated_iso": data.get("last_updated_iso") or "",
        "processed_messages": pm,
        "events": ev,
    }


def save_state(path: str, state: dict[str, Any], now_iso: str) -> None:
    """Atomically write state to disk via tempfile + os.replace."""
    state["schema_version"] = CURRENT_SCHEMA_VERSION
    state["last_updated_iso"] = now_iso
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False, sort_keys=True)
    os.replace(tmp_path, path)


# ── Cache operations ─────────────────────────────────────────────────────


def filter_unprocessed(
    emails: list[dict[str, Any]], state: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return the subset of `emails` whose messageId isn't in the cache."""
    seen = state.get("processed_messages", {})
    return [e for e in emails if e.get("messageId") not in seen]


def _completeness(ev: dict[str, Any]) -> int:
    """Mirror the completeness scorer from process_events.py::dedupe.

    The two callers treat "empty" slightly differently: process_events
    normalizes fields via classify() before scoring (so missing fields
    become the sentinel strings "Time TBD" / "Location TBD" / "unknown
    source"); this version scores raw-or-sentinel so pre- and post-
    classify events produce the same score. If either module's scorer
    drifts the merge winner changes and cached events can get silently
    overwritten by less-complete ones.
    """
    score = 0
    if (ev.get("time") or "") not in ("", "Time TBD"):
        score += 2
    if (ev.get("location") or "") not in ("", "Location TBD"):
        score += 2
    if (ev.get("child") or "").strip():
        score += 1
    if (ev.get("source") or "") not in ("", "unknown source"):
        score += 1
    return score


def stamp_event_ids(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add an 'id' field to each event dict. Mutates + returns the list."""
    for ev in events:
        ev["id"] = _event_id(
            ev.get("name", ""), ev.get("date", ""), ev.get("child", "")
        )
    return events


def merge_events(
    state: dict[str, Any],
    new_events: list[dict[str, Any]],
    now_iso: str,
) -> dict[str, Any]:
    """Merge new events into state['events'] keyed by event_id.

    Requires each new event to carry an 'id' (call stamp_event_ids first).
    On collision, keep whichever has higher completeness; ties keep the
    cached entry. `first_seen_iso` is set on insertion and preserved on
    replacement.
    """
    cached = state.get("events", {})
    for ev in new_events:
        eid = ev.get("id")
        if not eid:
            continue  # caller bug; skip defensively
        if eid not in cached:
            ev["first_seen_iso"] = now_iso
            cached[eid] = ev
            continue
        existing = cached[eid]
        if _completeness(ev) > _completeness(existing):
            ev["first_seen_iso"] = existing.get("first_seen_iso") or now_iso
            cached[eid] = ev
    state["events"] = cached
    return state


def mark_processed(
    state: dict[str, Any], message_ids: list[str], now_iso: str
) -> dict[str, Any]:
    """Stamp each message ID as processed at now_iso."""
    pm = state.get("processed_messages", {})
    for mid in message_ids:
        pm[mid] = now_iso
    state["processed_messages"] = pm
    return state


# ── Garbage collection ───────────────────────────────────────────────────


def _iso_to_date(iso_str: str) -> dt.date | None:
    if not iso_str:
        return None
    try:
        return dt.datetime.fromisoformat(iso_str).date()
    except ValueError:
        return None


def _parse_date(s: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def gc_state(
    state: dict[str, Any],
    today: dt.date,
    gc_days: int = DEFAULT_GC_DAYS,
) -> dict[str, int]:
    """Drop stale entries in place. Return {'messages_dropped', 'events_dropped'}.

    processed_messages: drop entries whose processed_at date is older than
        (today - gc_days). Unparseable timestamps also drop (defensive).
    events: drop if date is parseable and strictly before today. Undated or
        unparseable-dated events drop when first_seen_iso is older than
        (today - gc_days); otherwise they stay so undated entries linger
        long enough to be corrected manually.
    """
    cutoff = today - dt.timedelta(days=gc_days)

    pm = state.get("processed_messages", {})
    kept_pm: dict[str, str] = {}
    for mid, iso_str in pm.items():
        d = _iso_to_date(iso_str)
        if d is not None and d >= cutoff:
            kept_pm[mid] = iso_str
    pm_dropped = len(pm) - len(kept_pm)
    state["processed_messages"] = kept_pm

    ev_map = state.get("events", {})
    kept_ev: dict[str, dict[str, Any]] = {}
    for eid, ev in ev_map.items():
        date_str = (ev.get("date") or "").strip()
        event_date = _parse_date(date_str) if date_str else None
        if event_date is not None:
            if event_date >= today:
                kept_ev[eid] = ev
            # else: past-dated, drop
        else:
            fs = _iso_to_date(ev.get("first_seen_iso") or "")
            if fs is None or fs >= cutoff:
                kept_ev[eid] = ev
    ev_dropped = len(ev_map) - len(kept_ev)
    state["events"] = kept_ev

    return {"messages_dropped": pm_dropped, "events_dropped": ev_dropped}
