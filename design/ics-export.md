# Per-event `.ics` export button

Roadmap item 5. Each dated event card gets an "Add to calendar" link that
opens the native calendar app with the event pre-populated.

## Status

Shipped. Initial implementation (Blob download from inline `data-ics`)
pivoted to hosted `.ics` files + `webcal://` links after live testing
showed iOS routes downloads to the Files app and offers the wrong
handler. See "Pivot to hosted files + webcal" below for the reasoning
and the final architecture.

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

**Attribute encoding (superseded).** Initial version HTML-escaped the
full `.ics` body into `data-ics="..."` and let client JS build a Blob
for download. Worked on desktop but on iOS the `a.download` flow writes
to Files and then offers "Add to Reminders" (wrong app). Replaced with
hosted files + webcal links — see below.

## Pivot to hosted files + webcal (final design)

**Problem.** iOS Safari/Edge/Chrome all use WebKit and all treat
`a.download` of `.ics` Blobs the same way: save to Files, let the user
re-open from there. That's two taps in the wrong place and the wrong
default-app picker.

**Fix.** Commit one `.ics` file per displayed event to
`docs/ics/{event_id}.ics`; the card's button is a plain `<a>` whose
`href` is `webcal://<pages-host>/<path>/ics/{event_id}.ics`. iOS routes
the `webcal://` scheme at the OS level, so Calendar opens directly
regardless of browser. No JS, no Blob, no download folder.

**Where it lives.**
- `scripts/process_events.py::_webcal_base(pages_url)` strips the scheme
  and normalizes trailing slash. Empty pages_url → empty base → button
  not rendered. Dev preview degrades gracefully.
- `scripts/process_events.py::write_ics_files(events, out_dir)` wipes
  `.ics` files in `out_dir` (preserves non-.ics siblings, e.g.
  `.nojekyll`) and writes one file per event named by the stable
  12-char event ID.
- `render_html(... pages_url=...)` is the new signature; each card
  renders the webcal anchor only when `_webcal_base` is non-empty.
- `main.py::step4_process_events` passes `--ics-out-dir docs/ics` when
  `dry_run=False`; dry-run skips the write so the publish dir stays
  clean.
- No workflow change needed: `docs/` is not tracked in git (except
  `.nojekyll`); it's rebuilt each run and uploaded as the Pages
  artifact via `actions/upload-pages-artifact@v3` with `path: docs/`,
  which picks up `docs/ics/` automatically.

**Filename.** `{event_id}.ics` (not `{slug}-{date}.ics`). Stable across
runs, guaranteed unique, no slug collisions. The download-filename slug
helper is retained for potential future use but unused by the current
card.

**Cleanup.** `write_ics_files` deletes every `.ics` in the target dir
before writing, so stale entries never leak between runs. Non-`.ics`
files are preserved.

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
