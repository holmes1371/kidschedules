# Kids Schedule — QoL Roadmap

Authoritative backlog for quality-of-life improvements to the kids-schedule-github pipeline. Edit in place; commit changes alongside code.

Always load the karpathy-guidelines skill before starting anything here.

Closed `[x]` items are archived in `COMPLETED.md` with their full post-mortem prose. Stubs below preserve the original numbering so past session summaries and commit messages still resolve.

## Last session summary

Replace this block at the end of each session. Keep it to what the next agent actually needs to walk in cold: what just closed, what's open, where to pick up, and any non-obvious observations that aren't captured under a numbered item.

**2026-04-17 (session 8 — #13 "New this week" badges closed)**

- **Just closed: #13 "New this week" badges.** Three commits on `main`: `5ab4a01` (design note at `design/new-this-week-badges.md` + ROADMAP flip `[ ]` → `[~]`), `ac4ae3b` (helpers + render wiring + CSS + 17 new tests + `main.py` `--prior-events` wiring + `.gitignore` entry), `4cbfc68` (workflow restore/save plumbing). Tom signed off same session. Close-out commit moves full prose to `COMPLETED.md` and flips the stub to `[x]` — this is that commit.
- **Next step (pick up here):** next backlog item is `#14` — "Manual 'refresh now' button in the UI". `#15` (conflict highlighting) and `#17` (multi-event newsletter robustness) also open. `#17` is design-note-first per the ROADMAP entry; `#14` has its full threat model + defense-in-depth rationale baked into the ROADMAP entry already, so design-note-first is lighter lift there.
- **Key design decisions worth remembering for #13**, captured more fully in the COMPLETED entry:
  - **Missing file ≠ empty list.** `_load_prior_event_ids` returns `None` when the file is missing / unreadable / malformed / wrong-shape; returns `set()` when present and well-formed. Caller writes `new_ids = (current - prior) if prior is not None else set()` — so first-run (no manifest) suppresses every badge, but a legitimate empty-prior state still badges everything. The distinction is load-bearing and pinned by four loader edge-case tests.
  - **`prior_events.json` is its own file, not a new key in `events_state.json`.** Different concerns, different GC rules, different parity contracts. File-per-concern matches the existing state-branch pattern. Cache eviction does not touch the render manifest, and a cache-clear still diffs correctly because event IDs are deterministic from `(name, date, child)`.
  - **Badge renders on ignored-but-new cards.** No special-case. The `<span class="new-badge">` ships in the HTML; `display:none` from `.ignored` hides the whole card until Show ignored is clicked, at which point the NEW correctly signals a newly-extracted auto-ignored card.
  - **CSS rule always ships; span is conditional.** The `.new-badge` selector lives in the inline `<style>` block regardless of whether any card renders the span. Suppression tests assert on `<span class="new-badge">` (the rendered element), not the selector string — easy mistake to make, caught during iteration.
  - **Dry-run gating piggybacks on the existing save-step `if:`.** No new flag threaded into `process_events.py`. Dry-run renders badges against the last real prior-run state but doesn't advance the manifest — next real run's diff stays correct.
  - **"New" is binary per run — no aging.** Mon's run overwrites the manifest with Mon's union; by Wed a Mon-new event loses its badge. Simplest possible semantic.
- **One deviation from the design note.** The design note's "Files touched" list said "No changes to `main.py`". Implementation added `PRIOR_EVENTS_PATH` + `--prior-events` wiring through `step4_process_events` — symmetric with the existing state-branch-file pattern. Rationale in the COMPLETED entry; worth noting because the design note is otherwise still load-bearing for explaining why things are shaped the way they are.
- **Repo state at session end:** 329 tests passing (was 312 at session 7 close; +17 #13 tests = 329). Worktree clean. `.to_delete/` picked up another batch of stale `.git/*.lock` + `.git/objects/*/tmp_obj_*` files from this session's git churn — safe to leave until Tom sweeps.
- **Carry-over from sessions 6–7 — Cowork permission re-prompts.** Still re-prompts every `mv`/`git commit`. Broader allowlist entries like `Bash(git:*)` and `Bash(mv:*)` in `.claude/settings.local.json` would kill the noise if Tom asks to address it.
- **FUSE stale-lock reminder.** Today's session hit stale `.git/index.lock` and `.git/HEAD.lock` from an earlier crashed git process on this mount. Pre-commit ritual: `if [ -f .git/index.lock ]; then mv .git/index.lock .to_delete/...; fi; if [ -f .git/HEAD.lock ]; then mv .git/HEAD.lock .to_delete/...; fi` before every `git commit`. The "unable to unlink" warnings that follow a successful commit are cosmetic — commit already landed via rename. Full recovery ritual at `design/soft-delete-convention.md`.

## For future agents

Read this file at the start of any session where Tom mentions "kids-schedule", "the QoL list", or asks about the next feature. The prioritization below is settled — do not re-debate it without prompting. Work items in order unless Tom explicitly says otherwise.

Session discipline:

- Invoke the `karpathy-guidelines` skill via the Skill tool at the start of every session that touches code. Reading `reference/guidelines.md` directly does not count — the skill-load step is what anchors the discipline for the rest of the session.
- git commits need the -c user.name=... -c user.email=... flags since there's no default identity
- **Soft-delete convention, not `rm`.** The FUSE mount this repo lives on refuses `unlink` but permits `rename`. `rm` fails with `Operation not permitted` even under `dangerouslyDisableSandbox`; `mv` works. When you need to discard a file — most often a stale `.git/index.lock` or `.git/HEAD.lock` left by an interrupted git op — `mkdir -p .to_delete && mv <file> .to_delete/<tag>-$(date +%Y%m%d-%H%M%S)`. The folder isn't tracked (no `.gitkeep`); agents create it on demand so Tom can select-all-delete inside it without working around a stub file. Tom empties it manually from Windows periodically. Full convention + stale-lock recovery + corrupt-index recovery ritual at `design/soft-delete-convention.md`. Unlink warnings on a successful git commit (`warning: unable to unlink '.git/index.lock': Operation not permitted`) are cosmetic; the commit landed, move on.
- Before starting a non-trivial feature, write a short design note to `design/{feature-name}.md` capturing the scope, the decisions already made, and the test fixtures needed. A fresh session should be able to pick up mid-feature from that note plus the last commit, without re-litigating choices.
- Commit at every natural boundary, not just at feature completion. Half-finished work behind a clear commit message is recoverable; a dirty worktree is not.
- Use the built-in TodoWrite tool before starting each commit, and keep it current as you work. Tom watches the todo widget to see where you are in the plan; a stale or absent list means he can't track progress. At the start of every new commit, add/refresh todos for that commit's sub-tasks and mark one `in_progress`.
- **Flip `[ ]` → `[~]` as soon as Tom approves the plan for a backlog item — before the design note, before any code.** The status flag is there to tell the next agent what's actually in flight; flipping only at session end means a mid-session interruption leaves the item falsely marked "not started" even though a design note and half the commits exist. Record the flip in whichever commit introduces the first artifact for the item (usually the design note); if the plan is approved but no commit has landed yet, include the flip alongside the first real change so it doesn't need its own throwaway commit.
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

### 12. [x] Per-kid filter chips — f0976f6 (design note) / fd0c264 (roster subtask) / 399d383 (chips) — see COMPLETED.md

### 13. [x] "New this week" badges — 5ab4a01 / ac4ae3b / 4cbfc68 — see COMPLETED.md

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

### 18. [x] Ignore affordance for undated "Needs Verification" cards — 41505aa / aade8aa — see COMPLETED.md

### 19. [x] Deterministic kid attribution from grade / teacher / activity — eb65f8a (design note) / 2ee6a17 (module + unit tests) / ad145ba (wiring + render tests) — see COMPLETED.md