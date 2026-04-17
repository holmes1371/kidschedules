# Kids Schedule — QoL Roadmap

Authoritative backlog for quality-of-life improvements to the kids-schedule-github pipeline. Edit in place; commit changes alongside code.

Always load the karpathy-guidelines skill before starting anything here.

Closed `[x]` items are archived in `COMPLETED.md` with their full post-mortem prose. Stubs below preserve the original numbering so past session summaries and commit messages still resolve.

## Last session summary

Replace this block at the end of each session. Keep it to what the next agent actually needs to walk in cold: what just closed, what's open, where to pick up, and any non-obvious observations that aren't captured under a numbered item.

**2026-04-17 (session 5 — #12 filter chips + roster w/ activities, plus a cleanup sweep)**

- **Just closed (pending visual QA): #12 per-kid filter chips.** Three commits for the feature: `f0976f6` (design note), `fd0c264` (class_roster.json + agent.py prose injection), `399d383` (chip row + filter JS + data-child on cards). ROADMAP flipped `[ ]` → `[~]` in `0f1c3f5`. Tom's visual QA on the next live GitHub Pages build flips to `[x]` next session with SHA `399d383` preserved.
- **Next step (pick up here):** if Tom greenlights the visual QA, flip #12 to `[x]` and move its prose into `COMPLETED.md` with a one-line stub. Next backlog item is `#13` — "New this week" badges. `#18` (undated-card ignore affordance, added this session) sits at the tail of the list; pick it up when Tom signals priority.
- **Key design decisions locked in `design/per-kid-filter-chips.md`**, worth skimming before the next QoL UI change:
  - Chip set is hard-coded to three buttons (`All` / `Everly` / `Isla`). It does NOT iterate over unique children in the run — guarded by `test_filter_chip_row_is_static_not_derived_from_events`. Free-text audience values (`All LAES students`, `6th grade AAP`) are an unstable taxonomy and were deliberately excluded.
  - Filter semantics are non-lossy: clicking `Everly` hides only `data-child="isla"` cards; audience-line and empty-child cards stay visible. This is why a school-wide closure still surfaces while you're filtered to Isla. If Tom ever asks for strict "only this kid" view, it's a two-line CSS change.
  - Filter state is ephemeral — no localStorage key. Cold reload returns to `All`. Intentional; keeps the filter distinct from the `ignore` localStorage bookkeeping.
  - The hide rule uses `display: none !important` for specificity symmetry with the existing `.show-ignored .event-card.ignored` override. Without `!important` an Everly-filter + Show-ignored combo would leak an Isla ignored card. Covered by `test_filter_hide_css_uses_important`.
- **Roster subtask shape, for future edits:** `class_roster.json` at repo root is the source of truth. `agent.py::_load_roster_prose` reads it at module import, `_format_roster_prose` (pure, unit-tested) builds the prose block, and it's appended to `_EXTRACTION_BASE_PROMPT` to produce `EXTRACTION_SYSTEM_PROMPT`. The loader **crashes on missing/malformed roster** rather than falling back silently — the file is committed, so absence is a bug. Edit-and-commit is the whole update flow; no code change required. Current values: Everly in 6th grade (Ms. Anita Sahai) with activity "Born 2 Dance Studio (B2D)"; Isla in 3rd grade (Ms. Meredith Rohde) with activity "Cuppett Performing Arts Center"; both at Louise Archer Elementary. The `activities` key is optional per kid (empty list or absent → no clause) so adding a third kid who hasn't started anything yet is a one-line edit.
- **Cleanup sweep also landed this session:** four post-feature commits addressing small Tom-requested items. `4cbc8dc` retires `.to_delete/.gitkeep` — the folder is no longer tracked and agents `mkdir -p .to_delete` on demand; Tom can now select-all-delete inside it from Windows without working around a stub file. `c27ee03` changes session discipline so `[ ]` → `[~]` flips on plan approval (not at session end) — so a mid-session interruption leaves honest state for the next agent. `e0547c6` retires the stale "Glasgow Middle School / Isla" rows in `fixtures/sample_candidates.json` (now LAES, matches the real roster); tests/snapshots still use "Glasgow" as generic unit-test text and were intentionally left alone. `cd816f0` adds the `activities` roster key described above.
- **New backlog item added: #18 — Ignore affordance for undated "Needs Verification" cards.** Same mechanics as dated-card ignore (per-event button, stable id, localStorage, webhook POST); no sender-ignore variant because undated cards usually lack a reliable sender domain. Straight port of the existing pattern, no design note required; main risk is id-collision between dated and undated stable ids.
- **Session-start repo state worth noting:** branch was already up to date with `origin/main` (Tom pushed session 4's four commits plus post-hoc cleanups of his own: `812a4ca` closed #11 into COMPLETED.md, `8d1f198` pinned LF endings). So #11 is fully `[x]` — any UI follow-ups on the card should reference `COMPLETED.md` for the locked decisions.
- **Repo state at session end:** 250 tests passing (was 238 at session start; +3 roster tests + 8 chip tests + 1 activities-clause test = 12). **Nine session-5 commits on `main`, all pushed to `origin/main` by Tom mid-wrap-up.** Chronological: `f0976f6` (design note), `fd0c264` (roster subtask), `399d383` (chip row), `0f1c3f5` (ROADMAP flip → [~]), `4cbc8dc` (drop .gitkeep), `c27ee03` (flip-on-approval discipline), `e0547c6` (Glasgow retirement), `cd816f0` (activities key), `b9b8c3a` (session-5 close-out + #18). Worktree clean aside from gitignored `docs/dev_preview.html`. `.to_delete/` accumulated more stale `.git/*.lock` files from this session's git churn — safe to leave until Tom sweeps.
- **Open thread (carry-over) — Cowork permission re-prompts:** every `mv`/`git commit` was still re-prompted each session for the same command-string-uniqueness reason. If Tom asks to address it, broader allowlist entries like `Bash(git:*)` and `Bash(mv:*)` in `.claude/settings.local.json` would kill the noise.

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

### 18. [ ] Ignore affordance for undated "Needs Verification" cards

The "Needs Verification" section at the bottom of the page renders cards for events the extractor surfaced without a date. Those cards go through `_undated_card` in `scripts/process_events.py` and currently carry no Ignore button — dated cards have one, undated cards don't. That's the whole asymmetry to close.

Scope: extend the existing ignore mechanics to undated cards. Reuse the stable-id scheme, the per-event Ignore button, the `ignored` localStorage list, and the Apps Script webhook POST so an ignored undated card stays ignored across runs the same way a dated one does. Touches `_undated_card` (button markup, id wiring) and the click router / hydration IIFE in `render_html` (so the handler recognizes undated cards).

Out of scope: the "Ignore sender" variant. Undated cards often lack a usable sender domain (the ones that leak through to this section are typically the ones the extractor couldn't ground fully), and sender-ignore without a reliable domain is worse than useless. Per-event ignore only.

No design note needed — straight port of the existing pattern. The risk is subtle id-collision across dated/undated sections; the implementation session should confirm the stable-id hash inputs don't overlap before shipping.

### 19. [~] Deterministic kid attribution from grade / teacher / activity — eb65f8a (design note) / 2ee6a17 (roster_match module + 46 unit tests) / ad145ba (wiring + 10 render tests)

Tom reported that the 2026-04-16 live run missed attribution for a "6th grade AAP" card (should go to Everly) and a "Cuppett Performing Arts" card (should go to Isla). The extractor prompt already tells the model to use grade / teacher / activity to pick a kid, but the model follows that inconsistently. Per the standing order, roster-based attribution is deterministic and belongs in Python rather than in the prompt.

Scope: a pure `_derive_child_slug(ev, roster)` helper in a new `scripts/roster_match.py`, wired into `_event_card` and `_undated_card`. Five signal tiers (name / teacher / grade / activity / school), with grade matching on both current and rising year (so 7th or "rising 7th grader" → Everly, 4th → Isla), activity matching on parenthetical-extracted aliases (so "B2D" → Everly), and a distinctiveness filter that drops signals shared across kids (so "LAES" stays a no-op until the roster has kids at different schools). The `child` field is not mutated — the event-ID hash depends on it, and the audience-line display stays as context next to the kid pill.

Design note at `design/kid-attribution-derivation.md`. Full signal priority, alias extraction, distinctiveness, rendering impact, and the required test cases are captured there.

Landed as two implementation commits on top of the design note. `scripts/roster_match.py` owns the pure helpers (`advance_grade`, `_grade_matches`, `_activity_aliases`, `_school_aliases`, `build_distinctive_signals`, `derive_child_slug`) plus the hard-coded `LAES` school-alias row; 46 unit tests exercise every tier, the distinctiveness filter, tie-break priority, and the no-mutate contract. `scripts/process_events.py` initializes the roster + distinctive signals at module load, derives `(slug, tier)` per event inside `_child_markup()`, and renders pill vs audience-line per tier so name-match stays pill-only while grade/activity/teacher matches surface the routing reason. 10 render tests cover the two live misses (6th-grade AAP → Everly, Cuppett → Isla) plus regression guards for shared LAES (no pill, audience-line retained) and name-match audience suppression. Full suite: 306 passed. Pending Tom's visual QA on the next GitHub Pages build before flipping to `[x]`.