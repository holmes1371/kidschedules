"""Learned per-sender event-count statistics.

Tracks how many events the agent extracts from each email sender over
time so the pipeline can:

  (a) classify frequently-observed high-yield senders as "newsletters"
  (b) flag a run where a known newsletter sender produced markedly
      fewer events than its rolling median, which usually signals
      under-extraction

Persistence: sender_stats.json at repo root, synced to the state branch
by the workflow. File-per-concern; independent of events_state.json.

All helpers are pure except load_stats / save_stats, which do I/O.
Classification is sticky (is_newsletter flips False→True, never back).
See design/newsletter-robustness.md for the full design.
"""
from __future__ import annotations

import json
import os
import statistics
from typing import Any


CURRENT_SCHEMA_VERSION = 1

# Sender is promoted to is_newsletter=True once BOTH conditions hold.
PROMOTION_MIN_MESSAGES = 3
PROMOTION_MIN_MEDIAN = 5

# Rolling window size for per_message_counts. Newer values append,
# older values fall off the front. Bounds file growth and keeps the
# rolling median insensitive to year-old data.
ROLLING_WINDOW = 10

# Outlier floor: even if a newsletter's prior median is very low,
# current count must drop below this (or below half the median,
# whichever is larger) to trigger an alert.
OUTLIER_FLOOR = 2


def _empty_stats() -> dict[str, Any]:
    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "last_updated_iso": "",
        "senders": {},
    }


def load_stats(path: str) -> dict[str, Any]:
    """Read the stats file. Return empty stats on missing/corrupt/wrong-version.

    Mirrors events_state.load_state's warn-and-fall-back posture so a
    bad file does not fail the pipeline; the next save overwrites it.
    """
    if not os.path.exists(path):
        return _empty_stats()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  WARNING: sender_stats.json unreadable ({e}); starting empty")
        return _empty_stats()
    if not isinstance(data, dict):
        print("  WARNING: sender_stats.json not a JSON object; starting empty")
        return _empty_stats()
    if data.get("schema_version") != CURRENT_SCHEMA_VERSION:
        print(
            f"  WARNING: sender_stats.json schema version mismatch "
            f"(expected {CURRENT_SCHEMA_VERSION}, "
            f"got {data.get('schema_version')!r}); starting empty"
        )
        return _empty_stats()
    senders = data.get("senders")
    if not isinstance(senders, dict):
        senders = {}
    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "last_updated_iso": data.get("last_updated_iso") or "",
        "senders": senders,
    }


def save_stats(path: str, stats: dict[str, Any], now_iso: str) -> None:
    """Atomically write stats to disk via tempfile + os.replace."""
    stats["schema_version"] = CURRENT_SCHEMA_VERSION
    stats["last_updated_iso"] = now_iso
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False, sort_keys=True)
    os.replace(tmp_path, path)


def _ensure_sender_entry(
    stats: dict[str, Any], sender_key: str, now_iso: str
) -> dict[str, Any]:
    """Return the sender's entry, creating it on first observation."""
    senders = stats.setdefault("senders", {})
    entry = senders.get(sender_key)
    if entry is None:
        entry = {
            "messages_seen": 0,
            "total_events": 0,
            "per_message_counts": [],
            "first_seen_iso": now_iso,
            "last_seen_iso": now_iso,
            "is_newsletter": False,
        }
        senders[sender_key] = entry
    return entry


def update_sender_counts(
    stats: dict[str, Any],
    per_message_counts: list[tuple[str, str, int]],
    now_iso: str,
) -> dict[str, Any]:
    """Fold per-message event counts into the stats.

    Args:
        stats: loaded stats dict (mutated in place).
        per_message_counts: list of (sender_key, message_id, count)
            triples. One entry per email sent to the agent this run.
            A newsletter-sender email that yielded zero events still
            appears here with count=0 so the rolling median stays
            honest.
        now_iso: timestamp to stamp as last_seen on touched senders.

    Each message contributes one value to its sender's
    per_message_counts. messages_seen and total_events are lifetime
    counters. The rolling window keeps only the most recent
    ROLLING_WINDOW entries.

    Mutates and returns stats for convenience.
    """
    for sender_key, _msg_id, count in per_message_counts:
        if not sender_key:
            continue  # defensive: unparseable From header upstream
        entry = _ensure_sender_entry(stats, sender_key, now_iso)
        entry["messages_seen"] += 1
        entry["total_events"] += count
        window = entry.get("per_message_counts") or []
        window.append(count)
        if len(window) > ROLLING_WINDOW:
            window = window[-ROLLING_WINDOW:]
        entry["per_message_counts"] = window
        entry["last_seen_iso"] = now_iso
    return stats


def classify_senders(stats: dict[str, Any]) -> dict[str, Any]:
    """Promote senders to is_newsletter=True where thresholds are met.

    Promotion is sticky: once True, never flipped back by this function.
    Manual edit to sender_stats.json is the only way to demote.

    Promotion thresholds (must both hold):
      - messages_seen >= PROMOTION_MIN_MESSAGES
      - median(per_message_counts) >= PROMOTION_MIN_MEDIAN

    Mutates and returns stats.
    """
    senders = stats.get("senders", {})
    for _key, entry in senders.items():
        if entry.get("is_newsletter"):
            continue
        if entry.get("messages_seen", 0) < PROMOTION_MIN_MESSAGES:
            continue
        window = entry.get("per_message_counts") or []
        if not window:
            continue
        if statistics.median(window) >= PROMOTION_MIN_MEDIAN:
            entry["is_newsletter"] = True
    return stats


def newsletter_senders(stats: dict[str, Any]) -> set[str]:
    """Return the set of sender keys currently classified as newsletters."""
    senders = stats.get("senders", {})
    return {
        key for key, entry in senders.items()
        if entry.get("is_newsletter")
    }


def _outlier_threshold(prior_median: float) -> int:
    """The count at or above which we do NOT flag an outlier.

    An alert fires when current < threshold. Threshold is
    max(OUTLIER_FLOOR, round(prior_median * 0.5)).
    """
    return max(OUTLIER_FLOOR, round(prior_median * 0.5))


def outlier_alerts(
    stats: dict[str, Any],
    per_message_counts: list[tuple[str, str, int]],
) -> list[dict[str, Any]]:
    """Return outlier alerts for newsletter senders in this run.

    Uses stats *as passed* — caller must invoke before update_sender_counts
    so the rolling median reflects prior runs only, not including this run.

    One alert per (sender, message_id) pair from per_message_counts
    where the sender is a known newsletter and the current count falls
    below the threshold. Non-newsletter senders produce no alerts.

    Alert shape:
      {
        "sender": <sender_key>,
        "message_id": <gmail_msg_id>,
        "prior_median": <int>,
        "current_count": <int>,
        "threshold": <int>,
      }
    """
    senders = stats.get("senders", {})
    alerts: list[dict[str, Any]] = []
    for sender_key, message_id, count in per_message_counts:
        entry = senders.get(sender_key)
        if not entry or not entry.get("is_newsletter"):
            continue
        window = entry.get("per_message_counts") or []
        if not window:
            continue
        prior_median = statistics.median(window)
        threshold = _outlier_threshold(prior_median)
        if count < threshold:
            alerts.append({
                "sender": sender_key,
                "message_id": message_id,
                "prior_median": int(prior_median),
                "current_count": count,
                "threshold": threshold,
            })
    return alerts
