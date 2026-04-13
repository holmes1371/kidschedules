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
import json
import sys
from collections import OrderedDict
from typing import Any


VALID_CATEGORIES = {
    "School Activity",
    "Appointment",
    "Academic Due Date",
    "Sports & Extracurriculars",
}


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _parse_date(s: str) -> dt.date | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def load_candidates(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "events" in data:
        data = data["events"]
    if not isinstance(data, list):
        raise SystemExit("candidates file must be a JSON list or {events: [...]}")
    return data


def classify(events: list[dict[str, Any]], cutoff: dt.date
             ) -> tuple[list[dict[str, Any]], list[dict[str, Any]],
                        list[dict[str, Any]], list[str]]:
    """Return (future_dated, undated, dropped_past, warnings)."""
    future: list[dict[str, Any]] = []
    undated: list[dict[str, Any]] = []
    past: list[dict[str, Any]] = []
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
        }
        d = _parse_date(norm["date"])
        if d is None:
            undated.append(norm)
        elif d < cutoff:
            past.append(norm)
        else:
            norm["_date_obj"] = d
            future.append(norm)
    return future, undated, past, warnings


def dedupe(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Key = (normalized name, date). Keep the most complete entry."""
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

    best: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
    for ev in events:
        key = (_norm(ev["name"]), ev.get("date", ""))
        if key not in best or completeness(ev) > completeness(best[key]):
            best[key] = ev
    return list(best.values())


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
                lookback_days: int) -> str:
    """Render a complete, self-contained HTML page for GitHub Pages."""

    def _event_card(ev: dict[str, Any]) -> str:
        d: dt.date = ev["_date_obj"]
        cat = ev["category"]
        fg, bg = CATEGORY_COLORS.get(cat, CATEGORY_COLORS["Uncategorized"])
        day_name = d.strftime("%A")
        month_day = d.strftime("%B %-d")
        child_html = (f'<span class="child">{ev["child"]}</span> &middot; '
                      if ev["child"] else "")
        return f"""\
      <div class="event-card" style="border-left: 4px solid {fg};">
        <div class="event-date">{day_name}, {month_day}</div>
        <div class="event-name">{ev["name"]}</div>
        <div class="event-details">
          <span class="badge" style="background:{bg};color:{fg};">{cat}</span>
          <span class="time">{ev["time"]}</span>
          &middot; <span class="location">{ev["location"]}</span>
        </div>
        <div class="event-meta">{child_html}<span class="source">{ev["source"]}</span></div>
      </div>"""

    def _undated_card(ev: dict[str, Any]) -> str:
        cat = ev["category"]
        fg, bg = CATEGORY_COLORS.get(cat, CATEGORY_COLORS["Uncategorized"])
        child_html = (f'<span class="child">{ev["child"]}</span> &middot; '
                      if ev["child"] else "")
        time_html = f' &middot; <span class="time">{ev["time"]}</span>' if ev["time"] != "Time TBD" else ""
        loc_html = f' &middot; <span class="location">{ev["location"]}</span>' if ev["location"] != "Location TBD" else ""
        return f"""\
      <div class="event-card undated" style="border-left: 4px solid #f9ab00;">
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
        weeks_html = """\
    <section class="empty-state">
      <p>No upcoming kids' events were found in the last {lookback_days} days of email.</p>
    </section>"""

    generated = today.strftime("%B %-d, %Y")

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
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #1a1a1a;
        --surface: #2d2d2d;
        --text: #e8eaed;
        --text-secondary: #9aa0a6;
        --border: #3c4043;
        --accent: #8ab4f8;
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
  </div>
  <div class="container">
{weeks_html}
{undated_html}
  </div>
  <div class="footer">
    Auto-generated from Gmail &middot; Updated every Monday
    <br><a href="archive.html" style="color:var(--accent);">View past schedules</a>
  </div>
</body>
</html>
"""


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--candidates", required=True,
                   help="Path to candidate events JSON file.")
    p.add_argument("--today", default=None, help="Override today (YYYY-MM-DD).")
    p.add_argument("--lookback-days", type=int, default=60)
    p.add_argument("--body-out", default=None,
                   help="Write rendered body here (default: stdout).")
    p.add_argument("--html-out", default=None,
                   help="Write rendered HTML page here for GitHub Pages.")
    p.add_argument("--meta-out", default=None,
                   help="Write JSON metadata (subject, counts, warnings) here.")
    args = p.parse_args()

    today = (dt.date.fromisoformat(args.today) if args.today
             else dt.date.today())
    raw = load_candidates(args.candidates)
    future, undated, past, warnings = classify(raw, today)
    future = dedupe(future)
    undated = dedupe(undated)
    weeks = group_by_week(future)
    body = render_body(today, weeks, undated, len(future), args.lookback_days)

    if args.body_out:
        with open(args.body_out, "w", encoding="utf-8") as f:
            f.write(body)
    else:
        sys.stdout.write(body)

    if args.html_out:
        html = render_html(today, weeks, undated, len(future), args.lookback_days)
        with open(args.html_out, "w", encoding="utf-8") as f:
            f.write(html)

    meta = {
        "subject": f"Kids' Schedule — {today.strftime('%B %-d, %Y')}",
        "today_iso": today.isoformat(),
        "counts": {
            "candidates_in": len(raw),
            "future_dated": len(future),
            "undated": len(undated),
            "dropped_past": len(past),
        },
        "warnings": warnings,
        "has_events": bool(future or undated),
    }
    if args.meta_out:
        with open(args.meta_out, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
    else:
        sys.stderr.write(json.dumps(meta, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
