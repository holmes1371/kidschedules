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


def is_protected(sender: str, patterns: list[str]) -> bool:
    """Return True if ``sender`` matches any ``pattern`` in the list.

    Pattern shapes (all matched case-insensitively):

    - **Bare domain** (``fcps.edu``): matches the exact registrable
      domain *or* any subdomain of it (``elementary.fcps.edu``,
      ``a.b.c.fcps.edu``). The ``.`` boundary is required, so
      ``notfcps.edu`` does NOT match ``fcps.edu`` — substring confusion
      is guarded against. When ``sender`` is an address, the
      comparison runs against the part after the last ``@``.
    - **Domain-suffix** (``*pta.org``): matches any domain that ends
      with the literal part after the asterisk
      (e.g. ``louisearcherpta.org``). Comparison runs against the
      domain part as for bare-domain patterns. Distinct from bare
      patterns: ``*pta.org`` matches ``louisearcherpta.org`` (no dot
      boundary), whereas a bare ``pta.org`` pattern does not.
    - **Address-form** (``alice@example.com``): matches the full
      lowercased email address. Pinned by the ``@`` in the pattern
      itself; the matcher does not attempt to extract a domain from
      these patterns. Used to protect specific personal addresses
      (e.g. parents' Gmail) without protecting the entire freemail
      domain. Added in ROADMAP #26.

    Sender forms accepted: a bare domain (``fcps.edu``), a full
    address (``alice@fcps.edu``), or empty (never protected).

    Matching rules:

    - Address-form patterns only fire when the sender is itself an
      address — i.e. the comparison is full-address against
      full-address. A bare-domain sender against an address-form
      pattern returns False (the bare domain does not protect every
      mailbox under it).
    - Bare-domain and ``*``-suffix patterns continue to work for both
      bare-domain senders and address senders (the matcher derives
      the domain from an address sender as before).
    - Empty ``sender`` is never protected — callers should check for
      that case before deciding whether to render the Ignore-sender
      button.

    The address-form branch (#20) is the load-bearing guarantee that
    nothing in the Ignored-Senders sheet — whether a button click, a
    hand edit, or a stale row — can land a protected domain in the
    Gmail query. The address-form pattern support (#26) extends that
    guarantee to specific addresses, so a one-shot agent flag of a
    parent's personal email cannot result in an auto-block.

    Three consumers share this matcher: ``process_events.py`` (gating
    the Ignore-sender button), ``build_queries.py`` (filtering the
    exclusion union), and ``update_auto_blocklist.py`` (gating the
    bot's own additions to ``blocklist_auto.txt``). Expanding here
    locks the guarantee in one place.
    """
    s = (sender or "").strip().lower()
    if not s:
        return False
    sender_addr = s if "@" in s else ""
    sender_domain = s.rsplit("@", 1)[1] if "@" in s else s
    if not sender_domain:
        return False
    for pat in patterns:
        if "@" in pat:
            # Address-form pattern: full-address equality. Skip when the
            # sender is bare-domain (no address to compare against).
            if sender_addr and sender_addr == pat:
                return True
        elif pat.startswith("*"):
            suffix = pat[1:]
            if suffix and sender_domain.endswith(suffix):
                return True
        elif sender_domain == pat or sender_domain.endswith("." + pat):
            # Bare-domain pattern: exact match or subdomain (with `.`
            # boundary so `notfcps.edu` does not match `fcps.edu`).
            return True
    return False
