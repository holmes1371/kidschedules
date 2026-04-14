# Per-event `.ics` export button

Roadmap item 4. Each dated event card gets an "Add to calendar" button that
downloads an RFC 5545 `.ics` file via a `Blob` in the browser. All
mechanical work (parsing, generation, UID stability) lives in Python; the
browser side is a ~10-line handler that turns a `data-ics` attribute into
a download.

## Status

Plan approved by Tom; no code written yet. Resume directly at the commit
plan below.

## Settled decisions

**Time parsing.** Regex extracts the first `\d{1,2}(?::\d{2})?\s*[AaPp][Mm]`
from `ev["time"]`. Clean match → timed event. Anything else (including
"Time TBD", "1:30 PM dismissal", "All day (deadline)") → all-day fallback.
This keeps the button available on every dated card without inventing fake
times.

**Timed events.** `DTSTART;TZID=America/New_York:YYYYMMDDTHHMMSS` plus
`DURATION:PT1H` (one-hour default — simple, predictable, revisit later if
it's wrong for specific cases). Single hand-coded America/New_York
`VTIMEZONE` block included in the VCALENDAR.

**All-day events.** `DTSTART;VALUE=DATE:YYYYMMDD` and
`DTEND;VALUE=DATE:<next-day>` (RFC 5545 makes DTEND exclusive). No
`VTIMEZONE` — floating date values don't need a zone.

**UID.** `<12-char-event-id>@kidschedules.holmes1371.github.io`. Stable
across runs because the event ID is deterministic (`_event_id`), so
re-imports overwrite rather than duplicate in the calendar.

**Filename.** `<slug>-<YYYY-MM-DD>.ics` where `slug` is the event name
lowercased with non-alphanum runs collapsed to `-`.

**Button placement.** On each dated card, to the left of Ignore. Undated
cards skip the button entirely (no valid DTSTART).

**Attribute encoding.** HTML-escape the full `.ics` body and put it in
`data-ics="..."`. Newlines survive attribute parsing; calendar apps accept
LF endings in practice. If this turns out not to be true we pivot to
base64 later.

## Where rendering lives

`scripts/process_events.py` adds:

- `VTIMEZONE_NY: str` — module-level constant, hand-coded STANDARD +
  DAYLIGHT subcomponents with RRULE transitions.
- `_parse_clock_time(s: str) -> dt.time | None` — regex parser; returns
  `None` when no clean time found.
- `_ics_slug(name: str) -> str` — filename slug helper.
- `build_ics(ev: dict) -> str` — pure function, full VCALENDAR text.

`_event_card` in `render_html` embeds the escaped `.ics` body in
`data-ics` and renders the "Add to calendar" button. A small inline JS
handler wires the button to `new Blob([ics])` + `URL.createObjectURL` +
anchor click.

## Explicit non-goals

- No VALARM / reminders (roadmap doesn't ask).
- No RRULE / recurrence — every event is a one-off.
- No parsing of ranges like "3:00–4:30 PM" — clean single time or all-day.
- No server-side download endpoint — client-side Blob is enough.

## Commit plan

1. This design note.
2. `build_ics` + helpers + tests + snapshots (cohesive script + tests
   commit). Two snapshots: one timed, one all-day. Unit test for
   `_parse_clock_time` edge cases. UID-stability test (running on the
   same event twice yields identical UID).
3. HTML card + button + JS handler (UI commit). Substring asserts on
   the rendered HTML ("Add to calendar", `data-ics="BEGIN:VCALENDAR`).
4. ROADMAP close-out with SHAs.

## Verification gates

- After step 2: `pytest -v` green including new snapshots.
- After step 3: full pytest run still green; render a sample page via
  the CLI, eyeball a decoded `data-ics`, confirm it imports cleanly into
  at least one calendar client before marking done.

## Test fixtures

- Extend `fixtures/test/basic_mixed.json` as-is for snapshot inputs —
  it already has both a timed event (Spring Concert, 7:00 PM) and an
  all-day-style entry (Book Report Due, blank time). One snapshot per.
- No new fixture file needed unless edge cases surface during
  implementation.
