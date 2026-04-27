# "Completed" checkbox on event cards

ROADMAP item #32. Adds a per-card "Completed" affordance so Ellen can mark
an event done as it happens, without waiting for the date to pass. The
feature is a near-perfect mirror of the ignore flow (item #6 / #7) — same
12-hex event-id surface, same Apps Script round-trip pattern, same
optimistic-toggle-with-rollback client wiring. The narrower scope (no
sender attribution, no Gmail-query gating, no schema bump) makes this a
medium feature, not a large one.

**Complexity: medium → think hard.** Multi-file change (process_events.py
render + classify + CLI, apps_script.gs router, new sync_completed_events.py
helper, main.py wiring, workflow YAML). Touches the workflow YAML but only
adds a new sync step alongside the two existing ones — not a structural
change. Pytest fixtures extend in-step.

## Resolved decisions (2026-04-27 with Tom)

These four are settled and will not be re-litigated:

- **Persistence: durable.** Completion sticks across page reloads and cron
  rebuilds. Per-browser session-only is NOT acceptable.
- **Cross-device: synced via Apps Script.** Same webhook path as ignore.
- **Completed supersedes ignore.** Both Ignore-event and Ignore-sender
  buttons are removed from completed cards. Unchecking restores them.
- **Retirement: unchanged.** Completed cards retire on the normal
  date-passed threshold. No early sweep.

## Architecture (mirrors ignore flow)

| Concern | Ignore flow | Completed flow |
|---|---|---|
| Sheet tab | "Ignored Events" | "Completed Events" |
| Sheet columns | `[timestamp, id, name, date, sender]` | `[timestamp, id, name, date]` |
| POST actions | `ignore` / `unignore` | `complete` / `uncomplete` |
| GET kind | `ignored` (default) | `completed` |
| Cache file | `ignored_events.json` | `completed_events.json` |
| Sync helper | inline curl in workflow | `scripts/sync_completed_events.py` |
| Loader in process_events | `_load_ignored_ids` | `_load_completed_ids` |
| classify() flag | `is_ignored` | `is_completed` |
| Card class | `event-card ignored` | `event-card completed` |
| localStorage key | `kids_schedule_ignored_ids` | `kids_schedule_completed_ids` |

No sender column on the Completed Events tab — completion is per-event
only; there is no "complete all events from this sender" semantic.

### Sheet is the single source of truth (invariant preserved)

The master record of completed events lives in the "Completed Events" sheet
tab — full stop. `completed_events.json` is an **ephemeral** per-run cache:
written to the runner's working directory by the new sync step at the start
of every workflow run, consumed by `process_events.py` during that same run,
torn down with the runner. It is **never committed to the repo** and the
state-branch save step does not pick it up. Mirrors `ignored_events.json`
exactly (see `.github/workflows/weekly-schedule.yml` "Sync ignored events
from Apps Script" — the file is written to the runner cwd; only
`events_state.json` / `prior_events.json` / `sender_stats.json` / blocklist
files are pushed to the state branch).

Client-side localStorage (`kids_schedule_completed_ids`) is purely a
paint-flicker fix for the gap between "user checks the box" and "next cron
rebuilds the page from the sheet". On every fresh page load, server-rendered
`is_completed` (derived from the sheet via `completed_events.json`) is the
authoritative starting state; localStorage layers on top only to preserve
optimistic flips that haven't round-tripped through the next cron yet.

There is no path from `completed_events.json` → sheet. Sync is one-way:
sheet → cache. The only writes that mutate the master record are the
`complete` and `uncomplete` POST actions hitting Apps Script directly.

## Design Q&A (engineering judgment calls flagged in ROADMAP #32)

**Q1 — Event-identity key.** Use the existing 12-hex `_event_id` (sha1 of
`name|date|child`). Same key the ignore flow uses, computed identically by
`process_events.py::_event_id` and `events_state.py::_event_id`. No new
identity surface; the `id` already lives on every rendered card as
`data-event-id`.

**Q2 — Apps Script endpoint shape.** Extend the existing `doPost` action
router (`scripts/apps_script.gs`) with two new actions: `complete` and
`uncomplete`. Auth, validation, and routing machinery is already there.
A new helper `_getCompletedEventsSheet()` returns the "Completed Events"
tab, creating it on first use (matches the existing `_getIgnoredEventsSheet`
/ `_getIgnoredSendersSheet` pattern). `doGet` grows a third route key:
`?kind=completed` returns rows as JSON, dedup-on-id like `_listIgnoredEvents`.

**Q3 — Storage layout.** Mirror `ignored_events.json` exactly:
`completed_events.json` at the repo root, ephemeral (written by the new
sync step into the runner's working directory each run, never committed,
torn down with the runner). The sheet is the single persistent record;
the workflow file system is a fast cache. Posture on missing/malformed
file: graceful degrade to empty set (no completion state applied this
run, sheet rows still survive for next run's sync).

**Q4 — Where does the checkbox live on the card?** Top action row, before
the existing ICS button. Real `<input type="checkbox" class="complete-checkbox">`
inside a wrapping `<label>` — native checkbox semantics for accessibility,
matches Tom's "checkbox" framing in the roadmap, and gives the user the
state at-a-glance without inspecting the card style.

**Q5 — "Completed" label.** When checked, render a small green chip inline
with the event name, mirroring how the NEW badge renders today (#13). When
unchecked, the chip is absent. Avoids overloading the checkbox label with
all the visual weight; the chip provides the at-a-glance "this is done"
read while the checkbox is the affordance.

**Q6 — Card visual treatment.** Subtle green tint on the card background
when completed:

- Light mode: `background: #e6f4ea` (Google Material green-50, complements
  the existing `#0d652d` Unignore button accent).
- Dark mode: `background: #1e3526` (deep green that reads as "tinted" not
  "alarming" against the `#2d2d2d` default surface).

No strikethrough on the event name — Tom's spec says "subtle". The
left-border accent stripe (currently keyed on category color) stays as-is.

**Q7 — Completed and ignored simultaneously.** Ignored cards do NOT render
a completed checkbox. The two affordances are mutually exclusive on a
given card: an ignored card has only the Unignore button; a completed
card has only the checkbox (Ignore-event and Ignore-sender hidden via
Q8). Implementation: same one-line CSS rule pattern that already
suppresses `.ignore-sender-btn` on `[data-ignored="1"]` cards today —
`.event-card[data-ignored="1"] .complete-checkbox-wrap { display: none; }`.
The HTML is rendered for every card so the wrap is always in the DOM;
visibility is purely CSS-driven, which means an Unignore that strips
`data-ignored` automatically reveals the checkbox without touching markup.

If a card somehow ends up tagged as both ignored AND completed in the
sheets (Ellen edits both tabs by hand, or a race during a manual sheet
edit), ignored wins visually — the card is `display:none` and the
checkbox is hidden too. Once unignored, the checkbox reappears in its
checked state, reflecting the still-extant Completed Events sheet row.

**Q8 — Ignore buttons on completed cards.** Both Ignore-event and
Ignore-sender buttons are suppressed via CSS:
`.event-card.completed .ignore-btn,
 .event-card.completed .ignore-sender-btn { display: none; }`.
No HTML restructure — render the buttons as today, hide via class. This
keeps the markup symmetric and means unchecking trivially restores the
buttons (CSS class drops, buttons reappear). Mirrors the existing pattern
for hiding `.ignore-sender-btn` on `[data-ignored="1"]` cards.

**Q9 — Sheet GC.** Apps Script does NOT GC the Completed Events tab. Sheet
rows accumulate indefinitely; volume is small (Ellen marks ~5–20 events
complete per week max, retirement-on-date-passed bounds the relevant set
to ~100 rows). The existing Ignored Events tab has the same posture and
hasn't caused issues. If row count ever becomes a problem, add a sweep
later — YAGNI for now.

**Q10 — process_events.py response on date-passed completed entries.**
`completed_events.json` is filtered through the same date-passed retirement
logic as everything else: completed events whose date is in the past are
dropped from `display`, so the cached id never matters. The sheet row stays
behind (Q9) but is silently inert.

## Test fixtures

`tests/test_process_events.py` extends `_render_ignored_fixture` pattern:

- New `_render_completed_fixture(completed_names=(), ignored_names=())`
  helper that computes `completed_ids` + `ignored_ids` from the existing
  `fixtures/test/ignored_and_sender.json` events and threads both into
  `classify`. No new fixture file.
- New tests:
  - `test_classify_marks_completed_events_and_keeps_them_in_display` —
    completed events stay in `display` with `is_completed=True`.
  - `test_classify_completed_and_ignored_independent_flags` — both flags
    coexist server-side on a card listed in both id sets (the renderer's
    visibility-suppression is a separate CSS-level concern, pinned in its
    own test below).
  - `test_render_html_ignored_card_hides_completed_checkbox` — pin the
    CSS rule that suppresses the checkbox on `[data-ignored="1"]` cards.
  - `test_render_html_completed_card_has_class_and_chip` — `event-card
    completed`, `Completed` chip beside event name, green tint inline style.
  - `test_render_html_completed_card_hides_ignore_buttons` — CSS rule
    present, ignore-event and ignore-sender buttons still in DOM (so
    uncheck restores them), but hidden via class selector.
  - `test_render_html_completed_checkbox_renders_checked` — checkbox is
    rendered with the `checked` attribute when `is_completed=True`.
  - `test_render_html_completed_checkbox_renders_unchecked` — control case.
  - `test_render_html_js_posts_complete_action` — inline JS POSTs
    `{action: 'complete', id, name, date}` on check.
  - `test_render_html_js_posts_uncomplete_action` — POSTs
    `{action: 'uncomplete', id}` on uncheck.
  - `test_render_html_js_complete_optimistic_with_rollback` — pin the
    rollback substring (`setActive` equivalent for completion state) so a
    future client-side rewrite that drops it fails CI.
  - `test_render_html_js_complete_hydrates_from_localstorage` — pin the
    `kids_schedule_completed_ids` storage key + the hydration loop.

`tests/test_sync_completed_events.py` (new file, mirrors
`test_sync_ignored_senders.py`):

- Validates 12-hex id shape; drops malformed rows.
- First-wins dedup on id.
- Stable sort by id for diff readability.
- `write_if_changed` short-circuits when serialized output is unchanged.
- Network failure → graceful degrade, no file written, exit 0.

Apps Script changes have no automated test (matches existing posture); the
router additions are small enough that visual review + manual smoke test
on Tom's deploy is adequate.

## Responsibility table

| Concern | Python | LLM (agent.py) | Apps Script |
|---|---|---|---|
| Event extraction | — | ✅ | — |
| Event-id hashing | ✅ `_event_id` | — | — |
| Validate `id` shape on POST | — | — | ✅ `/^[a-f0-9]{12}$/` |
| Append/delete sheet rows | — | — | ✅ |
| Fetch + normalize cache file | ✅ `sync_completed_events.py` | — | — |
| classify completed flag | ✅ `process_events.py::classify` | — | — |
| Render checkbox/chip/style | ✅ `process_events.py::render_html` | — | — |
| Optimistic toggle + rollback | ✅ inline JS in `render_html` | — | — |

No runtime LLM calls introduced. Completion is a pure UI affordance with
deterministic round-tripping.

## Commit plan

Commit at each natural boundary, not just at feature completion:

1. **Design note + ROADMAP "Last session summary" update** (this commit).
   ROADMAP #32 already `[~]` (flipped 2026-04-27 in commit `378e183`); this
   commit just lands the design note and updates the session-summary block.
2. **process_events.py classify + render markup + CLI flag + tests** —
   server-side: `completed_ids` parameter on `classify`, `is_completed`
   passthrough, `--completed` arg, `_load_completed_ids`. Render: checkbox,
   chip, green-tint inline style, CSS rules for hiding ignore buttons on
   completed cards. No client JS yet. Pytest fixtures extended.
3. **scripts/apps_script.gs** — `complete` / `uncomplete` POST actions,
   `?kind=completed` GET route, `_getCompletedEventsSheet` helper.
4. **scripts/sync_completed_events.py + tests/test_sync_completed_events.py** —
   fetch-and-write helper mirroring `sync_ignored_senders.py`.
5. **main.py + workflow YAML** — wire `--completed` flag through the step4
   call; add the new "Sync completed events from Apps Script" step. Both
   surfaces are mechanical orchestration; bundling them keeps the wiring
   in one commit.
6. **process_events.py inline JS** — checkbox click handler, hydration
   from localStorage, optimistic toggle + rollback on POST failure. Tests
   for JS substring asserts.
7. **ROADMAP update + SHAs**, hand off to Tom for live verification.

Each step on `process_events.py` extends pytest fixtures in step (not after).

## Open for future work (explicit non-goals)

- **Bulk uncheck / "clear all completed".** Volume is low; per-card uncheck
  is fine.
- **"Completed sender" sweep.** Marking every event from a sender as
  complete in one click. Not requested; would risk silent over-completion
  on multi-event newsletters.
- **Audit/history view.** No per-event "completed at" surface on the page;
  the sheet timestamp is the audit trail if Ellen ever needs it.
- **Auto-complete on date-passed.** Events retire on date-passed regardless;
  there's no need to flip them to completed first. The two states are
  logically distinct (Ellen confirms the event happened vs. the calendar
  rolled past the date).
