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
- POST-DEPLOY FIX: timed cards with trailing text ('10:00 AM ... (approx.)')
  failed the strict parser, fell into the all-day bucket, floated above a
  clean 9:45 card. `_event_start_time` now `_CLOCK_RE.search` fallback +
  shared `_clock_match_to_time`. `.ics` export unchanged. Regression test added.
- Branch `claude/card-ordering-start-time-4lo7kg` (first cut already merged).
  Awaiting Tom live-verify of the follow-up. See design note.
