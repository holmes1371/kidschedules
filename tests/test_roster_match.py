"""Unit tests for the pure helpers in scripts/roster_match.py.

Exercises grade alias expansion, activity alias extraction, distinctive
signal filtering, and the tier-priority behavior of derive_child_slug
against a synthetic two-kid roster that mirrors the current
class_roster.json layout.
"""
from __future__ import annotations

import pytest

import roster_match as rm


# Canonical roster shape used by most tests. Mirrors the production
# class_roster.json so failures here directly predict production
# behavior. Tests that need a different shape build their own dict.
ROSTER = {
    "Everly": {
        "teacher": "Ms. Anita Sahai",
        "grade": "6th",
        "school": "Louise Archer Elementary",
        "activities": ["Born 2 Dance Studio (B2D)"],
    },
    "Isla": {
        "teacher": "Ms. Meredith Rohde",
        "grade": "3rd",
        "school": "Louise Archer Elementary",
        "activities": ["Cuppett Performing Arts Center"],
    },
}


@pytest.fixture
def sigs():
    return rm.build_distinctive_signals(ROSTER)


# ─── advance_grade ────────────────────────────────────────────────────────


def test_advance_grade_elementary():
    assert rm.advance_grade("6th") == "7th"
    assert rm.advance_grade("3rd") == "4th"
    assert rm.advance_grade("1st") == "2nd"


def test_advance_grade_kindergarten():
    assert rm.advance_grade("K") == "1st"
    assert rm.advance_grade("k") == "1st"


def test_advance_grade_terminal():
    assert rm.advance_grade("12th") == ""


def test_advance_grade_unknown():
    assert rm.advance_grade("") == ""
    assert rm.advance_grade("senior") == ""


# ─── activity alias extraction ───────────────────────────────────────────


def test_activity_aliases_with_parenthetical():
    """Parenthetical content becomes an alias alongside the primary
    name. Tom's standing ask was "keep alias extraction implicit" — this
    is the sole implicit rule for acronyms."""
    aliases = rm._activity_aliases("Born 2 Dance Studio (B2D)")
    assert "Born 2 Dance Studio" in aliases
    assert "B2D" in aliases


def test_activity_aliases_no_parenthetical_adds_first_word():
    """Activities without an explicit acronym pick up the first word as
    an alias only when it is distinctive-looking (>=6 chars). Catches
    bare "Cuppett" in sources that lack the full name."""
    aliases = rm._activity_aliases("Cuppett Performing Arts Center")
    assert "Cuppett Performing Arts Center" in aliases
    assert "Cuppett" in aliases


def test_activity_aliases_short_first_word_skipped():
    """Short first words like "Born" would false-match unrelated text
    ("born yesterday", "reborn") and must not become standalone
    aliases. The parenthetical still acts as the acronym."""
    aliases = rm._activity_aliases("Born 2 Dance Studio (B2D)")
    assert "Born" not in aliases


def test_activity_aliases_single_word_no_duplicate():
    """A bare activity like "Scouts" yields just itself — the
    first-word alias would be the whole name, so de-dup."""
    aliases = rm._activity_aliases("Scouts")
    assert aliases == ["Scouts"]


def test_activity_aliases_empty_input():
    assert rm._activity_aliases("") == []
    assert rm._activity_aliases("   ") == []


# ─── school alias table ──────────────────────────────────────────────────


def test_school_aliases_laes():
    aliases = rm._school_aliases("Louise Archer Elementary")
    assert "laes" in aliases


def test_school_aliases_case_insensitive():
    aliases = rm._school_aliases("LOUISE ARCHER ELEMENTARY")
    assert "laes" in aliases


def test_school_aliases_unknown_school():
    assert rm._school_aliases("Fictional Academy") == []


