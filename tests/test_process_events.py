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


def test_event_id_undated_vs_dated_no_collision():
    """#18 relies on undated (date="") and dated event IDs being disjoint
    so the same name+child under a TBD-date card can't collide with a
    future dated build of the same event. Pin the contract."""
    dated = pe._event_id("Summer Camp Registration", "2026-06-01", "")
    undated = pe._event_id("Summer Camp Registration", "", "")
    assert dated != undated


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


def test_classify_marks_ignored_events_and_keeps_them_in_display():
    """Render-but-hide: ignored events flow through to their date bucket
    (tagged is_ignored=True) AND are appended to the ignored bucket for
    count logging. The page hides them via CSS; users can Unignore per-card."""
    events = load_fixture("past_future_banked")
    soccer_id = pe._event_id("Soccer Practice", "2026-04-21", "Isla")
    display, undated, past, banked, ignored, _ = pe.classify(
        events, cutoff=TODAY, horizon=HORIZON,
        ignored_ids=frozenset([soccer_id]),
    )
    # Still rendered (for hide-but-restorable UX), with is_ignored flag on.
    soccer_in_display = [e for e in display if e["name"] == "Soccer Practice"]
    assert len(soccer_in_display) == 1
    assert soccer_in_display[0]["is_ignored"] is True
    # And simultaneously counted in the ignored bucket for meta logging.
    assert any(e["name"] == "Soccer Practice" for e in ignored)


def test_classify_is_ignored_false_on_non_matching_events():
    events = load_fixture("past_future_banked")
    display, _, _, _, _, _ = pe.classify(
        events, cutoff=TODAY, horizon=HORIZON,
        ignored_ids=frozenset(),
    )
    assert display, "fixture must have at least one displayed event"
    assert all(e["is_ignored"] is False for e in display)


def test_classify_passes_through_sender_domain():
    raw = [
        {"name": "With Sender", "date": "2026-04-20", "sender_domain": "laes.org",
         "category": "School Activity", "child": "Isla", "source": "x"},
        {"name": "No Sender Key", "date": "2026-04-20",
         "category": "School Activity", "child": "Isla", "source": "x"},
        {"name": "Empty Sender", "date": "2026-04-20", "sender_domain": "",
         "category": "School Activity", "child": "Isla", "source": "x"},
    ]
    display, _, _, _, _, _ = pe.classify(raw, cutoff=TODAY, horizon=HORIZON)
    by_name = {e["name"]: e for e in display}
    assert by_name["With Sender"]["sender_domain"] == "laes.org"
    assert by_name["No Sender Key"]["sender_domain"] == ""
    assert by_name["Empty Sender"]["sender_domain"] == ""


def test_classify_passes_through_sender_block_key():
    # #20: classify normalizes sender_block_key alongside sender_domain.
    # No default derivation — an explicit "" or missing key stays "".
    raw = [
        {"name": "Freemail Addr", "date": "2026-04-20",
         "sender_domain": "gmail.com", "sender_block_key": "alice@gmail.com",
         "category": "School Activity", "child": "Isla", "source": "x"},
        {"name": "Institutional", "date": "2026-04-20",
         "sender_domain": "laes.org", "sender_block_key": "laes.org",
         "category": "School Activity", "child": "Isla", "source": "x"},
        {"name": "Missing Key", "date": "2026-04-20",
         "sender_domain": "laes.org",
         "category": "School Activity", "child": "Isla", "source": "x"},
    ]
    display, _, _, _, _, _ = pe.classify(raw, cutoff=TODAY, horizon=HORIZON)
    by_name = {e["name"]: e for e in display}
    assert by_name["Freemail Addr"]["sender_block_key"] == "alice@gmail.com"
    assert by_name["Institutional"]["sender_block_key"] == "laes.org"
    assert by_name["Missing Key"]["sender_block_key"] == ""


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
    """After the Layout A flip (design/card-redesign.md), missing time/
    location become empty strings — not "Time TBD" / "Location TBD"
    sentinels. Render helpers now use truthy guards, so the sentinels
    are no longer needed and would be distinguishable from a real
    'TBD' string if one ever came in from a sender."""
    events = load_fixture("edge_cases")
    display, _, _, _, _, _ = pe.classify(
        events, cutoff=TODAY, horizon=HORIZON
    )
    sparse = next(e for e in display if e["name"] == "Sparse Event")
    assert sparse["time"] == ""
    assert sparse["location"] == ""
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


def test_render_html_ics_button_is_https_link():
    """Every dated card gets an https:// Add-to-calendar anchor when a
    pages_url is supplied; undated cards never do. https (not webcal) so
    iOS treats the tap as a one-shot event import rather than a calendar
    subscription."""
    display = _classified_display("basic_mixed")
    display = pe.dedupe(display)
    events = load_fixture("basic_mixed")
    _, undated, _, _, _, _ = pe.classify(events, cutoff=TODAY, horizon=HORIZON)
    undated = pe.dedupe(undated)
    weeks = pe.group_by_week(display)

    html = pe.render_html(
        today=TODAY, weeks=weeks, undated=undated,
        total_future=len(display), lookback_days=60, webhook_url="",
        pages_url="https://holmes1371.github.io/kidschedules/",
    )

    assert "Add to calendar" in html
    assert 'href="https://holmes1371.github.io/kidschedules/ics/' in html
    # Must not emit webcal:// anywhere — that's the subscription flow we
    # explicitly moved away from.
    assert "webcal://" not in html

    # At least one timed and one all-day event ID should appear in an href.
    timed = next(e for e in display if e["name"] == "Spring Concert")
    allday = next(e for e in display if e["name"] == "Book Report Due")
    assert f'href="https://holmes1371.github.io/kidschedules/ics/{timed["id"]}.ics"' in html
    assert f'href="https://holmes1371.github.io/kidschedules/ics/{allday["id"]}.ics"' in html

    # No inline .ics body on any card — we host the files now.
    assert "data-ics=" not in html

    # Undated cards never carry an Add-to-calendar button (no date → no
    # .ics target). They DO carry an Ignore button as of #18; that
    # affordance is pinned by the dedicated undated tests below.
    undated_card_start = html.find('class="event-card undated"')
    assert undated_card_start != -1, "expected an undated card in this fixture"
    undated_slice = html[undated_card_start:undated_card_start + 1500]
    assert "Add to calendar" not in undated_slice


def test_render_html_omits_ics_button_when_pages_url_empty():
    """No pages_url → no host to link to → no button. Dev preview
    degrades gracefully rather than emitting a broken link."""
    display = _classified_display("basic_mixed")
    display = pe.dedupe(display)
    weeks = pe.group_by_week(display)

    html = pe.render_html(
        today=TODAY, weeks=weeks, undated=[],
        total_future=len(display), lookback_days=60, webhook_url="",
        pages_url="",
    )

    assert "Add to calendar" not in html
    assert 'href="https://holmes1371.github.io/kidschedules/ics/' not in html


# ─── render_html ignored + sender markup ─────────────────────────────────


def _render_ignored_fixture(ignored_names: tuple[str, ...] = ()) -> tuple[str, dict[str, dict]]:
    """Load ignored_and_sender fixture, mark the named events as ignored,
    and render the page. Returns (html, {name -> display event dict}) so
    tests can pull the canonical event id for substring asserts."""
    events = load_fixture("ignored_and_sender")
    ignored_ids = frozenset(
        pe._event_id(e["name"], e["date"], e.get("child", ""))
        for e in events if e["name"] in ignored_names
    )
    display, undated, _, _, _, _ = pe.classify(
        events, cutoff=TODAY, horizon=HORIZON, ignored_ids=ignored_ids,
    )
    display = pe.dedupe(display)
    weeks = pe.group_by_week(display)
    html = pe.render_html(
        today=TODAY, weeks=weeks, undated=undated,
        total_future=len(display), lookback_days=60, webhook_url="",
    )
    by_name = {e["name"]: e for e in display}
    return html, by_name


def test_render_html_ignored_card_has_class_and_display_none():
    html, by_name = _render_ignored_fixture(("Ignored With Sender",))
    ev = by_name["Ignored With Sender"]
    # Find just this card to avoid cross-contamination from other cards.
    start = html.find(f'data-event-id="{ev["id"]}"')
    assert start != -1
    card = html[html.rfind("<div", 0, start):html.find("</div>", start) + 6]
    assert "event-card ignored" in card
    assert 'data-ignored="1"' in card
    assert "display:none" in card


def test_render_html_ignored_card_has_unignore_button_not_ignore():
    html, by_name = _render_ignored_fixture(("Ignored With Sender",))
    ev = by_name["Ignored With Sender"]
    start = html.find(f'data-event-id="{ev["id"]}"')
    card = html[start:html.find("</div>", start) + 200]
    # After the card's opening div we should see the Unignore button for this
    # event and no Ignore-event button for the same event.
    assert 'class="unignore-btn"' in card
    assert 'data-event-name="Ignored With Sender"' in card
    assert 'class="ignore-btn"' not in card
    assert "Unignore event" in card


def test_render_html_active_card_has_ignore_button_not_unignore():
    html, by_name = _render_ignored_fixture(ignored_names=())
    ev = by_name["Active With Sender"]
    start = html.find(f'data-event-id="{ev["id"]}"')
    card = html[start:html.find("</div>", start) + 200]
    assert 'class="ignore-btn"' in card
    assert 'class="unignore-btn"' not in card
    assert "Ignore event" in card


def test_render_html_data_sender_attr_present_only_when_sender_set():
    html, _ = _render_ignored_fixture(ignored_names=())
    assert 'data-sender="laes.org"' in html
    assert 'data-sender="greenfield.k12.ny.us"' in html
    # "Active No Sender" and "Ignored No Sender" have empty sender_domain —
    # they must not get a data-sender attribute at all.
    assert 'data-sender=""' not in html


