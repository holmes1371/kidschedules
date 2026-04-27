"""Tests for scripts/pdf_sender_domains.py (ROADMAP #33).

The loader + matcher are thin aliases over `protected_senders.is_protected` /
`load_protected_senders` — file format and matching semantics are
identical. Tests below pin only what `pdf_sender_domains` adds on top:

- The aliases delegate correctly (loader → list, matcher → bool).
- The committed `pdf_sender_domains.txt` at the repo root is loadable
  and contains at least the seed entry (`fcps.edu`) so a future
  refactor that accidentally empties the file fails CI.
- The matcher behaves correctly on the realistic FCPS sender shapes
  (`alice@fcps.edu`, `bob@elementary.fcps.edu`) so the gate doesn't
  silently miss the case the feature was built for.

Full pattern-matching semantics (subdomains, *suffix, dot boundary,
address-form patterns) live in `tests/test_protected_senders.py`.
Don't duplicate that surface here — the alias module's job is only
to give the call site readable names.
"""
from __future__ import annotations

from pathlib import Path

from pdf_sender_domains import is_pdf_sender, load_pdf_sender_domains


REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_FILE = REPO_ROOT / "pdf_sender_domains.txt"


def test_load_pdf_sender_domains_missing_file_returns_empty(tmp_path):
    """Defensive default: a missing file yields an empty list, which
    main.py treats as 'no senders qualify' — every PDF is dropped.
    Matches the silent-degrade posture of the other domain-list
    loaders so a deployment that forgot to ship the file doesn't
    crash."""
    assert load_pdf_sender_domains(str(tmp_path / "nope.txt")) == []


def test_load_pdf_sender_domains_parses_basic_file(tmp_path):
    """The loader strips comments + blanks and lowercases — same
    contract as load_protected_senders. Pin one happy-path case here
    so a regression at the alias boundary fails CI even if the
    underlying loader's tests pass."""
    path = tmp_path / "pdf.txt"
    path.write_text(
        "# header\n"
        "\n"
        "FCPS.edu  # trailing comment\n"
        "*ptsa.org\n"
    )
    assert load_pdf_sender_domains(str(path)) == ["fcps.edu", "*ptsa.org"]


def test_committed_pdf_sender_domains_loadable():
    """The committed file at repo root must exist and load cleanly.
    Without this test, an editor that accidentally moved or renamed
    the file would only fail at workflow runtime."""
    assert COMMITTED_FILE.is_file(), (
        f"Expected {COMMITTED_FILE} to exist; if it was renamed, "
        f"update this test plus the path constants in main.py."
    )
    patterns = load_pdf_sender_domains(str(COMMITTED_FILE))
    assert "fcps.edu" in patterns, (
        f"pdf_sender_domains.txt must contain at least 'fcps.edu' "
        f"as the seed school district. If the seed was intentionally "
        f"removed (e.g. the family moved districts), update this "
        f"assertion to whichever domain is now the seed."
    )


def test_is_pdf_sender_realistic_fcps_addresses():
    """Pin the realistic teacher-address shape against the seed
    pattern — the case the feature exists to handle. A drift here
    means the gate silently drops the very PDFs we built it for."""
    patterns = ["fcps.edu"]
    assert is_pdf_sender("mlrohde@fcps.edu", patterns) is True
    assert is_pdf_sender("teacher@elementary.fcps.edu", patterns) is True
    # Personal-account email sharing a topic word — must NOT match.
    assert is_pdf_sender("parent@gmail.com", patterns) is False


def test_is_pdf_sender_empty_patterns_returns_false():
    """Empty pattern list (file missing / empty) means no PDFs are
    eligible. Confirms the safe-default branch."""
    assert is_pdf_sender("mlrohde@fcps.edu", []) is False


def test_is_pdf_sender_empty_sender_returns_false():
    """A blank From: header should never match. The pipeline
    upstream tolerates malformed headers; this matcher must mirror
    that posture."""
    assert is_pdf_sender("", ["fcps.edu"]) is False
