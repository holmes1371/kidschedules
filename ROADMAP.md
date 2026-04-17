# Kids Schedule — QoL Roadmap

Authoritative backlog for quality-of-life improvements to the kids-schedule-github pipeline. Edit in place; commit changes alongside code.

Always load the karpathy-guidelines skill before starting anything here.

Closed `[x]` items are archived in `COMPLETED.md` with their full post-mortem prose. Stubs below preserve the original numbering so past session summaries and commit messages still resolve.

## Last session summary

This section holds **exactly one block** — the current/most-recent session. When you write a new session summary, REPLACE what's here in place; do not append and do not archive the old block below. Older sessions' context is already preserved in commit messages, `COMPLETED.md` entries, and `design/*.md` notes — the point of this block is "what the next agent needs to walk in cold", not a historical log. Keep it to: what just closed, what's open, where to pick up, and any non-obvious observations that aren't captured under a numbered item.

**2026-04-17 (session 10 — #20 freemail-aware sender-block granularity closed)**

- **Just closed: #20 "Freemail-aware sender-block granularity".** Eight commits on `main`, each self-contained and testable: `f855dee` (design note + ROADMAP insert + `[~]` flip), `745957a` (`freemail_domains.txt` + `scripts/freemail.py` loader + 9 tests), `d5820c2` (`main.py::_attach_sender_block_keys` + 10 tests), `563354c` (`events_state.py` `CURRENT_SCHEMA_VERSION 2 → 3` + 1 test), `bf9fe35` (`process_events.py` render wiring + fixture parity + 5 tests), `8170081` (`is_protected` address-aware + 4 tests), `03b44c5` (`build_queries.py` docstring updates + 1 integration test), `e448a8a` (`apps_script.gs` `DOMAIN_RE → SENDER_RE` at four call sites, no automated tests — smoke-verify against live deploy). Tom redeployed the Apps Script Web app same session and has the live smoke checklist in hand (full list in the COMPLETED.md entry). Close-out commit moves the full prose to `COMPLETED.md` and flips the stub to `[x]` — this is that commit.
- **Mid-flight on `#21` "Dedupe candidate messages before agent extraction" — C1–C3 landed, awaiting Tom's live QA.** Three commits on `main`: `9882a1c` (C1: design note at `design/dedupe-candidate-messages.md` + ROADMAP flip `[ ]` → `[~]`), `775f173` (C2: pure `_dedupe_by_thread` helper in `main.py` + 8 unit tests), `44283b6` (C3: wire into `step2b_read_promising` between the existing messageId pass and `read_message` + 1 integration test + three-line log funnel "Collected N stub(s) across 5 queries" / "Unique messageIds: M" / "After thread dedup: K"). **Tom has NOT yet run this live — he stepped away without testing.** The suite is green (458 passing) but the first real weekly cron is the ground truth. If the next agent picks up before Tom signs off: leave `#21` at `[~]`, do not touch `COMPLETED.md`, do not prepare a close-out commit. Tom's pickup flow is normally to verify the three-line funnel appears in the next live Actions log with realistic numbers (the four-hit dance-studio thread should collapse to one `[i/N]` hit), and possibly eyeball the generated page for missing items. If he reports the live log looks right, close-out commit = move the #21 prose stub to `COMPLETED.md`, flip `[~]` → `[x]`, record the three SHAs + close-out SHA in the new COMPLETED entry, replace this session block. If he reports missing items, the escape hatch is `python main.py --reextract <messageId>` for the specific thread; the dedup still runs but the forced messageId bypasses cache-level filtering. **Key correction to the original ROADMAP entry scope:** `step2b_read_promising` already does messageId-level dedup via its `seen_ids` loop (lines 264–273 pre-C3), so the four-hit dance-studio pattern Tom observed is four distinct messageIds sharing one threadId. The fix turned out to be threadId-level dedup — the "slightly more ambitious" option named in the original entry. Full framing, decisions, and accepted-risk list in `design/dedupe-candidate-messages.md`. **Policy call on latest-per-thread** (Tom, 2026-04-17): "assume the most recent message has the most relevant information". Earlier replies' details already persist via prior-run events (120-day GC); `--reextract <messageId>` is the escape hatch if a thread's latest reply turns out too thin. `#14` (manual refresh button) and `#15` (conflict highlighting) were descoped in the earlier `0bccede` commit — full prose preserved under "Descoped / on hold" at the bottom of the ROADMAP.
- **Key design decisions worth remembering for #20**, captured more fully in the COMPLETED entry:
  - **Two fields per event, not one.** `sender_domain` (registrable domain, unchanged semantic, drives `is_protected` and institutional Gmail-query exclusion) plus new `sender_block_key` (address for freemail, domain otherwise — drives the button, the `data-sender` attribute, the POST payload, the sheet row, the Gmail-query exclusion for freemail). Single-field "switch shape" would have produced mixed-granularity cards during the 120-day state GC window, which is confusing UX; two fields + v3 blow-away retires the ambiguous-shape rows in one pass.
  - **Address-awareness in `is_protected` is the load-bearing protected-domains guarantee.** Both consumers (`process_events.render_html` suppressing the button, `build_queries.main` filtering the ignored-senders union) share the one matcher, so `alice@fcps.edu` trips the same `fcps.edu` pattern as the bare domain. Even a direct sheet edit or stale pre-#20 row that tries to land `alice@fcps.edu` as a blocker is rejected by the build-queries filter. C7's integration test (`test_build_queries_drops_address_form_protected_from_ignored_senders`) pins the end-to-end path.
  - **Freemail list is hand-curated, not computed.** 19-host seed (gmail/yahoo/outlook/hotmail/icloud/aol/me/mac/live/msn/comcast/verizon/protonmail/proton/fastmail/gmx×2/yandex/zoho). Loader mirrors `protected_senders.load_protected_senders` (tolerant of comments and blank lines, case-folded, missing-file → empty). Adding a host is a one-line edit; automating from MX/PSL data would add network deps for minimal gain.
  - **Wire-protocol key stays `payload.domain`.** `DOMAIN_RE = /^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$/` became `SENDER_RE = /^(?:[^\s@]+@)?[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$/` — strictly broader, so first-wave clients still sending domain-only payloads continue to validate with no flag-day rename in client JS. Four Apps Script call sites swapped.
  - **Existing sheet rows not migrated.** Any `gmail.com` / `yahoo.com` / other freemail-domain rows accumulated in the Ignored Senders tab pre-#20 continue blocking the entire domain until Ellen deletes them manually. Automating the cleanup would have required Apps Script code that reads `freemail_domains.txt` and filters retroactively — a one-time operation that's easier to eyeball in the sheet than to write code for.
  - **Unignore-sender exact-match semantics.** `alice@gmail.com` does NOT match `gmail.com` and vice versa — address-level and domain-level ignores unignore at the same level. Documented in the Apps Script header block and enforced by the lowercased exact-equality compare in `_handleUnignoreSender`.
- **One-time reprocessing cost.** The schema v2 → v3 bump blew away the `events_state.json` cache on first run post-C4, so the run after `563354c` landed handed all 66 candidates in the 60-day window to the extractor instead of hitting the incremental cache. Expected per the design note and confirmed live by Tom ("it's passing all 66 emails to the agent"). Subsequent runs return to normal incremental caching.
- **Observation surfaced live during the #20 rollout.** Looking at the post-C4 run log, Tom flagged that a single dance-studio thread was producing 4 `[N/66]` hits in the extractor input because the thread matched 3+ of the 5 overlapping Gmail query templates. That became #21, filed before C9 landed so the ROADMAP is accurate the moment close-out publishes.
- **Repo state mid-#21 at Tom's step-away:** 458 tests passing (was 449 at #20 close; +9 across #21: C1 +0, C2 +8 unit, C3 +1 integration). Worktree clean. Branch is 3 commits ahead of `origin/main` — push at Tom's discretion (past pattern has been to push after live-QA confirmation, not before, so that a broken live run can be amended locally before hitting GitHub).
- **FUSE stale-lock situation this session.** Same pattern as sessions 8–9. Stale `.git/index.lock` and `.git/HEAD.lock` appeared repeatedly during the C1–C8 run plus the `0bccede` ROADMAP reorg commit. Pre-commit ritual (soft-delete both to `.to_delete/` via `mv`) ran ~10 times successfully. One git index corruption in C5 recovered via `mv .git/index` + `git read-tree HEAD`. Full recovery ritual at `design/soft-delete-convention.md`.
- **Carry-over from sessions 6–9 — Cowork permission re-prompts.** Still re-prompts every `mv`/`git commit`. Untouched this session.

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
- **Update the "Last session summary" block between each commit during a multi-commit feature, not just at session end.** The block should always reflect what *just* landed and what's next, so a mid-feature handoff — mid-session or across agents — has a clean pickup point. The block is single-slot: replace in place, do not append. Older sessions' context lives in commit messages, `COMPLETED.md`, and `design/*.md`.
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
- `[-]` descoped / on hold — full prose preserved in "Descoped / on hold" at the bottom for possible future revival

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

### 17. [x] Robust handling of multi-event newsletter emails — 2f68501 / 85ae9fa / 89fe4be / bcee931 / 191edaf / 00d0a19 / 3d4bcaa — see COMPLETED.md

### 20. [x] Freemail-aware sender-block granularity — f855dee / 745957a / d5820c2 / 563354c / bf9fe35 / 8170081 / 03b44c5 / e448a8a — see COMPLETED.md

### 21. [~] Dedupe candidate messages before agent extraction

Filed 2026-04-17 (session 10) after Tom spotted the symptom in live logs: a single dance-studio "Re: First dibs on Recital TICKETS…" thread produced four hits (`[31/66]`, `[32/66]`, `[35/66]`, `[36/66]`) in the extractor input, and the "Reverb Dance Comp" reminder produced two (separate timestamps — those may be legitimately distinct). The 5 Gmail search templates in `scripts/build_queries.py` overlap by design (a dance-studio email plausibly matches `school_activities`, `sports_extracurriculars`, and `newsletters_calendars` at once), and Gmail returns each message independently per query — so the union of candidates before extraction is noisier than it needs to be. Agent cost scales roughly linearly with candidate count, so this is real waste on every run.

Discovery in session 11 corrected the original framing: `step2b_read_promising` already dedupes by `messageId` via its `seen_ids` set, so the four hits are four *distinct* messages in the same Gmail thread rather than four copies of one message. The cheap fix is therefore `threadId`-level dedup (the "slightly more ambitious" option originally named below), not another `messageId` pass. Policy decided same session: keep only the latest message per thread — the most recent reply usually restates the operative date/decision. Drop happens in `step2b_read_promising` after the existing messageId pass and before the `read_message` body fetch, so the Gmail API call is saved alongside the agent cost.

Scope in progress: `_dedupe_by_thread` helper in `main.py` (pure function), wired into `step2b_read_promising`; three new log lines replace `Unique messages to read: {N}` to show the full funnel (stubs / unique messageIds / after thread dedup). Tests cover the helper's edge cases (empty, no collisions, clear Date ordering, tiebreaker, missing threadId, malformed Date) plus one integration test that simulates the dance-studio four-hit pattern against the step2b flow. See `design/dedupe-candidate-messages.md` for the full decision record.

### 22. [ ] Bug: page header "N day lookback" ignores `--lookback-days` CLI value

Filed 2026-04-17 by Tom. He ran the workflow with `lookback_days=120` via the dispatch input; the Gmail searches correctly used the 120-day window and extra events surfaced, but the rendered header on `docs/index.html` still read "60 day lookback" (screenshot confirmed: "32 events / 60 day lookback" under "Updated April 17, 2026 @ 5:14PM"). So the page lies about how wide a window it was built from, and the data-vs-display-copy drift is the kind of mismatch that will eventually cause the wrong call on "is this event old enough to still trust".

Root cause is a single missing argument pass-through. `main.py::step4_process_events` (around line 817 at `6d80f53`) hardcodes `"--display-window-days", "60"` when invoking `scripts/process_events.py` and never forwards `args.lookback_days` as `--lookback-days`. `process_events.py`'s argparse (line 1928) defaults `--lookback-days` to `60`, so the value that ends up rendered in the `"{lookback_days} day lookback"` template (line 1405) and in the no-events fallback paragraph (line 1001) is always `60` regardless of what the workflow was triggered with. `--display-window-days` (future horizon for the published page) is a genuinely separate knob and the `"60"` hardcode there is fine — that's always 60 days forward of today.

Fix:

- Add a `lookback_days: int` parameter to `main.py::step4_process_events`.
- Thread `args.lookback_days` in at the single caller (line 1073).
- Append `"--lookback-days", str(lookback_days)` to the `script_args` list built around line 809.
- No changes needed in `process_events.py` — the flag is already defined and already rendered into the header template.

Tests: one pytest that mocks out `run_script` in `step4_process_events`, calls it with `lookback_days=120`, and asserts `--lookback-days 120` appears in the captured script args. Existing snapshot tests cover the header template; no new render-side tests needed.

Accepted risk / non-goal: weekly cron runs with no `lookback_days` dispatch input still default to 60 (the workflow's default), so the page continues to read "60 day lookback" on the common path. This is intentional — the bug is only visible when Tom overrides the window manually.
### 14. [-] Manual "refresh now" button in the UI — descoped 2026-04-17, see "Descoped / on hold" at bottom

### 15. [-] Conflict highlighting — descoped 2026-04-17, see "Descoped / on hold" at bottom

### 16. [x] Node 20 → Node 24 action upgrades (before 2026-06-02) — ea081da — see COMPLETED.md

### 18. [x] Ignore affordance for undated "Needs Verification" cards — 41505aa / aade8aa — see COMPLETED.md

### 19. [x] Deterministic kid attribution from grade / teacher / activity — eb65f8a (design note) / 2ee6a17 (module + unit tests) / ad145ba (wiring + render tests) — see COMPLETED.md

## Descoped / on hold

Items parked here aren't dead — they're off the active queue but preserved in case priorities shift. Revive by moving the full prose back under "Backlog" at the original number and flipping `[-]` → `[ ]`.

### 14. [-] Manual "refresh now" button in the UI

Descoped 2026-04-17 (session 10). The weekly cron cadence has been sufficient in practice — Tom has not hit a real case of needing a mid-week rebuild since the feature was originally filed, and the threat-model / PAT-rotation overhead no longer looks worth the payoff. Preserving the full scope below in case that changes.

Button in `docs/index.html` that triggers the weekly workflow on demand, so a fresh build can be forced after a late schedule email without waiting for the next scheduled run or opening GitHub. GitHub's `workflow_dispatch` API requires an authenticated call, so the existing Apps Script webhook grows a new `action=refresh` endpoint that holds a fine-grained PAT (scope: `workflow`, single-repo) as a Script Property and POSTs to the dispatches endpoint. Client fires `fetch(APPS_SCRIPT_URL, {method:'POST', body: JSON.stringify({secret, action:'refresh'})})` and shows a "Rebuilding… reload in ~2 min" toast; no live polling.

Threat model accepted: the shared secret is effectively public (embedded in page source on a page with near-zero organic traffic), worst case is a handful of wasted workflow runs. Defense in depth: Apps Script rate-limits to one dispatch per 5 minutes via `PropertiesService`. The workflow's existing `concurrency: {group: pages, cancel-in-progress: false}` already prevents pileups from rapid clicks. PAT rotation: 1-year expiry with a calendar reminder.

### 15. [-] Conflict highlighting

Descoped 2026-04-17 (session 10). Same-day multi-kid overlaps are visually obvious on the current card layout — the week grouping already co-locates them — and Tom has not seen a missed-conflict incident that would justify the render complexity. Preserving the scope below in case a kid adds a second activity that creates regular overlaps.

In `process_events.py`, detect overlapping timed events on the same day via interval intersection; flag both cards with a visible conflict marker. Prioritize different-kid overlaps as the high-signal case. Same-day all-day + timed events should NOT be flagged as conflicts — they coexist by design.