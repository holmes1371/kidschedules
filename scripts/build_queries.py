#!/usr/bin/env python3
"""Emit date windows and the 5 Gmail query strings for the pipeline.

Deterministic. No judgment. Output is a single JSON blob to stdout.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BLOCKLIST = os.path.join(_PROJECT_ROOT, "blocklist.txt")
DEFAULT_AUDIT_STATE = os.path.join(_PROJECT_ROOT, ".filter_audit.json")


def load_audit_state(path: str, today: dt.date) -> dict:
    """Return audit status: last_verified, days_since, threshold, due."""
    default = {
        "last_verified_iso": None,
        "threshold_days": 30,
        "days_since": None,
        "due": True,
        "reason": "no audit state file found",
    }
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return {**default, "reason": f"audit state unreadable: {e}"}
    threshold = int(data.get("threshold_days", 30))
    last_iso = data.get("last_verified_iso")
    if not last_iso:
        return {**default, "threshold_days": threshold,
                "reason": "last_verified_iso missing"}
    try:
        last = dt.date.fromisoformat(last_iso)
    except ValueError:
        return {**default, "threshold_days": threshold,
                "reason": f"invalid last_verified_iso: {last_iso!r}"}
    days_since = (today - last).days
    due = days_since >= threshold
    return {
        "last_verified_iso": last_iso,
        "threshold_days": threshold,
        "days_since": days_since,
        "due": due,
        "reason": ("stale: %d days since last verification" % days_since
                   if due else "fresh"),
    }


def load_blocklist(path: str) -> list[str]:
    """Return a list of sender patterns (addresses or domains), deduped."""
    if not os.path.exists(path):
        return []
    out: list[str] = []
    seen: set[str] = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line not in seen:
                seen.add(line)
                out.append(line)
    return out


def build_exclusion_clause(blocklist: list[str]) -> str:
    """Build the `-category:promotions -from:...` clause appended to each query."""
    bits = ["-category:promotions"]
    for sender in blocklist:
        bits.append(f"-from:{sender}")
    return " ".join(bits)


SEARCH_TEMPLATES = {
    "school_activities": (
        '(field trip OR "picture day" OR "spirit day" OR assembly OR '
        '"parent teacher" OR "open house" OR "school event" OR fundraiser '
        'OR "book fair" OR "report card")'
    ),
    "appointments": (
        "(appointment OR doctor OR dentist OR orthodontist OR pediatrician "
        "OR therapy OR physical OR checkup)"
    ),
    "sports_extracurriculars": (
        '(practice OR game OR match OR tournament OR recital OR rehearsal '
        'OR "club meeting" OR tryout OR "game day" OR scrimmage OR ballet '
        'OR dance OR swim OR gymnastics OR karate)'
    ),
    "academic_due_dates": (
        '("due date" OR "due by" OR "permission slip" OR "homework due" '
        'OR "project due" OR "forms due" OR "fees due" OR deadline)'
    ),
    "newsletters_calendars": (
        "(from:school OR from:district OR from:pta OR from:ptsa "
        "OR subject:calendar OR subject:newsletter OR subject:reminder "
        "OR subject:upcoming OR subject:schedule)"
    ),
}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--lookback-days", type=int, default=60,
                   help="How many days of received email to search.")
    p.add_argument("--today", type=str, default=None,
                   help="Override today (YYYY-MM-DD). Default: system date.")
    p.add_argument("--blocklist", type=str, default=DEFAULT_BLOCKLIST,
                   help="Path to sender blocklist file. Pass '' to disable.")
    p.add_argument("--no-category-filter", action="store_true",
                   help="Do not append -category:promotions to queries.")
    p.add_argument("--audit-state", type=str, default=DEFAULT_AUDIT_STATE,
                   help="Path to filter audit state file.")
    args = p.parse_args()

    today = (dt.date.fromisoformat(args.today) if args.today
             else dt.date.today())
    start = today - dt.timedelta(days=args.lookback_days)

    gmail_fmt = "%Y/%m/%d"
    after = start.strftime(gmail_fmt)
    before = today.strftime(gmail_fmt)

    blocklist = load_blocklist(args.blocklist) if args.blocklist else []
    exclusion = build_exclusion_clause(blocklist)
    if args.no_category_filter:
        exclusion = " ".join(
            tok for tok in exclusion.split()
            if tok != "-category:promotions"
        )

    def assemble(body: str) -> str:
        parts = [f"after:{after}", f"before:{before}", body]
        if exclusion:
            parts.append(exclusion)
        return " ".join(parts)

    queries = {name: assemble(body) for name, body in SEARCH_TEMPLATES.items()}

    def assemble_loose(body: str) -> str:
        return f"after:{after} before:{before} {body}"

    loose_queries = {name: assemble_loose(body)
                     for name, body in SEARCH_TEMPLATES.items()}

    audit = load_audit_state(args.audit_state, today)

    out = {
        "today_iso": today.isoformat(),
        "today_human": today.strftime("%B %d, %Y"),
        "email_window": {
            "after": after,
            "before": before,
            "lookback_days": args.lookback_days,
        },
        "event_cutoff_iso": today.isoformat(),
        "queries": queries,
        "max_results_per_query": 25,
        "exclusions": {
            "category_promotions": not args.no_category_filter,
            "blocklist_path": args.blocklist if args.blocklist else None,
            "blocklist_size": len(blocklist),
        },
        "filter_audit": audit,
        "loose_queries": loose_queries,
    }
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
