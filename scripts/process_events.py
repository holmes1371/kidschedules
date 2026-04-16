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


LOCAL_TZ = ZoneInfo("America/New_York")

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
            "time": (ev.get("time") or "").strip() or "Time TBD",
            "location": (ev.get("location") or "").strip() or "Location TBD",
            "category": cat or "Uncategorized",
            "child": (ev.get("child") or "").strip(),
            "source": (ev.get("source") or "").strip() or "unknown source",
            "sender_domain": (ev.get("sender_domain") or "").strip(),
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
        if ev["time"] != "Time TBD":
            score += 2
        if ev["location"] != "Location TBD":
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
    child_source = []
    if ev["child"]:
        child_source.append(f"Child: {ev['child']}")
    child_source.append(f"Source: {ev['source']}")
    return (
        f"{header}\n"
        f"Time: {ev['time']}\n"
        f"Location: {ev['location']}\n"
        f"Category: {ev['category']}\n"
        f"{' | '.join(child_source)}\n"
    )


def render_undated(ev: dict[str, Any]) -> str:
    bits = [f"- {ev['name']}"]
    if ev["time"] != "Time TBD":
        bits.append(f"  Time: {ev['time']}")
    if ev["location"] != "Location TBD":
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


def render_html(today: dt.date,
                weeks: list[tuple[dt.date, list[dict[str, Any]]]],
                undated: list[dict[str, Any]],
                total_future: int,
                lookback_days: int,
                webhook_url: str = "",
                pages_url: str = "",
                protected_senders: list[str] | None = None) -> str:
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
    """

    webcal_base = _webcal_base(pages_url)
    protected = protected_senders or []

    def _event_card(ev: dict[str, Any]) -> str:
        d: dt.date = ev["_date_obj"]
        cat = ev["category"]
        fg, bg = CATEGORY_COLORS.get(cat, CATEGORY_COLORS["Uncategorized"])
        day_name = d.strftime("%A")
        month_day = d.strftime("%B %-d")
        child_html = (f'<span class="child">{ev["child"]}</span> &middot; '
                      if ev["child"] else "")
        ics_btn_html = ""
        if webcal_base:
            ics_href = f"https://{webcal_base}ics/{ev['id']}.ics"
            ics_btn_html = (
                f'<a class="ics-btn" href="{ics_href}" '
                f'aria-label="Add this event to your calendar">Add to calendar</a>'
            )
        is_ignored = bool(ev.get("is_ignored"))
        ignored_class = " ignored" if is_ignored else ""
        ignored_attr = ' data-ignored="1"' if is_ignored else ""
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
        sender = (ev.get("sender_domain") or "").strip()
        sender_attr = f' data-sender="{sender}"' if sender else ""
        sender_btn_html = ""
        # Never render the Ignore-sender button for protected domains
        # (schools, PTAs, health providers, team-management platforms) —
        # blocking those at the Gmail-query level would drop real events.
        # The Ignore-event button on the same card is unaffected.
        if sender and not is_protected(sender, protected):
            sender_btn_html = (
                '\n        <div class="event-actions-bottom">\n'
                f'          <button class="ignore-sender-btn" '
                f'aria-label="Ignore future events from this sender"\n'
                f'                  data-sender="{sender}" type="button">'
                f'Ignore sender ({sender})</button>\n'
                '        </div>'
            )
        return f"""\
      <div class="event-card{ignored_class}" data-event-id="{ev["id"]}"{ignored_attr}{sender_attr}
           style="{card_style}">
        <div class="event-actions-top">{ics_btn_html}{ignore_btn_html}</div>
        <div class="event-date">{day_name}, {month_day}</div>
        <div class="event-name">{ev["name"]}</div>
        <div class="event-details">
          <span class="badge" style="background:{bg};color:{fg};">{cat}</span>
          <span class="time">{ev["time"]}</span>
          &middot; <span class="location">{ev["location"]}</span>
        </div>
        <div class="event-meta">{child_html}<span class="source">{ev["source"]}</span></div>
        <div class="ignore-status" aria-live="polite"></div>{sender_btn_html}
      </div>"""

    def _undated_card(ev: dict[str, Any]) -> str:
        cat = ev["category"]
        fg, bg = CATEGORY_COLORS.get(cat, CATEGORY_COLORS["Uncategorized"])
        child_html = (f'<span class="child">{ev["child"]}</span> &middot; '
                      if ev["child"] else "")
        time_html = f' &middot; <span class="time">{ev["time"]}</span>' if ev["time"] != "Time TBD" else ""
        loc_html = f' &middot; <span class="location">{ev["location"]}</span>' if ev["location"] != "Location TBD" else ""
        # Undated cards don't carry Ignore/Unignore affordances (pre-existing
        # behavior — there's no UI path to ignore an undated event). If one
        # arrives with is_ignored=True via a stale ignored_events.json entry
        # we still honor it as a hide so the event doesn't leak through.
        is_ignored = bool(ev.get("is_ignored"))
        ignored_class = " ignored" if is_ignored else ""
        ignored_attr = ' data-ignored="1"' if is_ignored else ""
        card_style = ("display:none; border-left: 4px solid #f9ab00;" if is_ignored
                      else "border-left: 4px solid #f9ab00;")
        return f"""\
      <div class="event-card undated{ignored_class}"{ignored_attr} style="{card_style}">
        <div class="event-date">Date TBD</div>
        <div class="event-name">{ev["name"]}</div>
        <div class="event-details">
          <span class="badge" style="background:{bg};color:{fg};">{cat}</span>{time_html}{loc_html}
        </div>
        <div class="event-meta">{child_html}<span class="source">{ev["source"]}</span></div>
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

    # Show-ignored toggle: rendered server-side only when there's at least one
    # ignored event in the display buckets, so the header stays clean when
    # nothing is hidden. Count follows the design note — display only, not
    # undated.
    ignored_n = sum(
        1 for wk in weeks for ev in wk[1] if ev.get("is_ignored")
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
      --border: #e0e0e0;
      --accent: #1a73e8;
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
    .ignore-btn, .unignore-btn {{
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
    .unignore-btn {{
      background: #0d652d;
      color: #ceead6;
      border: 1px solid #0d652d;
    }}
    .unignore-btn:hover {{
      filter: brightness(1.15);
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
    .event-date {{
      font-size: 0.8rem;
      color: var(--text-secondary);
      font-weight: 500;
    }}
    .event-name {{
      font-size: 1.05rem;
      font-weight: 600;
      margin: 0.15rem 0 0.35rem;
    }}
    .event-details {{
      font-size: 0.85rem;
      color: var(--text-secondary);
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.35rem;
    }}
    .badge {{
      display: inline-block;
      font-size: 0.7rem;
      font-weight: 600;
      padding: 0.1rem 0.5rem;
      border-radius: 10px;
      text-transform: uppercase;
      letter-spacing: 0.3px;
    }}
    .event-meta {{
      font-size: 0.78rem;
      color: var(--text-secondary);
      margin-top: 0.3rem;
      opacity: 0.75;
    }}
    .child {{ font-weight: 500; }}
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
        --border: #3c4043;
        --accent: #8ab4f8;
      }}
      .unignore-btn {{
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
  <div class="container">
{weeks_html}
{undated_html}
  </div>
  <div class="footer">
    Auto-generated from Gmail &middot; Updated every Monday
    <br><a href="archive.html" style="color:var(--accent);">View past schedules</a>
  </div>
  <script>
    (function () {{
      var WEBHOOK_URL = {json.dumps(webhook_url)};
      var STORAGE_KEY = "kids_schedule_ignored_ids";

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
      function setIgnored(card) {{
        card.classList.add("ignored");
        card.style.display = "none";
        card.setAttribute("data-ignored", "1");
        var btn = card.querySelector(".ignore-btn");
        if (btn) {{
          btn.className = "unignore-btn";
          btn.textContent = "Unignore event";
          btn.setAttribute("aria-label", "Unignore this event");
          btn.disabled = false;
        }}
      }}
      function setActive(card) {{
        card.classList.remove("ignored");
        card.style.display = "";
        card.removeAttribute("data-ignored");
        var btn = card.querySelector(".unignore-btn");
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
        var m = showLabel.match(/\((\d+)\)/);
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
      // For each locally-stored id, hide the matching card AND swap its
      // button to Unignore so the Show-ignored toggle + Unignore flow work
      // before the next workflow run picks up the server-side list.
      var localIds = loadIgnored();
      document.querySelectorAll(".event-card[data-event-id]").forEach(function (card) {{
        if (localIds.indexOf(card.getAttribute("data-event-id")) === -1) return;
        if (card.getAttribute("data-ignored") === "1") return;
        setIgnored(card);
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

        // Ignore event — optimistic hide, restore on failure.
        if (t.classList.contains("ignore-btn")) {{
          var card = t.closest(".event-card");
          if (!card) return;
          var id = card.getAttribute("data-event-id");
          var name = t.getAttribute("data-event-name") || "";
          var date = t.getAttribute("data-event-date") || "";
          t.disabled = true;
          var current = loadIgnored();
          if (current.indexOf(id) === -1) current.push(id);
          saveIgnored(current);
          card.classList.add("fading");
          setTimeout(function () {{
            setIgnored(card);
            card.classList.remove("fading");
          }}, 300);
          postAction({{ action: "ignore", id: id, name: name, date: date }}).catch(function () {{
            setActive(card);
            var remaining = loadIgnored().filter(function (x) {{ return x !== id; }});
            saveIgnored(remaining);
            showToast("Ignore failed — try again");
          }});
          return;
        }}

        // Unignore event — pessimistic, apply only after 2xx.
        if (t.classList.contains("unignore-btn")) {{
          var ucard = t.closest(".event-card");
          if (!ucard) return;
          var uid = ucard.getAttribute("data-event-id");
          t.disabled = true;
          postAction({{ action: "unignore", id: uid }}).then(function () {{
            setActive(ucard);
            var remaining = loadIgnored().filter(function (x) {{ return x !== uid; }});
            saveIgnored(remaining);
            bumpToggle(-1);
          }}).catch(function () {{
            t.disabled = false;
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
        // same domain locally, persist their IDs to the shared ignored-events
        // store, and bump the counter by the number newly hidden. Revert the
        // whole sweep on POST failure. No fade (staggered fades across many
        // cards read as jank; the toast covers the "something happened" cue).
        if (t.classList.contains("ignore-sender-btn")) {{
          var domain = t.getAttribute("data-sender") || "";
          if (!domain) return;
          t.disabled = true;
          var siblings = document.querySelectorAll(
            '.event-card[data-sender="' + domain + '"]'
          );
          var current = loadIgnored();
          var swept = [];
          siblings.forEach(function (card) {{
            if (card.getAttribute("data-ignored") === "1") return;
            var sid = card.getAttribute("data-event-id");
            if (!sid) return;
            if (current.indexOf(sid) === -1) current.push(sid);
            setIgnored(card);
            swept.push({{ id: sid, card: card }});
          }});
          saveIgnored(current);
          if (swept.length) bumpToggle(swept.length);
          postAction({{ action: "ignore_sender", domain: domain }}).then(function () {{
            showToast("Ignoring " + domain + ". New events will stop appearing after the next refresh.");
          }}).catch(function () {{
            swept.forEach(function (s) {{ setActive(s.card); }});
            var sweptIds = swept.map(function (s) {{ return s.id; }});
            var remaining = loadIgnored().filter(function (x) {{
              return sweptIds.indexOf(x) === -1;
            }});
            saveIgnored(remaining);
            if (swept.length) bumpToggle(-swept.length);
            t.disabled = false;
            showToast("Ignore failed — try again");
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


def render_digest_text(weeks: list[tuple[dt.date, list[dict[str, Any]]]],
                       today: dt.date,
                       pages_url: str = "") -> str:
    """Plain-text Gmail digest body."""
    evs = _digest_this_week(weeks, today)
    lines: list[str] = [digest_subject(today), ""]
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
                       pages_url: str = "") -> str:
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

    if args.html_out:
        protected = (
            load_protected_senders(args.protected_senders)
            if args.protected_senders else []
        )
        html = render_html(today, weeks, undated, len(display),
                           args.lookback_days, webhook_url=args.webhook_url,
                           pages_url=args.pages_url,
                           protected_senders=protected)
        with open(args.html_out, "w", encoding="utf-8") as f:
            f.write(html)

    if args.ics_out_dir:
        count = write_ics_files(display, args.ics_out_dir)
        print(f"Wrote {count} .ics files to {args.ics_out_dir}", file=sys.stderr)

    digest_text = render_digest_text(weeks, today, pages_url=args.pages_url)
    digest_html = render_digest_html(weeks, today, pages_url=args.pages_url)
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
