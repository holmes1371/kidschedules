# Card ordering by start time (intraday)

## Problem

Within a single day, cards were ordered alphabetically, not chronologically.
A 12:45 PM event could appear above a 9:30 AM event on the same day. Weeks and
days were already ascending and correct — only the *intraday* order was wrong.

## Decision (settled with Tom, 2026-06-16)

- **Direction:** everything ascending. Within a day, timed cards sort
  earliest-start first (9:30 above 12:45). Weeks/days untouched.
- **All-day placement:** all-day / untimed cards (empty time, "Time TBD",
  "All day", etc.) lead the day, then timed cards. All-day cards keep sorting
  by name among themselves. (Google-Calendar-style all-day-row-on-top.)

## Implementation

`scripts/process_events.py`:

- New pure helper `_event_start_time(ev) -> dt.time | None` — reuses the
  existing `_parse_time_range` (takes range start) then `_parse_clock_time`;
  returns `None` for all-day / unparseable strings.
- New `_day_sort_key(ev)` returns `(date, all_day_rank, start, name.lower())`
  where `all_day_rank` is `0` for untimed (None start → `dt.time.min`
  placeholder) and `1` for timed. `name.lower()` stays the tie-break, so the
  pre-existing all-day-only name sort is preserved.
- `group_by_week` now sorts with `_day_sort_key` instead of the old
  `(date, name)` lambda.

## Follow-up fix (2026-06-16, post-deploy)

First cut used the strict `fullmatch` parsers for the sort key. A timed card
with trailing text — `10:00 AM – 11:30 AM (approx.)` — failed `fullmatch`,
returned `None`, and fell into the all-day bucket, floating *above* a clean
`9:45 AM–12:05 PM` card. (Seen live on the deployed page.)

Fix: `_event_start_time` now falls back to `_CLOCK_RE.search` for a leading
clock time when both strict parsers miss, rescuing `(approx.)`-style ranges and
`1:30 PM dismissal`. Strict parsers stay authoritative for clean inputs and
meridian-shared ranges (`11-1 PM`). Extracted `_clock_match_to_time` so the
match→`dt.time` conversion is shared. `.ics` export is unchanged — it never
uses the search fallback.

## Tests (`tests/test_process_events.py`)

- `test_group_by_week_sorts_same_day_events_by_start_time` — out-of-order
  9:30 / 12:45 / 2-5 PM, with the alphabetically-first name on the latest
  time so a name-only sort would fail.
- `test_group_by_week_all_day_events_sort_before_timed` — all-day + "Time TBD"
  lead (by name), then timed earliest-first.
- Existing `test_group_by_week_sorts_same_day_events_by_name` (all-day only)
  still green — the rank/name tie-break preserves it.
