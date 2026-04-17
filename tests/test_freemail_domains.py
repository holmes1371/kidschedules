"""Tests for scripts/freemail_domains.py — the loader that decides
which registrable domains trigger address-level Ignore-sender blocking
(freemail / consumer email) versus today's domain-level blocking
(institutional).

See design/sender-block-granularity.md for the full decision record.
"""
from __future__ import annotations

from freemail_domains import load_freemail_domains


def test_load_missing_file_returns_empty_frozenset(tmp_path):
    # Tolerant-parse posture — callers degrade cleanly to today's
    # domain-level behavior when the file is absent.
    result = load_freemail_domains(str(tmp_path / "nope.txt"))
    assert result == frozenset()


def test_load_strips_comments_and_blanks(tmp_path):
    path = tmp_path / "freemail.txt"
    path.write_text(
        "# top-of-file comment\n"
        "\n"
        "gmail.com  # trailing comment\n"
        "\n"
        "yahoo.com\n"
        "# another comment\n"
    )
    result = load_freemail_domains(str(path))
    assert result == frozenset({"gmail.com", "yahoo.com"})


def test_load_lowercases(tmp_path):
    path = tmp_path / "freemail.txt"
    path.write_text("GMAIL.COM\nYahoo.Com\nOutLook.com\n")
    result = load_freemail_domains(str(path))
    assert result == frozenset({"gmail.com", "yahoo.com", "outlook.com"})


def test_load_dedupes(tmp_path):
    path = tmp_path / "freemail.txt"
    path.write_text("gmail.com\nGMAIL.COM\ngmail.com\n")
    result = load_freemail_domains(str(path))
    assert result == frozenset({"gmail.com"})


def test_load_skips_only_whitespace_lines(tmp_path):
    path = tmp_path / "freemail.txt"
    path.write_text("\n   \n\t\ngmail.com\n\n")
    result = load_freemail_domains(str(path))
    assert result == frozenset({"gmail.com"})


def test_load_real_repo_seed_contains_common_freemail():
    # Smoke-check the committed list so the common providers Tom names
    # in the design note actually land in the set.
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    result = load_freemail_domains(str(repo_root / "freemail_domains.txt"))
    # Spot-check a handful of the must-haves; full list is self-documenting.
    for must in ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com",
                 "icloud.com", "aol.com", "comcast.net", "protonmail.com"):
        assert must in result, f"{must!r} missing from freemail_domains.txt"
