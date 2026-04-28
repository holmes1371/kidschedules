# Auto-GC the Ignored + Completed sheets — design note

ROADMAP item #37. Filed 2026-04-27, plan approved 2026-04-28 (Tier 1 + Tier 2 in
one go).

## Problem

The Apps Script-backed sheets "Ignored Events" (#6/#7) and "Completed Events"
(#32) grow monotonically. There is no cleanup path today. Once an event's date
passes, it GCs out of `events_state.json` daily (per `events_state.gc_state`),
so the matching row in either sheet is dead weight: it cannot match a live
event again, but it still ships in the next cron's GET response and the page's
localStorage hydration.

The on-page counter "Show ignored (N)" is **not** affected — `ignored_n` is
computed from events that flow into render (`weeks` + `undated`), not from
sheet rows. Past-dated rows are invisible to the counter as soon as their event
GCs from `events_state.json`. So this is purely sheet hygiene + GET-payload
size, not a counter-correctness issue. (Confirmed in the 2026-04-28 session
before any code.)

At realistic cadence (a few flips per week) the bloat is tolerable for years;
auto-cleanup is preventive maintenance so Tom never has to hand-purge later.

## Approach: two tiers, ship together

Tom approved both tiers in one go (2026-04-27). Tier 1 alone solves the
runtime / payload concern but leaves the sheets growing at the source. Tier 2
keeps the sheets tidy too. One-and-done.

### Tier 1 — lazy filter at sync time (Python, fully tested)

Both Python sync helpers drop past-dated rows before writing the runner's
local cache. Pure-Python change in two helpers. Past-dated = `date` parses as
ISO-8601 `YYYY-MM-DD` and is strictly `< today` (matches
`events_state.gc_state`'s rule). Defensive default: rows with empty,
malformed, or any unparseable `date` string pass through. They may be undated
events legitimately ignored / completed; the same posture
`events_state.gc_state` takes for undated events.

Two changes in one commit:

1. **Promote the inline workflow step to a Python helper.** The "Sync ignored
   events" step in `weekly-schedule.yml` is currently 25 lines of inline bash
   (curl + JSON validation + atomic mv). That shape predates `sync_completed_events.py`
   and `sync_ignored_senders.py`; promoting it is a prerequisite for adding
   the lazy filter under test. New file `scripts/sync_ignored_events.py`,
   modeled one-for-one on `sync_completed_events.py` (validation regex
   `/^[a-f0-9]{12}$/` for id, dedup first-wins, sort by id, write_if_changed).
2. **Add `_drop_past_dated(rows, today)` to both helpers.** `today` is passed
   in (no `date.today()` call inside the helper) so tests are deterministic.
   Applied between `normalize_rows` and `write_if_changed` in `main()`.

Workflow update: replace the inline 25-line "Sync ignored events" bash block
with a one-line `python scripts/sync_ignored_events.py …` invocation matching
the two sibling steps. No behavior change for prod beyond the new lazy filter.

### Tier 2 — server-side GC (Apps Script)

New `gcPastDatedRows()` in `apps_script.gs`. Walks "Ignored Events" + "Completed
Events" bottom-up (so row indices stay stable as we delete), parses column 4
(`date`) with `Date.parse(...)`, deletes if strictly before midnight-local
today. Skips rows where `date` is empty or unparseable — same defensive
posture as Tier 1.

**Does NOT touch "Ignored Senders".** Senders aren't date-bound; an ignore
decision is intentionally permanent. Calling this out explicitly because the
ROADMAP item flagged it as the open question — the answer is "no, leave
senders alone."

Comment block at top of the function with the trigger-setup ritual: Apps
Script editor → ⏰ Triggers → Add Trigger → `gcPastDatedRows`, time-based,
day timer, 02:00–03:00 (lines up with the 06:15 ET cron's pre-roll). Manual
deploy + trigger setup is on Tom; same posture as #34's `?secret=` patch.

## Why no automated test for Apps Script

Project's standing posture: deterministic Python work gets pytest, Apps
Script does not. Tier 2 is structurally simple — bottom-up walk + date
compare + deleteRow — and the failure modes (sheet missing, network blip)
already have prod telemetry via the cron's GET. Adding a GAS test harness
would more than double the project's tooling surface for a 30-line function.

## Why no retroactive bulk purge

Considered 2026-04-28 and explicitly rejected. The daily trigger gets to
steady state in a few days; a one-time bulk purge is more destructive (single
button, hand-deployed, no testable rollback path) for marginal speed gain.
Less destructive, smaller blast radius.

## Tests (Tier 1)

- `tests/test_sync_completed_events.py`: extend with `_drop_past_dated_*`
  cases — past-dated dropped, today-dated kept, future-dated kept,
  empty-`date` kept defensively, malformed-`date` kept defensively. Frozen
  `today` arg for determinism.
- `tests/test_sync_ignored_events.py` (new): mirror the test surface from
  `test_sync_completed_events.py` (normalize_rows id-validation, write_if_changed,
  _fetch with `kind=ignored`, main() CLI integration) plus the same
  `_drop_past_dated_*` cases. The new helper diverges from the completed
  one only in the GET kind parameter and the field surface.

## Files touched

- `design/auto-gc-sheets.md` (new — this note)
- `ROADMAP.md` (`[ ]` → `[~]` flip + last-session-summary refresh)
- `scripts/sync_ignored_events.py` (new)
- `scripts/sync_completed_events.py` (extend)
- `tests/test_sync_ignored_events.py` (new)
- `tests/test_sync_completed_events.py` (extend)
- `.github/workflows/weekly-schedule.yml` (replace inline bash with Python invocation)
- `scripts/apps_script.gs` (Tier 2 GC function + setup-ritual comment)

## Out of scope

- Cleaning the "Ignored Senders" sheet (intentionally permanent — see Tier 2).
- Any change to the `ignored_events.json` / `completed_events.json` on-disk
  shape (consumers stay byte-compatible).
- Any change to the on-page "Show ignored (N)" counter logic — the counter
  was verified correct already (see Problem section).
- A `--purge-now` workflow input or one-shot sweep — daily trigger handles
  it.
