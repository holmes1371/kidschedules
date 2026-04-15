# Kids Schedule — QoL Roadmap

Authoritative backlog for quality-of-life improvements to the kids-schedule-github pipeline. Edit in place; commit changes alongside code.

## For future agents

Read this file at the start of any session where Tom mentions "kids-schedule", "the QoL list", or asks about the next feature. The prioritization below is settled — do not re-debate it without prompting. Work items in order unless Tom explicitly says otherwise.

Session discipline:

- Before starting a non-trivial feature, write a short design note to `design/{feature-name}.md` capturing the scope, the decisions already made, and the test fixtures needed. A fresh session should be able to pick up mid-feature from that note plus the last commit, without re-litigating choices.
- Commit at every natural boundary, not just at feature completion. Half-finished work behind a clear commit message is recoverable; a dirty worktree is not.
- End each session by updating this file — check off completed items, mark in-progress items, note any deviations or follow-ups — and commit the update.
- Any feature that modifies `scripts/process_events.py` must extend the pytest fixtures in step with the change, not after. Item 2 below establishes the suite.
- Tests live in `tests/` and run on every push + PR via `.github/workflows/tests.yml`. A red test check blocks merge; don't mark a feature done with tests failing.
- Honor the standing order: deterministic work lives in Python scripts; the agent does only judgment and interpretation. If a feature tempts you to move mechanical work into agent-handled text, push back.
- The `Ellen's ToDo` mount in this project is retired and should be ignored (see memory). All work happens in `kids-schedule-github/`.

Status legend:

- `[ ]` not started
- `[~]` in progress — include a note with what is done and what remains
- `[x]` done — include the commit SHA

## Backlog (priority order)

### 1. [x] Failure notifications via GitHub mobile app — c3d2e5b

Tom enables Actions push notifications for the repo in the GitHub mobile app. On the code side, verify `main.py` and the workflow exit non-zero on real failures (Gmail token expiry, Anthropic 5xx, unexpected exceptions) so the push actually fires. Add a small dry-run or intentional-failure path to confirm the notification arrives end-to-end.

Audited existing propagation paths (most were already correct). Removed the `except Exception: continue` in `agent.py::extract_events` so post-retry API failures propagate instead of silently returning `([], [])`; parse failures and filter-audit failures remain tolerant by design (see `design/failure-notifications.md`). Added `main.py --intentional-failure` plus a matching `intentional_failure` workflow_dispatch input. Tom verified the mobile push arrives when the intentional-failure run finishes.

### 2. [x] Pytest suite for `scripts/process_events.py` — 8375e9c (suite) / 8a9f4b3 (CI)

Cover current behavior: past-event filtering, dedupe, sort order, week grouping, event-ID stability (12-char sha1), HTML body rendering, subject-line construction. Use fixture JSON inputs under `fixtures/`; prefer string-equality or snapshot assertions over structural asserts. Wire into a GitHub Actions check so regressions fail the workflow. Foundational — every subsequent feature extends the fixture set.

26 tests in `tests/test_process_events.py`. Fixtures under `fixtures/test/`, body snapshot under `tests/snapshots/`. CI in `.github/workflows/tests.yml` runs on push + PR. Design note at `design/pytest-suite.md`. Session-discipline block updated to point at `tests/` and note that a red test check blocks merge.

### 3. [x] Weekly email digest to Gmail drafts, with test-mode toggle — b5200cb … f312d90

After publishing, create a Gmail draft summarizing this-week events with a link to the Pages URL. Built with a three-layer safety model (see `design/weekly-digest-draft.md`):

- `main.py --create-draft` is explicit opt-in; default is no-draft. `CREATE_DRAFT=1` env var is equivalent for workflow plumbing. `--dry-run` always suppresses.
- Workflow sets `CREATE_DRAFT=1` only when `github.event_name == 'schedule'` or the new `create_draft` workflow_dispatch input is true.
- Preview of the rendered digest subject + body prints to stdout on every run regardless of the gate, so local/manual runs can eyeball content without touching Gmail.

Render functions (`digest_subject`, `render_digest_text`, `render_digest_html`) live in `scripts/process_events.py`. Draft is HTML with plain-text alternative (`gmail_client.py::create_draft` now accepts `text_alternative`). Empty-week short-circuit: no draft when `this_week_count == 0`. Pages URL pulled from committed `pages_url.txt` (empty-safe). `should_create_draft` is unit-tested exhaustively across all gate combinations.