def test_render_html_ignore_sender_button_only_when_sender_set():
    html, by_name = _render_ignored_fixture(ignored_names=())
    # Card with sender → button present.
    with_sender_id = by_name["Active With Sender"]["id"]
    ws_start = html.find(f'data-event-id="{with_sender_id}"')
    ws_end = html.find("</div>\n      </div>", ws_start)
    assert 'class="ignore-sender-btn"' in html[ws_start:ws_end]
    assert "Ignore sender (laes.org)" in html[ws_start:ws_end]

    # Card without sender → no ignore-sender button.
    no_sender_id = by_name["Active No Sender"]["id"]
    ns_start = html.find(f'data-event-id="{no_sender_id}"')
    ns_end = html.find("</div>\n      </div>", ns_start)
    assert 'class="ignore-sender-btn"' not in html[ns_start:ns_end]


def test_render_sender_block_key_freemail_address():
    """#20: freemail senders (gmail.com, yahoo.com, ...) render the
    Ignore-sender button with the full address so two unrelated people
    on the same freemail provider block independently."""
    html, by_name = _render_ignored_fixture(ignored_names=())
    a_id = by_name["Parent Freemail A"]["id"]
    b_id = by_name["Parent Freemail B"]["id"]
    a_start = html.find(f'data-event-id="{a_id}"')
    a_end = html.find("</div>\n      </div>", a_start)
    b_start = html.find(f'data-event-id="{b_id}"')
    b_end = html.find("</div>\n      </div>", b_start)
    assert 'data-sender="parenta@gmail.com"' in html[a_start:a_end]
    assert 'data-sender="parentb@gmail.com"' in html[b_start:b_end]
    assert "Ignore sender (parenta@gmail.com)" in html[a_start:a_end]
    assert "Ignore sender (parentb@gmail.com)" in html[b_start:b_end]
    # Guard: the bare domain must not leak into either card as a button
    # label — that's the exact regression this feature prevents.
    assert "Ignore sender (gmail.com)" not in html


def test_render_sender_block_key_institutional_domain_unchanged():
    """#20: institutional senders keep blocking at domain granularity —
    block_key equals sender_domain for them, so button and data-sender
    render exactly as before the freemail split."""
    html, by_name = _render_ignored_fixture(ignored_names=())
    ev_id = by_name["Active With Sender"]["id"]
    start = html.find(f'data-event-id="{ev_id}"')
    end = html.find("</div>\n      </div>", start)
    assert 'data-sender="laes.org"' in html[start:end]
    assert "Ignore sender (laes.org)" in html[start:end]


def test_render_sender_block_key_missing_suppresses_button():
    """#20: an event with empty sender_block_key emits no data-sender
    attribute and no Ignore-sender button, matching today's behavior
    when attribution fails upstream in main.py."""
    html, by_name = _render_ignored_fixture(ignored_names=())
    ev_id = by_name["Active No Sender"]["id"]
    start = html.find(f'data-event-id="{ev_id}"')
    end = html.find("</div>\n      </div>", start)
    card = html[start:end]
    assert "data-sender=" not in card
    assert 'class="ignore-sender-btn"' not in card


def test_render_protected_still_suppresses_even_with_block_key():
    """#20 load-bearing guarantee: an event whose sender_domain is a
    protected pattern (fcps.edu) does not render the Ignore-sender
    button — even if its sender_block_key carries an address form
    (alice@fcps.edu). The guard keys on sender_domain specifically so
    the domain-level pattern file stays authoritative. The card still
    carries data-sender for sweep-parity, but the button never renders,
    so an address-form block_key cannot reach localSenders or the
    sheet."""
    events = [
        {
            "name": "Protected With Address Block Key",
            "date": "2026-04-22",
            "time": "9:00 AM",
            "location": "School",
            "category": "School Activity",
            "child": "Isla",
            "source": "Teacher email",
            "sender_domain": "fcps.edu",
            "sender_block_key": "alice@fcps.edu",
        }
    ]
    display, undated, _, _, _, _ = pe.classify(
        events, cutoff=TODAY, horizon=HORIZON,
    )
    weeks = pe.group_by_week(pe.dedupe(display))
    html = pe.render_html(
        today=TODAY, weeks=weeks, undated=undated,
        total_future=len(display), lookback_days=60, webhook_url="",
        protected_senders=["fcps.edu"],
    )
    eid = pe._event_id("Protected With Address Block Key",
                       "2026-04-22", "Isla")
    start = html.find(f'data-event-id="{eid}"')
    end = html.find("</div>\n      </div>", start)
    card = html[start:end]
    # The button never renders for protected senders — checked at the
    # card slice so CSS/JS mentions of "ignore-sender-btn" elsewhere in
    # the document don't give a false pass.
    assert '<button class="ignore-sender-btn"' not in card
    assert "Ignore sender (" not in card


def test_render_html_show_ignored_toggle_appears_with_count():
    html, _ = _render_ignored_fixture(("Ignored With Sender", "Ignored No Sender"))
    # Two ignored events in the display buckets (both are dated inside horizon).
    assert 'class="show-ignored-toggle"' in html
    assert "Show ignored (2)" in html
    assert 'data-hide-label="Hide ignored (2)"' in html


def test_render_html_show_ignored_toggle_omitted_when_none_ignored():
    html, _ = _render_ignored_fixture(ignored_names=())
    # The CSS rule for `.show-ignored-toggle` is always in the stylesheet,
    # and the client JS references the same label template for its
    # counter-update helper — what we care about is that the *button
    # element* isn't rendered. Check the attribute form so the test is
    # robust to the label literal appearing elsewhere in the script.
    assert 'class="show-ignored-toggle"' not in html
    assert 'data-show-label="Show ignored (' not in html


# ─── #18 undated-card ignore affordance ──────────────────────────────────


def _undated_card_slice(html: str, eid: str) -> str:
    """Return the full markup span for an undated card by event id.

    Rewinds to the `<div` that opens the card (so class="..." is captured)
    and stops at the card's closing `</div>`, pinned by the 6-space indent
    that wraps every card. No nested event-actions-bottom on undated
    cards, so the end boundary is unambiguous."""
    attr_pos = html.find(f'data-event-id="{eid}"')
    assert attr_pos != -1, f"undated card {eid} not rendered"
    start = html.rfind("<div", 0, attr_pos)
    assert start != -1, "could not locate undated card open"
    end_marker = "</div>\n      </div>"
    end = html.find(end_marker, attr_pos)
    assert end != -1, "could not locate undated card close"
    return html[start:end + len(end_marker)]


def test_render_html_undated_card_has_event_id_and_ignore_button():
    html, _ = _render_ignored_fixture(ignored_names=())
    eid = pe._event_id("Undated Needs Verify", "", "")
    card = _undated_card_slice(html, eid)
    # Parity with dated cards: the card opens with the event-card class
    # and carries the stable id. The Ignore-event button follows.
    assert 'class="event-card undated"' in card
    assert 'class="ignore-btn"' in card
    assert 'data-event-name="Undated Needs Verify"' in card
    assert 'data-event-date=""' in card
    assert 'class="unignore-btn"' not in card
    assert "Ignore event" in card


def test_render_html_undated_ignored_renders_unignore_button():
    html, _ = _render_ignored_fixture(("Undated Needs Verify",))
    eid = pe._event_id("Undated Needs Verify", "", "")
    card = _undated_card_slice(html, eid)
    # Server-side is_ignored=True flips the button + hides the card, same
    # as dated cards. data-ignored-reason="event" lets the click router
    # dispatch Unignore-event correctly on click.
    assert 'class="event-card undated ignored"' in card
    assert 'data-ignored="1"' in card
    assert 'data-ignored-reason="event"' in card
    assert "display:none" in card
    assert 'class="unignore-btn"' in card
    assert "Unignore event" in card
    assert 'class="ignore-btn"' not in card


def test_render_html_undated_card_has_no_ignore_sender_button():
    """Per-event ignore only on undated cards — #18 explicitly excludes
    the Ignore-sender affordance because undated senders are unreliable.
    Even a fixture undated event with a populated sender_domain must not
    render data-sender or the sender button."""
    html, _ = _render_ignored_fixture(ignored_names=())
    eid = pe._event_id("Undated With Sender", "", "")
    card = _undated_card_slice(html, eid)
    assert 'class="ignore-sender-btn"' not in card
    assert "data-sender=" not in card


def test_render_html_show_ignored_toggle_counts_undated_only():
    """Ignoring only an undated event must still surface the Show-ignored
    toggle — otherwise a card ignored on a prior run reloads hidden with
    no way back. #18 extended the server-side count across both buckets."""
    html, _ = _render_ignored_fixture(("Undated Needs Verify",))
    assert 'class="show-ignored-toggle"' in html
    assert "Show ignored (1)" in html


def test_render_html_show_ignored_toggle_counts_dated_plus_undated():
    """Dated and undated ignored events both contribute to the counter."""
    html, _ = _render_ignored_fixture((
        "Ignored With Sender", "Undated Needs Verify",
    ))
    assert "Show ignored (2)" in html
    assert 'data-hide-label="Hide ignored (2)"' in html


# ─── render_html client JS wiring (step 9) ───────────────────────────────

# These tests lock in that the delegated click router in the rendered page
# references each Apps Script action name and the selectors it binds to.
# They're intentionally substring-level — richer behavior is validated
# manually in the browser against the live Apps Script deploy.


def test_render_html_js_posts_action_names():
    html, _ = _render_ignored_fixture(ignored_names=())
    # All three Apps Script actions are wired into the client POST body.
    assert '"action": "ignore"' in html or 'action: "ignore"' in html
    assert '"action": "unignore"' in html or 'action: "unignore"' in html
    assert '"action": "ignore_sender"' in html or 'action: "ignore_sender"' in html


def test_render_html_js_binds_new_selectors():
    html, _ = _render_ignored_fixture(("Ignored With Sender",))
    # Delegated router inspects each of the three button classes.
    assert 'classList.contains("unignore-btn")' in html
    assert 'classList.contains("show-ignored-toggle")' in html
    assert 'classList.contains("ignore-sender-btn")' in html


def test_render_html_js_has_toast_helper():
    html, _ = _render_ignored_fixture(ignored_names=())
    # Single toast helper is used for failure + confirmation paths.
    assert "function showToast(" in html
    assert 'id="toast"' in html or 'createElement("div")' in html