# ─── grade token matching ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "grade, text, expected",
    [
        ("6th", "6th grade aap", True),           # ordinal digit
        ("6th", "sixth grade",   True),            # word form
        ("6th", "grade 6",       True),            # bare digit + grade
        ("6th", "16th birthday", False),           # word boundary rejects "16th"
        ("6th", "rising 6th grader", True),        # rising Nth
        ("6th", "6th-grade field trip", True),     # hyphen is a word boundary
        ("3rd", "3rd place",     True),            # matches even outside grade context
        ("3rd", "third period",  True),            # word form
        ("3rd", "grade 6",       False),           # bare digit only matches own digit
        ("7th", "rising 7th grader", True),        # next-grade form
        ("7th", "7 up",          False),           # bare "7" shouldn't match "7th"
        ("6th", "",              False),
    ],
)
def test_grade_matches(grade, text, expected):
    assert rm._grade_matches(grade, text) is expected


# ─── distinctive signal building ─────────────────────────────────────────


def test_distinctive_signals_drops_shared_school():
    """Both kids attend LAES. The school-name signal and every alias
    must be dropped so events about "LAES" don't pick a kid."""
    sigs = rm.build_distinctive_signals(ROSTER)
    everly_sigs = [s for _, s in sigs["Everly"]]
    isla_sigs = [s for _, s in sigs["Isla"]]
    assert "louise archer elementary" not in everly_sigs
    assert "louise archer elementary" not in isla_sigs
    assert "laes" not in everly_sigs
    assert "laes" not in isla_sigs


def test_distinctive_signals_keeps_unique_teacher():
    sigs = rm.build_distinctive_signals(ROSTER)
    everly_sigs = [s for _, s in sigs["Everly"]]
    isla_sigs = [s for _, s in sigs["Isla"]]
    assert "sahai" in everly_sigs
    assert "rohde" in isla_sigs


def test_distinctive_signals_keeps_current_and_rising_grades():
    sigs = rm.build_distinctive_signals(ROSTER)
    everly_grades = [s for tier, s in sigs["Everly"] if tier == "grade"]
    isla_grades = [s for tier, s in sigs["Isla"] if tier == "grade"]
    assert "6th" in everly_grades and "7th" in everly_grades
    assert "3rd" in isla_grades and "4th" in isla_grades


def test_distinctive_signals_drops_shared_activity():
    """Two kids sharing an activity lose that signal — the
    distinctiveness filter is agnostic to which tier originated it."""
    shared_roster = {
        "A": {
            "teacher": "Ms. X",
            "grade": "6th",
            "school": "School One",
            "activities": ["Chess Club"],
        },
        "B": {
            "teacher": "Ms. Y",
            "grade": "3rd",
            "school": "School Two",
            "activities": ["Chess Club"],
        },
    }
    sigs = rm.build_distinctive_signals(shared_roster)
    a_sigs = [s for _, s in sigs["A"]]
    assert "chess club" not in a_sigs


# ─── derive_child_slug (end-to-end over the canonical roster) ────────────


def test_derive_name_tier(sigs):
    assert rm.derive_child_slug({"child": "Everly"}, sigs) == ("everly", "name")
    assert rm.derive_child_slug({"child": "Isla"}, sigs) == ("isla", "name")


def test_derive_name_tier_case_insensitive(sigs):
    assert rm.derive_child_slug({"child": "everly"}, sigs) == ("everly", "name")


def test_derive_grade_current_everly(sigs):
    """The original Tom-reported bug: '6th grade AAP' cards must route
    to Everly even though the extractor set child to the audience
    string, not the name."""
    assert rm.derive_child_slug(
        {"child": "6th grade AAP"}, sigs
    ) == ("everly", "grade")


def test_derive_grade_rising_everly(sigs):
    """New-this-session requirement: 'rising 7th grader' (and bare '7th')
    should route to Everly, who advances to 7th next year."""
    assert rm.derive_child_slug(
        {"child": "rising 7th grader"}, sigs
    ) == ("everly", "grade")
    assert rm.derive_child_slug(
        {"child": "7th grade info night"}, sigs
    ) == ("everly", "grade")


def test_derive_grade_word_form_everly(sigs):
    assert rm.derive_child_slug(
        {"child": "sixth grade"}, sigs
    ) == ("everly", "grade")


def test_derive_grade_bare_digit_plus_grade_isla(sigs):
    assert rm.derive_child_slug(
        {"child": "grade 3"}, sigs
    ) == ("isla", "grade")


