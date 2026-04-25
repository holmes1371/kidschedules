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

from protected_senders import is_protected, load_protected_senders
from roster_match import load_roster


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BLOCKLIST = os.path.join(_PROJECT_ROOT, "blocklist.txt")
DEFAULT_AUTO_BLOCKLIST = os.path.join(_PROJECT_ROOT, "blocklist_auto.txt")
DEFAULT_IGNORED_SENDERS = os.path.join(_PROJECT_ROOT, "ignored_senders.json")
DEFAULT_PROTECTED_SENDERS = os.path.join(_PROJECT_ROOT, "protected_senders.txt")
DEFAULT_AUDIT_STATE = os.path.join(_PROJECT_ROOT, ".filter_audit.json")
DEFAULT_ROSTER = os.path.join(_PROJECT_ROOT, "class_roster.json")


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
    """Return a list of sender patterns (addresses or domains), deduped.

    Strips inline ``# ...`` comments so auto-added entries
    (``addr  # auto YYYY-MM-DD: reason``) don't leak their comment into the
    Gmail query.
    """
    if not os.path.exists(path):
        return []
    out: list[str] = []
    seen: set[str] = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Strip inline comments; auto-added entries carry "# auto ...".
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if line not in seen:
                seen.add(line)
                out.append(line)
    return out


def load_ignored_senders(path: str) -> list[str]:
    """Return the block-identifier strings from the ephemeral
    ignored_senders.json cache.

    The file is produced by scripts/sync_ignored_senders.py and is a JSON
    list of ``{"domain": ..., "source": ..., "timestamp": ...}`` dicts.
    Historically the ``domain`` field always held a bare registrable
    domain; since ROADMAP #20 it may also hold a lowercased email
    address (``alice@gmail.com``) for freemail senders. The payload key
    stays ``domain`` for wire-protocol backward compatibility — treat
    the returned strings as opaque block identifiers.

    Gmail's ``from:`` operator accepts both shapes, so the exclusion
    clause works unchanged regardless of which shape the row carries.

    Missing file, malformed JSON, or a non-list payload all degrade
    silently to ``[]`` — matches the posture of the sync helper, which
    keeps the on-disk cache rather than zeroing it out on fetch failure.
    """
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        domain = row.get("domain")
        if not isinstance(domain, str):
            continue
        domain = domain.strip()
        if domain:
            out.append(domain)
    return out


def build_exclusion_clause(blocklist: list[str]) -> str:
    """Build the `-category:promotions -from:...` clause appended to each query."""
    bits = ["-category:promotions"]
    for sender in blocklist:
        bits.append(f"-from:{sender}")
    return " ".join(bits)


