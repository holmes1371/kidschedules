# Kids Schedule — QoL Roadmap

Authoritative backlog for quality-of-life improvements to the kids-schedule-github pipeline. Edit in place; commit changes alongside code.

Always load the karpathy-guidelines skill before starting anything here.

Closed `[x]` items are archived in `COMPLETED.md` with their full post-mortem prose. Stubs below preserve the original numbering so past session summaries and commit messages still resolve.

## Last session summary

Replace this block at the end of each session. Keep it to what the next agent actually needs to walk in cold: what just closed, what's open, where to pick up, and any non-obvious observations that aren't captured under a numbered item.

**2026-04-17 (session 6 — #19 deterministic kid attribution closed, plus #12 chip carry-over closed)**

- **Just closed: #19 deterministic kid attribution + #12 per-kid filter chips.** Both stubs left at their original numbers in the backlog; full prose moved to `COMPLETED.md`. Four #19 implementation commits on `main`: `eb65f8a` (design note + ROADMAP `[ ]` → `[~]`), `2ee6a17` (`scripts/roster_match.py` module + 46 unit tests), `ad145ba` (wire into `process_events.py` + 10 render tests), `a854946` (ROADMAP SHAs + implementation summary). Tom confirmed visual QA on the live Pages build: "6th grade AAP" → Everly, "Cuppett Performing Arts" → Isla, "All LAES students" renders with no pill under every chip, direct name matches stay pill-only. `84aef8a` closed #19 (flip + move-to-COMPLETED). Then Tom also signed off #12's carry-over from session 5 — chips had been working live the whole time and the #19 attribution fix made them useful on messier audience-string cases. This session's close-out commit for #12 followed — see git log tail.
- **Next step (pick up here):** next backlog item is `#13` — "New this week" badges. `#17` (multi-event newsletter robustness) and `#18` (undated-card ignore affordance) remain open at Tom's priority — no work started on either.
- **Key design decisions locked in `design/kid-attribution-derivation.md`**, worth skimming before any change that touches attribution, filter chips, or the roster:
  - **Five signal tiers, priority `name → teacher → grade → activity → school`.** Earlier tiers win on tie. `derive_child_slug` iterates tiers outermost so a teacher match trumps an activity match even if both fire for different kids.
  - **Distinctiveness filter is tier-agnostic.** Any signal string appearing in two or more kids' signal lists is dropped from everyone — not just from one tier. This is why both kids sharing LAES silences the school tier entirely and "All LAES students" events attribute to nobody (correctly, per the non-lossy filter contract from #12).
  - **`_FIRST_WORD_ALIAS_MIN_LEN = 6` is the activity-alias knob.** It picks up bare "Cuppett" (7 chars, distinctive) while rejecting "Born" (4 chars, would false-match "born yesterday" / "reborn"). Short distinctive company names need an explicit parenthetical alias to be caught in isolation (e.g. "Born 2 Dance Studio (B2D)" catches "B2D" via the parenthetical).
  - **`_grade_matches` recognizes three forms per grade:** ordinal digit (`\b6th\b`), word (`\bsixth\b`), and `\bgrade\s+N\b`. Word boundaries reject "16th" and "256th" but allow hyphenated "6th-grade". `advance_grade` adds the rising-year signal so 7th/4th route to Everly/Isla a year ahead of their roster grade.
  - **`derive_child_slug` does not mutate `ev`.** Covered by `test_derive_does_not_mutate_event`. The event-ID hash in `events_state.py` is `sha1(name|date|child)[:12]`; any mutation would orphan cached entries on the next run.
  - **Rendering contract in `_child_markup()`:** slug + `name` tier → pill only (clean, no redundant "For: Everly"); slug + non-`name` tier + child text → pill plus "For: <child>" so the routing reason stays visible; slug + empty child → pill only; no slug + child text → audience line only; no slug + empty child → nothing. Covered by the 10 render tests under `# ─── #19 roster-backed attribution (data-child derivation) ───`.
- **Shape of `scripts/roster_match.py`, for future edits:** pure helpers only — no I/O except `load_roster`. The module-level `_SCHOOL_ALIASES` table is where to add new school-alias rows (lowercased canonical name → list of lowercase aliases). Activities alias extraction is implicit (parenthetical + first-word) — Tom's standing ask was "keep alias extraction implicit" per session-5 discussion. `_kid_signals` builds the raw per-kid list; `build_distinctive_signals` runs the sharing filter; `derive_child_slug` is the entry point `process_events.py` calls. When adding a third kid to `class_roster.json`, no code change is needed unless the new kid's school or activity needs an alias that doesn't fit the implicit rules.
- **Shape of `process_events.py` integration:** module-level initialization reads the roster and builds distinctive signals once at import. The `_child_markup()` closure inside `render_html` is the single rendering point for both `_event_card` and `_undated_card`, so any future change to the pill-vs-audience-line rule edits one place. `_SLUG_TO_NAME` is a module-level map from lowercase slug back to the canonical roster key name (used to recover display casing when rendering the pill).
- **Repo state at session end:** 306 tests passing (was 250 at session 5 end; +46 roster_match + 10 render = 56). **Five session-6 commits on `main` — four already described above plus the close-out commit from this wrap-up.** Pushed by Tom mid-session after the live test; branch should be clean at `origin/main` once the close-out commit goes out. Worktree otherwise clean aside from gitignored `docs/dev_preview.html`. `.to_delete/` picked up another batch of stale `.git/*.lock` files from this session's git churn — safe to leave until Tom sweeps.
- **Open thread (carry-over) — Cowork permission re-prompts:** still re-prompts every `mv`/`git commit`. Session 5's note still applies: broader allowlist entries like `Bash(git:*)` and `Bash(mv:*)` in `.claude/settings.local.json` would kill the noise if Tom asks to address it.

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

### 18. [ ] Ignore affordance for undated "Needs Verification" cards

The "Needs Verification" section at the bottom of the page renders cards for events the extractor surfaced without a date. Those cards go through `_undated_card` in `scripts/process_events.py` and currently carry no Ignore button — dated cards have one, undated cards don't. That's the whole asymmetry to close.

Scope: extend the existing ignore mechanics to undated cards. Reuse the stable-id scheme, the per-event Ignore button, the `ignored` localStorage list, and the Apps Script webhook POST so an ignored undated card stays ignored across runs the same way a dated one does. Touches `_undated_card` (button markup, id wiring) and the click router / hydration IIFE in `render_html` (so the handler recognizes undated cards).

Out of scope: the "Ignore sender" variant. Undated cards often lack a usable sender domain (the ones that leak through to this section are typically the ones the extractor couldn't ground fully), and sender-ignore without a reliable domain is worse than useless. Per-event ignore only.

No design note needed — straight port of the existing pattern. The risk is subtle id-collision across dated/undated sections; the implementation session should confirm the stable-id hash inputs don't overlap before shipping.

### 19. [x] Deterministic kid attribution from grade / teacher / activity — eb65f8a (design note) / 2ee6a17 (module + unit tests) / ad145ba (wiring + render tests) — see COMPLETED.md