def test_render_html_js_hydration_swaps_button_to_unignore():
    html, _ = _render_ignored_fixture(ignored_names=())
    # Hydration must call setIgnored on locally-ignored cards so the button
    # flips to Unignore even without a server-side round-trip.
    assert "function setIgnored(" in html
    assert "function setActive(" in html


# ─── Ignore-sender sweeps sibling cards locally (6+7 sub-item 13) ────────

# Ignore-sender should hide every sibling card from the same domain in the
# current view (not just toast and wait for the next rebuild). These are
# substring guards — the richer interaction is browser-tested against the
# live Apps Script deploy.


def test_render_html_js_ignore_sender_queries_sibling_cards():
    html, _ = _render_ignored_fixture(ignored_names=())
    # Sweep selector must target cards by data-sender so all siblings are hit.
    assert "querySelectorAll(" in html
    assert ".event-card[data-sender=" in html


def test_render_html_js_ignore_sender_hides_siblings_and_bumps_counter():
    html, _ = _render_ignored_fixture(ignored_names=())
    # Sweep path tags each sibling with reason=sender and bumps the counter
    # by the number actually swept — not the total siblings (already-ignored
    # cards are skipped to avoid double-counting).
    assert 'setIgnored(card, "sender")' in html
    assert "bumpToggle(swept.length)" in html


def test_render_html_js_ignore_sender_reverts_sweep_on_failure():
    html, _ = _render_ignored_fixture(ignored_names=())
    # On POST failure every swept card is restored, the domain is dropped
    # from the ignored-senders store, and the counter is decremented.
    assert "setActive(card)" in html
    assert "saveIgnoredSenders(remainingSenders)" in html
    assert "bumpToggle(-swept.length)" in html


def test_render_html_js_bump_toggle_creates_row_when_absent():
    # Zero → one transition: on a page that built with ignored_n == 0 the
    # toggle row doesn't exist, so bumpToggle(+N) must build it.
    html, _ = _render_ignored_fixture(ignored_names=())
    assert 'createElement("button")' in html
    assert 'insertAdjacentElement("afterend"' in html
    # Decrement with no existing button is a no-op (don't create a negative row).
    assert "if (delta <= 0) return;" in html


# ─── Ignore/Unignore counter parity (ROADMAP #8) ─────────────────────────

# Unignore has always called bumpToggle(-1) on success; Ignore must mirror
# it with bumpToggle(+1) so the "Show ignored (N)" badge stays accurate
# mid-session, plus bumpToggle(-1) in the failure path to unwind the
# optimistic bump.


def test_render_html_js_ignore_event_bumps_counter():
    html, _ = _render_ignored_fixture(ignored_names=())
    # The ignore-btn branch increments, and the catch decrements on failure.
    assert "bumpToggle(1)" in html
    assert "bumpToggle(-1)" in html


def test_render_html_js_unignore_event_still_decrements_counter():
    # Regression guard: the existing unignore path must keep its bumpToggle.
    html, _ = _render_ignored_fixture(ignored_names=())
    # Locate the unignore branch by its class-contains check and assert the
    # decrement is still reachable from there (substring-level — the branch
    # is short and bumpToggle(-1) only appears in two places, both intended).
    u_start = html.find('classList.contains("unignore-btn")')
    assert u_start != -1
    # Scan forward a bounded window for the decrement call.
    assert "bumpToggle(-1)" in html[u_start:u_start + 1500]


# ─── Unignore-sender + sender-column schema bump (sub-item 14) ────────────

# Sub-item 14 adds the unignore-sender action, a dedicated
# `kids_schedule_ignored_senders` localStorage key, a `data-ignored-reason`
# attribute on cards, and flips both Unignore paths to optimistic so their
# latency matches Ignore.


def test_render_html_ignored_card_carries_ignored_reason_event():
    html, by_name = _render_ignored_fixture(("Ignored With Sender",))
    ev = by_name["Ignored With Sender"]
    start = html.find(f'data-event-id="{ev["id"]}"')
    assert start != -1
    card = html[html.rfind("<div", 0, start):html.find("</div>", start) + 6]
    # Server-side is_ignored always means reason=event (individual ignore
    # from ignored_events.json). Sender-swept state is client-only.
    assert 'data-ignored-reason="event"' in card


def test_render_html_css_hides_ignore_sender_btn_on_ignored_card():
    html, _ = _render_ignored_fixture(ignored_names=())
    # CSS rule must hide the Ignore-sender button once a card is in the
    # ignored state (regardless of reason). Simpler than re-rendering.
    assert '.event-card[data-ignored="1"] .ignore-sender-btn' in html
    assert "display: none" in html


def test_render_html_js_ignore_event_posts_sender():
    html, _ = _render_ignored_fixture(ignored_names=())
    # Ignore payload now carries sender so the Apps Script can tag the row
    # for later bulk-delete by Unignore-sender.
    assert "sender: sender" in html
    assert 'action: "ignore"' in html or '"action": "ignore"' in html


def test_render_html_js_ignore_sender_uses_senders_storage_key():
    html, _ = _render_ignored_fixture(ignored_names=())
    # Under X semantics, sender-sweep persists only the domain — not the
    # swept event-ids — so Ignored Events stays a pure record of individual
    # user ignores.
    assert 'SENDERS_STORAGE_KEY = "kids_schedule_ignored_senders"' in html
    assert "saveIgnoredSenders(currentSenders)" in html


def test_render_html_js_has_unignore_sender_handler():
    html, _ = _render_ignored_fixture(ignored_names=())
    # New click branch + new POST action.
    assert 'classList.contains("unignore-sender-btn")' in html
    assert 'action: "unignore_sender"' in html or '"action": "unignore_sender"' in html


def test_render_html_js_unignore_sender_restores_all_matching_cards():
    html, _ = _render_ignored_fixture(ignored_names=())
    u_start = html.find('classList.contains("unignore-sender-btn")')
    assert u_start != -1
    branch = html[u_start:u_start + 3000]
    # Walks every card with matching data-sender and setActive's the ignored ones.
    assert ".event-card[data-sender=" in branch
    assert "setActive(card)" in branch
    # Event-reason ids are dropped from the ids store, domain dropped from senders.
    assert "saveIgnoredSenders(remainingDomains)" in branch
    assert "bumpToggle(-restored.length)" in branch


def test_render_html_js_unignore_event_is_optimistic():
    html, _ = _render_ignored_fixture(ignored_names=())
    u_start = html.find('classList.contains("unignore-btn")')
    assert u_start != -1
    # Bounded window covering just the unignore-event branch.
    branch = html[u_start:u_start + 1200]
    # Optimistic: setActive fires before the POST, not inside its .then.
    set_active_pos = branch.find("setActive(ucard)")
    post_pos = branch.find('postAction({ action: "unignore"')
    assert set_active_pos != -1
    assert post_pos != -1
    assert set_active_pos < post_pos


def test_render_html_js_hydration_reads_both_stores():
    html, _ = _render_ignored_fixture(ignored_names=())
    # Hydration now applies sender-swept state (reason=sender) before
    # individually-ignored ids (reason=event takes precedence on overlap).
    assert "loadIgnoredSenders()" in html
    assert 'setIgnored(card, "sender")' in html
    assert 'setIgnored(card, "event")' in html


# ─── card redesign (Layout A) ────────────────────────────────────────────


def _render_cr() -> tuple[str, list[dict]]:
    """Render the card_redesign fixture with no ignored events. Returns
    (html, display_events) for substring probing in the tests below."""
    events = load_fixture("card_redesign")
    display, undated, _, _, _, _ = pe.classify(
        events, cutoff=TODAY, horizon=HORIZON,
    )
    display = pe.dedupe(display)
    undated = pe.dedupe(undated)
    weeks = pe.group_by_week(display)
    html = pe.render_html(
        today=TODAY, weeks=weeks, undated=undated,
        total_future=len(display), lookback_days=60, webhook_url="",
    )
    return html, display


def _card_slice(html: str, needle: str, width: int = 1200) -> str:
    """Return a single card's HTML by walking back from an event-name
    occurrence to the nearest `<div class="event-card`. Keeps asserts
    from bleeding between cards when substrings are common."""
    idx = html.find(needle)
    assert idx != -1, f"expected to find {needle!r} in render"
    open_idx = html.rfind('<div class="event-card', 0, idx)
    assert open_idx != -1, "no opening event-card tag before needle"
    return html[open_idx:open_idx + width]


def test_layout_a_meta_strip_has_day_label_separator_and_time():
    """Dated cards emit `<div class="meta-strip">` with the abbreviated
    day (e.g. `Thu, Apr 16`), a middot separator, and a `.time` span.
    The old standalone `.event-date` block is gone."""
    html, _ = _render_cr()
    card = _card_slice(html, "Pediatrician Check-up")
    assert 'class="meta-strip"' in card
    assert '<span class="day">Thu, Apr 16</span>' in card
    assert '<span class="sep">·</span>' in card
    assert '<span class="time">3:45 PM</span>' in card
    assert 'class="event-date"' not in card


def test_layout_a_child_chip_renders_for_everly():
    html, _ = _render_cr()
    card = _card_slice(html, "Pediatrician Check-up")
    assert '<span class="child-chip everly" title="Everly">E</span>' in card


def test_layout_a_child_chip_renders_for_isla():
    html, _ = _render_cr()
    card = _card_slice(html, "Soccer Practice")
    assert '<span class="child-chip isla" title="Isla">I</span>' in card


def test_layout_a_no_chip_or_audience_when_child_empty():
    """Early Release Day's fixture entry has child="" — no chip, no
    audience line."""
    html, _ = _render_cr()
    card = _card_slice(html, "Early Release Day")
    assert "child-chip" not in card
    assert "event-audience" not in card


def test_layout_a_audience_line_for_non_kid_child():
    """Yearbook fixture entry's child is "All LAES students" — renders
    as an audience line rather than a chip."""
    html, _ = _render_cr()
    card = _card_slice(html, "Yearbook Photos Submission Deadline")
    assert "child-chip" not in card
    assert '<div class="event-audience">For: All LAES students</div>' in card


