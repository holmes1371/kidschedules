"""Pin the Monday CREATE_DRAFT gate to the Monday schedule line.

`weekly-schedule.yml` branches on `github.event.schedule` to decide
whether a given scheduled run creates a Gmail draft. The gate compares
the live schedule string against the literal `'15 10 * * 1'`. If either
side drifts — someone retimes the Monday cron, or edits the gate
literal — the Monday digest silently stops. There is no runtime signal
because the other schedule entry (Sun + Tue–Sat) still runs to completion.

This test reads the workflow file as text and asserts the two literals
match. Parsing as text rather than with PyYAML keeps the dev-deps
footprint at just pytest.
"""
from __future__ import annotations

import re
from pathlib import Path


WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent
    / ".github" / "workflows" / "weekly-schedule.yml"
)


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_workflow_file_exists():
    """Sanity: the path the rest of the tests read must exist. A
    rename of the workflow file would otherwise silently neuter every
    assertion below — they'd be running on empty text."""
    assert WORKFLOW_PATH.is_file(), f"missing: {WORKFLOW_PATH}"


def test_schedule_has_monday_and_other_days_crons():
    """Exactly two cron entries under `on.schedule`: Monday alone and
    Sun + Tue–Sat. The split is load-bearing for the CREATE_DRAFT gate —
    folding them back into one entry would make the gate unreachable.
    The two entries together cover every day of the week (daily cadence
    landed 2026-05-03 to keep the page in sync with the live sheet
    within ~24h instead of ~48h)."""
    text = _workflow_text()
    crons = re.findall(r'-\s*cron:\s*"([^"]+)"', text)
    assert crons == ["15 10 * * 1", "15 10 * * 0,2,3,4,5,6"], (
        f"Unexpected cron set: {crons!r}"
    )


def test_create_draft_gate_references_monday_cron():
    """The gate expression compares github.event.schedule against a
    literal cron string. That literal must exactly match the Monday
    entry in the schedule block above."""
    text = _workflow_text()
    # Capture the quoted cron literal on the RHS of the equality check.
    m = re.search(
        r"github\.event\.schedule\s*==\s*'([^']+)'",
        text,
    )
    assert m is not None, (
        "No `github.event.schedule == '...'` expression found in the "
        "workflow. Either the gate was removed or the expression shape "
        "changed — update this test if the change was intentional."
    )
    gate_literal = m.group(1)

    crons = re.findall(r'-\s*cron:\s*"([^"]+)"', text)
    monday_cron = crons[0]
    assert gate_literal == monday_cron, (
        f"CREATE_DRAFT gate literal {gate_literal!r} does not match the "
        f"Monday cron entry {monday_cron!r}. A change to one without the "
        f"other silently disables the Monday Gmail digest."
    )


def test_create_draft_gate_is_unique():
    """One and only one gate expression. A duplicate — or a leftover
    line from a refactor — would mean CREATE_DRAFT depends on something
    other than what this test pins."""
    text = _workflow_text()
    matches = re.findall(r"github\.event\.schedule\s*==\s*'[^']+'", text)
    assert len(matches) == 1, (
        f"Expected exactly one CREATE_DRAFT gate expression, found "
        f"{len(matches)}: {matches!r}"
    )


def test_gate_literal_does_not_match_other_days_cron():
    """Defense-in-depth: the gate must not match the non-Monday cron.
    A typo that landed '15 10 * * 0,2,3,4,5,6' in the gate would make
    every non-Monday run create a Gmail draft — the exact failure mode
    item 10 was introduced to prevent."""
    text = _workflow_text()
    m = re.search(r"github\.event\.schedule\s*==\s*'([^']+)'", text)
    assert m is not None
    gate_literal = m.group(1)
    assert gate_literal != "15 10 * * 0,2,3,4,5,6"


def test_schedule_covers_every_day_of_week():
    """Daily cadence sanity: union of all cron entries' day-of-week
    fields must be {0,1,2,3,4,5,6}. Catches a future edit that drops
    a day silently — would resurface the prior Sat→Mon ~48h gap."""
    text = _workflow_text()
    crons = re.findall(r'-\s*cron:\s*"([^"]+)"', text)
    days_seen: set[int] = set()
    for c in crons:
        # cron field 5 is day-of-week. Format is "min hr dom mon dow".
        parts = c.split()
        assert len(parts) == 5, f"Malformed cron: {c!r}"
        dow_field = parts[4]
        # Either a list "0,2,3" or a single value "1".
        for tok in dow_field.split(","):
            days_seen.add(int(tok))
    assert days_seen == {0, 1, 2, 3, 4, 5, 6}, (
        f"Schedule must cover every day; got {sorted(days_seen)}"
    )
