# Kids Schedule — QoL Roadmap

Authoritative backlog for quality-of-life improvements to the kids-schedule-github pipeline. Edit in place; commit changes alongside code.

## For future agents

Read this file at the start of any session where Tom mentions "kids-schedule", "the QoL list", or asks about the next feature. The prioritization below is settled — do not re-debate it without prompting. Work items in order unless Tom explicitly says otherwise.

Session discipline:

- Before starting a non-trivial feature, write a short design note to `design/{feature-name}.md` capturing the scope, the decisions already made, and the test fixtures needed. A fresh session should be able to pick up mid-feature from that note plus the last commit, without re-litigating choices.
- Commit at every natural boundary, not just at feature completion. Half-finished work behind a clear commit message is recoverable; a dirty worktree is not.
- End each session by updating this file — check off completed items, mark in-progress items, note any deviations or follow-ups — and commit the update.
- Any feature that modifies `scripts/process_events.py` must extend the pytest fixtures in step with the change, not after. Item 2 below establishes the suite.
- Honor the standing order: deterministic work lives in Python scripts; the agent does only judgment and interpretation. If a feature tempts you to move mechanical work into agent-handled text, push back.
- The `Ellen's ToDo` mount in this project is retired and should be ignored (see memory). All work happens in `kids-schedule-github/`.

Status legend:

- `[ ]` not started
- `[~]` in progress — include a note with what is done and what remains
- `[x]` done — include the commit SHA

## Backlog (priority order)

### 1. [ ] Failure notifications via GitHub mobile app

Tom enables Actions push notifications for the repo in the GitHub mobile app. On the code side, verify `main.py` and the workflow exit non-zero on real failures (Gmail token expiry, Anthropic 5xx, unexpected exceptions) so the push actually fires. Add a small dry-run or intentional-failure path to confirm the notification arrives end-to-end.

### 2. [ ] Pytest suite for `scripts/process_events.py`

Cover current behavior: past-event filtering, dedupe, sort order, week grouping, event-ID stability (12-char sha1), HTML body rendering, subject-line construction. Use fixture JSON inputs under `fixtures/`; prefer string-equality or snapshot assertions over structural asserts. Wire into a GitHub Actions check so regressions fail the workflow. Foundational — every subsequent feature extends the fixture set.

### 3. [ ] Weekly email digest to Gmail drafts, with test-mode toggle

After publishing, create a Gmail draft summarizing the top-of-week schedule with a link to the Pages URL. Must be toggleable (an env var such as `SKIP_DRAFT=1` plus a `--no-draft` CLI flag on `main.py`) so pipeline testing does not spam Ellen's drafts folder. The existing Gmail scope includes `gmail.modify`; reuse `gmail_client.py`.

### 4. [ ] "New this week" badges

Persist prior-run event IDs to a manifest file in the repo (e.g. `prior_events.json`). On each run, `process_events.py` diffs current IDs against the manifest and stamps cards whose IDs did not exist last week with a visible "NEW" badge. First run: manifest empty, no badges — degrade gracefully. The workflow commits the updated manifest alongside the other outputs.

### 5. [ ] Per-kid filter chips

`process_events.py` renders a chip row at the top of the page from the unique children in this run, plus an "All" reset chip. Client JS toggles card visibility via a CSS class on click. Pure-UI, self-contained.

### 6. [ ] Conflict highlighting

In `process_events.py`, detect overlapping timed events on the same day via interval intersection; flag both cards with a visible conflict marker. Prioritize different-kid overlaps as the high-signal case. Same-day all-day + timed events should NOT be flagged as conflicts — they coexist by design.

### 7. [ ] Per-event `.ics` export button

Embed the `.ics` body in a `data-ics` attribute on each card; a small "Add to calendar" button (next to Ignore) runs a JS handler that turns the attribute into a Blob download. The RFC 5545 generation lives in `process_events.py`. `DTSTART` uses `TZID=America/New_York` with a single `VTIMEZONE` block; all-day events use `VALUE=DATE` and no TZ. `UID` is tied to the 12-char event ID so re-imports overwrite rather than duplicate. Snapshot-test the `.ics` strings as part of the pytest suite.

### 8. [ ] Undo recently ignored (5-minute toast)

After an ignore, show an "Undo" toast or button in the client for 5 minutes. Clicking: POSTs an unignore to a new Apps Script delete-row endpoint, restores the card visually, removes the localStorage entry. Auto-dismiss after 5 minutes. Matching endpoint work lives in `scripts/apps_script.gs`.

### 9. [ ] "Ignore sender" button

Stamp each card with its sender domain. A new Apps Script endpoint appends to a separate "blocked senders" sheet (distinct from the ignored-events sheet). The workflow adds a step that syncs that sheet into `blocklist.txt` — merging with existing entries, deduping, preserving manual edits — and commits the updated file alongside `ignored_events.json`. Most infrastructure of any item; deliberately last.