def test_layout_a_all_day_pill_for_blank_time():
    """Book Report Due has time="" after the ingest flip — the card
    renders the all-day pill instead of a bare `<span class="time">`."""
    html, _ = _render_cr()
    card = _card_slice(html, "Book Report Due")
    assert '<span class="time allday">All day</span>' in card


def test_layout_a_all_day_pill_for_deadline_time_string():
    """`All day (deadline)` normalizes to the same pill as empty time —
    the user-facing label collapses either shape to `All day`."""
    html, _ = _render_cr()
    card = _card_slice(html, "Yearbook Photos Submission Deadline")
    assert '<span class="time allday">All day</span>' in card


def test_layout_a_time_range_preserved_verbatim():
    """Non-deadline times (including ranges) render verbatim in
    `<span class="time">` — no `.allday` class, no reformatting."""
    html, _ = _render_cr()
    card = _card_slice(html, "Soccer Practice")
    assert '<span class="time">5:30 - 6:45 PM</span>' in card
    assert "time allday" not in card


def test_layout_a_location_renders_when_plain_string():
    html, _ = _render_cr()
    card = _card_slice(html, "Pediatrician Check-up")
    assert ('<div class="event-location">Tysons Pediatrics, '
            '8350 Greensboro Dr</div>') in card


def test_layout_a_url_location_renders_as_link():
    """A drive.google.com URL in the location field renders as a
    clickable <a href> anchor (not suppressed) — Ellen can tap
    straight through to the destination from the card. Per the #29
    follow-up: URLs are useful info, just not as a static "place"
    label.

    Anchors carry target="_blank" + rel="noopener noreferrer" so
    the click opens a new tab without leaking session via
    window.opener."""
    html, _ = _render_cr()
    card = _card_slice(html, "Yearbook Photos Submission Deadline")
    assert 'class="event-location"' in card
    assert 'drive.google.com' in card
    assert 'target="_blank"' in card
    assert 'rel="noopener noreferrer"' in card
    # Link target is the original https URL.
    assert 'href="https://drive.google.com/' in card


def test_layout_a_empty_location_emits_no_location_div():
    """Book Report Due has location="" after the ingest flip — the
    card should not emit any `.event-location` element at all."""
    html, _ = _render_cr()
    card = _card_slice(html, "Book Report Due")
    assert "event-location" not in card


def test_layout_a_drops_deprecated_badge_and_meta_classes():
    """Regression guard: none of the pre-Layout-A class names leak
    onto any rendered card (checks whole HTML, not a slice, so we
    catch orphans anywhere in the render)."""
    html, _ = _render_cr()
    assert 'class="badge"' not in html
    assert 'class="event-meta"' not in html
    assert 'class="event-details"' not in html
    assert '<span class="child"' not in html
    assert '<span class="source"' not in html
    assert 'class="event-date"' not in html


def test_layout_a_undated_card_uses_meta_strip_with_date_tbd():
    """The Needs-Verification card shares the Layout A shell — same
    meta-strip, child-chip, all-day pill — but with `Date TBD` as the
    day label. `camps.fcps.edu` is a bare domain (not http:// and not
    an email), so the location line still renders. Per #29 it carries
    the new "Location: " prefix (bare domain is NOT address-shape) AND
    the bare domain itself is wrapped in <a href> with implicit
    https:// scheme (per #29 v2 linkification)."""
    raw = [{
        "name": "Camp Signup",
        "date": "",
        "time": "All day (deadline)",
        "location": "camps.fcps.edu",
        "category": "Academic Due Date",
        "child": "Isla",
        "source": "FCPS camps (Apr 3)",
        "sender_domain": "fcps.edu",
    }]
    _, undated, _, _, _, _ = pe.classify(
        raw, cutoff=TODAY, horizon=HORIZON,
    )
    undated = pe.dedupe(undated)
    html = pe.render_html(
        today=TODAY, weeks=[], undated=undated,
        total_future=0, lookback_days=60, webhook_url="",
    )
    idx = html.find('class="event-card undated"')
    assert idx != -1, "expected an undated card in this render"
    card = html[idx:idx + 1500]
    assert 'class="meta-strip"' in card
    assert '<span class="day">Date TBD</span>' in card
    assert '<span class="time allday">All day</span>' in card
    assert '<span class="child-chip isla" title="Isla">I</span>' in card
    # Multi-piece assertion (more robust against future cosmetic
    # changes than a single brittle string match): location div present,
    # "Location: " prefix preserved BEFORE the anchor, bare domain
    # linkified with implicit https://.
    assert 'class="event-location"' in card
    assert 'Location: <a ' in card
    assert 'href="https://camps.fcps.edu"' in card
    assert 'target="_blank"' in card
    assert 'rel="noopener noreferrer"' in card
    assert '>camps.fcps.edu</a>' in card
    # Parallel regression guards:
    assert 'class="event-date"' not in card
    assert 'class="badge"' not in card


def test_layout_a_css_ships_new_tokens_and_selectors():
    """Smoke-check that the CSS block ships the Layout A design tokens
    and selectors — catches accidental stylesheet regressions."""
    html, _ = _render_cr()
    # Design tokens
    assert "--text-tertiary" in html
    assert "--everly" in html
    assert "--isla" in html
    # Selectors
    assert ".meta-strip" in html
    assert ".child-chip.everly" in html
    assert ".child-chip.isla" in html
    assert ".meta-strip .time.allday" in html
    assert ".event-location" in html
    assert ".event-audience" in html


# ─── per-kid filter chips (#12) ──────────────────────────────────────────


def test_filter_chip_row_is_present():
    """The top-of-page chip row renders three buttons (All, Everly, Isla)
    each with a stable data-filter-child attribute the JS reads."""
    html, _ = _render_cr()
    assert 'class="filter-chips"' in html
    assert 'data-filter-child="all"' in html
    assert 'data-filter-child="everly"' in html
    assert 'data-filter-child="isla"' in html
    assert ">All</button>" in html
    assert ">Everly</button>" in html
    assert ">Isla</button>" in html


def test_filter_chip_row_is_static_not_derived_from_events():
    """The chip row is hard-coded — it does NOT iterate over unique
    children in the input. Render against an Isla-only fixture: all
    three chips must still appear."""
    isla_only = [{
        "name": "Solo Isla Event",
        "date": "2026-04-21",
        "time": "5:30 PM",
        "location": "Wakefield Park",
        "category": "Sports & Extracurriculars",
        "child": "Isla",
        "source": "test",
        "sender_domain": "teamsnap.com",
    }]
    display, undated, _, _, _, _ = pe.classify(
        isla_only, cutoff=TODAY, horizon=HORIZON,
    )
    display = pe.dedupe(display)
    weeks = pe.group_by_week(display)
    html = pe.render_html(
        today=TODAY, weeks=weeks, undated=undated,
        total_future=len(display), lookback_days=60, webhook_url="",
    )
    assert 'data-filter-child="all"' in html
    assert 'data-filter-child="everly"' in html
    assert 'data-filter-child="isla"' in html


def test_event_card_carries_data_child_everly():
    html, _ = _render_cr()
    card = _card_slice(html, "Pediatrician Check-up")
    assert 'data-child="everly"' in card


def test_event_card_carries_data_child_isla():
    html, _ = _render_cr()
    card = _card_slice(html, "Soccer Practice")
    assert 'data-child="isla"' in card


def test_event_card_data_child_empty_for_audience_line():
    """Audience-line cards (e.g. `All LAES students`) render with
    `data-child=""` so kid filters leave them visible."""
    html, _ = _render_cr()
    card = _card_slice(html, "Yearbook Photos Submission Deadline")
    assert 'data-child=""' in card


def test_event_card_data_child_empty_for_empty_child():
    """Cards with no child at all render with `data-child=""`; no kid
    filter should hide them."""
    html, _ = _render_cr()
    card = _card_slice(html, "Early Release Day")
    assert 'data-child=""' in card


def test_filter_hide_css_uses_important():
    """Regression guard for the `.show-ignored` interaction: the filter
    hide rule needs `!important` to match the specificity of
    `.show-ignored .event-card.ignored { display: block !important; }`,
    otherwise an ignored Isla card would stay visible when the Everly
    filter is active and Show-ignored is on."""
    html, _ = _render_cr()
    assert 'body.filter-everly .event-card[data-child="isla"]' in html
    assert 'body.filter-isla' in html
    assert 'display: none !important' in html


def test_undated_card_carries_data_child():
    """Parallel change to `_undated_card`: filters apply to the Needs
    Verification section the same way as to dated weeks."""
    raw = [{
        "name": "Camp Signup",
        "date": "",
        "time": "All day (deadline)",
        "location": "camps.fcps.edu",
        "category": "Academic Due Date",
        "child": "Isla",
        "source": "FCPS camps (Apr 3)",
        "sender_domain": "fcps.edu",
    }]
    _, undated, _, _, _, _ = pe.classify(
        raw, cutoff=TODAY, horizon=HORIZON,
    )
    undated = pe.dedupe(undated)
    html = pe.render_html(
        today=TODAY, weeks=[], undated=undated,
        total_future=0, lookback_days=60, webhook_url="",
    )
    idx = html.find('class="event-card undated"')
    assert idx != -1, "expected an undated card in this render"
    card = html[idx:idx + 1500]
    assert 'data-child="isla"' in card


# ─── #19 roster-backed attribution (data-child derivation) ───────────────
#
# These tests exercise the end-to-end rendering path: given an event
# whose `child` field is an audience string (or empty) but whose other
# fields carry a roster signal (grade, teacher, activity), the card must
# render with both the kid pill AND a `data-child` attribute the #12
# filter chips can hide.


def _render_single(raw: list[dict]) -> str:
    """Render one or more candidate events into HTML for card-level asserts."""
    display, undated, _, _, _, _ = pe.classify(
        raw, cutoff=TODAY, horizon=HORIZON,
    )
    display = pe.dedupe(display)
    weeks = pe.group_by_week(display)
    return pe.render_html(
        today=TODAY, weeks=weeks, undated=undated,
        total_future=len(display), lookback_days=60, webhook_url="",
    )


