"""Deterministic kid-attribution helpers.

Given an event dict (as produced by agent.py) and a parsed roster, derive
the kid slug that should land on the card's `data-child` attribute. The
five signal tiers and the distinctiveness rule are documented in
`design/kid-attribution-derivation.md`.

All functions here are pure — no I/O except `load_roster`. The loader
crashes loudly on missing / malformed files; absence of the roster is a
bug, not a condition to paper over.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


# Ordinal grade forms we understand. Grade keys are case-insensitive
# externally but stored lowercase internally. "K" (kindergarten) is
# handled separately in _grade_matches.
_ORDINAL_TO_WORD = {
    "1st": "first",
    "2nd": "second",
    "3rd": "third",
    "4th": "fourth",
    "5th": "fifth",
    "6th": "sixth",
    "7th": "seventh",
    "8th": "eighth",
    "9th": "ninth",
    "10th": "tenth",
    "11th": "eleventh",
    "12th": "twelfth",
}
_ORDINAL_TO_DIGIT = {
    "1st": "1", "2nd": "2", "3rd": "3", "4th": "4", "5th": "5",
    "6th": "6", "7th": "7", "8th": "8", "9th": "9", "10th": "10",
    "11th": "11", "12th": "12",
}
_GRADE_ORDER = [
    "k", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th",
    "9th", "10th", "11th", "12th",
]

# Static school-alias table. Lowercased canonical name → list of lowercase
# aliases. Add rows as the roster grows; no runtime coupling to the
# roster schema — aliasing stays in this file.
_SCHOOL_ALIASES: dict[str, list[str]] = {
    "louise archer elementary": ["laes", "louise archer"],
    "louise archer elementary school": ["laes", "louise archer"],
}

# First-word-of-activity minimum length to qualify as an auto-alias.
# "Cuppett" (7 chars) qualifies; "Born" (4) does not. Short distinctive
# company names would need an explicit parenthetical alias to be matched
# in isolation.
_FIRST_WORD_ALIAS_MIN_LEN = 6


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_ROSTER_PATH = _REPO_ROOT / "class_roster.json"


def load_roster(path: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """Read and return the roster mapping.

    Raises on missing or malformed JSON — the file is committed and its
    absence is a bug. Mirrors the posture in agent.py._load_roster_prose.
    """
    resolved = Path(path) if path else _DEFAULT_ROSTER_PATH
    return json.loads(resolved.read_text())


def advance_grade(grade: str) -> str:
    """Return the next grade ("6th" → "7th"). Empty string if unknown / last."""
    key = (grade or "").strip().lower()
    if key not in _GRADE_ORDER:
        return ""
    idx = _GRADE_ORDER.index(key)
    if idx + 1 >= len(_GRADE_ORDER):
        return ""
    return _GRADE_ORDER[idx + 1]


def _grade_matches(grade: str, text_lower: str) -> bool:
    """Return True if text_lower mentions the given grade in any common form.

    Forms recognized:
        ordinal digit   ("6th")       via \\b6th\\b  — also catches "rising 6th grader"
        word            ("sixth")     via \\bsixth\\b
        "grade N"       ("grade 6")   via \\bgrade\\s+6\\b
        kindergarten    ("K")         via \\bkindergarten\\b

    Word-boundary matching keeps "16th" and "256th" from matching "6th",
    but still catches hyphenated forms like "6th-grade" (hyphen is a
    word boundary).
    """
    key = (grade or "").strip().lower()
    if not key:
        return False
    if key == "k" or key == "kindergarten":
        return re.search(r"\bkindergarten\b", text_lower) is not None
    if key not in _ORDINAL_TO_WORD:
        return False
    word = _ORDINAL_TO_WORD[key]
    digit = _ORDINAL_TO_DIGIT[key]
    patterns = (
        rf"\b{re.escape(key)}\b",
        rf"\b{re.escape(word)}\b",
        rf"\bgrade\s+{re.escape(digit)}\b",
    )
    return any(re.search(p, text_lower) is not None for p in patterns)


def _activity_aliases(activity: str) -> list[str]:
    """Return the list of strings that should trigger an activity match.

    Implicit aliasing:
        - The primary name (the part before any parenthetical).
        - Any parenthetical content ("(B2D)" → "B2D").
        - The first word of the primary name IF it is at least
          _FIRST_WORD_ALIAS_MIN_LEN characters. This catches bare
          "Cuppett" when the full "Cuppett Performing Arts Center"
          doesn't appear verbatim in the email source, while still
          rejecting short ambiguous first words like "Born" (which
          would false-match unrelated text).

    All aliases are returned in their original casing; callers lowercase.
    """
    raw = (activity or "").strip()
    if not raw:
        return []
    paren = re.search(r"\(([^)]+)\)", raw)
    if paren:
        primary = raw[: paren.start()].strip()
        alias = paren.group(1).strip()
    else:
        primary = raw
        alias = ""
    out: list[str] = []
    if primary:
        out.append(primary)
    if alias:
        out.append(alias)
    if primary:
        first_word = primary.split()[0] if primary.split() else ""
        if (
            len(first_word) >= _FIRST_WORD_ALIAS_MIN_LEN
            and first_word != primary
        ):
            out.append(first_word)
    # De-duplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for s in out:
        if s.lower() not in seen:
            seen.add(s.lower())
            deduped.append(s)
    return deduped


def _school_aliases(school: str) -> list[str]:
    """Return known aliases for a school. Unknown schools → []."""
    key = (school or "").strip().lower()
    return list(_SCHOOL_ALIASES.get(key, []))


def _kid_signals(kid_name: str, info: dict[str, Any]) -> list[tuple[str, str]]:
    """Build a list of (tier, signal_string_lower) pairs for one kid.

    The grade tier carries the ordinal key ("6th"), not the pre-expanded
    match forms; _grade_matches expands at match time. All other tiers
    carry the literal string to match (lowercased).
    """
    sigs: list[tuple[str, str]] = []
    name = (kid_name or "").strip().lower()
    if name:
        sigs.append(("name", name))
    teacher = (info.get("teacher") or "").strip()
    if teacher:
        last = teacher.split()[-1].lower()
        if last:
            sigs.append(("teacher", last))
    grade = (info.get("grade") or "").strip().lower()
    if grade:
        sigs.append(("grade", grade))
        rising = advance_grade(grade)
        if rising:
            sigs.append(("grade", rising))
    for activity in info.get("activities") or []:
        for alias in _activity_aliases(activity):
            sigs.append(("activity", alias.lower()))
    school = (info.get("school") or "").strip()
    if school:
        sigs.append(("school", school.lower()))
        for alias in _school_aliases(school):
            sigs.append(("school", alias.lower()))
    return sigs


def build_distinctive_signals(
    roster: dict[str, dict[str, Any]],
) -> dict[str, list[tuple[str, str]]]:
    """Compute per-kid signals, dropping those shared across kids.

    A signal string (e.g. "laes") that appears in more than one kid's
    signal list is removed from all of them. The tier it was classified
    under for each kid doesn't matter — shared means shared.

    Iteration order of the returned dict mirrors the roster's insertion
    order, which is the tie-break order used by derive_child_slug.
    """
    per_kid = {
        kid: _kid_signals(kid, info) for kid, info in roster.items()
    }
    counts: Counter[str] = Counter()
    for sigs in per_kid.values():
        for _, s in set(sigs):
            counts[s] += 1
    return {
        kid: [t for t in sigs if counts[t[1]] == 1]
        for kid, sigs in per_kid.items()
    }


# Tier priority for derive_child_slug. Earlier tiers win over later ones
# if multiple kids would otherwise match the same event.
_TIER_ORDER = ("name", "teacher", "grade", "activity", "school")


def derive_child_slug(
    ev: dict[str, Any],
    distinctive_signals: dict[str, list[tuple[str, str]]],
) -> tuple[str, str]:
    """Return (slug, tier_name) for the kid this event should attach to.

    Returns ("", "") if no distinctive signal matches any kid. The slug
    is the kid's roster key lowercased; callers write it directly into
    the `data-child` HTML attribute.

    tier_name is one of "name" | "teacher" | "grade" | "activity" |
    "school" | "" and is surfaced so renderers can decide whether to
    show an audience line alongside the kid pill (e.g. a "grade" match
    with `child="6th grade AAP"` keeps the audience line; a "name"
    match with `child="Everly"` does not).
    """
    child_field_lower = (ev.get("child") or "").strip().lower()
    text_blob_lower = " ".join(
        (ev.get(k) or "") for k in ("name", "source", "location", "child")
    ).lower()

    for tier in _TIER_ORDER:
        for kid, sigs in distinctive_signals.items():
            for sig_tier, sig in sigs:
                if sig_tier != tier:
                    continue
                if tier == "name":
                    if child_field_lower == sig:
                        return kid.lower(), tier
                elif tier == "grade":
                    if _grade_matches(sig, text_blob_lower):
                        return kid.lower(), tier
                elif tier == "teacher":
                    pattern = rf"\b{re.escape(sig)}\b"
                    if re.search(pattern, text_blob_lower):
                        return kid.lower(), tier
                elif tier == "activity":
                    if sig and sig in text_blob_lower:
                        return kid.lower(), tier
                elif tier == "school":
                    pattern = rf"\b{re.escape(sig)}\b"
                    if re.search(pattern, text_blob_lower):
                        return kid.lower(), tier
    return "", ""
