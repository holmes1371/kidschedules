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

Sender-stats reject (#27 lever 1): the sender_stats.json gate is the
cheapest of the three #27 levers — no new state file, just one extra
dict lookup per suggestion. It catches the case where the agent makes
a per-email "no kid events" call on a sender that has produced kid
events in prior weeks.

N-strikes pending ledger (#27 lever 2): a high-confidence flag with a
new ``source_message_id`` for an unknown sender lands in the
``pending`` section of ``blocklist_auto_state.json`` (a watchlist —
NOT in ``blocklist_auto.txt``). Only when a second flag with a
*distinct* message_id arrives does the address promote to active and
get written to the txt. Same-message re-flags (e.g. ``--reextract``)
do not advance the strike count. Pre-deploy txt entries are seeded
into the ``active`` section on first run with a synthetic
``last_flagged_iso = today``.

TTL decay (#27 lever 3): after the suggestion loop, ``tick_ttl`` prunes
active entries unflagged for ``--active-ttl-days`` (default 90) and
pending entries unflagged for ``--pending-ttl-days`` (default 30).
Expired addresses are also removed from ``blocklist_auto.txt`` via a
full file rewrite. Refresh-on-flag (the ``active_refreshed`` outcome)
keeps real spammers blocked indefinitely; only senders that go quiet
age out.

Outcomes per suggestion (logged on stdout and to the audit JSONL):

- ``promoted``: 2nd distinct-message flag → added to ``blocklist_auto.txt``
- ``pending_added``: 1st flag → ``state["pending"]``, no txt write
- ``active_refreshed``: address is already active → bump
  ``last_flagged_iso`` (defends TTL expiry)
- ``duplicate_flag``: same message_id seen before in pending →
  no strike advance; ``last_flagged_iso`` still bumps
- ``resolved_by_main_blocklist``: operator hand-blocked the address;
  drop any pending entry as resolved
- ``rejected``: failed an upstream gate (confidence, address shape,
  missing source_message_id, protected, useful sender)

Per-run TTL events (logged on stdout and to the audit JSONL):

- ``expired``: active entry's ``last_flagged_iso`` exceeded the active
  TTL; entry dropped from state and txt
- ``aged_out``: pending entry's ``last_flagged_iso`` exceeded the
  pending TTL without a corroborating second flag; entry dropped
  from state (no txt presence to clean up)
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
import auto_blocklist_state as abls  # noqa: E402


DEFAULT_PROTECTED_SENDERS = os.path.join(_PROJECT_ROOT, "protected_senders.txt")
DEFAULT_SENDER_STATS = os.path.join(_PROJECT_ROOT, "sender_stats.json")
DEFAULT_STATE_FILE = os.path.join(_PROJECT_ROOT, "blocklist_auto_state.json")


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
    p.add_argument("--state-file", default=DEFAULT_STATE_FILE,
                   help="Path to blocklist_auto_state.json (#27 v1). Holds "
                        "the pending+active sections that drive N-strikes "
                        "promotion and TTL decay. Required — pre-existing "
                        "blocklist_auto.txt entries are seeded on first "
                        "run via auto_blocklist_state.seed_active_from_legacy.")
    p.add_argument("--active-ttl-days", type=int, default=abls.ACTIVE_TTL_DAYS,
                   help=f"Active entries unflagged for more than this many "
                        f"days are pruned (default: {abls.ACTIVE_TTL_DAYS}). "
                        f"Real spammers stay blocked indefinitely via "
                        f"refresh-on-flag; this is the recovery path for "
                        f"senders that go quiet.")
    p.add_argument("--pending-ttl-days", type=int, default=abls.PENDING_TTL_DAYS,
                   help=f"Pending entries unflagged for more than this many "
                        f"days are aged out (default: {abls.PENDING_TTL_DAYS}). "
                        f"Pending is 'watching, not blocking'; if no second "
                        f"flag arrives within the window, drop the suspicion.")
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

    today = dt.date.today()
    today_iso = today.isoformat()

    # Load pending/active state and seed legacy txt entries that have
    # no state row yet. Idempotent — second-and-later runs no-op the
    # seed because the state has caught up to the txt.
    state = abls.load_state(args.state_file)
    txt_addrs = list(_parse_block_file(args.auto_blocklist))
    seeded = abls.seed_active_from_legacy(state, txt_addrs, today)
    if seeded:
        print(
            f"  Seeded {seeded} legacy auto-blocklist entry(ies) with "
            f"synthetic last_flagged_iso={today_iso}"
        )
    main_block_addrs = _parse_block_file(args.main_blocklist)

    # Per-outcome buckets. The `_` placeholder marks fields not used by
    # downstream logging — kept on the tuple so iteration is uniform.
    promoted: list[tuple[str, str, str, str]] = []          # (addr, msg_id, reason, conf)
    pending_added: list[tuple[str, str, str, str]] = []
    active_refreshed: list[tuple[str, str, str, str]] = []
    duplicates: list[tuple[str, str, str, str]] = []
    resolved_by_main: list[tuple[str, str, str, str]] = []
    rejected: list[tuple[str, str, str]] = []               # (addr, why, conf)

    for s in suggestions:
        if not isinstance(s, dict):
            rejected.append((repr(s), "not a dict", ""))
            continue
        addr = (s.get("from") or "").strip().lower()
        message_id = (s.get("source_message_id") or "").strip()
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
        if not message_id:
            # #27: missing source_message_id is malformed at the gate. The
            # agent's prompt asks for this field; flags without it can't
            # participate in N-strikes corroboration so we drop them
            # rather than silently treat empty-string as a real id.
            rejected.append((addr, "missing source_message_id", conf))
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
        # Sender-stats reject (#27 lever 1): if the sender has produced
        # kid events historically, this is a useful sender — refuse the
        # auto-block regardless of how confident the agent's per-email
        # judgment is. Empty stats / unknown sender / below-threshold
        # all fall through to the pending/active routing below.
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
        # Pending/active routing via the state module (#27 lever 2).
        # First flag → pending; second distinct-message flag → promote
        # to active and append to blocklist_auto.txt below.
        already_active = addr in state["active"]
        already_in_main_blocklist = addr in main_block_addrs
        outcome = abls.add_or_promote(
            state, addr, message_id, reason, today,
            already_active=already_active,
            already_in_main_blocklist=already_in_main_blocklist,
        )
        if outcome == "pending_added":
            pending_added.append((addr, message_id, reason, conf))
        elif outcome == "pending_promoted":
            promoted.append((addr, message_id, reason, conf))
        elif outcome == "active_refreshed":
            active_refreshed.append((addr, message_id, reason, conf))
        elif outcome == "duplicate_flag":
            duplicates.append((addr, message_id, reason, conf))
        elif outcome == "resolved_by_main_blocklist":
            resolved_by_main.append((addr, message_id, reason, conf))
        # Unknown outcome would be a programming error in the state
        # module; let it land in no bucket so the test will catch it.

    # TTL decay (#27 lever 3). Prune expired active entries and aged-out
    # pending entries. Runs AFTER the suggestion loop so any active
    # entry that just got refreshed-on-flag has the fresh
    # last_flagged_iso and won't be expired in the same run. Returns
    # `{expired, aged_out}` lists for the audit log; the txt rewrite
    # below removes expired entries (aged-out are pending-only, no
    # txt presence to clean up).
    ttl_result = abls.tick_ttl(
        state, today,
        active_ttl_days=args.active_ttl_days,
        pending_ttl_days=args.pending_ttl_days,
    )
    expired_addrs = set(ttl_result["expired"])

    # Remove expired entries from blocklist_auto.txt by full rewrite.
    # Comment-only and blank lines are preserved (auto-header lives on
    # the first lines as comments, so it survives unchanged).
    if expired_addrs and os.path.exists(args.auto_blocklist):
        with open(args.auto_blocklist, "r", encoding="utf-8") as f:
            lines = f.readlines()
        kept_lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                kept_lines.append(line)
                continue
            addr_in_line = stripped.split("#", 1)[0].strip().lower()
            if addr_in_line in expired_addrs:
                continue  # drop the expired entry's line
            kept_lines.append(line)
        with open(args.auto_blocklist, "w", encoding="utf-8") as f:
            f.writelines(kept_lines)

    # Promoted entries land in blocklist_auto.txt with the same trailer
    # format as before (#26). Header written on first creation. Append
    # AFTER the expiry rewrite so promotions survive cleanly.
    if promoted:
        write_header = not os.path.exists(args.auto_blocklist)
        with open(args.auto_blocklist, "a", encoding="utf-8") as f:
            if write_header:
                f.write(_AUTO_HEADER)
            for addr, _mid, reason, _conf in promoted:
                short = reason[:80].replace("\n", " ").replace("#", "").strip()
                f.write(f"{addr}  # auto {today_iso}: {short}\n")

    # Persist state. Always saves — the pending/active section is the
    # cron-cadence ledger; even a no-op run records the timestamp.
    abls.save_state(args.state_file, state, today_iso)

    # Stdout per-outcome lines so a workflow log reader can scan
    # what happened this run without parsing the audit JSONL.
    for addr, _mid, reason, conf in promoted:
        print(f"  PROMOTED: {addr} [conf={conf}] — {reason}")
    for addr, _mid, _r, _c in pending_added:
        print(f"  pending:    {addr} (1 strike, awaiting corroboration)")
    for addr, _mid, _r, _c in active_refreshed:
        print(f"  refreshed:  {addr} (active TTL extended)")
    for addr, _mid, _r, _c in duplicates:
        print(f"  duplicate:  {addr} (same message_id; no strike)")
    for addr, _mid, _r, _c in resolved_by_main:
        print(f"  resolved:   {addr} (in main blocklist; pending dropped)")
    for addr in sorted(ttl_result["expired"]):
        print(f"  EXPIRED:    {addr} (active TTL elapsed)")
    for addr in sorted(ttl_result["aged_out"]):
        print(f"  aged_out:   {addr} (pending TTL elapsed without 2nd flag)")
    for addr, why, conf in rejected:
        conf_display = conf if conf else "n/a"
        print(f"  rejected:   {addr} [conf={conf_display}] — {why}")

    summary = {
        # `added_count` and `added` preserved for backwards-compat with
        # any reader of the prior schema; both equal the promoted set.
        "added_count": len(promoted),
        "rejected_count": len(rejected),
        "promoted_count": len(promoted),
        "pending_added_count": len(pending_added),
        "active_refreshed_count": len(active_refreshed),
        "duplicate_count": len(duplicates),
        "resolved_count": len(resolved_by_main),
        "expired_count": len(ttl_result["expired"]),
        "aged_out_count": len(ttl_result["aged_out"]),
        "added": [
            {"from": a, "reason": r, "confidence": c}
            for a, _m, r, c in promoted
        ],
    }
    print(json.dumps(summary), file=sys.stderr)

    if args.audit_log:
        entry = {
            "run_iso": today_iso,
            "suggestion_count": len(suggestions),
            # Legacy `added` field preserved for backward-compat = promoted.
            "added": [
                {"from": a, "reason": r, "confidence": c}
                for a, _m, r, c in promoted
            ],
            "promoted": [
                {"from": a, "source_message_id": m,
                 "reason": r, "confidence": c}
                for a, m, r, c in promoted
            ],
            "pending_added": [
                {"from": a, "source_message_id": m,
                 "reason": r, "confidence": c}
                for a, m, r, c in pending_added
            ],
            "active_refreshed": [
                {"from": a, "source_message_id": m,
                 "reason": r, "confidence": c}
                for a, m, r, c in active_refreshed
            ],
            "duplicate_flag": [
                {"from": a, "source_message_id": m, "confidence": c}
                for a, m, _r, c in duplicates
            ],
            "resolved_by_main_blocklist": [
                {"from": a, "source_message_id": m, "confidence": c}
                for a, m, _r, c in resolved_by_main
            ],
            "expired": [{"from": a} for a in ttl_result["expired"]],
            "aged_out": [{"from": a} for a in ttl_result["aged_out"]],
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
