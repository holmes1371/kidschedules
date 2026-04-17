# "New this week" badges (#13)

## Problem

The site is republished every Mon/Wed/Sat. Ellen scans the page looking
for anything she hasn't already internalised — but with 60-day lookback
and ~30–60 rendered cards, fresh extractions blend into the wall of
already-known events. She's asked for a visible marker on cards that
showed up for the first time in the most recent extraction cycle.

## Why Python, not the prompt

Pure deterministic diff of the rendered ID set against a persisted prior
set. No judgment, no narrative, no out-of-band context — the
skill-building standing order says this belongs in a script. The agent
touches nothing at runtime; all work is in `scripts/process_events.py`
and the workflow YAML.

## Scope

Render-time flag on event cards. No changes to:

- The Anthropic extractor prompt or the events cache schema
- The event-ID hashing scheme (`_event_id(name, date, child)` in both
  `events_state.py` and `scripts/process_events.py`; the parity test in
  `tests/test_events_state.py` still guards that contract)
- The `child` field on event dicts or any other field consulted by the
  cache merge logic

What we add:

- A new persistent-state file `prior_events.json` at repo root. Same
  restore/save pattern as `events_state.json` — the workflow copies it
  in from the `state` branch at the start of a run and pushes the
  updated copy back at the end, gated on `dry_run != 'true'`.
- Two module-level helpers in `scripts/process_events.py`:
  `_load_prior_event_ids(path) -> set[str] | None` and
  `_save_prior_event_ids(path, ids, now_iso) -> None`.
- Render wiring: `render_html` threads a `new_ids: set[str]` through
  `_event_card` and `_undated_card`, which emit a `<span class="new-badge">NEW</span>`
  inline with the event name when the card's `data-event-id` is in the
  set.
- One CSS rule in the inline `<style>` block inside `render_html`.
- Persistence call site: after the HTML render succeeds,
  `_save_prior_event_ids` overwrites `prior_events.json` with the
  current render set.

## Decisions

### "Prior" lives in its own file, not inside `events_state.json`

`events_state.json` is the Gmail-message extraction cache. Its schema,
its GC rules, and its parity with `events_state.py`'s `_event_id` are
load-bearing. `prior_events.json` is a render-time artefact — what was
on the published site last run. Separate concern, separate file. This
matches the existing state-branch file-per-concern pattern
(`.filter_audit.json`, `blocklist_auto.txt`, `events_state.json`).

File format (intentionally minimal):

```json
{
  "generated_at_iso": "2026-04-17T10:15:32Z",
  "event_ids": ["abc123def456", "..."]
}
```

Stored sorted for diff-friendliness across runs. `generated_at_iso`
is informational only — nothing downstream parses it.

### First-run semantics: missing file ≠ empty list

- **File missing** (`_load_prior_event_ids` returns `None`): first run
  ever, or someone deleted the state branch. Suppress all badges —
  flashing NEW on every card on first publish is visually useless.
- **File present, `event_ids: []`** (loader returns `set()`): last run
  legitimately rendered zero events (email drought week). Any current
  event is genuinely new; badge them all. Not a degenerate case.
- **File present, malformed JSON or wrong shape**: loader warns to
  stdout (matches `events_state.py::load_state` warning style) and
  returns `None` — same suppression as "file missing". Next run
  overwrites the bad file.

Caller logic:

```python
prior = _load_prior_event_ids(path)
new_ids = (current_ids - prior) if prior is not None else set()
```

### Badge placement: inline with event name

Injected into the `<div class="event-name">`:

```html
<div class="event-name">{name} <span class="new-badge">NEW</span></div>
```

Chosen for tight visual association with the title. Alternative (slot
into `event-actions-top` next to Ignore) was considered — it keeps the
action row as the "chrome" of the card, but the badge is a content
signal, not an action, and pulling it up to the title reads more like
"this item is new" than "this item has a NEW action".

Undated and dated cards both render the badge via the same code path —
both already stamp `data-event-id` and both own an `event-name` div.

### Ignored-but-new events: badge renders

No special-case. If an event is both ignored and new, the `<span>` is
in the HTML but the card's `display:none` (the `.ignored` rule) hides
it until the user clicks "Show ignored". Once visible the badge
correctly signals that this is a newly-extracted event that was
auto-ignored (e.g. matched a sender-swept pattern on extraction).

