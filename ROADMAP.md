# Kids Schedule — QoL Roadmap

Authoritative backlog for quality-of-life improvements to the kids-schedule-github pipeline. Edit in place; commit changes alongside code.

Always load the karpathy-guidelines skill before starting anything here. 

## For future agents

Read this file at the start of any session where Tom mentions "kids-schedule", "the QoL list", or asks about the next feature. The prioritization below is settled — do not re-debate it without prompting. Work items in order unless Tom explicitly says otherwise.

Session discipline:

- Invoke the `karpathy-guidelines` skill via the Skill tool at the start of every session that touches code. Reading `reference/guidelines.md` directly does not count — the skill-load step is what anchors the discipline for the rest of the session.
- Before starting a non-trivial feature, write a short design note to `design/{feature-name}.md` capturing the scope, the decisions already made, and the test fixtures needed. A fresh session should be able to pick up mid-feature from that note plus the last commit, without re-litigating choices.
- Commit at every natural boundary, not just at feature completion. Half-finished work behind a clear commit message is recoverable; a dirty worktree is not.
- Use the built-in TodoWrite tool before starting each commit, and keep it current as you work. Tom watches the todo widget to see where you are in the plan; a stale or absent list means he can't track progress. At the start of every new commit, add/refresh todos for that commit's sub-tasks and mark one `in_progress`.
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

### 5. [x] Per-event `.ics` export button — 52ebd73 … cc7ac82

RFC 5545 generator in `scripts/process_events.py` (`build_ics`) plus an "Add to calendar" anchor on every dated card. Three-step path, each pivot driven by a concrete iOS testing failure the prior version couldn't solve:

1. **Blob download (superseded).** Inline `data-ics` attribute + JS Blob handler. iOS Safari/Edge/Chrome all route `.ics` downloads through Files and offer Reminders as the default handler — wrong app, two taps in the wrong place.
2. **Hosted files + `webcal://` (superseded).** Per-event files at `docs/ics/{event_id}.ics` linked via `webcal://<pages-host>/…`. Fixed the handler routing (Calendar opens directly), but `webcal://` is iOS's *subscription* scheme — tapping a link opens the "Subscribe to calendar?" sheet and adds a remote feed, rather than importing the single event.
3. **Hosted files + `https://` + range parsing (final).** Plain `https://` anchor to the same `.ics` files; GitHub Pages serves `Content-Type: text/calendar`, so iOS recognizes the MIME and opens Calendar's single-event preview sheet ("Add Event"), pre-populated, no subscription step. Added `_parse_time_range` for strings like "2PM - 5PM" so camp-style events emit a real `DURATION:PT3H` instead of degrading to all-day.

Architecture: `write_ics_files()` wipes and repopulates `docs/ics/` on every non-dry-run; `_webcal_base(pages_url)` strips the scheme and returns the host+path used to build the anchor href (name is historical — it's now the base for the https link); render gracefully omits the button when `pages_url` is empty (dev preview). UID stays `{event_id}@kidschedules.holmes1371.github.io` (opaque — UIDs don't need to resolve); timed events get `DTSTART;TZID=America/New_York` + real duration + a single hand-coded `VTIMEZONE`; all-day events get `VALUE=DATE` with exclusive `DTEND`. No workflow change needed — `docs/` is rebuilt each run and uploaded via `actions/upload-pages-artifact`.