def test_derive_grade_rising_isla(sigs):
    """Isla's rising year: 4th grade events should route to her."""
    assert rm.derive_child_slug(
        {"child": "4th grade field trip"}, sigs
    ) == ("isla", "grade")


def test_derive_activity_full_name_isla(sigs):
    """The second original bug: Cuppett Performing Arts events should
    route to Isla, whose roster activity is Cuppett Performing Arts
    Center."""
    assert rm.derive_child_slug(
        {"child": "", "source": "Cuppett Performing Arts Center (Apr 10)"},
        sigs,
    ) == ("isla", "activity")


def test_derive_activity_first_word_alias_isla(sigs):
    """Emails often shorten the sender to just 'Cuppett' — the
    first-word alias for activities must catch it."""
    assert rm.derive_child_slug(
        {"child": "", "source": "Cuppett recital (Apr 10)"}, sigs,
    ) == ("isla", "activity")


def test_derive_activity_parenthetical_alias_everly(sigs):
    """The parenthetical 'B2D' is the acronym alias for Everly's
    dance studio."""
    assert rm.derive_child_slug(
        {"child": "", "source": "B2D recital reminder"}, sigs,
    ) == ("everly", "activity")


def test_derive_teacher_everly(sigs):
    assert rm.derive_child_slug(
        {"child": "", "name": "Ms. Sahai's class trip"}, sigs,
    ) == ("everly", "teacher")


def test_derive_teacher_isla(sigs):
    assert rm.derive_child_slug(
        {"child": "", "source": "Email from Ms. Rohde about reading"}, sigs,
    ) == ("isla", "teacher")


def test_derive_school_shared_no_attribution(sigs):
    """'All LAES students' is a school-wide audience. Because LAES is
    shared across kids, it is dropped from the distinctive signal set
    and does not attribute to either kid — the card will render with
    data-child='' and stay visible across every filter."""
    assert rm.derive_child_slug(
        {"child": "All LAES students"}, sigs,
    ) == ("", "")


def test_derive_school_shared_in_location_no_attribution(sigs):
    """Same rule applies when the school name lands in other fields —
    an event at 'Louise Archer Elementary' with no other distinctive
    signal attributes to neither kid."""
    assert rm.derive_child_slug(
        {"child": "", "location": "Louise Archer Elementary"}, sigs,
    ) == ("", "")


def test_derive_empty_event_no_attribution(sigs):
    assert rm.derive_child_slug({"child": ""}, sigs) == ("", "")
    assert rm.derive_child_slug({}, sigs) == ("", "")


def test_derive_name_beats_grade(sigs):
    """If the extractor did put a kid name in the child field, that
    wins over grade text in source/location. Covers the common case
    where a clean name extraction shouldn't be second-guessed by
    stray grade references."""
    ev = {"child": "Everly", "source": "3rd grade field trip"}
    # Tier 1 (name) fires before tier 3 (grade) in the iteration,
    # and grade wouldn't match Everly anyway — but the point is that
    # we return "name" tier, not some lower tier.
    assert rm.derive_child_slug(ev, sigs) == ("everly", "name")


def test_derive_grade_beats_activity(sigs):
    """If grade and activity both fire for different kids, grade (tier
    3) wins over activity (tier 4) per the documented priority."""
    ev = {
        "child": "3rd grade project",
        "source": "Born 2 Dance Studio update",
    }
    # Grade "3rd" → Isla; activity "Born 2 Dance Studio" → Everly.
    # Isla wins via grade tier (3) over Everly's activity tier (4).
    assert rm.derive_child_slug(ev, sigs) == ("isla", "grade")


def test_derive_does_not_mutate_event(sigs):
    """The event dict must not be modified — event-ID hashing in
    events_state.py depends on the (name, date, child) tuple being
    stable. Any mutation would orphan cached events."""
    ev = {"child": "6th grade AAP", "name": "Study Hall", "source": ""}
    snapshot = dict(ev)
    rm.derive_child_slug(ev, sigs)
    assert ev == snapshot
