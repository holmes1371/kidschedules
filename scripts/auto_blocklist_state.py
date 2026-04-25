#!/usr/bin/env python3
"""Pending+active state for the auto-blocklist (#27 v1).

Two sections in one JSON file (``blocklist_auto_state.json``):

- ``pending``: addresses that have been flagged once but haven't been
  promoted to active block yet. Keyed by lowercased address; values
  carry ``first_flagged_iso``, ``last_flagged_iso``,
  ``flagged_message_ids``, ``reason_samples``. A pending entry promotes
  to active on the second flag from a *distinct* ``message_id``.
- ``active``: TTL metadata for addresses that ARE in
  ``blocklist_auto.txt``. Keyed by lowercased address; values carry
  ``added_iso``, ``last_flagged_iso``, ``reason``. The actual block
  list (``txt``) stays canonical for ``build_queries.load_blocklist``;
  this section only carries the metadata that drives TTL decay and
  refresh-on-flag.

All helpers are pure except :func:`load_state` and :func:`save_state`,
which do I/O. Schema version is mismatch-tolerant: a wrong-version load
returns the empty state with a stderr warning, matching the
warn-and-fall-back posture of ``newsletter_stats`` and ``events_state``.

See ``design/auto-blocklist-hardening.md`` for the full design and the
precision/recall framing.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from typing import Any


CURRENT_SCHEMA_VERSION = 1

# Default TTLs. Active = 90 days (generous, rides out summer breaks).
# Pending = 30 days (short — pending entries are "watching, not blocking,"
# so let suspicions age out if no second flag arrives within a month).
ACTIVE_TTL_DAYS = 90
PENDING_TTL_DAYS = 30

# Cap on reason_samples per pending entry. With PROMOTION_THRESHOLD=2 the
# cap is never reached in normal operation (a second distinct-message
# flag promotes before a third sample can land), but the constant is
# kept as defense-in-depth against any future bump of the threshold or
# a schema-edit path that lands extra samples without promoting.
REASON_SAMPLES_CAP = 3

# Promotion threshold: pending → active on the Nth distinct message_id.
PROMOTION_THRESHOLD = 2


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "last_updated_iso": "",
        "pending": {},
        "active": {},
    }


def load_state(path: str) -> dict[str, Any]:
    """Read the state file. Empty state on missing/corrupt/wrong-version.

    Mirrors ``newsletter_stats.load_stats`` and
    ``events_state.load_state``'s warn-and-fall-back posture so a bad
    file does not fail the pipeline; the next save overwrites it.
    """
    if not os.path.exists(path):
        return _empty_state()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(
            f"  WARNING: blocklist_auto_state.json unreadable ({e}); "
            "starting empty",
            file=sys.stderr,
        )
        return _empty_state()
    if not isinstance(data, dict):
        print(
            "  WARNING: blocklist_auto_state.json not a JSON object; "
            "starting empty",
            file=sys.stderr,
        )
        return _empty_state()
    if data.get("schema_version") != CURRENT_SCHEMA_VERSION:
        print(
            f"  WARNING: blocklist_auto_state.json schema version mismatch "
            f"(expected {CURRENT_SCHEMA_VERSION}, "
            f"got {data.get('schema_version')!r}); starting empty",
            file=sys.stderr,
        )
        return _empty_state()
    pending = data.get("pending")
    if not isinstance(pending, dict):
        pending = {}
    active = data.get("active")
    if not isinstance(active, dict):
        active = {}
    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "last_updated_iso": data.get("last_updated_iso") or "",
        "pending": pending,
        "active": active,
    }


def save_state(path: str, state: dict[str, Any], now_iso: str) -> None:
    """Atomically write ``state`` to disk via tempfile + ``os.replace``.

    Stamps ``schema_version`` and ``last_updated_iso`` on save so the
    file always carries the current schema marker even if a caller
    forgot to set it. Atomicity matches the posture of
    ``newsletter_stats.save_stats`` and ``events_state.save_state``.
    """
    state["schema_version"] = CURRENT_SCHEMA_VERSION
    state["last_updated_iso"] = now_iso
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False, sort_keys=True)
    os.replace(tmp_path, path)


def add_or_promote(
    state: dict[str, Any],
    addr: str,
    message_id: str,
    reason: str,
    today: dt.date,
    *,
    already_active: bool,
    already_in_main_blocklist: bool,
) -> str:
    """Process a single agent flag. Returns one of:

    - ``"resolved_by_main_blocklist"``: the address is in
      ``blocklist.txt`` (hand-curated). Drop any pending entry; do not
      touch active. The operator's hand edit supersedes any bot state.
    - ``"active_refreshed"``: the address is already active. Bump its
      ``last_flagged_iso`` to today. Real spammers get re-flagged
      every cron run and so never expire under TTL decay; only senders
      that go quiet age out.
    - ``"duplicate_flag"``: the address is in pending and the
      ``message_id`` is already in ``flagged_message_ids``
      (e.g. ``--reextract`` re-fired the agent on the same message).
      ``last_flagged_iso`` bumps because the new flag is a fresh
      observation, but the strike count does not advance.
    - ``"pending_promoted"``: the address is in pending, the
      ``message_id`` is new, and total distinct messages reaches
      ``PROMOTION_THRESHOLD``. Caller appends the address to
      ``blocklist_auto.txt``; the state is updated to reflect the
      promotion (entry moved from ``pending`` to ``active``).
    - ``"pending_added"``: first flag for this address, or a
      sub-threshold subsequent flag with a new ``message_id``.

    Mutates ``state`` in place. Caller is responsible for adding the
    address to ``blocklist_auto.txt`` on ``pending_promoted`` and for
    deciding whether to update the txt's stale comment trailer on
    ``active_refreshed`` (the design note's accepted-risk discussion
    explains why the static trailer is acceptable).
    """
    addr = addr.strip().lower()
    today_iso = today.isoformat()

    # If the address is in the hand-curated blocklist, the operator
    # has already decided. Drop any pending entry as resolved; never
    # touch active (the operator is not the bot).
    if already_in_main_blocklist:
        state["pending"].pop(addr, None)
        return "resolved_by_main_blocklist"

    if already_active or addr in state["active"]:
        active_entry = state["active"].setdefault(addr, {
            "added_iso": today_iso,
            "last_flagged_iso": today_iso,
            "reason": reason,
        })
        active_entry["last_flagged_iso"] = today_iso
        # Drop any stale pending entry — once active, pending is
        # superseded.
        state["pending"].pop(addr, None)
        return "active_refreshed"

    pending_entry = state["pending"].get(addr)
    if pending_entry is None:
        # First flag → pending.
        state["pending"][addr] = {
            "first_flagged_iso": today_iso,
            "last_flagged_iso": today_iso,
            "flagged_message_ids": [message_id],
            "reason_samples": [reason] if reason else [],
        }
        return "pending_added"

    flagged_ids = pending_entry.setdefault("flagged_message_ids", [])
    if message_id in flagged_ids:
        # Same-message re-flag (e.g. via --reextract). Don't promote;
        # don't append. last_flagged_iso DOES bump because the new
        # flag is a fresh observation even though the message is the
        # same — keeps the pending entry alive against TTL aging.
        pending_entry["last_flagged_iso"] = today_iso
        return "duplicate_flag"

    flagged_ids.append(message_id)
    pending_entry["last_flagged_iso"] = today_iso
    samples = pending_entry.setdefault("reason_samples", [])
    if reason and len(samples) < REASON_SAMPLES_CAP:
        samples.append(reason)

    if len(flagged_ids) >= PROMOTION_THRESHOLD:
        # Promote: move metadata to active, drop pending entry.
        del state["pending"][addr]
        state["active"][addr] = {
            "added_iso": today_iso,
            "last_flagged_iso": today_iso,
            # Most-recent flag's reason — the audit log preserves
            # the full pending history if more context is needed.
            "reason": reason,
        }
        return "pending_promoted"

    return "pending_added"


def tick_ttl(
    state: dict[str, Any],
    today: dt.date,
    *,
    active_ttl_days: int = ACTIVE_TTL_DAYS,
    pending_ttl_days: int = PENDING_TTL_DAYS,
) -> dict[str, list[str]]:
    """Prune expired active entries and aged-out pending entries.

    Returns ``{"expired": [addr, ...], "aged_out": [addr, ...]}``. The
    caller is responsible for removing expired addresses from
    ``blocklist_auto.txt``; aged-out entries are pending-only and have
    no txt presence to clean up.

    Pruning is "older than" — an entry whose ``last_flagged_iso`` is
    exactly TTL days old is kept; one day older is dropped. Date
    arithmetic uses Python ``date`` subtraction, so leap days and DST
    transitions don't perturb the result.

    Entries with missing or unparseable ``last_flagged_iso`` are left
    alone — defensive against a hand-edited or partially-corrupt state
    file. The synthetic seed (``seed_active_from_legacy``) populates
    this field, so a missing value would only happen via direct file
    edit.
    """
    expired: list[str] = []
    for addr, entry in list(state["active"].items()):
        last = _parse_iso(entry.get("last_flagged_iso"))
        if last is None:
            continue
        if (today - last).days > active_ttl_days:
            expired.append(addr)
            del state["active"][addr]

    aged_out: list[str] = []
    for addr, entry in list(state["pending"].items()):
        last = _parse_iso(entry.get("last_flagged_iso"))
        if last is None:
            continue
        if (today - last).days > pending_ttl_days:
            aged_out.append(addr)
            del state["pending"][addr]

    return {"expired": expired, "aged_out": aged_out}


def seed_active_from_legacy(
    state: dict[str, Any],
    txt_addresses: list[str],
    today: dt.date,
) -> int:
    """Seed active entries for txt addresses that have no state entry.

    For each address in ``txt_addresses`` (the lowercased entries
    parsed from ``blocklist_auto.txt``) that has no corresponding
    entry in ``state["active"]``, create one with
    ``added_iso = last_flagged_iso = today`` and a placeholder reason.
    Returns the number of entries seeded. Idempotent: a second call
    with the same arguments seeds zero.

    The synthetic ``last_flagged_iso = today`` gives pre-deploy
    entries 90 days of additional life from deploy day. Acceptable
    per the design note's accepted-risk discussion: the alternative
    is a heuristic seed date that's always wrong, and the one-time
    delay is bounded.
    """
    today_iso = today.isoformat()
    seeded = 0
    for addr in txt_addresses:
        addr = addr.strip().lower()
        if not addr or addr in state["active"]:
            continue
        state["active"][addr] = {
            "added_iso": today_iso,
            "last_flagged_iso": today_iso,
            "reason": "legacy entry seeded post-deploy",
        }
        seeded += 1
    return seeded


def _parse_iso(s: Any) -> dt.date | None:
    """Return a ``date`` for a YYYY-MM-DD string, or ``None``."""
    if not isinstance(s, str):
        return None
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None
