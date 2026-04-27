"""Loader + matcher for PDF-eligible sender domains (ROADMAP #33).

The pipeline only fetches PDF attachments from emails whose sender
domain matches a pattern in `pdf_sender_domains.txt` at the repo
root. This gates token spend on the document content block — a
Costco receipt PDF in a personal email never reaches the agent.

File format mirrors `protected_senders.txt` exactly: one pattern
per line, `#` comments allowed, bare domain matches self+subdomains
(with `.` boundary), `*suffix` patterns supported, matching is
case-insensitive. Reusing the format means a single matcher
(`protected_senders.is_protected`) handles both files — no new
matching code lands here.

Two thin aliases below give the call site at main.py readable
names (`is_pdf_sender(...)` reads better than `is_protected(...)`
when the question is "does this sender qualify for PDF extraction?").
"""
from __future__ import annotations

from protected_senders import is_protected, load_protected_senders


def load_pdf_sender_domains(path: str) -> list[str]:
    """Return the list of PDF-eligible sender patterns from ``path``.

    Missing file → empty list. The caller treats an empty list as
    "no senders qualify" (i.e. PDFs are dropped from every email),
    matching the defensive default of the other domain-list loaders
    in this project: silent degrade rather than raise.
    """
    return load_protected_senders(path)


def is_pdf_sender(sender: str, patterns: list[str]) -> bool:
    """Return True if ``sender`` matches any ``pattern`` in the list.

    See `protected_senders.is_protected` for the full pattern-matching
    semantics. Aliased here so the main.py call site reads as
    "is this a PDF-eligible sender?" rather than "is this protected?",
    which would be a confusing reuse of the protected-senders concept.
    """
    return is_protected(sender, patterns)
