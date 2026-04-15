# Per-event `.ics` export button

Roadmap item 5. Each dated event card gets an "Add to calendar" link that
opens the native calendar app with the event pre-populated.

## Status

Shipped. Path taken: Blob download → hosted files + `webcal://` →
hosted files + `https://` with time-range parsing. Each pivot driven
by live iOS testing surfacing a concrete problem the prior version
couldn't solve. See "Pivot to hosted files + webcal" and "Second pivot:
https + range parsing" below for the full reasoning.

## Settled decisions

**Time parsing.** Two passes, in order:

1. `_parse_time_range` — matches `H[:MM][am/pm]? (-|–|—|to) H[:MM]am/pm`.
   End meridian required; start meridian optional (inherits from end, and
   flips if that would put start after end, so "11-1 PM" → 11 AM–1 PM).
   Returns `(start, end)` → real timed event with a real duration.
2. `_parse_clock_time` — single-time fullmatch as before. Clean match →
   timed event with `PT1H` default.

Anything else (including "Time TBD", "1:30 PM dismissal",
"All day (deadline)") → all-day fallback. The dismissal-as-all-day case is
deliberate: strings like "1:30 PM dismissal" describe a deadline inside a
day, not a meeting — fullmatch rejects them, which is the right call.

**Timed events.** `DTSTART;TZID=America/New_York:YYYYMMDDTHHMMSS` plus
either `DURATION:PT{h}H{m}M` (range) or `DURATION:PT1H` (single time —
simple, predictable default). Single hand-coded America/New_York
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

## Second pivot: https + range parsing

**Problem 1 — subscription flow.** `webcal://` is iOS's scheme for
calendar *subscriptions*: tapping a `webcal://` link opens Calendar's
"Subscribe to calendar?" sheet, which adds a remote feed rather than a
one-off event. Wrong UX for a per-event "add this to my calendar" button.

**Fix 1.** Swap the anchor scheme to plain `https://`. GitHub Pages serves
`.ics` with `Content-Type: text/calendar`, and iOS recognizes that MIME
type as a calendar event — tapping opens Calendar's single-event preview
sheet ("Add Event"), pre-populated, with no subscription step. Works in
every iOS browser because the handoff is MIME-driven at the OS level.

**Problem 2 — ranges fall through to all-day.** `_parse_clock_time` uses
`re.fullmatch` with a single-time pattern, so strings like "2:00 PM -
5:00 PM" don't match and the event silently degrades to all-day. The
original design note explicitly ruled out range parsing; that turned out
to be wrong for real fixtures (e.g. "Peter Pan Ballet Camp 2PM - 5PM").

**Fix 2.** Add `_parse_time_range` ahead of the single-time parser.
Accepts hyphen, en dash, em dash, or the word "to" as separator; end
meridian required; start meridian optional with share/flip inference.
Emits `DURATION:PT{h}H{m}M` via `_format_ics_duration`. Falls back to the
existing single-time path when the range parser returns `None`, which in
turn falls back to all-day — so nothing that already worked regresses.

## Where rendering lives

`scripts/process_events.py`:

- `VTIMEZONE_NY: str` — module-level constant, hand-coded STANDARD +
  DAYLIGHT subcomponents with RRULE transitions.
- `_parse_time_range(s) -> tuple[dt.time, dt.time] | None` — range parser
  (see "Second pivot" above).
- `_parse_clock_time(s) -> dt.time | None` — single-time fullmatch;
  returns `None` when no clean time found.
- `_format_ics_duration(start, end) -> str` — emits `PT{h}H{m}M` /
  `PT{h}H` / `PT{m}M`; `PT1H` for degenerate input.
- `_webcal_base(pages_url) -> str` — strips scheme, normalizes trailing
  slash; empty input → empty output (caller gates rendering on this).
- `build_ics(ev, now) -> str` — pure function, full VCALENDAR text.
- `write_ics_files(events, out_dir, now)` — wipes `.ics` in `out_dir`
  (preserves non-.ics siblings like `.nojekyll`), writes one file per
  dated event named `{event_id}.ics`.
- `render_html(..., pages_url="")` — passes `pages_url` through to
  `_event_card`, which renders an `<a class="ics-btn" href="https://...">`
  only when the base is non-empty.

## Explicit non-goals

- No VALARM / reminders (roadmap doesn't ask).
- No RRULE / recurrence — every event is a one-off.
- No server-side endpoint — static files on Pages are enough.

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
