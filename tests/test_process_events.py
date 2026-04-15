"""Pytest suite for scripts/process_events.py.

Covers event-ID stability, classify() paths, dedupe (both passes),
group_by_week ordering, subject-line format, _load_ignored_ids tolerance,
body snapshot, and targeted HTML substring asserts. One CLI smoke test
exercises the argparse + IO plumbing end-to-end.

All tests pin today = 2026-04-14 (a Tuesday) so fixture dates resolve
deterministically into past / displayed / banked buckets.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys

import pytest

import process_events as pe
from helpers import (
    FIXTURES_DIR,
    REPO_ROOT,
    load_fixture,
    read_snapshot,
)


TODAY = dt.date(2026, 4, 14)
HORIZON = TODAY + dt.timedelta(days=60)


# ─── _event_id ────────────────────────────────────────────────────────────


def test_event_id_is_deterministic():
    a = pe._event_id("Spring Concert", "2026-04-23", "Isla")
    b = pe._event_id("Spring Concert", "2026-04-23", "Isla")
    assert a == b
    assert len(a) == 12
    assert all(c in "0123456789abcdef" for c in a)


def test_event_id_normalizes_case_and_whitespace():
    canonical = pe._event_id("Spring Concert", "2026-04-23", "Isla")
    assert pe._event_id("  spring   CONCERT  ", "2026-04-23", "ISLA") == canonical
    assert pe._event_id("SPRING concert", "2026-04-23", "isla") == canonical


def test_event_id_changes_with_date():
    a = pe._event_id("Spring Concert", "2026-04-23", "Isla")
    b = pe._event_id("Spring Concert", "2026-04-24", "Isla")
    assert a != b


# ─── classify() paths ─────────────────────────────────────────────────────


def test_classify_drops_past_events():
    events = load_fixture("past_future_banked")
    display, undated, past, banked, ignored, _ = pe.classify(
        events, cutoff=TODAY, horizon=HORIZON
    )
    past_names = {e["name"] for e in past}
    assert "Last Week's Field Trip" in past_names
    assert all(e["name"] != "Last Week's Field Trip" for e in display)


def test_classify_bucket_undated_events():
    events = load_fixture("past_future_banked")
    display, undated, past, banked, ignored, _ = pe.classify(
        events, cutoff=TODAY, horizon=HORIZON
    )
    undated_names = {e["name"] for e in undated}
    assert "Yearbook Sales Deadline" in undated_names


def test_classify_banks_far_future_events():
    events = load_fixture("past_future_banked")
    display, undated, past, banked, ignored, _ = pe.classify(
        events, cutoff=TODAY, horizon=HORIZON
    )
    banked_names = {e["name"] for e in banked}
    assert "Summer Reading Kickoff" in banked_names
    assert all(e["name"] != "Summer Reading Kickoff" for e in display)


def test_classify_drops_events_matching_ignored_ids():
    events = load_fixture("past_future_banked")
    # Compute the ID of the one future event we want ignored.
    soccer_id = pe._event_id("Soccer Practice", "2026-04-21", "Isla")
    display, undated, past, banked, ignored_dropped, _ = pe.classify(
        events, cutoff=TODAY, horizon=HORIZON,
        ignored_ids=frozenset([soccer_id]),
    )
    ignored_names = {e["name"] for e in ignored_dropped}
    assert "Soccer Practice" in ignored_names
    assert all(e["name"] != "Soccer Practice" for e in display)


def test_classify_warns_on_missing_name_and_skips():
    events = load_fixture("edge_cases")
    display, undated, past, banked, ignored, warnings = pe.classify(
        events, cutoff=TODAY, horizon=HORIZON
    )
    all_names = {e["name"] for e in display + undated + past + banked}
    assert "" not in all_names
    assert any("missing name" in w for w in warnings)


def test_classify_warns_on_unknown_category_but_keeps_event():
    events = load_fixture("edge_cases")
    display, _, _, _, _, warnings = pe.classify(
        events, cutoff=TODAY, horizon=HORIZON
    )
    assert any(e["name"] == "Odd Category Event" for e in display)
    assert any("Mystery" in w for w in warnings)


# ─── field defaults ───────────────────────────────────────────────────────


def test_classify_applies_field_defaults_to_sparse_event():
    events = load_fixture("edge_cases")
    display, _, _, _, _, _ = pe.classify(
        events, cutoff=TODAY, horizon=HORIZON
    )
    sparse = next(e for e in display if e["name"] == "Sparse Event")
    assert sparse["time"] == "Time TBD"
    assert sparse["location"] == "Location TBD"
    assert sparse["child"] == ""
    assert sparse["source"] == "LAES (Apr 9)"


# ─── dedupe ───────────────────────────────────────────────────────────────


def _classified_display(fixture_name: str) -> list[dict]:
    events = load_fixture(fixture_name)
    display, _, _, _, _, _ = pe.classify(
        events, cutoff=TODAY, horizon=HORIZON
    )
    return display


def test_dedupe_pass1_exact_keeps_most_complete():
    display = _classified_display("duplicates_exact")
    merged = pe.dedupe(display)
    assert len(merged) == 1
    kept = merged[0]
    # The richest variant (the one with time, location, child, non-empty source) wins.
    assert kept["time"] == "1:30 PM dismissal"
    assert kept["location"] == "Glasgow Middle School"
    assert kept["child"] == "Isla"


def test_dedupe_pass2_fuzzy_collapses_subset_names_same_date():
    display = _classified_display("duplicates_fuzzy")
    merged = pe.dedupe(display)
    asl_matches = [e for e in merged if "asl" in e["name"].lower()]
    assert len(asl_matches) == 1
    # The richer "ASL Club Meeting" entry wins on completeness.
    assert asl_matches[0]["name"] == "ASL Club Meeting"
    assert asl_matches[0]["location"] == "LAES Room 204"


def test_dedupe_pass2_preserves_digit_only_tokens():
    display = _classified_display("duplicates_fuzzy")
    merged = pe.dedupe(display)
    swim_names = {e["name"] for e in merged if "Swim" in e["name"]}
    assert swim_names == {"Swim — Ages 3-5", "Swim — Ages 6-8"}


def test_dedupe_undated_skip_fuzzy_pass():
    """Two undated events with subset-token names must not collapse, since
    the fuzzy pass only compares within same-date buckets."""
    events = [
        {"name": "ASL Club", "date": "", "time": "", "location": "",
         "category": "School Activity", "child": "", "source": "a"},
        {"name": "ASL Club Meeting", "date": "", "time": "", "location": "",
         "category": "School Activity", "child": "", "source": "b"},
    ]
    # classify() puts these in `undated`; main() calls dedupe on undated too.
    _, undated, _, _, _, _ = pe.classify(
        events, cutoff=TODAY, horizon=HORIZON
    )
    merged = pe.dedupe(undated)
    names = {e["name"] for e in merged}
    assert names == {"ASL Club", "ASL Club Meeting"}


# ─── group_by_week ────────────────────────────────────────────────────────


def test_group_by_week_buckets_by_monday():
    display = _classified_display("basic_mixed")
    weeks = pe.group_by_week(display)
    week_starts = [w for w, _ in weeks]
    # 2026-04-13 is the Monday covering events on Apr 15–17.
    assert dt.date(2026, 4, 13) in week_starts
    # 2026-04-20 is the Monday covering Apr 23.
    assert dt.date(2026, 4, 20) in week_starts
    for w in week_starts:
        assert w.weekday() == 0, f"{w} is not a Monday"


def test_group_by_week_sorts_same_day_events_by_name():
    events = [
        {"name": "Zebra Event", "date": "2026-04-15", "time": "",
         "location": "", "category": "School Activity", "child": "", "source": "x"},
        {"name": "apple event", "date": "2026-04-15", "time": "",
         "location": "", "category": "School Activity", "child": "", "source": "x"},
        {"name": "Middle Event", "date": "2026-04-15", "time": "",
         "location": "", "category": "School Activity", "child": "", "source": "x"},
    ]
    display, _, _, _, _, _ = pe.classify(events, cutoff=TODAY, horizon=HORIZON)
    weeks = pe.group_by_week(display)
    _, day_events = weeks[0]
    names = [e["name"] for e in day_events]
    assert names == ["apple event", "Middle Event", "Zebra Event"]


# ─── render_body snapshot ────────────────────────────────────────────────


def test_render_body_matches_snapshot():
    display = _classified_display("basic_mixed")
    display = pe.dedupe(display)
    events = load_fixture("basic_mixed")
    _, undated, _, _, _, _ = pe.classify(events, cutoff=TODAY, horizon=HORIZON)
    undated = pe.dedupe(undated)
    weeks = pe.group_by_week(display)
    body = pe.render_body(
        today=TODAY, weeks=weeks, undated=undated,
        total_future=len(display), lookback_days=60,
    )
    expected = read_snapshot("basic_body")
    assert body == expected


# ─── render_html substring asserts ───────────────────────────────────────


def test_render_html_includes_event_id_and_ignore_attrs():
    display = _classified_display("basic_mixed")
    display = pe.dedupe(display)
    events = load_fixture("basic_mixed")
    _, undated, _, _, _, _ = pe.classify(events, cutoff=TODAY, horizon=HORIZON)
    undated = pe.dedupe(undated)
    weeks = pe.group_by_week(display)

    html = pe.render_html(
        today=TODAY, weeks=weeks, undated=undated,
        total_future=len(display), lookback_days=60,
        webhook_url="https://example.com/hook",
    )

    # Webhook URL appears inside the injected JS, JSON-escaped.
    assert '"https://example.com/hook"' in html

    # Every event card carries a data-event-id with the expected 12-char ID.
    spring_id = pe._event_id("Spring Concert", "2026-04-23", "Isla")
    assert f'data-event-id="{spring_id}"' in html

    # Ignore button carries the event name and date for the audit row.
    assert 'data-event-name="Spring Concert"' in html
    assert 'data-event-date="2026-04-23"' in html


def test_render_html_empty_state_when_no_events():
    html = pe.render_html(
        today=TODAY, weeks=[], undated=[],
        total_future=0, lookback_days=60, webhook_url="",
    )
    assert "No upcoming kids' events were found" in html
    # The literal string "data-event-id" appears in inline JS regardless, so
    # check for the rendered attribute on an actual card instead.
    assert 'class="event-card"' not in html
    assert 'class="event-card undated"' not in html


# ─── subject + metadata ──────────────────────────────────────────────────


def test_subject_line_format_via_cli(tmp_path):
    """Subject line is built in main() from today; assert via CLI smoke test."""
    candidates_path = FIXTURES_DIR / "basic_mixed.json"
    meta_path = tmp_path / "meta.json"
    body_path = tmp_path / "body.txt"
    subprocess.run(
        [sys.executable,
         str(REPO_ROOT / "scripts" / "process_events.py"),
         "--candidates", str(candidates_path),
         "--today", TODAY.isoformat(),
         "--body-out", str(body_path),
         "--meta-out", str(meta_path),
         ],
        check=True,
    )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["subject"] == "Kids' Schedule — April 14, 2026"
    assert meta["counts"]["future_dated"] >= 1
    assert meta["counts"]["undated"] >= 1
    assert meta["today_iso"] == TODAY.isoformat()


# ─── weekly digest ───────────────────────────────────────────────────────


def _digest_weeks(fixture_name: str):
    display = _classified_display(fixture_name)
    display = pe.dedupe(display)
    return pe.group_by_week(display)


def test_digest_subject_uses_monday_of_this_week():
    # TODAY is Tue 2026-04-14; Monday of that week is 2026-04-13.
    assert pe.digest_subject(TODAY) == "Kids' Schedule — Week of April 13"


def test_digest_text_lists_only_this_week_events():
    weeks = _digest_weeks("digest_this_week")
    text = pe.render_digest_text(weeks, TODAY, pages_url="https://example.com/sched")
    assert "Art & Crafts" in text
    assert "Book Report Due" in text
    # Spring Concert is 2026-04-23 — next week — must not appear.
    assert "Spring Concert" not in text
    assert "https://example.com/sched" in text


def test_digest_text_empty_week_message():
    # Force an empty this-week bucket by supplying a today well past all events.
    weeks = _digest_weeks("digest_this_week")
    far_future = dt.date(2027, 1, 4)
    text = pe.render_digest_text(weeks, far_future, pages_url="")
    assert "No events this week." in text
    # No pages link was supplied.
    assert "http" not in text


def test_digest_html_escapes_event_names():
    weeks = _digest_weeks("digest_this_week")
    html = pe.render_digest_html(weeks, TODAY, pages_url="https://example.com/s")
    # The "&" in "Art & Crafts" must be escaped; the literal raw "&" must
    # not appear outside of already-encoded entities.
    assert "Art &amp; Crafts" in html
    assert "Art & Crafts" not in html
    # Link is present.
    assert 'href="https://example.com/s"' in html


def test_digest_html_empty_week_no_list():
    weeks = _digest_weeks("digest_this_week")
    html = pe.render_digest_html(weeks, dt.date(2027, 1, 4), pages_url="")
    assert "No events this week." in html
    assert "<ul" not in html


def test_digest_meta_via_cli(tmp_path):
    """CLI emits digest block in meta and writes both digest bodies."""
    candidates_path = FIXTURES_DIR / "digest_this_week.json"
    meta_path = tmp_path / "meta.json"
    body_path = tmp_path / "body.txt"
    dtext_path = tmp_path / "digest.txt"
    dhtml_path = tmp_path / "digest.html"
    subprocess.run(
        [sys.executable,
         str(REPO_ROOT / "scripts" / "process_events.py"),
         "--candidates", str(candidates_path),
         "--today", TODAY.isoformat(),
         "--body-out", str(body_path),
         "--meta-out", str(meta_path),
         "--digest-text-out", str(dtext_path),
         "--digest-html-out", str(dhtml_path),
         "--pages-url", "https://example.com/s",
         ],
        check=True,
    )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["digest"]["subject"] == "Kids' Schedule — Week of April 13"
    # 2 this-week events: Art & Crafts (Apr 15), Book Report Due (Apr 17).
    assert meta["digest"]["this_week_count"] == 2
    # Bodies were written.
    assert dtext_path.read_text(encoding="utf-8").strip()
    assert dhtml_path.read_text(encoding="utf-8").strip()


# ─── _load_ignored_ids tolerance ─────────────────────────────────────────


@pytest.mark.parametrize(
    "file_contents,expected",
    [
        (None, frozenset()),                                  # file missing
        ("not json at all", frozenset()),                     # malformed JSON
        ('{"not": "a list"}', frozenset()),                   # wrong shape
        ('[]', frozenset()),                                  # empty list
        ('[{"id": "abc123abc123"}, {"id": "def456def456"}]',
         frozenset(["abc123abc123", "def456def456"])),        # happy path
        ('[{"id": "abc"}, {"no_id": true}, "garbage"]',
         frozenset(["abc"])),                                 # mixed, only valid dict entries survive
    ],
)
def test_load_ignored_ids_tolerant(tmp_path, file_contents, expected):
    path = tmp_path / "ignored.json"
    if file_contents is None:
        # Do not create the file at all.
        result = pe._load_ignored_ids(str(path))
    else:
        path.write_text(file_contents, encoding="utf-8")
        result = pe._load_ignored_ids(str(path))
    assert result == expected


# ─── .ics export ──────────────────────────────────────────────────────────


from zoneinfo import ZoneInfo  # noqa: E402  (local to keep helpers tests cohesive)


ICS_NOW = dt.datetime(2026, 4, 14, 12, 0, 0, tzinfo=ZoneInfo("UTC"))


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("7:00 PM", dt.time(19, 0)),
        ("3:45 PM", dt.time(15, 45)),
        ("8 AM", dt.time(8, 0)),
        ("8am", dt.time(8, 0)),
        ("12 AM", dt.time(0, 0)),
        ("12 PM", dt.time(12, 0)),
        ("  7:00 PM  ", dt.time(19, 0)),
        ("1:30 PM dismissal", None),
        ("Time TBD", None),
        ("All day (deadline)", None),
        ("", None),
        (None, None),
        ("13:00 PM", None),  # hour out of 1-12 range
    ],
)
def test_parse_clock_time(raw, expected):
    assert pe._parse_clock_time(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Spring Concert", "spring-concert"),
        ("Book Report Due", "book-report-due"),
        ("Pediatrician Check-up", "pediatrician-check-up"),
        ("   ", "event"),
        ("", "event"),
        ("!!!", "event"),
    ],
)
def test_ics_slug(raw, expected):
    assert pe._ics_slug(raw) == expected


def _fixture_event(name: str) -> dict:
    events = load_fixture("basic_mixed")
    for ev in events:
        if ev["name"] == name:
            return ev
    raise KeyError(name)


def test_build_ics_uid_stable_across_calls():
    ev = _fixture_event("Spring Concert")
    a = pe.build_ics(ev, now=ICS_NOW)
    b = pe.build_ics(
        ev, now=dt.datetime(2030, 1, 1, 0, 0, 0, tzinfo=ZoneInfo("UTC"))
    )
    # DTSTAMP will differ, but UID must be byte-identical across runs,
    # so calendar apps overwrite rather than duplicate on re-import.
    uid_a = [line for line in a.splitlines() if line.startswith("UID:")][0]
    uid_b = [line for line in b.splitlines() if line.startswith("UID:")][0]
    assert uid_a == uid_b


def test_build_ics_timed_snapshot():
    ev = _fixture_event("Spring Concert")
    assert pe.build_ics(ev, now=ICS_NOW) == read_snapshot("ics_spring_concert")


def test_build_ics_all_day_snapshot():
    ev = _fixture_event("Book Report Due")
    assert pe.build_ics(ev, now=ICS_NOW) == read_snapshot("ics_book_report")


def test_build_ics_rejects_undated_event():
    ev = {"name": "TBD", "date": "", "time": "", "child": ""}
    with pytest.raises(ValueError):
        pe.build_ics(ev, now=ICS_NOW)