def test_grade_in_child_field_attributes_to_everly():
    """Tom's first reported miss: a '6th grade AAP' audience string in
    the child field must surface as Everly's card under #19, because
    Everly is in 6th grade per class_roster.json."""
    html = _render_single([{
        "name": "AAP Enrichment Activity",
        "date": "2026-04-20",
        "time": "10:00 AM",
        "location": "Louise Archer Elementary",
        "category": "School Activity",
        "child": "6th grade AAP",
        "source": "LAES PTA Sunbeam (Apr 10)",
    }])
    card = _card_slice(html, "AAP Enrichment Activity")
    assert 'data-child="everly"' in card
    # Pill renders as well (not just the audience line) so the kid is
    # visually tagged in the schedule, not buried in a "For:" line.
    assert 'class="child-chip everly"' in card
    # The original audience context stays — E pill + "For: 6th grade AAP"
    # is the agreed display (design note §Rendering impact).
    assert "For: 6th grade AAP" in card


def test_rising_grade_in_child_field_attributes_to_everly():
    """Spring newsletters start talking about next year. 'Rising 7th
    grader' events must attach to Everly, who advances to 7th."""
    html = _render_single([{
        "name": "Rising 7th Grade Info Night",
        "date": "2026-05-05",
        "time": "7:00 PM",
        "location": "Kilmer Middle School",
        "category": "School Activity",
        "child": "rising 7th graders",
        "source": "FCPS Middle School Transition (Apr 14)",
    }])
    card = _card_slice(html, "Rising 7th Grade Info Night")
    assert 'data-child="everly"' in card
    assert 'class="child-chip everly"' in card


def test_rising_grade_in_child_field_attributes_to_isla():
    """Parallel to Everly: 4th grade events route to Isla (currently 3rd)."""
    html = _render_single([{
        "name": "4th Grade Field Trip Planning",
        "date": "2026-05-12",
        "time": "6:30 PM",
        "location": "LAES Cafeteria",
        "category": "School Activity",
        "child": "4th grade parents",
        "source": "LAES PTA (Apr 12)",
    }])
    card = _card_slice(html, "4th Grade Field Trip Planning")
    assert 'data-child="isla"' in card
    assert 'class="child-chip isla"' in card


def test_activity_in_source_attributes_to_isla():
    """Tom's second reported miss: a Cuppett Performing Arts email with
    an empty `child` field must route to Isla via the activity tier."""
    html = _render_single([{
        "name": "Spring Recital Rehearsal",
        "date": "2026-05-09",
        "time": "4:30 PM",
        "location": "Cuppett Performing Arts Center",
        "category": "Sports & Extracurriculars",
        "child": "",
        "source": "Cuppett Performing Arts Center (Apr 10)",
    }])
    card = _card_slice(html, "Spring Recital Rehearsal")
    assert 'data-child="isla"' in card
    assert 'class="child-chip isla"' in card
    # No audience line on activity matches with empty child field —
    # nothing worth surfacing beside the pill.
    assert "For:" not in card


def test_activity_first_word_alias_attributes_to_isla():
    """Shortened activity mentions ("Cuppett" alone, without "Performing
    Arts Center") still trigger attribution via the first-word alias."""
    html = _render_single([{
        "name": "Ballet Class",
        "date": "2026-04-24",
        "time": "5:00 PM",
        "location": "Vienna Town Center",
        "category": "Sports & Extracurriculars",
        "child": "",
        "source": "Cuppett reminder (Apr 10)",
    }])
    card = _card_slice(html, "Ballet Class")
    assert 'data-child="isla"' in card


def test_activity_parenthetical_alias_attributes_to_everly():
    """B2D is the implicit acronym alias for Everly's dance studio."""
    html = _render_single([{
        "name": "B2D Showcase",
        "date": "2026-05-02",
        "time": "6:00 PM",
        "location": "Dance Studio",
        "category": "Sports & Extracurriculars",
        "child": "",
        "source": "B2D monthly update (Apr 9)",
    }])
    card = _card_slice(html, "B2D Showcase")
    assert 'data-child="everly"' in card


def test_teacher_last_name_attributes_to_everly():
    """'Ms. Sahai' in the event text should tag the card as Everly
    without a name or grade hint."""
    html = _render_single([{
        "name": "Parent Conference",
        "date": "2026-04-22",
        "time": "3:15 PM",
        "location": "Room 204",
        "category": "Appointment",
        "child": "",
        "source": "Ms. Sahai's classroom (Apr 8)",
    }])
    card = _card_slice(html, "Parent Conference")
    assert 'data-child="everly"' in card


def test_shared_school_does_not_attribute_kid():
    """Regression guard: LAES is shared across both kids, so 'All LAES
    students' must still render with data-child='' (#12 non-lossy
    behavior — school-wide events stay visible under every filter)."""
    html = _render_single([{
        "name": "Yearbook Order Deadline",
        "date": "2026-04-30",
        "time": "All day (deadline)",
        "location": "",
        "category": "Academic Due Date",
        "child": "All LAES students",
        "source": "LAES PTA (Apr 11)",
    }])
    card = _card_slice(html, "Yearbook Order Deadline")
    assert 'data-child=""' in card
    # No kid pill either
    assert "child-chip" not in card
    # Audience line preserved for context
    assert "For: All LAES students" in card


def test_name_match_still_suppresses_audience_line():
    """A clean `child="Isla"` extraction must keep the pre-#19 render:
    the I pill alone, no audience line. Regression guard for the
    rendering branch that suppresses audience on tier-1 name matches."""
    html = _render_single([{
        "name": "Pediatrician Check-up",
        "date": "2026-04-23",
        "time": "3:45 PM",
        "location": "Tysons Pediatrics",
        "category": "Appointment",
        "child": "Isla",
        "source": "MyChart reminder (Apr 2)",
    }])
    card = _card_slice(html, "Pediatrician Check-up")
    assert 'data-child="isla"' in card
    assert 'class="child-chip isla"' in card
    assert "For: Isla" not in card


def test_undated_card_uses_roster_derivation():
    """The undated rendering path shares _child_markup; a 6th-grade
    undated event should pick up Everly attribution the same way."""
    raw = [{
        "name": "6th Grade AAP Signup",
        "date": "",
        "time": "All day (deadline)",
        "location": "",
        "category": "Academic Due Date",
        "child": "6th grade AAP",
        "source": "LAES PTA (Apr 11)",
    }]
    _, undated, _, _, _, _ = pe.classify(
        raw, cutoff=TODAY, horizon=HORIZON,
    )
    undated = pe.dedupe(undated)
    html = pe.render_html(
        today=TODAY, weeks=[], undated=undated,
        total_future=0, lookback_days=60, webhook_url="",
    )
    idx = html.find('class="event-card undated"')
    assert idx != -1
    card = html[idx:idx + 1500]
    assert 'data-child="everly"' in card
    assert 'class="child-chip everly"' in card
    assert "For: 6th grade AAP" in card


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


# ─── outlier alerts — loader, renderers, digest integration (#17) ────────


_SAMPLE_ALERT = {
    "sender": "newsletter@example.com",
    "message_id": "18e4f2abc",
    "prior_median": 6,
    "current_count": 1,
    "threshold": 3,
}


@pytest.mark.parametrize(
    "file_contents,expected",
    [
        (None, []),                                           # file missing
        ("not json at all", []),                              # malformed JSON
        ('{"not": "a list"}', []),                            # wrong shape
        ('"also not a list"', []),                            # wrong shape
        ('[]', []),                                           # empty list
        (
            json.dumps([_SAMPLE_ALERT]),
            [_SAMPLE_ALERT],
        ),                                                    # happy path
        (
            json.dumps([_SAMPLE_ALERT, "garbage", 42]),
            [_SAMPLE_ALERT],
        ),                                                    # non-dict entries skipped
    ],
)
def test_load_outlier_alerts_tolerant(tmp_path, file_contents, expected):
    path = tmp_path / "alerts.json"
    if file_contents is None:
        result = pe._load_outlier_alerts(str(path))
    else:
        path.write_text(file_contents, encoding="utf-8")
        result = pe._load_outlier_alerts(str(path))
    assert result == expected


def test_load_outlier_alerts_empty_path_returns_empty():
    assert pe._load_outlier_alerts("") == []
    assert pe._load_outlier_alerts(None) == []


def test_render_outlier_block_text_empty_returns_empty_list():
    assert pe._render_outlier_block_text([]) == []


def test_render_outlier_block_text_renders_alerts():
    alerts = [_SAMPLE_ALERT]
    lines = pe._render_outlier_block_text(alerts)
    # Block always leads with the warning header and ends with a blank line
    # so downstream concatenation gets a visual separator from the event list.
    assert lines[0] == "⚠️ Possible under-extraction:"
    assert lines[-1] == ""
    # The alert line carries sender, message ID, count, prior median, threshold.
    alert_line = lines[1]
    assert "newsletter@example.com" in alert_line
    assert "18e4f2abc" in alert_line
    assert "1 event" in alert_line  # singular
    assert "prior median 6" in alert_line
    assert "threshold 3" in alert_line
    # --reextract hint is present so the operator has the re-run recipe inline.
    assert any("--reextract" in line for line in lines)


def test_render_outlier_block_text_event_pluralization():
    two_events = dict(_SAMPLE_ALERT, current_count=2)
    lines = pe._render_outlier_block_text([two_events])
    assert any("2 events" in line for line in lines)


def test_render_outlier_block_html_empty_returns_empty_list():
    assert pe._render_outlier_block_html([]) == []


def test_render_outlier_block_html_renders_alerts():
    lines = pe._render_outlier_block_html([_SAMPLE_ALERT])
    html = "\n".join(lines)
    assert "⚠️ Possible under-extraction" in html
    assert "newsletter@example.com" in html
    assert "18e4f2abc" in html
    assert "1 event" in html
    assert "prior median 6" in html
    assert "threshold 3" in html
    # Amber warning styling is the block's visual cue.
    assert "#f9a825" in html or "fff8e1" in html


