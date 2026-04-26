#!/usr/bin/env python3
"""Filter, dedupe, sort, group, and render a kids' event list.

Input: a JSON file of candidate events produced by the agent after reading
Gmail messages. Each candidate is a dict with keys:
    name, date, time, location, category, child, source

Output: rendered Gmail-draft body to --body-out (or stdout), and structured
metadata (counts, subject line) as JSON to --meta-out (or stderr).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html as _html
import json
import os
import re
import sys
from collections import OrderedDict
from typing import Any
from zoneinfo import ZoneInfo

from protected_senders import is_protected, load_protected_senders
import roster_match


LOCAL_TZ = ZoneInfo("America/New_York")

# Roster-derived attribution table, built once at import time. See
# design/kid-attribution-derivation.md (#19). Module-level so render_html
# callers don't need to thread it through; tests that need a different
# roster can monkeypatch `_DISTINCTIVE_SIGNALS` and `_SLUG_TO_NAME`.
_ROSTER = roster_match.load_roster()
_DISTINCTIVE_SIGNALS = roster_match.build_distinctive_signals(_ROSTER)
_SLUG_TO_NAME = {kid.lower(): kid for kid in _ROSTER}

# Tokens of fewer than 3 chars are dropped from dedupe signatures to avoid
# matching on filler like "no", "a", "of", "to".
_NAME_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


VALID_CATEGORIES = {
    "School Activity",
    "Appointment",
    "Academic Due Date",
    "Sports & Extracurriculars",
}


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _event_id(name: str, date: str, child: str) -> str:
    """Stable 12-char hash identifying an event across runs.

    Re-extractions of the same underlying event (same normalized name,
    same date, same normalized child) produce the same ID, so an
    "ignore" decision can survive a future pipeline run.
    """
    key = "|".join([_norm(name), (date or "").strip(), _norm(child)])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _parse_date(s: str) -> dt.date | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


# ─── .ics export helpers ──────────────────────────────────────────────────


VTIMEZONE_NY = "\n".join([
    "BEGIN:VTIMEZONE",
    "TZID:America/New_York",
    "BEGIN:DAYLIGHT",
    "TZOFFSETFROM:-0500",
    "TZOFFSETTO:-0400",
    "TZNAME:EDT",
    "DTSTART:19700308T020000",
    "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU",
    "END:DAYLIGHT",
    "BEGIN:STANDARD",
    "TZOFFSETFROM:-0400",
    "TZOFFSETTO:-0500",
    "TZNAME:EST",
    "DTSTART:19701101T020000",
    "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU",
    "END:STANDARD",
    "END:VTIMEZONE",
])


_CLOCK_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([AaPp][Mm])")
_SLUG_SPLIT = re.compile(r"[^a-z0-9]+")

# Range separators: ASCII hyphen, en dash, em dash, or the word "to".
_RANGE_RE = re.compile(
    r"^(\d{1,2})(?::(\d{2}))?\s*([AaPp][Mm])?"
    r"\s*(?:-|\u2013|\u2014|to)\s*"
    r"(\d{1,2})(?::(\d{2}))?\s*([AaPp][Mm])$",
    re.IGNORECASE,
)


def _parse_clock_time(s: str) -> dt.time | None:
    """Parse a clean clock time like '7:00 PM' or '8am' to a dt.time.

    Uses fullmatch on the stripped input, so anything with extra text —
    '1:30 PM dismissal', 'Time TBD', 'All day (deadline)' — returns None.
    Callers treat None as "fall back to all-day", which keeps the .ics
    export button available on every dated card without inventing a time.
    """
    m = _CLOCK_RE.fullmatch((s or "").strip())
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = m.group(3).upper()
    if not (1 <= hour <= 12) or not (0 <= minute <= 59):
        return None
    if ampm == "AM":
        hour = 0 if hour == 12 else hour
    else:
        hour = 12 if hour == 12 else hour + 12
    return dt.time(hour, minute)


def _parse_time_range(s: str) -> tuple[dt.time, dt.time] | None:
    """Parse a clock-time range like '2 PM - 5 PM' or '2:00-5:00 PM'.

    Returns (start, end) when the whole string is a valid range. Accepts
    hyphen, en/em dash, or the word 'to' as separator. The end meridian
    is required; the start meridian is optional and defaults to the end
    meridian, flipped when that would make start > end (e.g. '11-1 PM'
    resolves to 11 AM → 1 PM). Returns None for non-range inputs — the
    caller falls back to single-time parsing.
    """
    m = _RANGE_RE.fullmatch((s or "").strip())
    if not m:
        return None
    sh = int(m.group(1))
    sm = int(m.group(2) or 0)
    sam = (m.group(3) or "").upper() or None
    eh = int(m.group(4))
    em = int(m.group(5) or 0)
    eam = m.group(6).upper()
    if not (1 <= sh <= 12 and 1 <= eh <= 12):
        return None
    if not (0 <= sm <= 59 and 0 <= em <= 59):
        return None

    def _to24(h: int, ampm: str) -> int:
        if ampm == "AM":
            return 0 if h == 12 else h
        return 12 if h == 12 else h + 12

    end_24 = _to24(eh, eam)
    if sam is None:
        start_shared = _to24(sh, eam)
        if start_shared * 60 + sm <= end_24 * 60 + em:
            start_24 = start_shared
        else:
            start_24 = _to24(sh, "AM" if eam == "PM" else "PM")
    else:
        start_24 = _to24(sh, sam)

    return dt.time(start_24, sm), dt.time(end_24, em)


def _format_ics_duration(start: dt.time, end: dt.time) -> str:
    """Format a start→end clock-time gap as an RFC 5545 DURATION value.

    Falls back to PT1H when end <= start (invalid range) — the all-day
    path is preferable in practice, but callers already gate on
    `_parse_time_range` returning a valid range so this is belt-and-
    suspenders.
    """
    mins = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    if mins <= 0:
        return "PT1H"
    h, m = divmod(mins, 60)
    if h and m:
        return f"PT{h}H{m}M"
    if h:
        return f"PT{h}H"
    return f"PT{m}M"


def _ics_slug(name: str) -> str:
    """Slug an event name for use in an .ics filename."""
    parts = [p for p in _SLUG_SPLIT.split((name or "").lower()) if p]
    return "-".join(parts) or "event"


def _ics_escape(s: str) -> str:
    """Escape text for an RFC 5545 property value."""
    return (
        (s or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _webcal_base(pages_url: str) -> str:
    """Return the webcal-ready 'host/path/' for pages_url, or ''.

    'https://holmes1371.github.io/kidschedules/' → 'holmes1371.github.io/kidschedules/'.
    Empty or malformed pages_url returns ''; callers must gate on that to
    decide whether to render the Add-to-calendar button at all.
    """
    s = (pages_url or "").strip()
    if not s:
        return ""
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    if not s.endswith("/"):
        s += "/"
    return s


def write_ics_files(events: list[dict[str, Any]], out_dir: str,
                    now: dt.datetime | None = None) -> int:
    """Wipe out_dir of .ics files and write one per dated event.

    Filename is '{event_id}.ics' (stable 12-char sha1). Events that fail
    build_ics (undated, bad date) are skipped. Returns count written.
    """
    os.makedirs(out_dir, exist_ok=True)
    for name in os.listdir(out_dir):
        if name.endswith(".ics"):
            try:
                os.unlink(os.path.join(out_dir, name))
            except OSError:
                pass
    count = 0
    for ev in events:
        try:
            body = build_ics(ev, now=now)
        except ValueError:
            continue
        eid = ev.get("id") or _event_id(
            ev.get("name", ""), ev.get("date", ""), ev.get("child", "")
        )
        path = os.path.join(out_dir, f"{eid}.ics")
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        count += 1
    return count


def build_ics(ev: dict[str, Any], now: dt.datetime | None = None) -> str:
    """Emit a VCALENDAR string for a single dated event.

    Timed events (clean `_parse_clock_time` match) use
    `DTSTART;TZID=America/New_York` with `DURATION:PT1H` and include a
    single hand-coded `VTIMEZONE` block. Everything else falls back to an
    all-day event with `VALUE=DATE` DTSTART/DTEND (RFC 5545 DTEND is
    exclusive, hence next-day).

    UID is keyed on the stable 12-char event ID so re-imports overwrite
    rather than duplicate. `now` is injectable so snapshot tests pin
    DTSTAMP; production uses wall-clock UTC.
    """
    if now is None:
        now = dt.datetime.now(ZoneInfo("UTC"))
    dtstamp = now.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")

    eid = ev.get("id") or _event_id(
        ev.get("name", ""), ev.get("date", ""), ev.get("child", "")
    )
    uid = f"{eid}@kidschedules.holmes1371.github.io"

    summary = _ics_escape((ev.get("name") or "").strip())
    loc_raw = (ev.get("location") or "").strip()
    loc = "" if loc_raw in ("", "Location TBD") else _ics_escape(loc_raw)

    d = _parse_date(ev.get("date") or "")
    if d is None:
        raise ValueError("build_ics requires an event with a parseable date")

    # Try range first (emits real DURATION); fall back to single-time (PT1H
    # default); fall back to all-day when neither matches.
    rng = _parse_time_range(ev.get("time") or "")
    t = None if rng is not None else _parse_clock_time(ev.get("time") or "")
    timed = rng is not None or t is not None

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//kids-schedule//ics-export//EN",
        "CALSCALE:GREGORIAN",
    ]
    if timed:
        lines.append(VTIMEZONE_NY)
    lines.append("BEGIN:VEVENT")
    lines.append(f"UID:{uid}")
    lines.append(f"DTSTAMP:{dtstamp}")
    lines.append(f"SUMMARY:{summary}")
    if loc:
        lines.append(f"LOCATION:{loc}")
    if rng is not None:
        start_t, end_t = rng
        start = f"{d.strftime('%Y%m%d')}T{start_t.strftime('%H%M%S')}"
        lines.append(f"DTSTART;TZID=America/New_York:{start}")
        lines.append(f"DURATION:{_format_ics_duration(start_t, end_t)}")
    elif t is not None:
        start = f"{d.strftime('%Y%m%d')}T{t.strftime('%H%M%S')}"
        lines.append(f"DTSTART;TZID=America/New_York:{start}")
        lines.append("DURATION:PT1H")
    else:
        next_d = d + dt.timedelta(days=1)
        lines.append(f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}")
        lines.append(f"DTEND;VALUE=DATE:{next_d.strftime('%Y%m%d')}")
    lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\n".join(lines) + "\n"


def load_candidates(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "events" in data:
        data = data["events"]
    if not isinstance(data, list):
        raise SystemExit("candidates file must be a JSON list or {events: [...]}")
    return data


def classify(events: list[dict[str, Any]], cutoff: dt.date,
             horizon: dt.date | None = None,
             ignored_ids: frozenset[str] = frozenset(),
             ) -> tuple[list[dict[str, Any]], list[dict[str, Any]],
                        list[dict[str, Any]], list[dict[str, Any]],
                        list[dict[str, Any]], list[str]]:
    """Return (display, undated, dropped_past, banked_far_future,
               dropped_ignored, warnings).

    Args:
        cutoff: events before this date are "past" and dropped.
        horizon: if set, events after this date go to "banked" instead of
                 "display". Use for the 60-day display window.
        ignored_ids: event IDs to drop entirely (user clicked Ignore on them).
    """
    display: list[dict[str, Any]] = []
    undated: list[dict[str, Any]] = []
    past: list[dict[str, Any]] = []
    banked: list[dict[str, Any]] = []
    ignored: list[dict[str, Any]] = []
    warnings: list[str] = []

    for i, ev in enumerate(events):
        name = (ev.get("name") or "").strip()
        if not name:
            warnings.append(f"event[{i}] missing name; skipped")
            continue
        cat = (ev.get("category") or "").strip()
        if cat and cat not in VALID_CATEGORIES:
            warnings.append(f"event[{i}] '{name}' has unknown category {cat!r}")
        norm = {
            "name": name,
            "date": (ev.get("date") or "").strip(),
            "time": (ev.get("time") or "").strip(),
            "location": (ev.get("location") or "").strip(),
            "category": cat or "Uncategorized",
            "child": (ev.get("child") or "").strip(),
            "source": (ev.get("source") or "").strip() or "unknown source",
            "sender_domain": (ev.get("sender_domain") or "").strip(),
            "sender_block_key": (ev.get("sender_block_key") or "").strip(),
        }
        norm["id"] = _event_id(norm["name"], norm["date"], norm["child"])
        # Render-but-hide model: ignored events still flow into their date
        # bucket (with is_ignored=True) so the page can offer an Unignore
        # affordance. The `ignored` return list is retained as a count
        # surrogate for meta logging — it duplicates the events that also
        # appear in display/undated/past/banked.
        norm["is_ignored"] = norm["id"] in ignored_ids
        if norm["is_ignored"]:
            ignored.append(norm)
        d = _parse_date(norm["date"])
        if d is None:
            undated.append(norm)
        elif d < cutoff:
            past.append(norm)
        elif horizon and d > horizon:
            norm["_date_obj"] = d
            banked.append(norm)
        else:
            norm["_date_obj"] = d
            display.append(norm)
    return display, undated, past, banked, ignored, warnings


def _load_ignored_ids(path: str | None) -> frozenset[str]:
    """Read the committed ignored_events.json and return a set of IDs.

    Tolerates missing/empty/malformed files (all → empty set).
    """
    if not path or not os.path.exists(path):
        return frozenset()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return frozenset()
    if not isinstance(data, list):
        return frozenset()
    return frozenset(
        e["id"] for e in data
        if isinstance(e, dict) and isinstance(e.get("id"), str)
    )


# ─── prior-run event manifest (#13) ───────────────────────────────────────


def _load_prior_event_ids(path: str | None) -> set[str] | None:
    """Read prior_events.json and return its set of event IDs.

    Semantics distinguish missing from empty:

    - Missing file / empty path / unreadable / wrong shape → return
      None. The caller reads None as "no prior state" and suppresses
      all NEW badges for this run. This is the first-run graceful
      degradation path — flashing NEW on every card when there's
      nothing to diff against would be visually useless.
    - File present, event_ids is a list (possibly empty) → return a
      set of the string IDs. An empty list is a legitimate "last run
      rendered zero events" state; callers SHOULD badge the current
      render in that case.

    The missing-vs-empty distinction is load-bearing — see
    design/new-this-week-badges.md.
    """
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(
            f"  WARNING: prior_events.json unreadable ({e}); "
            "suppressing NEW badges for this run"
        )
        return None
    if not isinstance(data, dict):
        print(
            "  WARNING: prior_events.json not a JSON object; "
            "suppressing NEW badges for this run"
        )
        return None
    ids = data.get("event_ids")
    if not isinstance(ids, list):
        print(
            "  WARNING: prior_events.json missing event_ids list; "
            "suppressing NEW badges for this run"
        )
        return None
    return {s for s in ids if isinstance(s, str)}


def _save_prior_event_ids(path: str, ids: set[str], now_iso: str) -> None:
    """Atomically overwrite prior_events.json with the current render's IDs.

    Writes event_ids sorted so week-over-week diffs on the state
    branch are readable. generated_at_iso is informational for
    operators looking at the committed file; nothing downstream
    parses it. The tempfile + os.replace pattern mirrors
    events_state.save_state — an interrupted write cannot leave a
    truncated manifest.
    """
    payload = {
        "generated_at_iso": now_iso,
        "event_ids": sorted(ids),
    }
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


# ─── outlier alerts (#17) ─────────────────────────────────────────────────


def _load_outlier_alerts(path: str | None) -> list[dict[str, Any]]:
    """Read the outlier-alerts JSON file and return a list of alert dicts.

    Missing / empty path / unreadable / wrong shape → `[]`. The Monday
    digest is the only consumer today, and an absent file on Wed/Sat
    runs (or a first-ever run before the state branch carries one) must
    degrade to "no warning block" rather than crash the pipeline.

    Each alert dict carries keys produced by
    `newsletter_stats.outlier_alerts`: sender, message_id, prior_median,
    current_count, threshold. Deep validation is deferred to the
    renderers, which use `.get()` with safe defaults.
    """
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(
            f"  WARNING: outlier_alerts file unreadable ({e}); "
            "no under-extraction block will be rendered"
        )
        return []
    if not isinstance(data, list):
        print(
            "  WARNING: outlier_alerts file is not a JSON list; "
            "no under-extraction block will be rendered"
        )
        return []
    return [a for a in data if isinstance(a, dict)]


def _name_signature(name: str) -> frozenset[str]:
    """Significant-token set for fuzzy dedupe of near-duplicate event names.

    Keeps any token ≥3 chars, plus standalone digit tokens regardless of
    length — otherwise "Ages 3–5" and "Ages 6–8" produce identical
    signatures and two concurrent age groups collapse wrongly.
    """
    return frozenset(
        t for t in _NAME_TOKEN_SPLIT.split(name.lower())
        if len(t) >= 3 or t.isdigit()
    )


def dedupe(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Two-pass dedupe.

    Pass 1 (exact): collapse events with identical (normalized name, date),
    keeping the most complete.

    Pass 2 (fuzzy): within each same-date bucket, collapse events whose
    significant-token signatures are in a subset relationship — e.g.
    "ASL Club" ({asl, club}) and "ASL Club Meeting" ({asl, club, meeting}).
    Union-find handles transitive chains ("ASL Club" links
    "ASL Club Meeting" and "ASL Club — 6th grade" even though the outer
    pair has no direct subset relation). Undated events skip the fuzzy
    pass since we can't confirm same-day.
    """
    def completeness(ev: dict[str, Any]) -> int:
        score = 0
        if ev["time"]:
            score += 2
        if ev["location"]:
            score += 2
        if ev["child"]:
            score += 1
        if ev["source"] != "unknown source":
            score += 1
        return score

    # Pass 1: exact match on normalized name + date.
    best: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
    for ev in events:
        key = (_norm(ev["name"]), ev.get("date", ""))
        if key not in best or completeness(ev) > completeness(best[key]):
            best[key] = ev
    unique = list(best.values())

    # Pass 2: fuzzy match within same-date buckets via token-signature subset.
    by_date: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    undated: list[dict[str, Any]] = []
    for ev in unique:
        d = ev.get("date") or ""
        if d:
            by_date.setdefault(d, []).append(ev)
        else:
            undated.append(ev)

    merged: list[dict[str, Any]] = []
    for bucket in by_date.values():
        if len(bucket) == 1:
            merged.append(bucket[0])
            continue
        sigs = [_name_signature(ev["name"]) for ev in bucket]
        parent = list(range(len(bucket)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i in range(len(bucket)):
            if not sigs[i]:
                continue
            for j in range(i + 1, len(bucket)):
                if sigs[j] and (sigs[i] <= sigs[j] or sigs[j] <= sigs[i]):
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[ri] = rj

        groups: OrderedDict[int, list[dict[str, Any]]] = OrderedDict()
        for i, ev in enumerate(bucket):
            groups.setdefault(find(i), []).append(ev)
        for g in groups.values():
            merged.append(max(g, key=completeness))

    return merged + undated


def week_start(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def group_by_week(events: list[dict[str, Any]]
                  ) -> list[tuple[dt.date, list[dict[str, Any]]]]:
    events = sorted(events, key=lambda e: (e["_date_obj"], e["name"].lower()))
    buckets: OrderedDict[dt.date, list[dict[str, Any]]] = OrderedDict()
    for ev in events:
        w = week_start(ev["_date_obj"])
        buckets.setdefault(w, []).append(ev)
    return list(buckets.items())


HR = "=" * 60
SUB = "-" * 60

# ── Category colors for HTML rendering ──────────────────────────
CATEGORY_COLORS = {
    "School Activity": ("#1a73e8", "#e8f0fe"),
    "Appointment": ("#d93025", "#fce8e6"),
    "Academic Due Date": ("#f9ab00", "#fef7e0"),
    "Sports & Extracurriculars": ("#0d652d", "#e6f4ea"),
    "Uncategorized": ("#5f6368", "#f1f3f4"),
}


def render_event(ev: dict[str, Any]) -> str:
    d: dt.date = ev["_date_obj"]
    header = f"{d.strftime('%A, %B %-d')} — {ev['name']}"
    lines = [header]
    if ev["time"]:
        lines.append(f"Time: {ev['time']}")
    if ev["location"]:
        lines.append(f"Location: {ev['location']}")
    lines.append(f"Category: {ev['category']}")
    child_source = []
    if ev["child"]:
        child_source.append(f"Child: {ev['child']}")
    child_source.append(f"Source: {ev['source']}")
    lines.append(" | ".join(child_source))
    return "\n".join(lines) + "\n"


def render_undated(ev: dict[str, Any]) -> str:
    bits = [f"- {ev['name']}"]
    if ev["time"]:
        bits.append(f"  Time: {ev['time']}")
    if ev["location"]:
        bits.append(f"  Location: {ev['location']}")
    bits.append(f"  Category: {ev['category']}")
    tail = []
    if ev["child"]:
        tail.append(f"Child: {ev['child']}")
    tail.append(f"Source: {ev['source']}")
    bits.append("  " + " | ".join(tail))
    return "\n".join(bits) + "\n"


def render_body(today: dt.date,
                weeks: list[tuple[dt.date, list[dict[str, Any]]]],
                undated: list[dict[str, Any]],
                total_future: int,
                lookback_days: int) -> str:
    lines: list[str] = []
    lines.append("UPCOMING KIDS' EVENTS")
    lines.append(
        f"Generated {today.strftime('%B %-d, %Y')} | "
        f"Events from {today.strftime('%B %-d, %Y')} onward | "
        f"Email lookback: {lookback_days} days | "
        f"{total_future} dated event(s)"
    )
    lines.append(HR)
    lines.append("")

    if not weeks and not undated:
        lines.append("No upcoming kids' events were found in the searched")
        lines.append("email window. Consider extending the lookback window.")
        lines.append("")
    for wstart, evs in weeks:
        lines.append(f"[Week of {wstart.strftime('%B %-d')}]")
        lines.append(SUB)
        lines.append("")
        for ev in evs:
            lines.append(render_event(ev))
    if undated:
        lines.append("UNDATED / NEEDS VERIFICATION — please confirm these dates.")
        lines.append(SUB)
        for ev in undated:
            lines.append(render_undated(ev))
    lines.append(HR)
    return "\n".join(lines).rstrip() + "\n"


# ─── HTML card render helpers ─────────────────────────────────────

# All three ingest/agent synonyms collapse to the same grey "All day"
# pill on the rendered card. Empty string is also classified here so
# the ingest-side flip to "" (dropping the legacy "Time TBD" sentinel)
# continues to render as all-day.
_ALL_DAY_STRINGS = frozenset({"", "time tbd", "all day", "all day (deadline)"})


def _is_all_day(time_str: str) -> bool:
    """Return True for time strings that should render as the all-day pill."""
    return (time_str or "").strip().lower() in _ALL_DAY_STRINGS


# Conservative email-address match: something@domain.tld. Intentionally
# does NOT catch bare domains like "camps.fcps.edu" — those are rare,
# still legible, and pattern-matching risks false positives against
# real venues that contain dots (e.g. "Mt. Vernon High School").
_SUPPRESS_LOCATION_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$")


# #29: locations that look like a fully-formed street address get
# rendered without the "Location: " prefix (the address is obviously
# a location). The pattern matches a digit-prefixed token followed
# anywhere by a street-suffix word — so "2371 Carlson Way" trips it,
# but "School Gym", "Bldg A, Room 215", "Online", and "Mr. Patel's
# Classroom" do not. Failure modes accepted: a venue named "Way Cool
# Studio" doesn't match (no leading digit); cases like "St. Patrick
# Hall" don't match either ("St." is anchored to the head, not
# preceded by digits). Single-line regex; suffix list is the common
# US street-naming set.
_ADDRESS_LIKE_RE = re.compile(
    r"\d+\s+\S+.*\b(?:Way|St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard"
    r"|Dr|Drive|Ln|Lane|Pkwy|Parkway|Cir|Circle|Ct|Court|Ter|Terrace"
    r"|Pl|Place|Hwy|Highway)\b",
    re.IGNORECASE,
)


# #29: source line truncation cap. Keeps a runaway From label from
# breaking card layout. Full string lands in the title= tooltip so
# the truncated version stays inspectable.
_SOURCE_DISPLAY_CAP = 80


def _is_address_like(loc: str) -> bool:
    """Return True for location strings that look like a fully-formed
    street address.

    Used by the card render to decide whether to prefix the location
    with "Location: " (#29). Address-like locations skip the prefix
    because the address itself is already self-evidently a location;
    everything else gets the prefix for consistency with "For:" and
    "From:" lines on the same card.
    """
    return bool(_ADDRESS_LIKE_RE.search(loc or ""))


def _is_suppressible_location(loc: str) -> bool:
    """Return True for locations that should be omitted from the card.

    Catches email-only locations the agent sometimes emits instead of
    a real venue (an email isn't a clickable destination Ellen wants
    to act on as a "place"), plus the stale "Location TBD" literal
    for fixtures that predate the ingest flip. Mixed strings
    ("Tysons Pediatrics, 8350 Greensboro Dr") are preserved.
    Empty strings are handled upstream by the caller's truthy check.

    URL-only locations are NOT suppressed any more (#29 follow-up):
    they render as clickable anchors via :func:`_linkify_inline_urls`,
    so Ellen can tap straight through to the destination from a card.
    """
    s = (loc or "").strip().lower()
    if not s:
        return False
    if s == "location tbd":
        return True
    return bool(_SUPPRESS_LOCATION_EMAIL_RE.match(s))


# #29 follow-up: detect URLs (full http(s):// shape AND bare domains
# like "myschoolbucks.com") embedded anywhere in a location string so
# they can be wrapped in <a href> anchors. The TLD constraint —
# 2-to-6 alphabetic characters with no surrounding whitespace — keeps
# common false-friends like "Mt. Vernon High School", "Dr. Smith's
# office", and "v1.0" from being mis-detected.
_INLINE_URL_RE = re.compile(
    r"\b("                                # group 1 = the full match
    r"(?:https?://)?"                     # optional scheme
    r"(?:[a-z0-9-]+\.)+"                  # one or more `label.` segments
    r"[a-z]{2,6}"                         # final TLD (alpha, 2-6 chars)
    r"(?:/[^\s)\]]*)?"                    # optional path/query/fragment
    r")\b",
    re.IGNORECASE,
)


def _linkify_inline_urls(loc: str) -> str:
    """Return *loc* with embedded URLs/bare-domains wrapped as
    ``<a href=...>`` anchors.

    Plain-text segments pass through verbatim — matches the existing
    escape-nothing posture in render_html for event names, source
    labels, etc. Only the anchor's ``href`` and visible text are
    HTML-escaped, so a query-string ``&`` (which would otherwise
    break out of the attribute) is rendered as ``&amp;``.

    Bare domains are linked with an implicit ``https://`` scheme on
    the assumption that any modern destination Ellen would tap is
    https-reachable. Anchors carry ``target="_blank"`` and
    ``rel="noopener noreferrer"`` so the click opens a new tab
    without leaking the host page's session via window.opener.
    """
    parts: list[str] = []
    last_end = 0
    for m in _INLINE_URL_RE.finditer(loc):
        start, end = m.start(), m.end()
        if last_end < start:
            parts.append(loc[last_end:start])
        url_text = m.group(0)
        if url_text.lower().startswith(("http://", "https://")):
            href = url_text
        else:
            href = "https://" + url_text
        parts.append(
            f'<a href="{_html.escape(href, quote=True)}" '
            f'target="_blank" rel="noopener noreferrer">'
            f'{_html.escape(url_text)}</a>'
        )
        last_end = end
    if last_end < len(loc):
        parts.append(loc[last_end:])
    return "".join(parts)


def render_html(today: dt.date,
                weeks: list[tuple[dt.date, list[dict[str, Any]]]],
                undated: list[dict[str, Any]],
                total_future: int,
                lookback_days: int,
                webhook_url: str = "",
                pages_url: str = "",
                protected_senders: list[str] | None = None,
                new_ids: set[str] | None = None) -> str:
    """Render a complete, self-contained HTML page for GitHub Pages.

    webhook_url: if non-empty, the rendered page will POST ignore decisions
    to this URL (expected to be a Google Apps Script web app). If empty,
    Ignore clicks only hide the card locally via localStorage.

    pages_url: if non-empty, each dated card gets an Add-to-calendar link
    pointing at `https://<host>/<path>/ics/<event_id>.ics`. iOS Safari
    recognizes the text/calendar MIME GitHub Pages serves and offers a
    one-shot "Add to Calendar" sheet — distinct from `webcal://` which
    triggers a calendar-subscription flow (the wrong behavior for a
    single event). Empty pages_url hides the button (e.g. dev preview
    without a deploy URL).

    new_ids: event IDs whose cards should render a "NEW" badge inline
    with the event name. Pass `None` on first-run (no prior manifest
    to diff against) to suppress all badges; pass `set()` to render
    with zero events flagged new. See design/new-this-week-badges.md
    (#13) for the diff semantics.
    """

    webcal_base = _webcal_base(pages_url)
    protected = protected_senders or []
    _new_ids = new_ids if new_ids is not None else set()

    def _child_markup(
        child: str, slug: str, tier: str,
    ) -> tuple[str, str]:
        """Render (chip_html, audience_html) for an event card.

        Rules:
          - Slug present → render the coloured kid pill using the
            roster's canonical name (from _SLUG_TO_NAME), so casing
            tracks the roster even if the event's `child` field was
            lowercased or missing.
          - Audience line is shown alongside the pill only when the
            derivation matched via a non-name tier (teacher / grade /
            activity / school) AND the event has a non-empty `child`
            field worth surfacing (e.g. "For: 6th grade AAP" next to
            the E pill). A tier-1 "name" match suppresses the audience
            line — the pill already tells you who.
          - No slug → fall back to the pre-#19 behavior: audience line
            if `child` is non-empty, nothing otherwise.
        """
        if slug:
            display_name = _SLUG_TO_NAME.get(slug, slug.capitalize())
            chip_html = (
                f'<span class="child-chip {slug}" '
                f'title="{display_name}">{display_name[0]}</span>'
            )
            if tier != "name" and child:
                audience_html = (
                    f'\n        <div class="event-audience">For: {child}</div>'
                )
            else:
                audience_html = ""
            return chip_html, audience_html
        if child:
            return "", (
                f'\n        <div class="event-audience">For: {child}</div>'
            )
        return "", ""

    def _event_card(ev: dict[str, Any]) -> str:
        d: dt.date = ev["_date_obj"]
        cat = ev["category"]
        # `_` was the old badge background — the category badge is gone
        # under Layout A; the coloured left rail (inline border-left: fg)
        # is the sole category signal now.
        fg, _ = CATEGORY_COLORS.get(cat, CATEGORY_COLORS["Uncategorized"])
        day_label = d.strftime("%a, %b %-d")
        # Child rendering uses the roster-backed derivation (#19). A
        # slug + tier is computed from the full event (child field,
        # source, location, name) against the distinctive signals of
        # each kid. See design/kid-attribution-derivation.md.
        child = (ev.get("child") or "").strip()
        slug, tier = roster_match.derive_child_slug(ev, _DISTINCTIVE_SIGNALS)
        chip_html, audience_html = _child_markup(child, slug, tier)
        # data-child drives the top-of-page filter chips (#12). The
        # derivation expands beyond strict name-match, so e.g. a
        # "6th grade AAP" card now carries data-child="everly".
        data_child_val = slug
        if _is_all_day(ev["time"]):
            time_html = '<span class="time allday">All day</span>'
        else:
            time_html = f'<span class="time">{ev["time"]}</span>'
        loc_raw = ev["location"]
        if loc_raw and not _is_suppressible_location(loc_raw):
            # #29: prefix with "Location: " for consistency with For:
            # and From:, except when the location itself is obviously
            # a street address. URLs/bare-domains anywhere in the
            # string get wrapped as <a href> via _linkify_inline_urls
            # — Ellen can tap straight through.
            base = (
                _linkify_inline_urls(loc_raw)
                if _INLINE_URL_RE.search(loc_raw)
                else loc_raw
            )
            loc_display = base if _is_address_like(loc_raw) else f"Location: {base}"
            location_html = f'\n        <div class="event-location">{loc_display}</div>'
        else:
            location_html = ""
        # #29: From: line with the agent's curated source label. Cap
        # the displayed text so a runaway label doesn't break the
        # card; the full string survives in the title= tooltip.
        source_raw = (ev.get("source") or "").strip()
        if source_raw:
            if len(source_raw) > _SOURCE_DISPLAY_CAP:
                source_display = source_raw[:_SOURCE_DISPLAY_CAP - 1] + "…"
            else:
                source_display = source_raw
            source_html = (
                f'\n        <div class="event-source" '
                f'title="From: {source_raw}">From: {source_display}</div>'
            )
        else:
            source_html = ""
        ics_btn_html = ""
        if webcal_base:
            ics_href = f"https://{webcal_base}ics/{ev['id']}.ics"
            ics_btn_html = (
                f'<a class="ics-btn" href="{ics_href}" '
                f'aria-label="Add this event to your calendar">Add to calendar</a>'
            )
        is_ignored = bool(ev.get("is_ignored"))
        ignored_class = " ignored" if is_ignored else ""
        # Server-side is_ignored always reflects ignored_events.json (i.e.
        # an individually-ignored event). Sender-swept state is client-only
        # and gets written via setIgnored(card, "sender") during hydration.
        ignored_attr = (' data-ignored="1" data-ignored-reason="event"'
                        if is_ignored else "")
        card_style = (f"display:none; border-left: 4px solid {fg};" if is_ignored
                      else f"border-left: 4px solid {fg};")
        if is_ignored:
            ignore_btn_html = (
                f'<button class="unignore-btn" aria-label="Unignore this event"\n'
                f'                data-event-name="{ev["name"]}" data-event-date="{ev["date"]}"\n'
                f'                type="button">Unignore event</button>'
            )
        else:
            ignore_btn_html = (
                f'<button class="ignore-btn" aria-label="Ignore this event"\n'
                f'                data-event-name="{ev["name"]}" data-event-date="{ev["date"]}"\n'
                f'                type="button">Ignore event</button>'
            )
        # `sender_domain` remains the registrable-domain identity (used
        # for grouping / display). The `sender_block_key` is what the
        # Ignore-sender button submits: full address for freemail
        # (gmail.com, yahoo.com, ...), the domain otherwise. See
        # design/sender-block-granularity.md.
        block_key = (ev.get("sender_block_key") or "").strip()
        domain = (ev.get("sender_domain") or "").strip()
        sender_attr = f' data-sender="{block_key}"' if block_key else ""
        sender_btn_html = ""
        # Never render the Ignore-sender button for protected senders
        # (schools, PTAs, health providers, team-management platforms,
        # plus address-form entries like the parents' personal Gmail).
        # The Ignore-event button on the same card is unaffected — the
        # user can still hide a single event from a protected sender;
        # they just can't sweep the whole sender by accident. The guard
        # keys on `block_key` (NOT `domain`) so address-form patterns
        # added in #26 actually fire here too — passing `domain`
        # reduces a freemail sender to its bare domain (gmail.com),
        # against which an address-form pattern (alice@gmail.com)
        # cannot match. block_key is the bare domain for institutional
        # senders and the full address for freemail, which
        # `is_protected` handles uniformly. See #28.
        if block_key and not is_protected(block_key, protected):
            sender_btn_html = (
                '\n        <div class="event-actions-bottom">\n'
                f'          <button class="ignore-sender-btn" '
                f'aria-label="Ignore future events from this sender"\n'
                f'                  data-sender="{block_key}" type="button">'
                f'Ignore sender ({block_key})</button>\n'
                '        </div>'
            )
        new_badge_html = (
            '<span class="new-badge">NEW</span>' if ev["id"] in _new_ids else ""
        )
        return f"""\
      <div class="event-card{ignored_class}" data-event-id="{ev["id"]}" data-child="{data_child_val}"{ignored_attr}{sender_attr}
           style="{card_style}">
        <div class="event-actions-top">{ics_btn_html}{ignore_btn_html}</div>
        <div class="meta-strip">
          {chip_html}<span class="day">{day_label}</span>
          <span class="sep">·</span>
          {time_html}
        </div>
        <div class="event-name">{ev["name"]}{new_badge_html}</div>{audience_html}{source_html}{location_html}
        <div class="ignore-status" aria-live="polite"></div>{sender_btn_html}
      </div>"""

    def _undated_card(ev: dict[str, Any]) -> str:
        child = (ev.get("child") or "").strip()
        slug, tier = roster_match.derive_child_slug(ev, _DISTINCTIVE_SIGNALS)
        chip_html, audience_html = _child_markup(child, slug, tier)
        # See `_event_card` for the data-child rationale (#12 + #19).
        data_child_val = slug
        if _is_all_day(ev["time"]):
            time_html = '<span class="time allday">All day</span>'
        else:
            time_html = f'<span class="time">{ev["time"]}</span>'
        loc_raw = ev["location"]
        if loc_raw and not _is_suppressible_location(loc_raw):
            # #29: parity with _event_card — Location: prefix (unless
            # address-shape) plus URL linkification anywhere in the
            # string.
            base = (
                _linkify_inline_urls(loc_raw)
                if _INLINE_URL_RE.search(loc_raw)
                else loc_raw
            )
            loc_display = base if _is_address_like(loc_raw) else f"Location: {base}"
            location_html = f'\n        <div class="event-location">{loc_display}</div>'
        else:
            location_html = ""
        # #29: From: line for source attribution. Same shape as the
        # dated-card path; truncation cap and title= tooltip identical.
        source_raw = (ev.get("source") or "").strip()
        if source_raw:
            if len(source_raw) > _SOURCE_DISPLAY_CAP:
                source_display = source_raw[:_SOURCE_DISPLAY_CAP - 1] + "…"
            else:
                source_display = source_raw
            source_html = (
                f'\n        <div class="event-source" '
                f'title="From: {source_raw}">From: {source_display}</div>'
            )
        else:
            source_html = ""
        # #18: undated cards carry the same per-event Ignore/Unignore
        # affordance as dated cards. The stable id is sha1(name|""|child);
        # it is disjoint from any dated hash because the middle segment
        # differs (see test_event_id_undated_vs_dated_no_collision). The
        # Ignore-sender variant stays out of scope — undated cards that
        # reach this section are typically the ones the extractor could
        # not fully ground, so their sender domain is unreliable.
        is_ignored = bool(ev.get("is_ignored"))
        ignored_class = " ignored" if is_ignored else ""
        ignored_attr = (' data-ignored="1" data-ignored-reason="event"'
                        if is_ignored else "")
        card_style = ("display:none; border-left: 4px solid #f9ab00;" if is_ignored
                      else "border-left: 4px solid #f9ab00;")
        if is_ignored:
            ignore_btn_html = (
                f'<button class="unignore-btn" aria-label="Unignore this event"\n'
                f'                data-event-name="{ev["name"]}" data-event-date="{ev["date"]}"\n'
                f'                type="button">Unignore event</button>'
            )
        else:
            ignore_btn_html = (
                f'<button class="ignore-btn" aria-label="Ignore this event"\n'
                f'                data-event-name="{ev["name"]}" data-event-date="{ev["date"]}"\n'
                f'                type="button">Ignore event</button>'
            )
        new_badge_html = (
            '<span class="new-badge">NEW</span>' if ev["id"] in _new_ids else ""
        )
        return f"""\
      <div class="event-card undated{ignored_class}" data-event-id="{ev["id"]}" data-child="{data_child_val}"{ignored_attr} style="{card_style}">
        <div class="event-actions-top">{ignore_btn_html}</div>
        <div class="meta-strip">
          {chip_html}<span class="day">Date TBD</span>
          <span class="sep">·</span>
          {time_html}
        </div>
        <div class="event-name">{ev["name"]}{new_badge_html}</div>{audience_html}{source_html}{location_html}
      </div>"""

    # Build week sections
    week_sections = []
    for wstart, evs in weeks:
        cards = "\n".join(_event_card(ev) for ev in evs)
        week_sections.append(f"""\
    <section class="week">
      <h2>Week of {wstart.strftime("%B %-d")}</h2>
{cards}
    </section>""")

    weeks_html = "\n".join(week_sections) if week_sections else ""

    # Undated section
    undated_html = ""
    if undated:
        undated_cards = "\n".join(_undated_card(ev) for ev in undated)
        undated_html = f"""\
    <section class="week undated-section">
      <h2>Needs Verification</h2>
      <p class="undated-note">These events were found but the date could not be confirmed.</p>
{undated_cards}
    </section>"""

    # Empty state
    if not weeks_html and not undated_html:
        weeks_html = f"""\
    <section class="empty-state">
      <p>No upcoming kids' events were found in the last {lookback_days} days of email.</p>
    </section>"""

    # Timestamp is rendered in local (Eastern) time so it lines up with when
    # the cron actually fired for the family, not UTC when the runner kicked off.
    now_local = dt.datetime.now(LOCAL_TZ)
    generated = now_local.strftime("%B %-d, %Y @ %-I:%M%p")

    # Show-ignored toggle: rendered server-side only when at least one
    # card is hidden on the page. #18 extended per-event ignore to
    # undated cards, so the count spans display + undated buckets —
    # otherwise an undated card ignored on a prior run would reload
    # hidden with no toggle available to reveal it.
    ignored_n = sum(
        1 for wk in weeks for ev in wk[1] if ev.get("is_ignored")
    ) + sum(
        1 for ev in undated if ev.get("is_ignored")
    )
    show_ignored_toggle_html = ""
    if ignored_n > 0:
        show_ignored_toggle_html = (
            '\n    <div class="stats">\n'
            f'      <button class="show-ignored-toggle" type="button"\n'
            f'              data-show-label="Show ignored ({ignored_n})"\n'
            f'              data-hide-label="Hide ignored ({ignored_n})">'
            f'Show ignored ({ignored_n})</button>\n'
            '    </div>'
        )

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kids' Schedule</title>
  <style>
    :root {{
      --bg: #fafafa;
      --surface: #ffffff;
      --text: #202124;
      --text-secondary: #5f6368;
      --text-tertiary: #80868b;
      --border: #e0e0e0;
      --accent: #1a73e8;
      --everly: #ec407a;
      --everly-bg: #fce4ec;
      --isla: #5c6bc0;
      --isla-bg: #e8eaf6;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                   "Helvetica Neue", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 0;
    }}
    .header {{
      background: var(--accent);
      color: white;
      padding: 1.5rem 1rem;
      text-align: center;
    }}
    .header h1 {{
      font-size: 1.5rem;
      font-weight: 600;
      margin-bottom: 0.25rem;
    }}
    .header .subtitle {{
      font-size: 0.85rem;
      opacity: 0.85;
    }}
    .stats {{
      display: flex;
      justify-content: center;
      gap: 1.5rem;
      padding: 0.75rem 1rem;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      font-size: 0.85rem;
      color: var(--text-secondary);
    }}
    .stats .stat-value {{
      font-weight: 600;
      color: var(--text);
    }}
    .container {{
      max-width: 640px;
      margin: 0 auto;
      padding: 1rem;
    }}
    .week {{
      margin-bottom: 1.5rem;
    }}
    .week h2 {{
      font-size: 1rem;
      font-weight: 600;
      color: var(--text-secondary);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 0.75rem;
      padding-bottom: 0.25rem;
      border-bottom: 2px solid var(--border);
    }}
    .event-card {{
      background: var(--surface);
      border-radius: 8px;
      padding: 0.75rem 1rem;
      margin-bottom: 0.5rem;
      box-shadow: 0 1px 2px rgba(0,0,0,0.06);
      transition: opacity 0.25s ease;
    }}
    .event-card.fading {{
      opacity: 0;
    }}
    .event-actions-top {{
      display: flex;
      justify-content: flex-end;
      gap: 0.35rem;
      flex-wrap: wrap;
      margin-bottom: 0.25rem;
    }}
    .ignore-btn, .unignore-btn, .unignore-sender-btn {{
      border-radius: 4px;
      padding: 0.2rem 0.55rem;
      font-size: 0.72rem;
      font-weight: 500;
      cursor: pointer;
      font-family: inherit;
      line-height: 1.4;
    }}
    .ignore-btn {{
      background: transparent;
      color: var(--text-secondary);
      border: 1px solid var(--border);
    }}
    .ignore-btn:hover {{
      background: var(--border);
      color: var(--text);
    }}
    .unignore-btn, .unignore-sender-btn {{
      background: #0d652d;
      color: #ceead6;
      border: 1px solid #0d652d;
    }}
    .unignore-btn:hover, .unignore-sender-btn:hover {{
      filter: brightness(1.15);
    }}
    /* Hide the Ignore-sender button on ignored cards — for sender-swept
       cards it's meaningless (the sender is already ignored), and for
       event-ignored cards the Unignore-event affordance is what the user
       wants. Unignore-sender lives in the top action row via class swap. */
    .event-card[data-ignored="1"] .ignore-sender-btn {{
      display: none;
    }}
    .event-card.ignored {{
      display: none;
    }}
    .show-ignored .event-card.ignored {{
      display: block !important;
    }}
    .show-ignored-toggle {{
      background: transparent;
      color: var(--text-secondary);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 0.2rem 0.55rem;
      font-size: 0.8rem;
      font-weight: 500;
      cursor: pointer;
      font-family: inherit;
      line-height: 1.4;
    }}
    .show-ignored-toggle:hover {{
      background: var(--border);
      color: var(--text);
    }}
    .event-actions-bottom {{
      margin-top: 0.5rem;
      display: flex;
      gap: 0.35rem;
    }}
    .ignore-sender-btn {{
      background: transparent;
      color: var(--text-secondary);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 0.2rem 0.55rem;
      font-size: 0.72rem;
      font-weight: 500;
      cursor: pointer;
      font-family: inherit;
      line-height: 1.4;
    }}
    .ignore-sender-btn:hover {{
      background: var(--border);
      color: var(--text);
    }}
    .ignore-sender-btn:disabled {{
      opacity: 0.5;
      cursor: default;
    }}
    .ics-btn {{
      background: transparent;
      color: var(--text-secondary);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 0.2rem 0.55rem;
      font-size: 0.72rem;
      font-weight: 500;
      cursor: pointer;
      font-family: inherit;
      line-height: 1.4;
      display: inline-block;
      text-decoration: none;
    }}
    .ics-btn:hover {{
      background: var(--border);
      color: var(--text);
    }}
    .ignore-status {{
      font-size: 0.72rem;
      color: #d93025;
      margin-top: 0.3rem;
      min-height: 0;
    }}
    .ignore-status:empty {{
      display: none;
    }}
    .meta-strip {{
      display: flex;
      align-items: center;
      gap: 0.5rem;
      font-size: 0.82rem;
      color: var(--text-secondary);
      margin-bottom: 0.2rem;
      flex-wrap: wrap;
    }}
    .child-chip {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 22px;
      height: 22px;
      border-radius: 50%;
      font-size: 0.72rem;
      font-weight: 700;
      flex-shrink: 0;
    }}
    .child-chip.everly {{ background: var(--everly); color: #fff; }}
    .child-chip.isla   {{ background: var(--isla);   color: #fff; }}
    .filter-chips {{
      display: flex;
      justify-content: center;
      gap: 0.4rem;
      padding: 0.5rem 1rem;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      flex-wrap: wrap;
    }}
    .filter-chip {{
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      background: transparent;
      color: var(--text-secondary);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 0.2rem 0.7rem;
      font-size: 0.8rem;
      font-weight: 500;
      cursor: pointer;
      font-family: inherit;
      line-height: 1.4;
    }}
    .filter-chip:hover {{
      background: var(--border);
      color: var(--text);
    }}
    .filter-chip.active {{
      background: var(--text);
      color: var(--surface);
      border-color: var(--text);
    }}
    /* !important matches .show-ignored .event-card.ignored so an active
       kid filter still hides the other kid's cards while ignored ones
       are revealed. */
    body.filter-everly .event-card[data-child="isla"],
    body.filter-isla   .event-card[data-child="everly"] {{ display: none !important; }}
    .meta-strip .sep {{
      color: var(--text-tertiary);
    }}
    .meta-strip .day {{
      font-weight: 600;
      color: var(--text);
    }}
    .meta-strip .time.allday {{
      background: #f1f3f4;
      color: var(--text-secondary);
      padding: 0.05rem 0.45rem;
      border-radius: 10px;
      font-size: 0.72rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.3px;
    }}
    .event-name {{
      font-size: 1.05rem;
      font-weight: 600;
      line-height: 1.3;
      margin: 0.1rem 0 0.2rem;
    }}
    .new-badge {{
      display: inline-block;
      background: var(--accent);
      color: white;
      padding: 0.05rem 0.45rem;
      border-radius: 10px;
      font-size: 0.65rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.3px;
      margin-left: 0.4rem;
      vertical-align: middle;
    }}
    .event-location {{
      font-size: 0.82rem;
      color: var(--text-secondary);
      margin-bottom: 0.15rem;
    }}
    /* #29 follow-up: linkified URL/domain in the location line. We
       inherit the surrounding muted color so the link doesn't shout,
       but underline it so it reads as clickable. Hover bumps to the
       primary text color for a clear affordance. */
    .event-location a {{
      color: inherit;
      text-decoration: underline;
    }}
    .event-location a:hover {{
      color: var(--text-primary);
    }}
    .event-audience {{
      font-size: 0.75rem;
      color: var(--text-tertiary);
      margin-bottom: 0.15rem;
    }}
    /* #29: source attribution line. Tonally subordinate to
       event-name and event-location; matches event-audience weight
       so the For:/From: pair reads as one block of metadata. The
       overflow guards keep a runaway label from breaking layout —
       the full string lives in the title= tooltip. */
    .event-source {{
      font-size: 0.75rem;
      color: var(--text-tertiary);
      margin-bottom: 0.15rem;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .undated-note {{
      font-size: 0.85rem;
      color: var(--text-secondary);
      margin-bottom: 0.75rem;
      font-style: italic;
    }}
    .empty-state {{
      text-align: center;
      padding: 3rem 1rem;
      color: var(--text-secondary);
    }}
    .footer {{
      text-align: center;
      padding: 2rem 1rem;
      font-size: 0.75rem;
      color: var(--text-secondary);
    }}
    .toast {{
      position: fixed;
      bottom: 1rem;
      left: 50%;
      transform: translateX(-50%);
      background: var(--text);
      color: var(--surface);
      padding: 0.5rem 1rem;
      border-radius: 6px;
      font-size: 0.85rem;
      box-shadow: 0 2px 8px rgba(0,0,0,0.25);
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.2s ease;
      z-index: 1000;
      max-width: 90%;
      text-align: center;
    }}
    .toast.visible {{
      opacity: 1;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #1a1a1a;
        --surface: #2d2d2d;
        --text: #e8eaed;
        --text-secondary: #9aa0a6;
        --text-tertiary: #80868b;
        --border: #3c4043;
        --accent: #8ab4f8;
      }}
      .meta-strip .time.allday {{
        background: #3c4043;
        color: var(--text-secondary);
      }}
      .unignore-btn, .unignore-sender-btn {{
        background: #1e8e3e;
        color: #e6f4ea;
        border: 1px solid #1e8e3e;
      }}
    }}
  </style>
</head>
<body>
  <div class="header">
    <h1>Kids' Schedule</h1>
    <div class="subtitle">Updated {generated}</div>
  </div>
  <div class="stats">
    <div><span class="stat-value">{total_future}</span> event{"s" if total_future != 1 else ""}</div>
    <div><span class="stat-value">{lookback_days}</span> day lookback</div>
  </div>{show_ignored_toggle_html}
  <div class="filter-chips" role="group" aria-label="Filter by child">
    <button class="filter-chip active" type="button" data-filter-child="all">All</button>
    <button class="filter-chip" type="button" data-filter-child="everly"><span class="child-chip everly" aria-hidden="true">E</span>Everly</button>
    <button class="filter-chip" type="button" data-filter-child="isla"><span class="child-chip isla" aria-hidden="true">I</span>Isla</button>
  </div>
  <div class="container">
{weeks_html}
{undated_html}
  </div>
  <div class="footer">
    Auto-generated from Gmail &middot; Updated Mon, Wed, and Sat
  </div>
  <script>
    (function () {{
      var WEBHOOK_URL = {json.dumps(webhook_url)};
      var STORAGE_KEY         = "kids_schedule_ignored_ids";
      var SENDERS_STORAGE_KEY = "kids_schedule_ignored_senders";

      function loadIgnored() {{
        try {{
          var raw = localStorage.getItem(STORAGE_KEY);
          return raw ? JSON.parse(raw) : [];
        }} catch (e) {{
          return [];
        }}
      }}
      function saveIgnored(ids) {{
        try {{ localStorage.setItem(STORAGE_KEY, JSON.stringify(ids)); }}
        catch (e) {{ /* storage unavailable */ }}
      }}

      function loadIgnoredSenders() {{
        try {{
          var raw = localStorage.getItem(SENDERS_STORAGE_KEY);
          return raw ? JSON.parse(raw) : [];
        }} catch (e) {{
          return [];
        }}
      }}
      function saveIgnoredSenders(domains) {{
        try {{ localStorage.setItem(SENDERS_STORAGE_KEY, JSON.stringify(domains)); }}
        catch (e) {{ /* storage unavailable */ }}
      }}

      // ── Toast ─────────────────────────────────────────────
      var toastTimer = null;
      function showToast(msg) {{
        var t = document.getElementById("toast");
        if (!t) {{
          t = document.createElement("div");
          t.id = "toast";
          t.className = "toast";
          document.body.appendChild(t);
        }}
        t.textContent = msg;
        t.classList.add("visible");
        if (toastTimer) clearTimeout(toastTimer);
        toastTimer = setTimeout(function () {{
          t.classList.remove("visible");
        }}, 3000);
      }}

      // ── Card state swap helpers ───────────────────────────
      // `reason` is "event" (default) or "sender". It drives both the
      // data-ignored-reason attribute (used by CSS + the click router to
      // dispatch the right Unignore action) and the button's label/class.
      function setIgnored(card, reason) {{
        var r = reason === "sender" ? "sender" : "event";
        card.classList.add("ignored");
        card.style.display = "none";
        card.setAttribute("data-ignored", "1");
        card.setAttribute("data-ignored-reason", r);
        var btn = card.querySelector(
          ".ignore-btn, .unignore-btn, .unignore-sender-btn"
        );
        if (btn) {{
          if (r === "sender") {{
            var domain = card.getAttribute("data-sender") || "";
            btn.className = "unignore-sender-btn";
            btn.textContent = domain
              ? "Unignore sender (" + domain + ")"
              : "Unignore sender";
            btn.setAttribute("aria-label", "Unignore this sender");
          }} else {{
            btn.className = "unignore-btn";
            btn.textContent = "Unignore event";
            btn.setAttribute("aria-label", "Unignore this event");
          }}
          btn.disabled = false;
        }}
      }}
      function setActive(card) {{
        card.classList.remove("ignored");
        card.style.display = "";
        card.removeAttribute("data-ignored");
        card.removeAttribute("data-ignored-reason");
        var btn = card.querySelector(".unignore-btn, .unignore-sender-btn");
        if (btn) {{
          btn.className = "ignore-btn";
          btn.textContent = "Ignore event";
          btn.setAttribute("aria-label", "Ignore this event");
          btn.disabled = false;
        }}
      }}

      // ── Show-ignored toggle counter ────────────────────────
      // Server renders the toggle only when ignored_n > 0 at build time.
      // Mid-session we may need to create it (first local ignore on a page
      // that built with zero ignored events) or remove it (count hits zero).
      function bumpToggle(delta) {{
        var btn = document.querySelector(".show-ignored-toggle");
        if (!btn) {{
          if (delta <= 0) return;
          var mainStats = document.querySelector(".stats");
          if (!mainStats) return;
          var wrap = document.createElement("div");
          wrap.className = "stats";
          var newBtn = document.createElement("button");
          newBtn.className = "show-ignored-toggle";
          newBtn.type = "button";
          newBtn.setAttribute("data-show-label", "Show ignored (" + delta + ")");
          newBtn.setAttribute("data-hide-label", "Hide ignored (" + delta + ")");
          newBtn.textContent = "Show ignored (" + delta + ")";
          wrap.appendChild(newBtn);
          mainStats.insertAdjacentElement("afterend", wrap);
          return;
        }}
        var showLabel = btn.getAttribute("data-show-label") || "";
        var m = showLabel.match(/\\((\\d+)\\)/);
        var n = m ? parseInt(m[1], 10) + delta : 0;
        if (n <= 0) {{
          document.body.classList.remove("show-ignored");
          var parent = btn.parentElement;
          if (parent) parent.remove();
          return;
        }}
        var newShow = "Show ignored (" + n + ")";
        var newHide = "Hide ignored (" + n + ")";
        btn.setAttribute("data-show-label", newShow);
        btn.setAttribute("data-hide-label", newHide);
        btn.textContent = document.body.classList.contains("show-ignored")
          ? newHide : newShow;
      }}

      // ── localStorage hydration ────────────────────────────
      // Apply sender-swept state first, then individually-ignored ids.
      // Ids take precedence because the event-level ignore is a deliberate
      // per-card user choice that should win over a bulk sender sweep.
      var localSenders = loadIgnoredSenders();
      if (localSenders.length) {{
        document.querySelectorAll(".event-card[data-sender]").forEach(function (card) {{
          if (localSenders.indexOf(card.getAttribute("data-sender")) === -1) return;
          if (card.getAttribute("data-ignored") === "1") return;
          setIgnored(card, "sender");
        }});
      }}
      var localIds = loadIgnored();
      document.querySelectorAll(".event-card[data-event-id]").forEach(function (card) {{
        if (localIds.indexOf(card.getAttribute("data-event-id")) === -1) return;
        // If already sender-ignored, upgrade to reason=event so Unignore-event
        // is the surfaced affordance.
        setIgnored(card, "event");
      }});

      // ── Per-kid filter chips (#12) ────────────────────────
      // Ephemeral view filter — no localStorage, no persistence. Clicking
      // a kid chip adds body.filter-<kid> which the stylesheet uses to
      // hide cards tagged with the *other* named kid. Audience-line and
      // empty-child cards stay visible across every selection.
      var filterChips = document.querySelectorAll(".filter-chip");
      filterChips.forEach(function (chip) {{
        chip.addEventListener("click", function () {{
          var kid = chip.getAttribute("data-filter-child");
          document.body.classList.remove("filter-everly", "filter-isla");
          if (kid !== "all") {{
            document.body.classList.add("filter-" + kid);
          }}
          filterChips.forEach(function (c) {{
            c.classList.toggle("active", c === chip);
          }});
        }});
      }});

      // ── POST helper ───────────────────────────────────────
      function postAction(payload) {{
        if (!WEBHOOK_URL) return Promise.resolve();  // dev/preview no-op success
        return fetch(WEBHOOK_URL, {{
          method: "POST",
          headers: {{ "Content-Type": "text/plain;charset=utf-8" }},
          body: JSON.stringify(payload)
        }});
      }}

      // ── Delegated click router ────────────────────────────
      document.addEventListener("click", function (e) {{
        var t = e.target;

        // Ignore event — optimistic hide, restore on failure. Sender is
        // included in the POST so the Apps Script can tag the Ignored
        // Events row for later bulk-delete by Unignore-sender.
        if (t.classList.contains("ignore-btn")) {{
          var card = t.closest(".event-card");
          if (!card) return;
          var id = card.getAttribute("data-event-id");
          var name = t.getAttribute("data-event-name") || "";
          var date = t.getAttribute("data-event-date") || "";
          var sender = card.getAttribute("data-sender") || "";
          t.disabled = true;
          var current = loadIgnored();
          if (current.indexOf(id) === -1) current.push(id);
          saveIgnored(current);
          card.classList.add("fading");
          setTimeout(function () {{
            setIgnored(card, "event");
            card.classList.remove("fading");
          }}, 300);
          bumpToggle(1);
          postAction({{
            action: "ignore", id: id, name: name, date: date, sender: sender
          }}).catch(function () {{
            setActive(card);
            var remaining = loadIgnored().filter(function (x) {{ return x !== id; }});
            saveIgnored(remaining);
            bumpToggle(-1);
            showToast("Ignore failed — try again");
          }});
          return;
        }}

        // Unignore event — optimistic reveal, restore on failure. Matches
        // the Ignore latency; the POST runs in the background.
        if (t.classList.contains("unignore-btn")) {{
          var ucard = t.closest(".event-card");
          if (!ucard) return;
          var uid = ucard.getAttribute("data-event-id");
          t.disabled = true;
          setActive(ucard);
          var remainingU = loadIgnored().filter(function (x) {{ return x !== uid; }});
          saveIgnored(remainingU);
          bumpToggle(-1);
          postAction({{ action: "unignore", id: uid }}).catch(function () {{
            setIgnored(ucard, "event");
            var restoredU = loadIgnored();
            if (restoredU.indexOf(uid) === -1) restoredU.push(uid);
            saveIgnored(restoredU);
            bumpToggle(1);
            showToast("Unignore failed — try again");
          }});
          return;
        }}

        // Show/Hide ignored toggle — pure client class flip.
        if (t.classList.contains("show-ignored-toggle")) {{
          var on = document.body.classList.toggle("show-ignored");
          t.textContent = on
            ? (t.getAttribute("data-hide-label") || t.textContent)
            : (t.getAttribute("data-show-label") || t.textContent);
          return;
        }}

        // Ignore sender — optimistic sweep: hide every sibling card from the
        // same domain locally under reason=sender, persist the domain to the
        // ignored-senders store, and bump the counter by the number newly
        // hidden. Event-ids are deliberately NOT pushed to the ignored-events
        // store — the Gmail query already excludes this sender on the next
        // build, so those events won't be fetched. Keeping Ignored Events
        // as a pure record of individual user ignores makes Unignore-sender
        // a clean single-delete on the sheet.
        if (t.classList.contains("ignore-sender-btn")) {{
          var domain = t.getAttribute("data-sender") || "";
          if (!domain) return;
          t.disabled = true;
          var currentSenders = loadIgnoredSenders();
          if (currentSenders.indexOf(domain) === -1) currentSenders.push(domain);
          saveIgnoredSenders(currentSenders);
          var siblings = document.querySelectorAll(
            '.event-card[data-sender="' + domain + '"]'
          );
          var swept = [];
          siblings.forEach(function (card) {{
            if (card.getAttribute("data-ignored") === "1") return;
            setIgnored(card, "sender");
            swept.push(card);
          }});
          if (swept.length) bumpToggle(swept.length);
          postAction({{ action: "ignore_sender", domain: domain }}).then(function () {{
            showToast("Ignoring " + domain + ". New events will stop appearing after the next refresh.");
          }}).catch(function () {{
            swept.forEach(function (card) {{ setActive(card); }});
            var remainingSenders = loadIgnoredSenders().filter(function (d) {{
              return d !== domain;
            }});
            saveIgnoredSenders(remainingSenders);
            if (swept.length) bumpToggle(-swept.length);
            t.disabled = false;
            showToast("Ignore failed — try again");
          }});
          return;
        }}

        // Unignore sender — optimistic: reveal every card from this sender
        // (whether sender-swept or individually ignored), drop matching ids
        // and the domain from localStorage, and POST unignore_sender. The
        // server-side handler clears both the Ignored Senders row and every
        // Ignored Events row tagged with this sender — so independently-
        // ignored events also come back, matching the "clean slate for this
        // sender" intent. Revert the full restore on POST failure.
        if (t.classList.contains("unignore-sender-btn")) {{
          var scard = t.closest(".event-card");
          if (!scard) return;
          var sdomain = scard.getAttribute("data-sender") || "";
          if (!sdomain) return;
          t.disabled = true;
          var restored = [];
          document.querySelectorAll(
            '.event-card[data-sender="' + sdomain + '"]'
          ).forEach(function (card) {{
            if (card.getAttribute("data-ignored") !== "1") return;
            var cid = card.getAttribute("data-event-id");
            var creason = card.getAttribute("data-ignored-reason") || "event";
            setActive(card);
            restored.push({{ card: card, id: cid, reason: creason }});
          }});
          var restoredEventIds = restored
            .filter(function (r) {{ return r.reason === "event"; }})
            .map(function (r) {{ return r.id; }});
          if (restoredEventIds.length) {{
            var remainingIds = loadIgnored().filter(function (x) {{
              return restoredEventIds.indexOf(x) === -1;
            }});
            saveIgnored(remainingIds);
          }}
          var remainingDomains = loadIgnoredSenders().filter(function (d) {{
            return d !== sdomain;
          }});
          saveIgnoredSenders(remainingDomains);
          if (restored.length) bumpToggle(-restored.length);
          postAction({{ action: "unignore_sender", domain: sdomain }}).catch(function () {{
            restored.forEach(function (r) {{ setIgnored(r.card, r.reason); }});
            if (restoredEventIds.length) {{
              var currentIds = loadIgnored();
              restoredEventIds.forEach(function (id) {{
                if (currentIds.indexOf(id) === -1) currentIds.push(id);
              }});
              saveIgnored(currentIds);
            }}
            var currentDomains = loadIgnoredSenders();
            if (currentDomains.indexOf(sdomain) === -1) currentDomains.push(sdomain);
            saveIgnoredSenders(currentDomains);
            if (restored.length) bumpToggle(restored.length);
            t.disabled = false;
            showToast("Unignore failed — try again");
          }});
          return;
        }}
      }});
    }})();
  </script>
</body>
</html>
"""


# ── Weekly Gmail digest ─────────────────────────────────────────


def digest_subject(today: dt.date) -> str:
    """Subject line for the weekly digest, anchored on this week's Monday."""
    return f"Kids' Schedule — Week of {week_start(today).strftime('%B %-d')}"


def _digest_this_week(weeks: list[tuple[dt.date, list[dict[str, Any]]]],
                      today: dt.date) -> list[dict[str, Any]]:
    """Events whose week_start matches today's week_start (Mon–Sun bucket)."""
    target = week_start(today)
    for w, evs in weeks:
        if w == target:
            return evs
    return []


def _event_noun(count: int) -> str:
    """Return "event" or "events" to match the count."""
    return "event" if count == 1 else "events"


def _render_outlier_block_text(alerts: list[dict[str, Any]]) -> list[str]:
    """Plain-text lines for the weekly digest under-extraction block.

    Returns an empty list when there are no alerts so the caller can
    `lines.extend(block)` unconditionally. Each alert renders as one
    bullet line; a trailing hint line points at `--reextract`. The
    message ID is included verbatim so the human operator can paste it
    into the re-extract invocation.
    """
    if not alerts:
        return []
    block: list[str] = ["⚠️ Possible under-extraction:"]
    for a in alerts:
        sender = a.get("sender", "")
        message_id = a.get("message_id", "")
        current = a.get("current_count", 0)
        prior = a.get("prior_median", 0)
        threshold = a.get("threshold", 0)
        block.append(
            f"  - {sender}: message {message_id} — "
            f"{current} {_event_noun(current)}, "
            f"prior median {prior}, threshold {threshold}"
        )
    block.append(
        "  (to re-run an under-extracted message: "
        "python main.py --reextract <MESSAGE_ID>)"
    )
    block.append("")
    return block


def _render_outlier_block_html(alerts: list[dict[str, Any]]) -> list[str]:
    """HTML lines for the weekly digest under-extraction block.

    Returns an empty list when there are no alerts. Amber left-border
    box echoes the ⚠️ glyph used in the text block. All alert fields
    are HTML-escaped because `sender` and `message_id` originate from
    untrusted Gmail headers.
    """
    if not alerts:
        return []
    block: list[str] = []
    block.append(
        '<div style="background-color:#fff8e1; '
        'border-left:4px solid #f9a825; padding:10px 12px; '
        'margin:0 0 12px 0;">'
    )
    block.append(
        '<strong>⚠️ Possible under-extraction</strong>'
    )
    block.append(
        '<ul style="margin:6px 0 0 0; padding-left:1.2em;">'
    )
    for a in alerts:
        sender = _html.escape(str(a.get("sender", "")))
        message_id = _html.escape(str(a.get("message_id", "")))
        current = a.get("current_count", 0)
        prior = a.get("prior_median", 0)
        threshold = a.get("threshold", 0)
        block.append(
            f'<li style="margin:0 0 4px 0;">'
            f'<code>{sender}</code> · message <code>{message_id}</code> — '
            f'{current} {_event_noun(current)}, '
            f'prior median {prior}, threshold {threshold}</li>'
        )
    block.append('</ul>')
    block.append('</div>')
    return block


def render_digest_text(weeks: list[tuple[dt.date, list[dict[str, Any]]]],
                       today: dt.date,
                       pages_url: str = "",
                       alerts: list[dict[str, Any]] | None = None) -> str:
    """Plain-text Gmail digest body."""
    evs = _digest_this_week(weeks, today)
    lines: list[str] = [digest_subject(today), ""]
    lines.extend(_render_outlier_block_text(alerts or []))
    if not evs:
        lines.append("No events this week.")
    else:
        for ev in evs:
            d: dt.date = ev["_date_obj"]
            day = d.strftime("%A, %B %-d")
            lines.append(f"{day} — {ev['name']} · {ev['time']}")
    lines.append("")
    if pages_url:
        lines.append(f"Full 60-day view: {pages_url}")
    return "\n".join(lines).rstrip() + "\n"


def render_digest_html(weeks: list[tuple[dt.date, list[dict[str, Any]]]],
                       today: dt.date,
                       pages_url: str = "",
                       alerts: list[dict[str, Any]] | None = None) -> str:
    """HTML Gmail digest body. Event names/times are HTML-escaped."""
    evs = _digest_this_week(weeks, today)
    parts: list[str] = []
    parts.append(
        '<div style="font-family: -apple-system, BlinkMacSystemFont, '
        '\'Segoe UI\', Roboto, sans-serif; max-width: 560px; color: #202124;">'
    )
    parts.append(
        f'<h2 style="margin:0 0 12px 0;">{_html.escape(digest_subject(today))}</h2>'
    )
    parts.extend(_render_outlier_block_html(alerts or []))
    if not evs:
        parts.append('<p>No events this week.</p>')
    else:
        parts.append('<ul style="padding-left: 1.2em; margin: 0 0 12px 0;">')
        for ev in evs:
            d: dt.date = ev["_date_obj"]
            day = _html.escape(d.strftime("%A, %B %-d"))
            name = _html.escape(ev["name"])
            time = _html.escape(ev["time"])
            parts.append(
                f'<li style="margin: 0 0 6px 0;">'
                f'<strong>{day}</strong> — {name} '
                f'<span style="color:#5f6368;">· {time}</span></li>'
            )
        parts.append('</ul>')
    if pages_url:
        parts.append(
            f'<p><a href="{_html.escape(pages_url, quote=True)}" '
            'style="color:#1a73e8;">View the full 60-day schedule</a></p>'
        )
    parts.append('</div>')
    return "\n".join(parts) + "\n"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--candidates", required=True,
                   help="Path to candidate events JSON file.")
    p.add_argument("--today", default=None, help="Override today (YYYY-MM-DD).")
    p.add_argument("--lookback-days", type=int, default=60)
    p.add_argument("--display-window-days", type=int, default=60,
                   help="Only show events within this many days from today. "
                        "Events beyond this are excluded from the render; "
                        "main.py keeps them in events_state.json for later.")
    p.add_argument("--body-out", default=None,
                   help="Write rendered body here (default: stdout).")
    p.add_argument("--html-out", default=None,
                   help="Write rendered HTML page here for GitHub Pages.")
    p.add_argument("--meta-out", default=None,
                   help="Write JSON metadata (subject, counts, warnings) here.")
    p.add_argument("--webhook-url", default="",
                   help="Ignore-button webhook URL baked into the HTML. "
                        "Leave empty to disable backend sync (dev preview).")
    p.add_argument("--ignored", default=None,
                   help="JSON file of previously-ignored events. Events "
                        "whose ID matches an entry here are dropped before "
                        "classifying. Missing/malformed file → no filter.")
    p.add_argument("--digest-html-out", default=None,
                   help="Write weekly Gmail digest HTML body here.")
    p.add_argument("--digest-text-out", default=None,
                   help="Write weekly Gmail digest plain-text body here.")
    p.add_argument("--pages-url", default="",
                   help="GitHub Pages URL to link from the weekly digest.")
    p.add_argument("--ics-out-dir", default="",
                   help="If set, wipe the directory and write one .ics per "
                        "displayed event ({event_id}.ics). The rendered "
                        "page links to webcal://<pages-host>/ics/<id>.ics.")
    p.add_argument("--protected-senders", default="",
                   help="Path to protected_senders.txt. Events whose sender "
                        "domain matches one of these patterns render without "
                        "the Ignore-sender button. Missing file → empty list.")
    p.add_argument("--prior-events", default="",
                   help="Path to prior_events.json (persisted across runs). "
                        "When set, events whose IDs are absent from the prior "
                        "manifest render with a NEW badge inline with the "
                        "title; the manifest is overwritten with the current "
                        "render set after HTML is written. Missing/malformed "
                        "file → no badges (first-run graceful degradation).")
    p.add_argument("--outlier-alerts", default="",
                   help="Path to a JSON list of outlier-alert dicts produced "
                        "by newsletter_stats.outlier_alerts. When non-empty, "
                        "an ⚠️ Possible under-extraction section renders at "
                        "the top of the weekly digest (text + HTML). "
                        "Missing/malformed file → no block.")
    args = p.parse_args()

    today = (dt.date.fromisoformat(args.today) if args.today
             else dt.date.today())
    horizon = today + dt.timedelta(days=args.display_window_days)
    raw = load_candidates(args.candidates)
    ignored_ids = _load_ignored_ids(args.ignored)
    display, undated, past, banked, ignored_dropped, warnings = classify(
        raw, today, horizon, ignored_ids=ignored_ids
    )
    display = dedupe(display)
    undated = dedupe(undated)
    banked = dedupe(banked)
    weeks = group_by_week(display)
    body = render_body(today, weeks, undated, len(display), args.lookback_days)

    if args.body_out:
        with open(args.body_out, "w", encoding="utf-8") as f:
            f.write(body)
    else:
        sys.stdout.write(body)

    # #13 NEW-badge diff: compute current render IDs, load the prior
    # manifest, and pass the delta into render_html. Missing manifest
    # (`prior is None`) suppresses all badges — the first-run graceful
    # degradation path.
    current_ids: set[str] = (
        {ev["id"] for ev in display} | {ev["id"] for ev in undated}
    )
    new_ids: set[str] | None = None
    if args.prior_events:
        prior = _load_prior_event_ids(args.prior_events)
        new_ids = (current_ids - prior) if prior is not None else set()

    if args.html_out:
        protected = (
            load_protected_senders(args.protected_senders)
            if args.protected_senders else []
        )
        html = render_html(today, weeks, undated, len(display),
                           args.lookback_days, webhook_url=args.webhook_url,
                           pages_url=args.pages_url,
                           protected_senders=protected,
                           new_ids=new_ids)
        with open(args.html_out, "w", encoding="utf-8") as f:
            f.write(html)
        # Only overwrite prior_events.json after a successful HTML write.
        # Dev-only runs (no --html-out) must not advance the baseline, and
        # a render failure above shouldn't poison next week's diff.
        if args.prior_events:
            now_iso = dt.datetime.now(dt.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            _save_prior_event_ids(args.prior_events, current_ids, now_iso)

    if args.ics_out_dir:
        count = write_ics_files(display, args.ics_out_dir)
        print(f"Wrote {count} .ics files to {args.ics_out_dir}", file=sys.stderr)

    outlier_alerts = _load_outlier_alerts(args.outlier_alerts)
    digest_text = render_digest_text(
        weeks, today, pages_url=args.pages_url, alerts=outlier_alerts
    )
    digest_html = render_digest_html(
        weeks, today, pages_url=args.pages_url, alerts=outlier_alerts
    )
    this_week_count = len(_digest_this_week(weeks, today))

    if args.digest_text_out:
        with open(args.digest_text_out, "w", encoding="utf-8") as f:
            f.write(digest_text)
    if args.digest_html_out:
        with open(args.digest_html_out, "w", encoding="utf-8") as f:
            f.write(digest_html)

    meta = {
        "subject": f"Kids' Schedule — {today.strftime('%B %-d, %Y')}",
        "today_iso": today.isoformat(),
        "counts": {
            "candidates_in": len(raw),
            "future_dated": len(display),
            "undated": len(undated),
            "dropped_past": len(past),
            "banked_far_future": len(banked),
            "dropped_ignored": len(ignored_dropped),
        },
        "warnings": warnings,
        "has_events": bool(display or undated),
        "digest": {
            "subject": digest_subject(today),
            "this_week_count": this_week_count,
        },
    }
    if args.meta_out:
        with open(args.meta_out, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
    else:
        sys.stderr.write(json.dumps(meta, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
