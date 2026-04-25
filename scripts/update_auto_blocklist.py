#!/usr/bin/env python3
"""Merge agent-flagged senders into blocklist_auto.txt with guardrails.

Reads:
  --suggestions: JSON list of {from, reason, confidence} dicts.
  --auto-blocklist: bot-owned blocklist file (created if missing).
  --main-blocklist: hand-curated blocklist.txt (read-only, dedupe source).
  --protected-senders: protected_senders.txt path. Suggestions matching
                       any pattern there (bare-domain, *-suffix, or
                       address-form) are rejected, never added.

Writes new entries to --auto-blocklist as:

    <address>  # auto YYYY-MM-DD: <reason>

Guardrails (any rejected suggestion is logged on stdout):
- confidence must be exactly "high"
- 'from' must be a plausible email address (contains '@' and TLD)
- sender must not match a pattern in protected_senders.txt (any shape:
  bare domain like fcps.edu, *-suffix like *pta.org, or full address
  like ellen.n.holmes@gmail.com — see protected_senders.py)
- already-present entries in either blocklist are skipped, not duplicated

Protected-list unification (#26): the previous hardcoded
PROTECTED_SUFFIXES tuple in this file was removed; gating now flows
through the shared protected_senders.is_protected matcher so the
list lives in one place. The address-form matcher (also #26) is what
prevents the recurrence of the Ellen-tax-email failure mode where a
parent's personal Gmail address got auto-blocked from a single agent
flag — see design/protect-parent-addresses.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys

from protected_senders import is_protected, load_protected_senders


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PROTECTED_SENDERS = os.path.join(_PROJECT_ROOT, "protected_senders.txt")


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
    args = p.parse_args()

    protected = (
        load_protected_senders(args.protected_senders)
        if args.protected_senders else []
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