def test_render_outlier_block_html_escapes_untrusted_fields():
    # Sender and message_id originate from Gmail headers → untrusted.
    evil = {
        "sender": "<script>alert(1)</script>@x.com",
        "message_id": "<img src=x onerror=1>",
        "prior_median": 5,
        "current_count": 0,
        "threshold": 3,
    }
    html = "\n".join(pe._render_outlier_block_html([evil]))
    assert "<script>" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;img" in html


def test_render_digest_text_injects_alert_block_above_events():
    weeks = _digest_weeks("digest_this_week")
    text = pe.render_digest_text(
        weeks, TODAY, pages_url="", alerts=[_SAMPLE_ALERT]
    )
    # The warning header precedes the first event line.
    subject_idx = text.index(pe.digest_subject(TODAY))
    alert_idx = text.index("⚠️ Possible under-extraction")
    first_event_idx = text.index("Art & Crafts")
    assert subject_idx < alert_idx < first_event_idx


def test_render_digest_text_no_alerts_matches_prior_behavior():
    weeks = _digest_weeks("digest_this_week")
    text_none = pe.render_digest_text(weeks, TODAY, pages_url="")
    text_empty = pe.render_digest_text(
        weeks, TODAY, pages_url="", alerts=[]
    )
    assert text_none == text_empty
    assert "Possible under-extraction" not in text_none


def test_render_digest_html_injects_alert_block_above_events():
    weeks = _digest_weeks("digest_this_week")
    html = pe.render_digest_html(
        weeks, TODAY, pages_url="", alerts=[_SAMPLE_ALERT]
    )
    alert_idx = html.index("Possible under-extraction")
    event_list_idx = html.index("<ul")
    assert alert_idx < event_list_idx


def test_render_digest_html_no_alerts_omits_block():
    weeks = _digest_weeks("digest_this_week")
    html = pe.render_digest_html(weeks, TODAY, pages_url="")
    assert "Possible under-extraction" not in html


def test_digest_cli_forwards_outlier_alerts_flag(tmp_path):
    """End-to-end: --outlier-alerts reaches the rendered digest bodies."""
    candidates_path = FIXTURES_DIR / "digest_this_week.json"
    alerts_path = tmp_path / "alerts.json"
    alerts_path.write_text(json.dumps([_SAMPLE_ALERT]), encoding="utf-8")
    dtext_path = tmp_path / "digest.txt"
    dhtml_path = tmp_path / "digest.html"
    meta_path = tmp_path / "meta.json"
    body_path = tmp_path / "body.txt"
    subprocess.run(
        [sys.executable,
         str(REPO_ROOT / "scripts" / "process_events.py"),
         "--candidates", str(candidates_path),
         "--today", TODAY.isoformat(),
         "--body-out", str(body_path),
         "--meta-out", str(meta_path),
         "--digest-text-out", str(dtext_path),
         "--digest-html-out", str(dhtml_path),
         "--outlier-alerts", str(alerts_path),
         ],
        check=True,
    )
    text = dtext_path.read_text(encoding="utf-8")
    html = dhtml_path.read_text(encoding="utf-8")
    assert "Possible under-extraction" in text
    assert "18e4f2abc" in text
    assert "Possible under-extraction" in html
    assert "18e4f2abc" in html


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


@pytest.mark.parametrize("raw,expected", [
    ("https://holmes1371.github.io/kidschedules/", "holmes1371.github.io/kidschedules/"),
    ("http://example.com/x", "example.com/x/"),
    ("holmes1371.github.io/kidschedules/", "holmes1371.github.io/kidschedules/"),
    ("", ""),
    ("   ", ""),
    ("https://a.b/", "a.b/"),
])
def test_webcal_base(raw, expected):
    assert pe._webcal_base(raw) == expected


def test_write_ics_files_writes_one_per_event_and_wipes_stale(tmp_path):
    """write_ics_files should write {event_id}.ics per dated event and
    remove any .ics file that isn't in the current set."""
    out_dir = tmp_path / "ics"
    out_dir.mkdir()
    # Pre-seed a stale .ics and an unrelated file that must survive.
    (out_dir / "stale123.ics").write_text("STALE", encoding="utf-8")
    (out_dir / "keepme.txt").write_text("KEEP", encoding="utf-8")

    display = _classified_display("basic_mixed")
    display = pe.dedupe(display)

    count = pe.write_ics_files(display, str(out_dir), now=ICS_NOW)

    assert count == len(display)
    # Stale file is gone.
    assert not (out_dir / "stale123.ics").exists()
    # Non-.ics file is untouched.
    assert (out_dir / "keepme.txt").read_text() == "KEEP"
    # Every dated event has a file at {id}.ics with a VCALENDAR body.
    for ev in display:
        fp = out_dir / f"{ev['id']}.ics"
        assert fp.exists()
        body = fp.read_text()
        assert body.startswith("BEGIN:VCALENDAR")
        assert f"UID:{ev['id']}@" in body


@pytest.mark.parametrize("raw,expected", [
    # Shared end meridian, various separators
    ("2 PM - 5 PM", ((14, 0), (17, 0))),
    ("2:00 PM - 5:00 PM", ((14, 0), (17, 0))),
    ("2PM-5PM", ((14, 0), (17, 0))),
    ("2:00 PM \u2013 5:00 PM", ((14, 0), (17, 0))),   # en dash
    ("2:00 PM \u2014 5:00 PM", ((14, 0), (17, 0))),   # em dash
    ("2 PM to 5 PM", ((14, 0), (17, 0))),
    # Start meridian omitted → shared with end
    ("2-5 PM", ((14, 0), (17, 0))),
    ("2:30-4:00 PM", ((14, 30), (16, 0))),
    # Start meridian omitted, shared would put start after end → flip
    ("11-1 PM", ((11, 0), (13, 0))),
    ("11:30-12:30 PM", ((11, 30), (12, 30))),
    # Crossing meridian with both meridians explicit
    ("10 AM - 12 PM", ((10, 0), (12, 0))),
    ("11:00 AM - 1:00 PM", ((11, 0), (13, 0))),
    # Rejects
    ("Time TBD", None),
    ("2 PM", None),
    ("2-5", None),
    ("", None),
])
def test_parse_time_range(raw, expected):
    got = pe._parse_time_range(raw)
    if expected is None:
        assert got is None
    else:
        (sh, sm), (eh, em) = expected
        assert got == (dt.time(sh, sm), dt.time(eh, em))


@pytest.mark.parametrize("start,end,expected", [
    ((14, 0), (17, 0), "PT3H"),
    ((14, 30), (16, 0), "PT1H30M"),
    ((10, 0), (10, 30), "PT30M"),
    ((10, 0), (10, 0), "PT1H"),   # degenerate: fallback to default
    ((10, 0), (9, 0), "PT1H"),    # invalid: fallback
])
def test_format_ics_duration(start, end, expected):
    assert pe._format_ics_duration(dt.time(*start), dt.time(*end)) == expected


def test_build_ics_range_snapshot():
    ev = {
        "id": "",  # let build_ics compute the deterministic ID
        "name": "Peter Pan Ballet Camp",
        "date": "2026-04-22",
        "time": "2:00 PM - 5:00 PM",
        "location": "Dance Studio",
        "child": "Ellen",
        "source": "camp email",
        "category": "Sports & Extracurriculars",
    }
    assert pe.build_ics(ev, now=ICS_NOW) == read_snapshot("ics_range_event")


def test_write_ics_files_skips_undated_events(tmp_path):
    """Events with no parseable date cause build_ics to raise; the writer
    should silently skip them rather than crashing the whole run."""
    out_dir = tmp_path / "ics"
    events = [
        {"id": "abc123def456", "name": "TBD event", "date": "", "time": "",
         "location": "", "child": "", "source": ""},
        {"id": "f00dcafe0000", "name": "Real Event", "date": "2026-04-20",
         "time": "10:00 AM", "location": "", "child": "", "source": ""},
    ]
    count = pe.write_ics_files(events, str(out_dir), now=ICS_NOW)
    assert count == 1
    assert (out_dir / "f00dcafe0000.ics").exists()
    assert not (out_dir / "abc123def456.ics").exists()


# ─── prior-events manifest + NEW badges (#13) ────────────────────────────


def test_load_prior_event_ids_missing_file(tmp_path):
    """First-run graceful-degradation: no file → None (suppress badges).

    `None` is distinct from `set()` — the caller reads `None` as "no
    prior state, don't badge anything this run". See
    design/new-this-week-badges.md for the missing-vs-empty contract."""
    result = pe._load_prior_event_ids(str(tmp_path / "missing.json"))
    assert result is None


def test_load_prior_event_ids_empty_path():
    """Empty/None path = feature disabled → returns None, no exception."""
    assert pe._load_prior_event_ids("") is None
    assert pe._load_prior_event_ids(None) is None


def test_load_prior_event_ids_empty_list(tmp_path):
    """File present, event_ids is empty → returns empty set (NOT None).

    This is the "prior render had zero events" case — a legitimate
    state, distinct from the first-run missing-file case. Caller
    should badge every current event under this loader result."""
    path = tmp_path / "prior.json"
    path.write_text(
        '{"generated_at_iso": "2026-04-10T00:00:00Z", "event_ids": []}',
        encoding="utf-8",
    )
    result = pe._load_prior_event_ids(str(path))
    assert result == set()
    assert result is not None


def test_load_prior_event_ids_happy_path(tmp_path):
    path = tmp_path / "prior.json"
    path.write_text(
        '{"generated_at_iso": "2026-04-10T00:00:00Z", '
        '"event_ids": ["abc123abc123", "def456def456", "789xyz789xyz"]}',
        encoding="utf-8",
    )
    result = pe._load_prior_event_ids(str(path))
    assert result == {"abc123abc123", "def456def456", "789xyz789xyz"}


