# 2026-06-16 — Card ordering by start time

- Fixed intraday card order in `group_by_week` (`scripts/process_events.py`):
  cards were alphabetical within a day, so 12:45 PM could sit above 9:30 AM.
- New `_event_start_time` + `_day_sort_key`: sort key is
  `(date, all_day_rank, start, name.lower())`. All-day cards lead the day
  (Tom's call), then timed earliest-first. Weeks/days unchanged.
- Tom confirmed: everything ascending; all-day-first within a day.
- Tests added in `tests/test_process_events.py`; full suite 896 green.
- Post-deploy fix: timed cards with trailing text (`10:00 AM – 11:30 AM
  (approx.)`) failed the strict parser and floated into the all-day bucket
  above earlier cards. `_event_start_time` now falls back to a leading-clock
  `_CLOCK_RE.search`; `.ics` export untouched.
- Branch `claude/card-ordering-start-time-4lo7kg`. Awaiting Tom live-verify
  before any board move to Done. See `design/card-ordering-start-time.md`.
