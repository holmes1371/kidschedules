"""Pytest suite for scripts/update_auto_blocklist.py helpers.

The script auto-mutates blocklist_auto.txt using agent suggestions.
Its three private helpers — _domain_of, _is_protected, _parse_block_file
— gate which addresses can be auto-added; a regression in any one
of them risks polluting a tracked file with bad entries (or missing
real ones). This suite pins each helper's tolerated and rejected
shapes; the larger main() flow is exercised end-to-end via the live
weekly workflow and is intentionally out of scope here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/ is added to sys.path by tests/conftest.py
import update_auto_blocklist as uab  # noqa: E402


# ─── _domain_of ───────────────────────────────────────────────────────────

def test_domain_of_simple_address():
    assert uab._domain_of("tom@example.com") == "example.com"


def test_domain_of_multi_level_tld():
    assert uab._domain_of("tom@sub.example.co.uk") == "sub.example.co.uk"


def test_domain_of_lowercases_mixed_case_input():
    assert uab._domain_of("Tom@Example.COM") == "example.com"


def test_domain_of_strips_surrounding_whitespace():
    assert uab._domain_of("  tom@example.com  ") == "example.com"


def test_domain_of_named_form_returns_none():
    """Regex is anchored — "Tom Holmes <tom@example.com>" doesn't match.
    Callers must pass a bare address; main() does this via .strip()."""
    assert uab._domain_of("Tom Holmes <tom@example.com>") is None


def test_domain_of_no_at_sign_returns_none():
    assert uab._domain_of("not-an-email") is None


def test_domain_of_no_local_part_returns_none():
    assert uab._domain_of("@example.com") is None


def test_domain_of_no_tld_returns_none():
    """example with no dot-suffix isn't a routable address."""
    assert uab._domain_of("tom@example") is None


def test_domain_of_single_char_tld_returns_none():
    """TLD must be 2+ chars; tom@x.y is rejected."""
    assert uab._domain_of("tom@example.x") is None


def test_domain_of_empty_string_returns_none():
    assert uab._domain_of("") is None


def test_domain_of_whitespace_only_returns_none():
    assert uab._domain_of("   ") is None


# ─── _is_protected ────────────────────────────────────────────────────────

def test_is_protected_exact_match():
    assert uab._is_protected("fcps.edu") is True


def test_is_protected_subdomain_match():
    """A subdomain of a protected suffix is also protected — covers
    "schoolname.fcps.edu" senders that share the umbrella domain."""
    assert uab._is_protected("elementary.fcps.edu") is True


def test_is_protected_deep_subdomain_match():
    assert uab._is_protected("a.b.c.pta.org") is True


def test_is_protected_unrelated_domain_not_protected():
    assert uab._is_protected("randomspam.com") is False


def test_is_protected_case_insensitive():
    assert uab._is_protected("FCPS.EDU") is True
    assert uab._is_protected("Elementary.FCPS.Edu") is True


def test_is_protected_substring_but_not_suffix_not_protected():
    """"notpta.org" contains the chars "pta.org" but is not a subdomain
    of "pta.org" — must not be protected. This is the classic substring-
    vs-suffix confusion guard."""
    assert uab._is_protected("notpta.org") is False


def test_is_protected_suffix_chars_in_middle_not_protected():
    """"pta.org.example.com" ends in ".example.com", not ".pta.org"."""
    assert uab._is_protected("pta.org.example.com") is False


# ─── _parse_block_file ────────────────────────────────────────────────────

def test_parse_block_file_missing_file_returns_empty_set(tmp_path):
    missing = tmp_path / "does-not-exist.txt"
    assert uab._parse_block_file(str(missing)) == set()


def test_parse_block_file_strips_comment_only_lines(tmp_path):
    p = tmp_path / "block.txt"
    p.write_text(
        "# header comment\n"
        "spam@example.com\n"
        "# another comment\n",
        encoding="utf-8",
    )
    assert uab._parse_block_file(str(p)) == {"spam@example.com"}


def test_parse_block_file_strips_inline_comments(tmp_path):
    """Auto-added entries carry "# auto YYYY-MM-DD: reason" trailers;
    the parse must drop the trailer when comparing to existing entries."""
    p = tmp_path / "block.txt"
    p.write_text("spam@example.com  # auto 2026-01-01: weekly digest\n", encoding="utf-8")
    assert uab._parse_block_file(str(p)) == {"spam@example.com"}


def test_parse_block_file_strips_blank_lines(tmp_path):
    p = tmp_path / "block.txt"
    p.write_text("\nspam@example.com\n\n   \n", encoding="utf-8")
    assert uab._parse_block_file(str(p)) == {"spam@example.com"}


def test_parse_block_file_lowercases_entries(tmp_path):
    """Comparison set must be lowercased so dedup catches case-variant
    duplicates between auto and main blocklists."""
    p = tmp_path / "block.txt"
    p.write_text("Spam@Example.COM\n", encoding="utf-8")
    assert uab._parse_block_file(str(p)) == {"spam@example.com"}


def test_parse_block_file_dedupes_repeated_entries(tmp_path):
    p = tmp_path / "block.txt"
    p.write_text(
        "spam@example.com\n"
        "spam@example.com\n"
        "Spam@Example.com\n",
        encoding="utf-8",
    )
    assert uab._parse_block_file(str(p)) == {"spam@example.com"}


def test_parse_block_file_inline_comment_only_line_yields_no_entry(tmp_path):
    """A line that's just whitespace before '#' has no entry to keep."""
    p = tmp_path / "block.txt"
    p.write_text("  # just a comment after some whitespace\n", encoding="utf-8")
    assert uab._parse_block_file(str(p)) == set()