def test_load_prior_event_ids_malformed_json(tmp_path, capsys):
    """Unparseable JSON degrades to None + a stdout warning so the
    workflow log surfaces the problem. Suppressing badges on a corrupt
    prior is safer than silently flagging every event as new."""
    path = tmp_path / "prior.json"
    path.write_text("{not valid json", encoding="utf-8")
    result = pe._load_prior_event_ids(str(path))
    assert result is None
    assert "WARNING" in capsys.readouterr().out


def test_load_prior_event_ids_wrong_shape_list(tmp_path, capsys):
    """A bare JSON list (e.g. a hand-hacked file) fails shape check →
    returns None with warning. The loader only accepts an object with
    an `event_ids` list."""
    path = tmp_path / "prior.json"
    path.write_text('["abc", "def"]', encoding="utf-8")
    result = pe._load_prior_event_ids(str(path))
    assert result is None
    assert "WARNING" in capsys.readouterr().out


def test_load_prior_event_ids_missing_event_ids_key(tmp_path, capsys):
    """Object present but `event_ids` absent → None + warning."""
    path = tmp_path / "prior.json"
    path.write_text(
        '{"generated_at_iso": "2026-04-10T00:00:00Z"}',
        encoding="utf-8",
    )
    result = pe._load_prior_event_ids(str(path))
    assert result is None
    assert "WARNING" in capsys.readouterr().out


def test_save_prior_event_ids_roundtrip(tmp_path):
    path = str(tmp_path / "prior.json")
    ids = {"abc", "def", "ghi"}
    pe._save_prior_event_ids(path, ids, "2026-04-17T10:15:32Z")
    assert pe._load_prior_event_ids(path) == ids


def test_save_prior_event_ids_writes_sorted_and_stamped(tmp_path):
    """Committed manifest is sorted so week-over-week diffs on the state
    branch are readable. generated_at_iso is stamped verbatim."""
    path = str(tmp_path / "prior.json")
    pe._save_prior_event_ids(
        path, {"zzz", "aaa", "mmm"}, "2026-04-17T10:15:32Z",
    )
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["event_ids"] == ["aaa", "mmm", "zzz"]
    assert data["generated_at_iso"] == "2026-04-17T10:15:32Z"


def test_save_prior_event_ids_atomic(tmp_path):
    """Writer goes via `<path>.tmp` + os.replace so an interrupted write
    cannot leave a truncated manifest. After a successful save, the
    `.tmp` file must not linger."""
    path = str(tmp_path / "prior.json")
    pe._save_prior_event_ids(path, {"abc"}, "2026-04-17T10:15:32Z")
    assert not (tmp_path / "prior.json.tmp").exists()
    assert (tmp_path / "prior.json").exists()


def _render_basic_mixed(new_ids):
    """Render the basic_mixed fixture with a given new_ids set and return
    the HTML. Factors out the classify→dedupe→render boilerplate shared
    by the #13 render tests below."""
    events = load_fixture("basic_mixed")
    display, undated, _, _, _, _ = pe.classify(
        events, cutoff=TODAY, horizon=HORIZON,
    )
    display = pe.dedupe(display)
    undated = pe.dedupe(undated)
    weeks = pe.group_by_week(display)
    return pe.render_html(
        today=TODAY, weeks=weeks, undated=undated,
        total_future=len(display), lookback_days=60, webhook_url="",
        new_ids=new_ids,
    )


def test_render_html_new_badge_when_id_absent_from_prior():
    """An event whose ID appears in new_ids renders with a NEW span inside
    its event-name div. Badge sits immediately after the name text — the
    inline-with-title placement is a committed design decision."""
    spring_id = pe._event_id("Spring Concert", "2026-04-23", "Isla")
    html = _render_basic_mixed(new_ids={spring_id})
    assert (
        '<div class="event-name">Spring Concert'
        '<span class="new-badge">NEW</span></div>'
    ) in html


def test_render_html_no_new_badge_when_id_absent_from_new_ids():
    """Cards whose IDs aren't in new_ids render without the badge even
    when OTHER cards are new — verifies the per-card branch."""
    spring_id = pe._event_id("Spring Concert", "2026-04-23", "Isla")
    html = _render_basic_mixed(new_ids={spring_id})
    # Pediatrician Check-up's ID isn't in new_ids → no badge on that card.
    ped_card = _card_slice(html, "Pediatrician Check-up")
    assert "new-badge" not in ped_card


def test_render_html_new_badge_on_undated_card():
    """Per Q1 of the approved plan: undated "Needs Verification" cards
    also carry the NEW badge. Their IDs are disjoint from dated IDs (the
    #18 id-collision contract), so the same badging rule applies."""
    undated_id = pe._event_id("Summer Camp Registration", "", "")
    html = _render_basic_mixed(new_ids={undated_id})
    assert (
        '<div class="event-name">Summer Camp Registration'
        '<span class="new-badge">NEW</span></div>'
    ) in html


def test_render_html_no_badges_when_new_ids_none_first_run():
    """new_ids=None is the first-run signal (no prior manifest to diff
    against). No card should render the badge."""
    html = _render_basic_mixed(new_ids=None)
    # CSS rule always ships; assert the <span> isn't emitted on any card.
    assert '<span class="new-badge">' not in html


def test_render_html_no_badges_when_new_ids_empty_set():
    """Post-first-run but with no new events (everything was in prior) →
    new_ids=set(). No badges render."""
    html = _render_basic_mixed(new_ids=set())
    assert '<span class="new-badge">' not in html


def test_render_html_new_badge_css_rule_always_present():
    """The .new-badge CSS rule ships in the inline <style> block
    unconditionally — same file is served whether or not any card is
    currently badged, and the rule costs nothing when unused."""
    html = _render_basic_mixed(new_ids=None)
    assert ".new-badge" in html
    # Tied to the accent colour token so the badge tracks the site palette.
    assert "background: var(--accent)" in html


def test_render_html_new_badge_survives_on_ignored_card():
    """Per Q3 of the approved plan: ignored-but-new events still carry
    the NEW span in rendered HTML. The card's .ignored CSS rule hides
    the whole card by default; the badge only appears when the user
    clicks "Show ignored" — no special-case suppression needed."""
    ignored_id = pe._event_id("Ignored With Sender", "2026-04-20", "Isla")
    events = load_fixture("ignored_and_sender")
    display, undated, _, _, _, _ = pe.classify(
        events, cutoff=TODAY, horizon=HORIZON,
        ignored_ids=frozenset([ignored_id]),
    )
    display = pe.dedupe(display)
    undated = pe.dedupe(undated)
    weeks = pe.group_by_week(display)
    html = pe.render_html(
        today=TODAY, weeks=weeks, undated=undated,
        total_future=len(display), lookback_days=60, webhook_url="",
        new_ids={ignored_id},
    )
    # Precondition: the card is rendered as server-side-ignored, so the
    # badge-on-ignored assertion below isn't vacuous.
    start = html.find(f'data-event-id="{ignored_id}"')
    assert start != -1
    card_open = html.rfind("<div", 0, start)
    assert 'class="event-card ignored"' in html[card_open:start + 200]
    # Ignored AND new → badge renders. CSS hides the whole card until
    # "Show ignored" is clicked; at that point the user sees the badge.
    assert (
        'Ignored With Sender<span class="new-badge">NEW</span></div>'
    ) in html


# ─── #29: source line + Location: prefix ──────────────────────────────────
#
# `_is_address_like` is the heuristic the card render uses to decide
# whether to prefix a location with "Location: " (#29). Pure-function
# unit tests run on every platform; the render-integration tests below
# go through render_html and currently fail on Windows due to the
# unrelated `%-d` strftime issue but pass on CI (Linux).


def test_is_address_like_full_street_address_matches():
    assert pe._is_address_like("Fredericksburg Convention Center, 2371 Carlson Way")
    assert pe._is_address_like("Tysons Pediatrics, 8350 Greensboro Dr")
    assert pe._is_address_like("100 Main Street")
    assert pe._is_address_like("4500 University Drive, Fairfax VA 22030")
    assert pe._is_address_like("Suite 200, 1234 Elm Avenue")


def test_is_address_like_venue_only_does_not_match():
    """No leading digit → not address-like → gets the Location: prefix."""
    assert not pe._is_address_like("School Gym")
    assert not pe._is_address_like("Mr. Patel's Classroom")
    assert not pe._is_address_like("Online")
    assert not pe._is_address_like("Cafeteria")


def test_is_address_like_room_number_alone_does_not_match():
    """A room number is a digit + word but no street suffix → not
    address-like. "Location: Room 215" reads naturally."""
    assert not pe._is_address_like("Room 215")
    assert not pe._is_address_like("Bldg A, Room 215")
    assert not pe._is_address_like("Room 215, second floor")


def test_is_address_like_suffix_alone_without_digits_does_not_match():
    """Street-suffix words on their own (no leading digit) don't trip
    the heuristic. "Way Cool Studio" stays "Location: Way Cool Studio"."""
    assert not pe._is_address_like("Way Cool Studio")
    assert not pe._is_address_like("Park Lane")  # no leading digit
    assert not pe._is_address_like("Highway Diner")


def test_is_address_like_case_insensitive():
    """Real agent output mixes case freely."""
    assert pe._is_address_like("100 main STREET")
    assert pe._is_address_like("123 PARK ave")


def test_is_address_like_empty_and_whitespace():
    assert not pe._is_address_like("")
    assert not pe._is_address_like("   ")
    assert not pe._is_address_like(None or "")


def test_render_html_event_card_renders_source_line():
    """Dated card carries a `<div class="event-source">From: ...</div>`
    line under the event-name (and audience, when present)."""
    event = {
        "id": "src1",
        "name": "PTA meeting",
        "date": "2026-04-20",
        "_date_obj": dt.date(2026, 4, 20),
        "time": "7:00 PM",
        "location": "School Gym",
        "category": "School",
        "child": "Kid",
        "source": "LAES newsletter (Apr 6)",
        "sender_domain": "fcps.edu",
        "sender_block_key": "fcps.edu",
    }
    weeks = [(dt.date(2026, 4, 20), [event])]
    html = pe.render_html(
        today=TODAY, weeks=weeks, undated=[],
        total_future=1, lookback_days=60, webhook_url="",
    )
    assert '<div class="event-source"' in html
    assert "From: LAES newsletter (Apr 6)" in html


