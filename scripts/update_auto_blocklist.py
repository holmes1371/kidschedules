#!/usr/bin/env python3
"""Merge agent-flagged senders into blocklist_auto.txt with guardrails.

Reads:
  --suggestions: JSON list of {from, reason, confidence} dicts.
  --auto-blocklist: bot-owned blocklist file (created if missing).
  --main-blocklist: hand-curated blocklist.txt (read-only, dedupe source).
  --protected-senders: protected_senders.txt path. Suggestions matching
                       any pattern there (bare-domain, *-suffix, or
                       address-form) are rejected, never added.
  --sender-stats: sender_stats.json path. Senders with a meaningful
                  history of kid-event yield are rejected (see #27).

Writes new entries to --auto-blocklist as:

    <address>  # auto YYYY-MM-DD: <reason>

Guardrails (any rejected suggestion is logged on stdout):
- confidence must be exactly "high"
- 'from' must be a plausible email address (contains '@' and TLD)
- sender must not match a pattern in protected_senders.txt (any shape:
  bare domain like fcps.edu, *-suffix like *pta.org, or full address
  like ellen.n.holmes@gmail.com — see protected_senders.py)
- sender_stats.json must not show the sender as historically useful
  (>= SENDER_STATS_MIN_MESSAGES messages seen and
  >= SENDER_STATS_MIN_EVENTS total events) — #27 v1
- already-present entries in either blocklist are skipped, not duplicated

Protected-list unification (#26): the previous hardcoded
PROTECTED_SUFFIXES tuple in this file was removed; gating now flows
through the shared protected_senders.is_protected matcher so the
list lives in one place. The address-form matcher (also #26) is what
prevents the recurrence of the Ellen-tax-email failure mode where a
parent's personal Gmail address got auto-blocked from a single agent
flag — see design/protect-parent-addresses.md.

Sender-stats reject (#27, this commit): the new
sender_stats.json gate is the cheapest of the three #27 levers — no
new state file, just one extra dict lookup per suggestion. It catches
the case where the agent makes a per-email "no kid events" call on a
sender that has produced kid events in prior weeks. Subsequent commits
add the N-strikes pending ledger and TTL decay; together those three
levers replace today's "first flag → permanent active block" gating
with "first flag → pending; second-flag-distinct-message → active;
expire on 90d quiet."
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys

# scripts/ resolves natively (it's the script's own directory). The
# project root is one level up; we add it to sys.path so newsletter_stats
# (a project-root module) can be imported. Idempotent under repeated
# import via the membership guard.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from protected_senders import is_protected, load_protected_senders  # noqa: E402
from newsletter_stats import load_stats as load_sender_stats  # noqa: E402


DEFAULT_PROTECTED_SENDERS = os.path.join(_PROJECT_ROOT, "protected_senders.txt")
DEFAULT_SENDER_STATS = os.path.join(_PROJECT_ROOT, "sender_stats.json")


# Sender-stats reject thresholds (#27 v1). A suggestion is rejected when
# sender_stats.json shows the sender has produced at least
# SENDER_STATS_MIN_EVENTS event(s) across at least
# SENDER_STATS_MIN_MESSAGES observed message(s). Historical kid-event
# yield is the ground-truth signal that this sender is useful, so a
# single high-confidence irrelevance flag is most likely a per-email
# judgment, not a per-sender judgment. Message threshold mirrors
# newsletter_stats.PROMOTION_MIN_MESSAGES (3) — both gates ask the same
# question: has this sender been around long enough to have a meaningful
# track record?
SENDER_STATS_MIN_MESSAGES = 3
SENDER_STATS_MIN_EVENTS = 1


_EMAIL_RE = re.compile(r"^[^@\s]+@([a-z0-9.-]+\.[a-z]{2,})$", re.IGNORECASE)


def _domain_of(addr: str) -> str | None:
    m = _EMAIL_RE.match(addr.strip())
    return m.group(1).lower() if m else None


def _parse_block_file(path: str) -> set[str]:
    """Return the set of non-comment, non-blank sender patterns (lowercased)."""
    out: set[str] = set()
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            # Strip any inline comment (auto-adds carry "# auto ...").
            s = s.split("#", 1)[0].strip()
            if s:
                out.add(s.lower())
    return out


_AUTO_HEADER = (
    "# Auto-populated blocklist managed by the pipeline.\n"
    "# DO NOT hand-edit — edit blocklist.txt instead.\n"
    "# Each entry is a high-confidence agent flag. The weekly filter\n"
    "# audit can recommend removals if any entry hid real kid mail.\n"
)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--suggestions", required=True,
                   help="JSON file of {from, reason, confidence} dicts.")
    p.add_argument("--auto-blocklist", required=True,
                   help="Path to blocklist_auto.txt (created if missing).")
    p.add_argument("--main-blocklist", required=True,
                   help="Read-only path to the hand-curated blocklist.txt.")
    p.add_argument("--audit-log",
                   help="Optional JSONL file; one line appended per run with "
                        "added and rejected suggestions for week-over-week audit.")
    p.add_argument("--protected-senders", default=DEFAULT_PROTECTED_SENDERS,
                   help="Path to protected_senders.txt. Suggestions matching "
                        "any pattern there are rejected. Pass '' to disable.")
    p.add_argument("--sender-stats", default=DEFAULT_SENDER_STATS,
                   help="Path to sender_stats.json (item #17 newsletter "
                        "telemetry). Suggestions for senders with "
                        f">= {SENDER_STATS_MIN_MESSAGES} messages seen and "
                        f">= {SENDER_STATS_MIN_EVENTS} total event(s) are "
                        "rejected — those senders are useful, not blockable. "
                        "Missing/corrupt file falls through silently (gate "
                        "skipped; N-strikes downstream still corroborates). "
                        "Pass '' to disable.")
    args = p.parse_args()

    protected = (
        load_protected_senders(args.protected_senders)
        if args.protected_senders else []
    )
    sender_stats = (
        load_sender_stats(args.sender_stats) if args.sender_stats
        else {"senders": {}}
    )

    try:
        with open(args.suggestions, "r", encoding="utf-8") as f:
            suggestions = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: could not read {args.suggestions}: {e}", file=sys.stderr)
        return 1
    if not isinstance(suggestions, list):
        print(
            f"ERROR: suggestions must be a JSON list, "
            f"got {type(suggestions).__name__}",
            file=sys.stderr,
        )
        return 1

    existing = _parse_block_file(args.auto_blocklist) | _parse_block_file(
        args.main_blocklist
    )

    # Each entry carries the agent's raw confidence value so the audit log
    # preserves it alongside the guardrail decision.
    added: list[tuple[str, str, str]] = []     # (address, reason, confidence)
    rejected: list[tuple[str, str, str]] = []  # (address, why, confidence)

    for s in suggestions:
        if not isinstance(s, dict):
            rejected.append((repr(s), "not a dict", ""))
            continue
        addr = (s.get("from") or "").strip().lower()
        reason = (s.get("reason") or "").strip()
        conf = (s.get("confidence") or "").strip().lower()

        if conf != "high":
            rejected.append(
                (addr or repr(s), f"confidence={conf!r} (need 'high')", conf)
            )
            continue
        domain = _domain_of(addr)
        if not domain:
            rejected.append((addr or repr(s), "not a valid email address", conf))
            continue
        if is_protected(addr, protected):
            # Differentiate the rejection reason so the audit log keeps the
            # historical "protected domain (...)" wording for the bare-domain
            # / *-suffix paths and surfaces the new "protected sender (...)"
            # form for an address-form match. The matcher's internals stay
            # opaque from here; we infer the shape from whether any
            # address-form pattern in the list equals the address.
            if addr in protected:
                rejected.append((addr, f"protected sender ({addr})", conf))
            else:
                rejected.append((addr, f"protected domain ({domain})", conf))
            continue
        # Sender-stats reject (#27): if the sender has produced kid events
        # historically, this is a useful sender — refuse the auto-block
        # regardless of how confident the agent's per-email judgment is.
        # Empty stats / unknown sender / below-threshold all fall through.
        sender_entry = sender_stats.get("senders", {}).get(addr)
        if sender_entry:
            msgs_seen = sender_entry.get("messages_seen", 0)
            events_total = sender_entry.get("total_events", 0)
            if (msgs_seen >= SENDER_STATS_MIN_MESSAGES
                    and events_total >= SENDER_STATS_MIN_EVENTS):
                rejected.append((
                    addr,
                    f"useful sender ({events_total} event(s) across "
                    f"{msgs_seen} msg(s))",
                    conf,
                ))
                continue
        if addr in existing:
            rejected.append((addr, "already in blocklist", conf))
            continue
        existing.add(addr)
        added.append((addr, reason, conf))

    if added:
        today = dt.date.today().isoformat()
        write_header = not os.path.exists(args.auto_blocklist)
        with open(args.auto_blocklist, "a", encoding="utf-8") as f:
            if write_header:
                f.write(_AUTO_HEADER)
            for addr, reason, _conf in added:
                short = reason[:80].replace("\n", " ").replace("#", "").strip()
                f.write(f"{addr}  # auto {today}: {short}\n")

    for addr, reason, conf in added:
        print(f"  AUTO-BLOCK: {addr} [conf={conf}] — {reason}")
    for addr, why, conf in rejected:
        conf_display = conf if conf else "n/a"
        print(f"  rejected:   {addr} [conf={conf_display}] — {why}")

    summary = {
        "added_count": len(added),
        "rejected_count": len(rejected),
        "added": [
            {"from": a, "reason": r, "confidence": c} for a, r, c in added
        ],
    }
    print(json.dumps(summary), file=sys.stderr)

    if args.audit_log:
        entry = {
            "run_iso": dt.date.today().isoformat(),
            "suggestion_count": len(suggestions),
            "added": [
                {"from": a, "reason": r, "confidence": c}
                for a, r, c in added
            ],
            "rejected": [
                {"from": a, "why": w, "confidence": c}
                for a, w, c in rejected
            ],
        }
        with open(args.audit_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
