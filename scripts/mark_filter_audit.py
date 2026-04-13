#!/usr/bin/env python3
"""Stamp the filter audit state file with today's date.

Run this after a successful loose-vs-tight filter audit (i.e. the agent
has reviewed the diff report, updated blocklist.txt if needed, and is
satisfied the tight filter isn't dropping legitimate kids' events).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

_SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_AUDIT_STATE = os.path.join(_SKILL_ROOT, ".filter_audit.json")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--audit-state", default=DEFAULT_AUDIT_STATE)
    p.add_argument("--today", default=None,
                   help="Override today (YYYY-MM-DD). Default: system date.")
    p.add_argument("--threshold-days", type=int, default=None,
                   help="Override threshold_days. Default: keep existing.")
    p.add_argument("--note", default=None,
                   help="Optional note appended to the state file.")
    args = p.parse_args()

    today = (dt.date.fromisoformat(args.today) if args.today
             else dt.date.today())

    existing: dict = {}
    if os.path.exists(args.audit_state):
        try:
            with open(args.audit_state, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            existing = {}

    threshold = (args.threshold_days
                 if args.threshold_days is not None
                 else int(existing.get("threshold_days", 30)))

    state = {
        "last_verified_iso": today.isoformat(),
        "threshold_days": threshold,
        "notes": args.note or existing.get(
            "notes",
            "Updated automatically by mark_filter_audit.py."),
    }

    with open(args.audit_state, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")

    sys.stdout.write(
        f"Filter audit stamped: {state['last_verified_iso']} "
        f"(threshold {threshold} days)\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