def test_render_html_undated_card_renders_source_line():
    """Parity with dated cards — undated cards render the same source
    line under the event-name."""
    event = {
        "id": "src2",
        "name": "Spring concert",
        "date": "",                   # undated
        "_date_obj": None,
        "time": "",
        "location": "Auditorium",
        "category": "School",
        "child": "Kid",
        "source": "Music Dept (Apr 5)",
        "sender_domain": "fcps.edu",
        "sender_block_key": "fcps.edu",
    }
    html = pe.render_html(
        today=TODAY, weeks=[], undated=[event],
        total_future=0, lookback_days=60, webhook_url="",
    )
    assert '<div class="event-source"' in html
    assert "From: Music Dept (Apr 5)" in html


def test_render_html_truncates_long_source_with_ellipsis_and_full_in_title():
    """A source label longer than _SOURCE_DISPLAY_CAP is truncated in
    the visible text but the full string survives in title= so hover
    still surfaces it."""
    long_source = "A" * 120  # >> 80 char cap
    event = {
        "id": "src3",
        "name": "Sprawling source",
        "date": "2026-04-20",
        "_date_obj": dt.date(2026, 4, 20),
        "time": "9:00 AM",
        "location": "School",
        "category": "School",
        "child": "Kid",
        "source": long_source,
        "sender_domain": "fcps.edu",
        "sender_block_key": "fcps.edu",
    }
    weeks = [(dt.date(2026, 4, 20), [event])]
    html = pe.render_html(
        today=TODAY, weeks=weeks, undated=[],
        total_future=1, lookback_days=60, webhook_url="",
    )
    # Visible: truncated + ellipsis.
    assert "From: " + ("A" * (pe._SOURCE_DISPLAY_CAP - 1)) + "…</div>" in html
    # title= carries the full string with the From: prefix.
    assert f'title="From: {long_source}"' in html


def test_render_html_location_gets_location_prefix_for_plain_venue():
    """Plain venue → `Location: <venue>` for visual consistency with
    the For: / From: lines."""
    event = {
        "id": "loc1",
        "name": "Practice",
        "date": "2026-04-20",
        "_date_obj": dt.date(2026, 4, 20),
        "time": "5:00 PM",
        "location": "School Gym",
        "category": "Sports",
        "child": "Kid",
        "source": "Coach (Apr 18)",
        "sender_domain": "example.com",
        "sender_block_key": "example.com",
    }
    weeks = [(dt.date(2026, 4, 20), [event])]
    html = pe.render_html(
        today=TODAY, weeks=weeks, undated=[],
        total_future=1, lookback_days=60, webhook_url="",
    )
    assert '<div class="event-location">Location: School Gym</div>' in html


def test_render_html_address_like_location_omits_prefix():
    """A location that's already a fully-formed street address skips
    the Location: prefix — the address is self-evidently a location."""
    event = {
        "id": "loc2",
        "name": "Recital",
        "date": "2026-04-20",
        "_date_obj": dt.date(2026, 4, 20),
        "time": "7:00 PM",
        "location": "Fredericksburg Convention Center, 2371 Carlson Way",
        "category": "Sports",
        "child": "Kid",
        "source": "Studio (Apr 18)",
        "sender_domain": "example.com",
        "sender_block_key": "example.com",
    }
    weeks = [(dt.date(2026, 4, 20), [event])]
    html = pe.render_html(
        today=TODAY, weeks=weeks, undated=[],
        total_future=1, lookback_days=60, webhook_url="",
    )
    # No "Location: " prefix; the raw address is the visible text.
    assert (
        '<div class="event-location">'
        'Fredericksburg Convention Center, 2371 Carlson Way</div>'
    ) in html
    assert ("Location: Fredericksburg") not in html


# ─── #29 follow-up: URL linkification in location field ───────────────────
#
# URLs anywhere in the location string get wrapped in <a href> so Ellen
# can tap straight through. Full http(s)://... URLs and bare domains
# (myschoolbucks.com) both linkify. Plain text segments around the URL
# pass through verbatim. Common false-friends ("Mt. Vernon", "Dr. Smith's
# office", "v1.0") must NOT be linkified.


def test_event_card_linkifies_url_only_location():
    """URL-only location renders as <a href> (was previously suppressed
    by `_is_suppressible_location` → no card line at all). Now Ellen
    clicks through. Query-string `&` is HTML-escaped against breaking
    out of the href attribute."""
    event = {
        "id": "url1",
        "name": "Yearbook deadline",
        "date": "2026-04-20",
        "_date_obj": dt.date(2026, 4, 20),
        "time": "All day",
        "location": "https://example.com/yearbook?year=2026&grade=6",
        "category": "School",
        "child": "Kid",
        "source": "yearbook (Apr 18)",
        "sender_domain": "example.com",
        "sender_block_key": "example.com",
    }
    weeks = [(dt.date(2026, 4, 20), [event])]
    html = pe.render_html(
        today=TODAY, weeks=weeks, undated=[],
        total_future=1, lookback_days=60, webhook_url="",
    )
    assert 'href="https://example.com/yearbook?year=2026&amp;grade=6"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html
    # "Location: " prefix preserved (URL-only is not address-like).
    assert "Location: " in html


def test_event_card_linkifies_bare_domain_with_implicit_https():
    """Mixed text + bare domain in parens: the domain becomes a
    clickable link with implicit https:// scheme; surrounding text
    stays plain. This is the "MySchoolBucks (myschoolbucks.com) or
    Louise Archer office" shape."""
    event = {
        "id": "url2",
        "name": "Lunch payment",
        "date": "2026-04-20",
        "_date_obj": dt.date(2026, 4, 20),
        "time": "All day",
        "location": "MySchoolBucks (myschoolbucks.com) or Louise Archer office",
        "category": "School",
        "child": "Kid",
        "source": "Cafeteria (Apr 18)",
        "sender_domain": "fcps.edu",
        "sender_block_key": "fcps.edu",
    }
    weeks = [(dt.date(2026, 4, 20), [event])]
    html = pe.render_html(
        today=TODAY, weeks=weeks, undated=[],
        total_future=1, lookback_days=60, webhook_url="",
    )
    # Bare domain becomes an https:// link.
    assert 'href="https://myschoolbucks.com"' in html
    assert ">myschoolbucks.com</a>" in html
    # Surrounding text stays plain (no anchors around "MySchoolBucks"
    # or "Louise Archer office").
    assert "Location: MySchoolBucks (" in html
    assert ") or Louise Archer office" in html


def test_event_card_does_not_linkify_proper_nouns_with_periods():
    """Pin against false positives: "Mt. Vernon High School" and
    "Dr. Smith's office" must NOT be linkified — the regex requires
    a TLD-shaped suffix (alpha 2-6) without intervening whitespace."""
    for loc in (
        "Mt. Vernon High School",
        "Dr. Smith's office",
        "Park Lane Elementary, version 1.0",
    ):
        event = {
            "id": f"fp_{abs(hash(loc))}",
            "name": "Test",
            "date": "2026-04-20",
            "_date_obj": dt.date(2026, 4, 20),
            "time": "9:00 AM",
            "location": loc,
            "category": "School",
            "child": "Kid",
            "source": "Test (Apr 18)",
            "sender_domain": "fcps.edu",
            "sender_block_key": "fcps.edu",
        }
        weeks = [(dt.date(2026, 4, 20), [event])]
        html = pe.render_html(
            today=TODAY, weeks=weeks, undated=[],
            total_future=1, lookback_days=60, webhook_url="",
        )
        # The location-line slice should contain no anchor.
        loc_div_idx = html.find('class="event-location"')
        assert loc_div_idx != -1, f"no event-location for {loc!r}"
        loc_div_end = html.find("</div>", loc_div_idx)
        loc_slice = html[loc_div_idx:loc_div_end]
        assert "<a " not in loc_slice, (
            f"unexpected anchor in {loc!r}: {loc_slice!r}"
        )


def test_undated_card_linkifies_url_location():
    """Parity with dated cards — undated cards linkify URLs the same
    way."""
    event = {
        "id": "uurl1",
        "name": "Camp signup",
        "date": "",
        "_date_obj": None,
        "time": "",
        "location": "https://camps.example.com/signup",
        "category": "Academic Due Date",
        "child": "Kid",
        "source": "Camps (Apr 3)",
        "sender_domain": "example.com",
        "sender_block_key": "example.com",
    }
    html = pe.render_html(
        today=TODAY, weeks=[], undated=[event],
        total_future=0, lookback_days=60, webhook_url="",
    )
    assert 'href="https://camps.example.com/signup"' in html
    assert 'target="_blank"' in html


def test_event_card_address_like_location_with_url_still_linkifies_no_prefix():
    """Belt-and-suspenders: a location that's both address-shape AND
    contains a URL keeps the no-prefix treatment AND linkifies the
    URL. Real-world: "Tysons Pediatrics, 8350 Greensboro Dr (info at
    tysonspeds.com)" — address-like wins on the prefix, URL still
    becomes a link."""
    event = {
        "id": "addr_url",
        "name": "Pediatrician check-up",
        "date": "2026-04-20",
        "_date_obj": dt.date(2026, 4, 20),
        "time": "9:00 AM",
        "location": "Tysons Pediatrics, 8350 Greensboro Dr (info at tysonspeds.com)",
        "category": "Medical",
        "child": "Kid",
        "source": "MyChart (Apr 18)",
        "sender_domain": "mychart.com",
        "sender_block_key": "mychart.com",
    }
    weeks = [(dt.date(2026, 4, 20), [event])]
    html = pe.render_html(
        today=TODAY, weeks=weeks, undated=[],
        total_future=1, lookback_days=60, webhook_url="",
    )
    # Address-like → no "Location: " prefix.
    assert "Location: Tysons" not in html
    # URL still linkified.
    assert 'href="https://tysonspeds.com"' in html
