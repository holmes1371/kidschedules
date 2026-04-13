#!/usr/bin/env python3
"""Diff two sets of Gmail search results for the filter audit.

Inputs: two JSON files. Each file is a dict keyed by category name
(school_activities, appointments, etc.). Each value is the raw
`gmail_search_messages` response (or at minimum a dict with a `messages`
list, each message having `messageId`, `headers.From`, `headers.Subject`,
`snippet`).

Output: a JSON report listing, per category, the messages present in the
loose result set but NOT in the tight result set. These are the messages
the filter stripped out. The agent reads this report and judges (a) which
of them look like legitimate kids' events that were lost, and (b) which
are correctly filtered noise. The agent does NOT compute this diff itself.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _messages(payload: Any) -> list[dict]:
    if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
        return payload["messages"]
    if isinstance(payload, list):
        return payload
    return []


def _summarize(m: dict) -> dict:
    h = m.get("headers") or {}
    return {
        "messageId": m.get("messageId"),
        "from": h.get("From", ""),
        "subject": h.get("Subject", ""),
        "date": h.get("Date", ""),
        "snippet": (m.get("snippet") or "")[:200],
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--loose", required=True,
                   help="JSON file of loose (unfiltered) search results.")
    p.add_argument("--tight", required=True,
                   help="JSON file of tight (filtered) search results.")
    p.add_argument("--out", default=None,
                   help="Write report JSON here (default: stdout).")
    args = p.parse_args()

    with open(args.loose, "r", encoding="utf-8") as f:
        loose = json.load(f)
    with open(args.tight, "r", encoding="utf-8") as f:
        tight = json.load(f)

    categories = sorted(set(loose.keys()) | set(tight.keys()))
    report: dict[str, Any] = {
        "categories": {},
        "totals": {
            "loose": 0,
            "tight": 0,
            "stripped": 0,
        },
    }

    for cat in categories:
        loose_msgs = _messages(loose.get(cat, {}))
        tight_msgs = _messages(tight.get(cat, {}))
        tight_ids = {m.get("messageId") for m in tight_msgs}
        stripped = [m for m in loose_msgs
                    if m.get("messageId") not in tight_ids]
        report["categories"][cat] = {
            "loose_count": len(loose_msgs),
            "tight_count": len(tight_msgs),
            "stripped_count": len(stripped),
            "stripped_messages": [_summarize(m) for m in stripped],
        }
        report["totals"]["loose"] += len(loose_msgs)
        report["totals"]["tight"] += len(tight_msgs)
        report["totals"]["stripped"] += len(stripped)

    text = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
