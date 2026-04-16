"""Shared loader for the hand-curated protected-senders list.

Two consumers read from the same file for defense-in-depth:

  - scripts/process_events.py suppresses the Ignore-sender button on any
    event card whose sender_domain matches, so the user can't click it in
    the first place.
  - scripts/build_queries.py filters protected domains out of the
    ignored_senders.json → Gmail-exclusion union, so a stale entry or a
    direct sheet edit can't land a protected domain in the Gmail query.

File format is documented in protected_senders.txt at the repo root.
"""
from __future__ import annotations

import os


def load_protected_senders(path: str) -> list[str]:
    """Return the list of protected patterns from ``path``, lowercased.

    Missing file → empty list (the two consumers both treat an empty list
    as 'nothing is protected' rather than raising; this matches the
    tolerant posture of the other blocklist loaders).
    """
    if not os.path.exists(path):
        return []
    out: list[str] = []
    seen: set[str] = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip().lower()
            if not line:
                continue
            if line in seen:
                continue
            seen.add(line)
            out.append(line)
    return out


def is_protected(domain: str, patterns: list[str]) -> bool:
    """Return True if ``domain`` matches any ``pattern`` in the list.

    - Bare patterns match the exact registrable domain.
    - A pattern starting with ``*`` matches any domain that ends with the
      literal part after the asterisk (e.g. ``*pta.org`` matches
      ``louisearcherpta.org``).
    - Matching is case-insensitive.
    - Empty ``domain`` is never protected — callers should check for that
      case before deciding whether to render the Ignore-sender button.
    """
    d = (domain or "").strip().lower()
    if not d:
        return False
    for pat in patterns:
        if pat.startswith("*"):
            suffix = pat[1:]
            if suffix and d.endswith(suffix):
                return True
        elif d == pat:
            return True
    return False