Commit trail: 52ebd73 (design note + ROADMAP insert) · e0a8aa6 (`build_ics` + helpers + snapshots + parser tests) · 8c060b9 (UI card — Blob version, superseded) · 2082da8 (pivot to hosted files + webcal://, superseded) · 1135405 (time-range parsing) · cc7ac82 (webcal:// → https:// swap, final).

Design note: `design/ics-export.md` (includes both pivot rationales).

### 6. [x] Undo recently ignored + 7. "Ignore sender" (bundled)

Bundled because they share all their surfaces — Apps Script routing, a second Google Sheet tab, client-side button/toggle work in the rendered HTML, and a new workflow sync step. Full design: `design/ignore-undo-and-block-sender.md` (includes locked decisions, 10-step commit plan, responsibility table, non-goals).

Locked model (from the design note, not re-debated): render-but-hide (no 5-minute toast); per-card Unignore button in solid-green variant replaces Ignore on ignored cards; header **Show ignored (N)** toggle; registrable-domain blocking via `tldextract`; LLM echoes `source_message_id` → Python does all sender parsing.

Progress against the 10-step commit plan:

1. [x] Design note + ROADMAP insert — f7f3425 · 82979d6 (palette amendment)
2. [x] `agent.py` schema bump (`source_message_id` field, prompt update, parser validation, 9 unit tests) — 518b4ad
3. [x] `main.py` sender-domain attachment + `tldextract>=5.1.0` added to `requirements.txt` (10 unit tests) — eebae6f
4. [x] `events_state.py` schema v2 (optional `sender_domain` per event; blow-away-and-rebuild on mismatch) — 96795dd
5. [x] `process_events.py` render-but-hide model (classify/render changes, Show/Hide toggle, Ignore-sender button) — 220b083; design amended post-step-5 to standardize on "ignored senders" vocabulary end-to-end (b07bdf7)
6. [x] `scripts/apps_script.gs` action router (`ignore` / `unignore` / `ignore_sender`; `?kind=ignored_senders` GET route; second tab "Ignored Senders") — 9935d60
7. [x] `scripts/sync_ignored_senders.py` fetch-and-write helper + 13 unit tests — 8d51750
8. [x] Workflow "Sync ignored senders" step — a9f070c. `ignored_senders.json` is ephemeral (option A): written to the runner's working dir only, no commit-on-main — matches the existing `ignored_events.json` sibling. Design note updated to reflect this (sections: intro, architecture-update, Workflow changes, Commit plan step 8, ripple-through).
9. [x] Client JS in `docs/index.html` (Unignore, Show/Hide toggle, Ignore sender, toast helpers, localStorage hydration) — 646993c
10. [x] CSS fix for action-row overlap (flex wrapper for Add-to-calendar + Ignore event buttons) — bf34506
11. [x] Gap closure: wire `ignored_senders.json` into `build_queries.py` so UI-clicked Ignore-sender decisions actually exclude those domains from Gmail searches at fetch time — e97f1b0
12. [x] Protected-senders guardrail: `protected_senders.txt` at repo root (seeded from `blocklist.txt`'s NEVER-add list) + shared `scripts/protected_senders.py` loader. Both `process_events.render_html` (suppresses the Ignore-sender button) and `build_queries.main` (filters protected domains out of the ignored_senders union) read the same file — defense in depth so the user can't accidentally block schools, PTAs, team-management platforms, or health providers — 2393d31
13. [ ] **Ignore-sender should hide cards locally, same as Ignore-event.** Today, clicking "Ignore sender" POSTs the domain to Apps Script and shows a toast, but the card (and any other cards from the same sender in the current view) stays visible until the next weekly build drops the sender's events at fetch time. Ellen expects the UI to reflect the decision immediately — the point of the button is to make a whole class of events go away. Scope for the next session:

    - In the client JS `postAction` handler for the `ignore_sender` branch (in `scripts/process_events.py`'s rendered `<script>` block), after the POST succeeds, find every `.event-card[data-sender="<domain>"]` in the DOM and apply the same render-but-hide treatment the Ignore-event path uses — set `display:none`, stamp `data-ignored="1"`, bump the `Show ignored (N)` counter, and persist the hidden-card IDs to the same `localStorage` key the existing hydration pass reads. Match the optimistic posture of Ignore-event (hide first, only revert on POST failure).
    - Unignore story for sender-ignored cards is intentionally **not** symmetric: there's no per-card "Un-ignore sender" button. Reasoning: un-ignoring a sender is a multi-card batch operation with side effects on future Gmail queries. The existing path — edit the "Ignored Senders" sheet tab, next build picks it up — stays the un-ignore affordance. The design note's "Open for future work" non-goal on this already anticipates the split.
    - Be careful about the card's own Ignore-event state: a card that was already individually Ignore-event'd before the user clicks Ignore-sender on a sibling should stay ignored-for-that-reason rather than double-flagging. The `data-ignored` attribute is binary, so this is mostly a matter of not overwriting the event-level ignore ID in `localStorage`.
    - Add a pytest for the render output: nothing to check at render time (this is pure client behavior), but a JS-free smoke test that the script block contains a `document.querySelectorAll('.event-card[data-sender=' + ...)` pattern in the `ignore_sender` branch is a cheap regression guard.
    - Karpathy-discipline note: the existing `postAction` / click-router structure already handles all three actions (`ignore`, `unignore`, `ignore_sender`) through a single helper. The new DOM work should slot into the existing `ignore_sender` branch — resist the urge to refactor the helper; just add the card-sweep after the successful POST. Surgical change.

### 8. [ ] "New this week" badges

Persist prior-run event IDs to a manifest file in the repo (e.g. `prior_events.json`). On each run, `process_events.py` diffs current IDs against the manifest and stamps cards whose IDs did not exist last week with a visible "NEW" badge. First run: manifest empty, no badges — degrade gracefully. The workflow commits the updated manifest alongside the other outputs.

### 9. [ ] Per-kid filter chips

`process_events.py` renders a chip row at the top of the page from the unique children in this run, plus an "All" reset chip. Client JS toggles card visibility via a CSS class on click. Pure-UI, self-contained.

### 10. [ ] Manual "refresh now" button in the UI

Button in `docs/index.html` that triggers the weekly workflow on demand, so a fresh build can be forced after a late schedule email without waiting for the next scheduled run or opening GitHub. GitHub's `workflow_dispatch` API requires an authenticated call, so the existing Apps Script webhook grows a new `action=refresh` endpoint that holds a fine-grained PAT (scope: `workflow`, single-repo) as a Script Property and POSTs to the dispatches endpoint. Client fires `fetch(APPS_SCRIPT_URL, {method:'POST', body: JSON.stringify({secret, action:'refresh'})})` and shows a "Rebuilding… reload in ~2 min" toast; no live polling.

Threat model accepted: the shared secret is effectively public (embedded in page source on a page with near-zero organic traffic), worst case is a handful of wasted workflow runs. Defense in depth: Apps Script rate-limits to one dispatch per 5 minutes via `PropertiesService`. The workflow's existing `concurrency: {group: pages, cancel-in-progress: false}` already prevents pileups from rapid clicks. PAT rotation: 1-year expiry with a calendar reminder.

### 11. [ ] Conflict highlighting

In `process_events.py`, detect overlapping timed events on the same day via interval intersection; flag both cards with a visible conflict marker. Prioritize different-kid overlaps as the high-signal case. Same-day all-day + timed events should NOT be flagged as conflicts — they coexist by design.

### 12. [ ] Node 20 → Node 24 action upgrades (before 2026-06-02)

Every workflow run currently prints:

> Warning: Node.js 20 actions are deprecated. The following actions are running on Node.js 20 and may not work as expected: actions/deploy-pages@v4.

GitHub timeline: Node 24 becomes the default on 2026-06-02 and Node 20 is removed from runners on 2026-09-16 (see https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/). The warning names `actions/deploy-pages@v4` specifically; also audit the other pinned actions in `.github/workflows/weekly-schedule.yml` and `.github/workflows/tests.yml` — `actions/checkout@v4`, `actions/setup-python@v5`, `actions/upload-pages-artifact@v3` — and bump any that are still on the Node 20 runtime.

Approach: check each action's latest major for a Node 24-compatible release, pin to the lowest version that silences the warning, run the workflow via `workflow_dispatch` with `dry_run=true` to verify no behavioral change, and only then commit. If a new major is not yet available for one of the actions by late May, the `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true` env var is an acceptable bridge — but prefer a real version bump.