def build_kid_names_query(roster: dict) -> str | None:
    """Return the OR-joined kid-names query body, or None for empty roster.

    Names with embedded whitespace are double-quoted so Gmail treats them
    as a single token. Names with no whitespace pass through unquoted —
    Gmail search is case-insensitive, so the roster's casing is cosmetic.
    Empty / whitespace-only keys are dropped.

    An empty roster yields ``None`` rather than ``"()"`` — Gmail's parser
    rejects an empty parenthetical, so the caller is expected to skip
    emitting a kid_names query in that case.
    """
    names = [n.strip() for n in roster.keys() if isinstance(n, str) and n.strip()]
    if not names:
        return None
    quoted = [f'"{n}"' if any(c.isspace() for c in n) else n for n in names]
    return "(" + " OR ".join(quoted) + ")"


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
                   help="Path to the hand-curated blocklist. Pass '' to disable.")
    p.add_argument("--auto-blocklist", type=str, default=DEFAULT_AUTO_BLOCKLIST,
                   help="Path to the bot-owned blocklist_auto.txt. Pass '' to disable.")
    p.add_argument("--ignored-senders", type=str, default=DEFAULT_IGNORED_SENDERS,
                   help="Path to ignored_senders.json (runner-ephemeral cache "
                        "of UI-clicked Ignore-sender block keys — bare "
                        "domains for institutional senders, full addresses "
                        "for freemail). Pass '' to disable.")
    p.add_argument("--protected-senders", type=str,
                   default=DEFAULT_PROTECTED_SENDERS,
                   help="Path to protected_senders.txt. Protected domains are "
                        "filtered OUT of the ignored_senders union as a "
                        "defense-in-depth guardrail; the filter matches on "
                        "both bare-domain and address-form block keys. Pass "
                        "'' to disable.")
    p.add_argument("--no-category-filter", action="store_true",
                   help="Do not append -category:promotions to queries.")
    p.add_argument("--audit-state", type=str, default=DEFAULT_AUDIT_STATE,
                   help="Path to filter audit state file.")
    p.add_argument("--roster", type=str, default=DEFAULT_ROSTER,
                   help="Path to class_roster.json. Roster keys (kid first "
                        "names) drive the kid_names query template. Pass "
                        "'' to disable.")
    p.add_argument("--no-kid-names", action="store_true",
                   help="Do not emit the kid_names query template.")
    args = p.parse_args()

    today = (dt.date.fromisoformat(args.today) if args.today
             else dt.date.today())
    start = today - dt.timedelta(days=args.lookback_days)

    gmail_fmt = "%Y/%m/%d"
    after = start.strftime(gmail_fmt)
    before = today.strftime(gmail_fmt)

    blocklist_main = load_blocklist(args.blocklist) if args.blocklist else []
    blocklist_auto = (
        load_blocklist(args.auto_blocklist) if args.auto_blocklist else []
    )
    blocklist_ignored_senders_raw = (
        load_ignored_senders(args.ignored_senders) if args.ignored_senders
        else []
    )
    protected = (
        load_protected_senders(args.protected_senders)
        if args.protected_senders else []
    )
    # Defense in depth: even though the UI suppresses the Ignore-sender
    # button for protected domains, a stale ignored_senders.json row or a
    # direct sheet edit must never land a protected domain in the Gmail
    # exclusion clause.
    blocklist_ignored_senders = [
        d for d in blocklist_ignored_senders_raw if not is_protected(d, protected)
    ]
    dropped_protected = (
        len(blocklist_ignored_senders_raw) - len(blocklist_ignored_senders)
    )
    # Union while preserving order: main list first, then auto entries, then
    # UI-ignored senders — each step dedupes case-insensitively against what
    # came before.
    seen_lower = {s.lower() for s in blocklist_main}
    blocklist = list(blocklist_main)
    for s in blocklist_auto:
        if s.lower() not in seen_lower:
            seen_lower.add(s.lower())
            blocklist.append(s)
    for s in blocklist_ignored_senders:
        if s.lower() not in seen_lower:
            seen_lower.add(s.lower())
            blocklist.append(s)
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

    if args.no_kid_names or not args.roster:
        kid_names_body = None
    else:
        kid_names_body = build_kid_names_query(load_roster(args.roster))

    queries = {name: assemble(body) for name, body in SEARCH_TEMPLATES.items()}
    if kid_names_body:
        queries["kid_names"] = assemble(kid_names_body)

    def assemble_loose(body: str) -> str:
        return f"after:{after} before:{before} {body}"

    loose_queries = {name: assemble_loose(body)
                     for name, body in SEARCH_TEMPLATES.items()}
    if kid_names_body:
        loose_queries["kid_names"] = assemble_loose(kid_names_body)

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
            "auto_blocklist_path": (args.auto_blocklist
                                    if args.auto_blocklist else None),
            "ignored_senders_path": (args.ignored_senders
                                     if args.ignored_senders else None),
            "protected_senders_path": (args.protected_senders
                                       if args.protected_senders else None),
            "blocklist_size": len(blocklist),
            "blocklist_size_main": len(blocklist_main),
            "blocklist_size_auto": len(blocklist_auto),
            "blocklist_size_ignored_senders": len(blocklist_ignored_senders),
            "ignored_senders_dropped_protected": dropped_protected,
            "protected_senders_size": len(protected),
        },
        "filter_audit": audit,
        "loose_queries": loose_queries,
        "kid_names_query_body": kid_names_body,
    }
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