Commit trail: c89bd19 (design) · b5200cb (render + CLI + tests) · 2ffc458 (gmail_client) · 4838af0 (pages_url.txt) · 91cd5fb (main wiring + gate tests) · f312d90 (workflow).

### 4. [x] Incremental extraction — skip already-processed Gmail messages — 008051c … 7528267

Every run used to send up to 60 days of email through the Anthropic agent even though almost none of those messages had changed since last week. Fixed by caching extracted events in `events_state.json` (on the `state` branch) keyed by Gmail message ID; each run only sends the agent messages whose IDs aren't already in the cache. Gmail search window stays at 60 days (cheap, self-healing). Event IDs are stable (`sha1(name|date|child)[:12]`), so dedupe across runs is trivial. Garbage-collects `processed_messages` entries older than 2× lookback (120 days) and events whose date is past. Subsumes `future_events.json`, which is retired.

Live verification 2026-04-14: first run after retirement bootstrapped 168 events from `future_events.json` and filtered 0 of 66 emails (everything new); second run filtered 61 of 66 and sent only 5 to the agent — a ~92% reduction in agent load. Key decisions locked in the design note: no per-message event attribution (YAGNI); top-level `schema_version` with blow-away-and-rebuild on mismatch; atomic write via tempfile + `os.replace`; load-time GC; cache failure modes always "warn and start empty"; reschedule detection deferred (manual ignore is adequate for now — see design note "Explicit non-goals"). Full design at `design/incremental-extraction.md`.

Commit trail: 008051c (design note + ROADMAP insert) · 440358f (`events_state.py` module + 33 unit tests) · bd56047 (main.py integration + workflow state-branch plumbing + zero-new-messages test) · 7528267 (retire `future_events.json`; one-time bootstrap).

### 5. [ ] Per-event `.ics` export button

Embed the `.ics` body in a `data-ics` attribute on each card; a small "Add to calendar" button (next to Ignore) runs a JS handler that turns the attribute into a Blob download. The RFC 5545 generation lives in `process_events.py`. `DTSTART` uses `TZID=America/New_York` with a single `VTIMEZONE` block; all-day events use `VALUE=DATE` and no TZ. `UID` is tied to the 12-char event ID so re-imports overwrite rather than duplicate. Snapshot-test the `.ics` strings as part of the pytest suite.

Plan approved 2026-04-14, no code yet. Full design + settled decisions + 4-step commit plan at `design/ics-export.md`. Resume there: next concrete action is commit 2 (`build_ics` + helpers + tests + two snapshots). Key judgment calls already locked: unparseable times fall back to all-day; timed events get `DURATION:PT1H`; UID domain is `kidschedules.holmes1371.github.io`; button only renders on dated cards.

### 6. [ ] "New this week" badges

Persist prior-run event IDs to a manifest file in the repo (e.g. `prior_events.json`). On each run, `process_events.py` diffs current IDs against the manifest and stamps cards whose IDs did not exist last week with a visible "NEW" badge. First run: manifest empty, no badges — degrade gracefully. The workflow commits the updated manifest alongside the other outputs.

### 7. [ ] Per-kid filter chips

`process_events.py` renders a chip row at the top of the page from the unique children in this run, plus an "All" reset chip. Client JS toggles card visibility via a CSS class on click. Pure-UI, self-contained.

### 8. [ ] Conflict highlighting

In `process_events.py`, detect overlapping timed events on the same day via interval intersection; flag both cards with a visible conflict marker. Prioritize different-kid overlaps as the high-signal case. Same-day all-day + timed events should NOT be flagged as conflicts — they coexist by design.

### 9. [ ] Undo recently ignored (5-minute toast)

After an ignore, show an "Undo" toast or button in the client for 5 minutes. Clicking: POSTs an unignore to a new Apps Script delete-row endpoint, restores the card visually, removes the localStorage entry. Auto-dismiss after 5 minutes. Matching endpoint work lives in `scripts/apps_script.gs`.

### 10. [ ] "Ignore sender" button

Stamp each card with its sender domain. A new Apps Script endpoint appends to a separate "blocked senders" sheet (distinct from the ignored-events sheet). The workflow adds a step that syncs that sheet into `blocklist.txt` — merging with existing entries, deduping, preserving manual edits — and commits the updated file alongside `ignored_events.json`. Most infrastructure of any item; deliberately last.