### "New" is binary per run

An event's ID is either in the prior set or not. There is no aging
(no "stays NEW for 2 runs"). Mon's extraction stamps badges on the
delta vs Sat. Wed's run immediately overwrites the manifest with
Mon+Wed's union, so by Wed a Mon-new event loses its badge. This is
the simplest possible semantic and matches the ROADMAP wording.

### Dry-run gating

The save step already skips on `dry_run == 'true'`. We do not write
`prior_events.json` in that branch — a dry-run render must not poison
next week's baseline. The restore step is unconditional (safe to
restore even for dry runs).

### ID source

Render consults `ev["id"]` (already stamped by
`events_state.stamp_event_ids` upstream of render). We do not
recompute. This avoids drift between the cache's ID view and the
render's ID view.

## Cache / re-render behaviour

`prior_events.json` is independent of `events_state.json`. Neither
affects the other:

- Cache eviction (an event's `first_seen_iso` aged out by GC) does not
  touch `prior_events.json`. If the same event reappears in a later
  extraction, it gets a fresh entry in `events_state.json` — but it may
  or may not show NEW, depending on whether its ID happened to be in
  the prior-render manifest.
- A cache-cleared run (someone nukes `events_state.json`) will still
  diff correctly against the prior-render manifest, because the render
  manifest is keyed by event ID, which is deterministic from
  `(name, date, child)`.

## Test fixtures

All new tests in `tests/test_process_events.py`. Unit-level:

- `test_load_prior_event_ids_missing_file` — tmp_path, no file →
  returns `None`, no exception, no stdout
- `test_load_prior_event_ids_empty_list` — file exists with
  `event_ids: []` → returns `set()` (not `None`) — the missing-vs-empty
  distinction is load-bearing
- `test_load_prior_event_ids_happy_path` — file with 3 IDs → returns
  exactly those 3 as a set
- `test_load_prior_event_ids_malformed_json` — file with invalid JSON
  → returns `None`, emits warning via `print`
- `test_load_prior_event_ids_wrong_shape` — file is a JSON list (not a
  dict) or missing `event_ids` → returns `None`, emits warning
- `test_save_prior_event_ids_roundtrip` — save then load yields the
  same set; saved file has `event_ids` sorted + `generated_at_iso`
  present
- `test_save_prior_event_ids_atomic` — `.tmp` file is used and renamed
  (mirror `events_state.py::save_state` invariant)

Render-level (snapshots / string-contains assertions on
`render_html` output):

- `test_new_badge_rendered_when_id_absent_from_prior` — one dated
  event, prior is `{"other_id"}` → card HTML contains
  `<span class="new-badge">NEW</span>`
- `test_no_new_badge_when_id_in_prior` — prior includes the event's
  ID → card HTML does NOT contain `new-badge`
- `test_new_badge_on_undated_card` — undated card with unseen ID gets
  the badge inline with its `event-name`
- `test_no_badges_when_prior_is_none_first_run` — pass `new_ids=set()`
  (simulating the first-run branch) → no `new-badge` anywhere in
  output
- `test_new_badge_css_rule_present` — rendered HTML contains a
  `.new-badge` CSS rule in the `<style>` block

## Files touched

- `design/new-this-week-badges.md` (this file)
- `ROADMAP.md` — flip `### 13. [ ]` → `### 13. [~]` with progress
  pointer
- `scripts/process_events.py` — two helpers, `render_html` threads
  `new_ids` into the two card functions, CSS rule in the inline style
  block, call site writes `prior_events.json` after render
- `.github/workflows/weekly-schedule.yml` — restore `prior_events.json`
  in the restore block, add to the `FILES` list in the save block
- `tests/test_process_events.py` — unit + render tests per the
  fixtures list above

No changes to: `events_state.py`, `main.py`, `agent.py`,
`class_roster.json`, any other script.

## Out of scope

- Aging badges across runs ("stays NEW for 2 cadence cycles") — YAGNI
  until Tom asks.
- Historical diff or "what's new since date X" UI — the site is a live
  view, not an archive.
- Badge styling variants (e.g. category-coloured NEW pills) — one
  accent colour, one rule.
