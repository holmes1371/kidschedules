# Kids Schedule — QoL Roadmap

Authoritative backlog for quality-of-life improvements to the kids-schedule-github pipeline. Edit in place; commit changes alongside code.

Always load the karpathy-guidelines skill before starting anything here.

Closed `[x]` items are archived in `COMPLETED.md` with their full post-mortem prose. Stubs below preserve the original numbering so past session summaries and commit messages still resolve.

## Last session summary

Replace this block at the end of each session. Keep it to what the next agent actually needs to walk in cold: what just closed, what's open, where to pick up, and any non-obvious observations that aren't captured under a numbered item.

**2026-04-17 (session 5 — #12 per-kid filter chips + teacher roster landed)**

- **Just closed (pending visual QA): #12 per-kid filter chips.** Three commits: `f0976f6` (design note), `fd0c264` (class_roster.json + agent.py prose injection), `399d383` (chip row + filter JS + data-child on cards). ROADMAP flipped `[ ]` → `[~]` in this commit. Tom's visual QA on the next live GitHub Pages build flips to `[x]` next session with SHA `399d383` preserved.
- **Next step (pick up here):** if Tom greenlights the visual QA, flip #12 to `[x]` and move `#12` prose into `COMPLETED.md` with a one-line stub. Next backlog item is `#13` — "New this week" badges.
- **Key design decisions locked in `design/per-kid-filter-chips.md`**, worth skimming before the next QoL UI change:
  - Chip set is hard-coded to three buttons (`All` / `Everly` / `Isla`). It does NOT iterate over unique children in the run — guarded by `test_filter_chip_row_is_static_not_derived_from_events`. Free-text audience values (`All LAES students`, `6th grade AAP`) are an unstable taxonomy and were deliberately excluded.
  - Filter semantics are non-lossy: clicking `Everly` hides only `data-child="isla"` cards; audience-line and empty-child cards stay visible. This is why a school-wide closure still surfaces while you're filtered to Isla. If Tom ever asks for strict "only this kid" view, it's a two-line CSS change.
  - Filter state is ephemeral — no localStorage key. Cold reload returns to `All`. Intentional; keeps the filter distinct from the `ignore` localStorage bookkeeping.
  - The hide rule uses `display: none !important` for specificity symmetry with the existing `.show-ignored .event-card.ignored` override. Without `!important` an Everly-filter + Show-ignored combo would leak an Isla ignored card. Covered by `test_filter_hide_css_uses_important`.
- **Roster subtask shape, for future edits:** `class_roster.json` at repo root is the source of truth. `agent.py::_load_roster_prose` reads it at module import, `_format_roster_prose` (pure, unit-tested) builds the prose block, and it's appended to `_EXTRACTION_BASE_PROMPT` to produce `EXTRACTION_SYSTEM_PROMPT`. The loader **crashes on missing/malformed roster** rather than falling back silently — the file is committed, so absence is a bug. Fall-update workflow: edit the JSON, commit; no code change. Current values: Everly in 6th grade (Ms. Anita Sahai), Isla in 3rd grade (Ms. Meredith Rohde), both at Louise Archer Elementary.
- **Stale fixture flag still open (carry-over from sessions 3 & 4):** `fixtures/sample_candidates.json` still contains "Glasgow Middle School / Isla". Not touched this session — session 4's note flagged it as "good first commit for session 5" but we went directly into #12 per Tom's direction. Now three sessions deep as an easy-pickup cleanup; consider sweeping before the next feature lands.
- **Session-start repo state worth noting:** branch was already up to date with `origin/main` (Tom pushed session 4's four commits plus at least three post-hoc cleanups of his own: `812a4ca` closed #11 into COMPLETED.md, `8d1f198` pinned LF endings, etc.). So #11 is fully `[x]` now — any UI follow-ups on the card should reference `COMPLETED.md` for the locked decisions.
- **Repo state at session end:** 249 tests passing (was 238; session added 3 roster tests + 8 chip tests = 11). Four commits local on `main`, ahead of `origin/main` and **not yet pushed**. In chronological order: `f0976f6` (design note), `fd0c264` (roster subtask), `399d383` (chip row), plus this ROADMAP commit. Worktree clean aside from gitignored `docs/dev_preview.html` which was regenerated during a visual smoke. `.to_delete/` accumulated three more stale `.git/*.lock` files from this session's git churn — safe to leave until Tom sweeps.
- **Open thread (carry-over) — Cowork permission re-prompts**: every `mv`/`git commit` was still re-prompted each session for the same command-string-uniqueness reason. If Tom asks to address it, broader allowlist entries like `Bash(git:*)` and `Bash(mv:*)` in `.claude/settings.local.json` would kill the noise.

## For future agents

Read this file at the start of any session where Tom mentions "kids-schedule", "the QoL list", or asks about the next feature. The prioritization below is settled — do not re-debate it without prompting. Work items in order unless Tom explicitly says otherwise.

Session discipline:

- Invoke the `karpathy-guidelines` skill via the Skill tool at the start of every session that touches code. Reading `reference/guidelines.md` directly does not count — the skill-load step is what anchors the discipline for the rest of the session.
- git commits need the -c user.name=... -c user.email=... flags since there's no default identity
- **Soft-delete convention, not `rm`.** The FUSE mount this repo lives on refuses `unlink` but permits `rename`. `rm` fails with `Operation not permitted` even under `dangerouslyDisableSandbox`; `mv` works. When you need to discard a file — most often a stale `.git/index.lock` or `.git/HEAD.lock` left by an interrupted git op — `mv` it into `.to_delete/` at the repo root with a timestamped name. Tom empties the folder manually from Windows periodically. Full convention + stale-lock recovery + corrupt-index recovery ritual at `design/soft-delete-convention.md`. Unlink warnings on a successful git commit (`warning: unable to unlink '.git/index.lock': Operation not permitted`) are cosmetic; the commit landed, move on.
- Before starting a non-trivial feature, write a short design note to `design/{feature-name}.md` capturing the scope, the decisions already made, and the test fixtures needed. A fresh session should be able to pick up mid-feature from that note plus the last commit, without re-litigating choices.
- Commit at every natural boundary, not just at feature completion. Half-finished work behind a clear commit message is recoverable; a dirty worktree is not.
- Use the built-in TodoWrite tool before starting each commit, and keep it current as you work. Tom watches the todo widget to see where you are in the plan; a stale or absent list means he can't track progress. At the start of every new commit, add/refresh todos for that commit's sub-tasks and mark one `in_progress`.
- End each session by updating this file — mark in-progress items, note any deviations or follow-ups — and commit the update. **Do not flip an item to `[x]` without explicit user signoff.** When the final code commit for an item lands, leave the item in `[~]`, record the SHA, and summarize what's pending manual verification. Tom pushes, tests manually, and either confirms the close (then the next session flips it to `[x]` with the SHA preserved) or returns feedback to address. Closing on your own reads as premature.
- **Closed items live in `COMPLETED.md`, not here.** When Tom signs off a `[~]` item, the next session moves its full prose into `COMPLETED.md` and leaves a one-line stub at the original item number in this file. Original numbers are stable — never renumber. When touching territory that overlaps a completed item, read its full entry in `COMPLETED.md` before re-deriving decisions.
- Any feature that modifies `scripts/process_events.py` must extend the pytest fixtures in step with the change, not after. Item 2 below establishes the suite.
- Tests live in `tests/` and run on every push + PR via `.github/workflows/tests.yml`. A red test check blocks merge; don't mark a feature done with tests failing.
- Honor the standing order: deterministic work lives in Python scripts; the agent does only judgment and interpretation. If a feature tempts you to move mechanical work into agent-handled text, push back.
- The `Ellen's ToDo` mount in this project is retired and should be ignored (see memory). All work happens in `kids-schedule-github/`.
- The site is a live view, not an archive. Old `docs/index.html` commits persist in git history but they are not a feature — do not design affordances for "view prior schedules" or commit versioned weekly snapshots under dated filenames.

Status legend:

- `[ ]` not started
- `[~]` in progress — include a note with what is done and what remains
- `[x]` done — include the commit SHA

## Backlog (priority order)

### 1. [x] Failure notifications via GitHub mobile app — c3d2e5b — see COMPLETED.md

### 2. [x] Pytest suite for `scripts/process_events.py` — 8375e9c (suite) / 8a9f4b3 (CI) — see COMPLETED.md

### 3. [x] Weekly email digest to Gmail drafts, with test-mode toggle — b5200cb … f312d90 — see COMPLETED.md

### 4. [x] Incremental extraction — skip already-processed Gmail messages — 008051c … 7528267 — see COMPLETED.md

### 5. [x] Per-event `.ics` export button — 52ebd73 … cc7ac82 — see COMPLETED.md

### 6. [x] Undo recently ignored + 7. "Ignore sender" (bundled) — see COMPLETED.md

### 8. [x] Bug: "Show ignored (N)" counter doesn't update mid-session — eb0236b — see COMPLETED.md

### 9. [x] Footer refresh-tempo copy out of date — 756428c / 2640c4b — see COMPLETED.md

### 10. [x] Gmail draft gating: Monday runs only — 65c86f3 — see COMPLETED.md

### 11. [x] Card information redesign (supersedes per-kid split) — fe6e272 — see COMPLETED.md

### 12. [~] Per-kid filter chips — 399d383 (chips) / fd0c264 (roster subtask) / f0976f6 (design note)

Implemented as three commits: design note → teacher roster (class_roster.json + agent.py prose injection) → chip row + filter JS. Final shape matches `design/per-kid-filter-chips.md`: three hard-coded chips (All / Everly / Isla), non-lossy hide semantics (audience-line and empty-child cards stay visible across every selection), ephemeral filter state. Pending Tom's visual QA on the next live GitHub Pages build before flipping to `[x]`.

### 13. [ ] "New this week" badges

Persist prior-run event IDs to a manifest file in the repo (e.g. `prior_events.json`). On each run, `process_events.py` diffs current IDs against the manifest and stamps cards whose IDs did not exist last week with a visible "NEW" badge. First run: manifest empty, no badges — degrade gracefully. The workflow commits the updated manifest alongside the other outputs.

### 14. [ ] Manual "refresh now" button in the UI

Button in `docs/index.html` that triggers the weekly workflow on demand, so a fresh build can be forced after a late schedule email without waiting for the next scheduled run or opening GitHub. GitHub's `workflow_dispatch` API requires an authenticated call, so the existing Apps Script webhook grows a new `action=refresh` endpoint that holds a fine-grained PAT (scope: `workflow`, single-repo) as a Script Property and POSTs to the dispatches endpoint. Client fires `fetch(APPS_SCRIPT_URL, {method:'POST', body: JSON.stringify({secret, action:'refresh'})})` and shows a "Rebuilding… reload in ~2 min" toast; no live polling.

Threat model accepted: the shared secret is effectively public (embedded in page source on a page with near-zero organic traffic), worst case is a handful of wasted workflow runs. Defense in depth: Apps Script rate-limits to one dispatch per 5 minutes via `PropertiesService`. The workflow's existing `concurrency: {group: pages, cancel-in-progress: false}` already prevents pileups from rapid clicks. PAT rotation: 1-year expiry with a calendar reminder.

### 15. [ ] Conflict highlighting

In `process_events.py`, detect overlapping timed events on the same day via interval intersection; flag both cards with a visible conflict marker. Prioritize different-kid overlaps as the high-signal case. Same-day all-day + timed events should NOT be flagged as conflicts — they coexist by design.

### 16. [x] Node 20 → Node 24 action upgrades (before 2026-06-02) — ea081da — see COMPLETED.md

### 17. [ ] Robust handling of multi-event newsletter emails

Newsletters routinely carry 5–15+ dates each. The extractor is already prompted to pull every calendar item (see `agent.py` rule 8, "Newsletter calendar items"), and per-batch parsing treats N events from one `source_message_id` as the normal case. But there's no signal when the extractor under-extracts, and no affordance to re-process a message once it lands in `events_state.json` — the message ID is cached and the next run skips it.

Scope: (a) log the event count per `source_message_id` on each run and flag outliers when a known-newsletter sender produces markedly fewer events than its prior issues; (b) add a `main.py --reextract <message-id>` flag that evicts the ID from `events_state.json` before the Gmail fetch so the next run rebuilds its events; (c) consider routing newsletter-shaped senders (LAES PTA Sunbeam, FCPS updates, etc.) to a smaller agent batch size for higher per-email attention. Design-note-first.

Non-goal: diffing newsletter issues across time to detect added/removed dates — YAGNI until a concrete miss justifies the infrastructure.