"""Loader for the freemail (consumer email) domain list.

Consumed by ``main.py::_attach_sender_domains`` to decide, for each
extracted event, whether the Ignore-sender button should block the
whole registrable domain (institutional default) or just the specific
address (freemail).

See ``design/sender-block-granularity.md`` for the full decision
record. The file format is documented at the top of
``freemail_domains.txt`` at the repo root.
"""
from __future__ import annotations

import os


def load_freemail_domains(path: str) -> frozenset[str]:
    """Return the freemail domains from ``path``, lowercased and deduped.

    Missing file returns an empty frozenset. Callers that pass an empty
    frozenset into ``_compute_block_key`` degrade cleanly to today's
    domain-level behavior — acceptable fallback posture that matches the
    tolerant-parse stance used by the other sender-related loaders.

    Lines get ``#``-comment stripping and whitespace trimming before
    lowercase membership. Blank lines drop silently.
    """
    if not os.path.exists(path):
        return frozenset()
    out: set[str] = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip().lower()
            if line:
                out.add(line)
    return frozenset(out)
