# Kids Schedule — QoL Roadmap

Authoritative backlog for quality-of-life improvements to the kids-schedule-github pipeline. Edit in place; commit changes alongside code.

Always load the karpathy-guidelines skill before starting anything here.

Closed `[x]` items are archived in `COMPLETED.md` with their full post-mortem prose. Stubs below preserve the original numbering so past session summaries and commit messages still resolve.

## Last session summary

Replace this block at the end of each session. Keep it to what the next agent actually needs to walk in cold: what just closed, what's open, where to pick up, and any non-obvious observations that aren't captured under a numbered item.

**2026-04-17 (session 10 — #20 freemail-aware sender-block granularity closed)**

- **Just closed: #20 "Freemail-aware sender-block granularity".** Eight commits on `main`, each self-contained and testable: `f855dee` (design note + ROADMAP insert + `[~]` flip), `745957a` (`freemail_domains.txt` + `scripts/freemail.py` loader + 9 tests), `d5820c2` (`main.py::_attach_sender_block_keys` + 10 tests), `563354c` (`events_state.py` `CURRENT_SCHEMA_VERSION 2 → 3` + 1 test), `bf9fe35` (`process_events.py` render wiring + fixture parity + 5 tests), `8170081` (`is_protected` address-aware + 4 tests), `03b44c5` (`build_queries.py` docstring updates + 1 integration test), `e448a8a` (`apps_script.gs` `DOMAIN_RE → SENDER_RE` at four call sites, no automated tests — smoke-verify against live deploy). Tom redeployed the Apps Script Web app same session and has the live smoke checklist in hand (full list in the COMPLETED.md entry). Close-out commit moves the full prose to `COMPLETED.md` and flips the stub to `[x]` — this is that commit.
- **Next step (pick up here):** next backlog item is `#21` — "Dedupe candidate messages before agent extraction". Filed this session at `0bccede` after Tom noticed a single dance-studio thread producing 4 hits across the 5 overlapping Gmail queries in the extractor input. Scope sketched in the ROADMAP entry: messageId-level dedup as the cheapest first pass, threadId-level as a possible follow-up if post-landing logs show remaining noise. `#14` (manual refresh button) and `#15` (conflict highlighting) were descoped in the same `0bccede` commit — full prose preserved under "Descoped / on hold" at the bottom of the ROADMAP.
- **Key design decisions worth remembering for #20**, captured more fully in the COMPLETED entry:
  - **Two fields per event, not one.** `sender_domain` (registrable domain, unchanged semantic, drives `is_protected` and institutional Gmail-query exclusion) plus new `sender_block_key` (address for freemail, domain otherwise — drives the button, the `data-sender` attribute, the POST payload, the sheet row, the Gmail-query exclusion for freemail). Single-field "switch shape" would have produced mixed-granularity cards during the 120-day state GC window, which is confusing UX; two fields + v3 blow-away retires the ambiguous-shape rows in one pass.
  - **Address-awareness in `is_protected` is the load-bearing protected-domains guarantee.** Both consumers (`process_events.render_html` suppressing the button, `build_queries.main` filtering the ignored-senders union) share the one matcher, so `alice@fcps.edu` trips the same `fcps.edu` pattern as the bare domain. Even a direct sheet edit or stale pre-#20 row that tries to land `alice@fcps.edu` as a blocker is rejected by the build-queries filter. C7's integration test (`test_build_queries_drops_address_form_protected_from_ignored_senders`) pins the end-to-end path.
  - **Freemail list is hand-curated, not computed.** 19-host seed (gmail/yahoo/outlook/hotmail/icloud/aol/me/mac/live/msn/comcast/verizon/protonmail/proton/fastmail/gmx×2/yandex/zoho). Loader mirrors `protected_senders.load_protected_senders` (tolerant of comments and blank lines, case-folded, missing-file → empty). Adding a host is a one-line edit; automating from MX/PSL data would add network deps for minimal gain.
  - **Wire-protocol key stays `payload.domain`.** `DOMAIN_RE = /^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$/` became `SENDER_RE = /^(?:[^\s@]+@)?[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$/` — strictly broader, so first-wave clients still sending domain-only payloads continue to validate with no flag-day rename in client JS. Four Apps Script call sites swapped.
  - **Existing sheet rows not migrated.** Any `gmail.com` / `yahoo.com` / other freemail-domain rows accumulated in the Ignored Senders tab pre-#20 continue blocking the entire domain until Ellen deletes them manually. Automating the cleanup would have required Apps Script code that reads `freemail_domains.txt` and filters retroactively — a one-time operation that's easier to eyeball in the sheet than to write code for.
  - **Unignore-sender exact-match semantics.** `alice@gmail.com` does NOT match `gmail.com` and vice versa — address-level and domain-level ignores unignore at the same level. Documented in the Apps Script header block and enforced by the lowercased exact-equality compare in `_handleUnignoreSender`.
- **One-time reprocessing cost.** The schema v2 → v3 bump blew away the `events_state.json` cache on first run post-C4, so the run after `563354c` landed handed all 66 candidates in the 60-day window to the extractor instead of hitting the incremental cache. Expected per the design note and confirmed live by Tom ("it's passing all 66 emails to the agent"). Subsequent runs return to normal incremental caching.
- **Observation surfaced live during the #20 rollout.** Looking at the post-C4 run log, Tom flagged that a single dance-studio thread was producing 4 `[N/66]` hits in the extractor input because the thread matched 3+ of the 5 overlapping Gmail query templates. That became #21, filed before C9 landed so the ROADMAP is accurate the moment close-out publishes.
- **Repo state at session end:** 449 tests passing (was 419 at session-9 close; +30 across #20 commits: C1 +0, C2 +9, C3 +10, C4 +1, C5 +5, C6 +4, C7 +1, C8 +0). Worktree clean pre-close-out.
- **FUSE stale-lock situation this session.** Same pattern as sessions 8–9. Stale `.git/index.lock` and `.git/HEAD.lock` appeared repeatedly during the C1–C8 run plus the `0bccede` ROADMAP reorg commit. Pre-commit ritual (soft-delete both to `.to_delete/` via `mv`) ran ~10 times successfully. One git index corruption in C5 recovered via `mv .git/index` + `git read-tree HEAD`. Full recovery ritual at `design/soft-delete-convention.md`.
- **Carry-over from sessions 6–9 — Cowork permission re-prompts.** Still re-prompts every `mv`/`git commit`. Untouched this session.

---

**2026-04-17 (session 9 — #17 robust newsletter handling closed)**

- **Just closed: #17 "Robust handling of multi-event newsletter emails".** Seven commits on `main`, each self-contained and testable: `2f68501` (design note at `design/newsletter-robustness.md` + ROADMAP flip `[ ]` → `[~]`), `85ae9fa` (new `newsletter_stats.py` module + 31 unit tests), `89fe4be` (`main.py --reextract <MESSAGE_ID>` CLI + eviction helper + 5 tests), `bcee931` (`agent.py` newsletter-isolated batching via `_sender_key` / `_plan_batches` helpers + 16 tests), `191edaf` (`main.py` stats integration + outlier-alert computation + STEP 3c banner + 13 tests), `00d0a19` (alerts → weekly digest bridge via `_load_outlier_alerts` + `_render_outlier_block_text`/`_html` + `--outlier-alerts` flag + 24 tests), `3d4bcaa` (workflow `sender_stats.json` restore/save + `.gitignore` entry). Tom signed off same session. Close-out commit moves full prose to `COMPLETED.md` and flips the stub to `[x]` — this is that commit.
- **Next step (pick up here):** next backlog item is `#14` — "Manual 'refresh now' button in the UI". After that, `#15` (conflict highlighting). Both have their full ROADMAP entries intact with scope and design sketches.
- **Key design decisions worth remembering for #17**, captured more fully in the COMPLETED entry:
  - **Sender key is lowercased mailbox, not domain.** Domain-level aggregation would dilute the signal across high-yield and low-yield senders on the same domain. `agent._sender_key` is the single source of truth — `main.py` imports it directly so the canonicalization can't drift.
  - **Promotion thresholds are `messages_seen >= 3 AND median(per_message_counts) >= 5`, sticky.** Demotion is not implemented; manual file edit is the escape hatch. Sticky avoids thrashing a newsletter in and out of classification on a quiet week.
  - **Outlier threshold is `max(2, round(prior_median * 0.5))` with STRICT `<`.** The floor of 2 protects low-median senders from false positives; strict inequality means a current count exactly half the prior does not fire the flag (debatable but pinned by test).
  - **`per_message_counts` rolling window is FIFO-capped at 10.** Bounds file growth and keeps the median insensitive to year-old data. Zero-yield runs STILL contribute a `0` to the window — a quiet-newsletter-issue keeps the median honest rather than drifting upward.
  - **Alerts computed BEFORE `update_sender_counts`.** Load-bearing ordering invariant. If a future refactor inlines the two calls or reorders them "to be tidy," `prior_median` silently becomes "current-including-this-run median" and the feature stops detecting under-extraction. Docstrings on `outlier_alerts` and `update_sender_counts` both call this out; tests pin it.
  - **Newsletter-isolated batches run FIRST.** Batch-of-1 per newsletter email for max agent attention; `BATCH_SIZE=10` for regulars as before. Newsletters first so a cheap-batch parse failure doesn't gate expensive newsletter work.
  - **Dry-run reads but does not write `sender_stats.json`.** Mirrors #13's `prior_events.json` handling. Stats save is double-gated: `not --dry-run AND per_message_counts non-empty` — an all-cached run does not save either.
  - **`--reextract` evicts BOTH `processed_messages[mid]` AND every event with matching `source_message_id`.** Destructive by design: the re-extraction merges its own events back in; originals it doesn't reproduce stay gone. Unknown message IDs warn-but-do-not-fail (fat-finger safety).
  - **Alerts surface in the Monday digest AND the Actions log, NOT on the published site.** The schedule page is a live view, not a status dashboard. Mon-only digest surfacing is implicit via step6's existing cadence gate; Wed/Sat runs still write the alerts tempfile but the draft never ships, so those alerts land in the Actions log only.
- **Session discipline clarification from Tom this session, worth carrying forward.** Update the ROADMAP session-notes block between each commit during a large multi-commit feature, not just at session end. The block should always reflect what *just* landed and what's next, so a mid-feature handoff — mid-session or across agents — has a clean pickup point. Applied throughout the seven-commit arc here.
- **First-real-run behaviour of the state branch.** On the first workflow run after `3d4bcaa` lands, `.state/sender_stats.json` is absent — the classifier boots empty and no sender can be promoted on that run (threshold unreachable from one run). Two more weekly cadences bring the first eligible newsletter across. Warm-up window is intentional, documented in both the C7 commit message and `design/newsletter-robustness.md`.
- **Repo state at session end:** 419 tests passing (was 329 at #13 close; +31 C2, +5 C3, +16 C4, +13 C5, +24 C6, +0 C1/C7; C4 log reported 382 but arithmetic is 381 — cosmetic drift in the session note only, not reflective of an actual suite count). Eight #17 commits on `main` (seven feature + this close-out); branch ahead of `origin/main` by 10+ commits — push at discretion. Worktree clean pre-close-out.
- **FUSE stale-lock situation this session.** Same as session 8 — stale `.git/index.lock` and `.git/HEAD.lock` appeared multiple times during the C1–C7 run. Pre-commit ritual (soft-delete both to `.to_delete/` via `mv`) ran six or seven times successfully. Phantom locks re-appeared between `mv` and `git add` on both C6 and C7 commits; re-running the soft-delete cleared them on the first retry. The "unable to unlink" warnings on successful commits remain cosmetic — the commit already landed via rename. Full recovery ritual at `design/soft-delete-convention.md`.
- **Carry-over from sessions 6–8 — Cowork permission re-prompts.** Still re-prompts every `mv`/`git commit`. Broader allowlist entries like `Bash(git:*)` and `Bash(mv:*)` in `.claude/settings.local.json` would kill the noise if Tom asks to address it. Untouched this session.

---

**2026-04-17 (session 8 — #13 "New this week" badges closed)**

- **Just closed: #13 "New this week" badges.** Three commits on `main`: `5ab4a01` (design note at `design/new-this-week-badges.md` + ROADMAP flip `[ ]` → `[~]`), `ac4ae3b` (helpers + render wiring + CSS + 17 new tests + `main.py` `--prior-events` wiring + `.gitignore` entry), `4cbfc68` (workflow restore/save plumbing). Tom signed off same session. Close-out commit moves full prose to `COMPLETED.md` and flips the stub to `[x]` — this is that commit.
- **Next step (pick up here):** next backlog item is `#17` — "Robust handling of multi-event newsletter emails". Tom re-prioritized it to the top of the open queue on 2026-04-17; the entry has been physically moved in the ROADMAP so it sits just after #13 in file order. Numbering is unchanged. #17 is design-note-first per its ROADMAP body. `#14` (manual refresh button) and `#15` (conflict highlighting) follow, in that order.
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

### 21. [ ] Dedupe candidate messages before agent extraction

Filed 2026-04-17 (session 10) after Tom spotted the symptom in live logs: a single dance-studio "Re: First dibs on Recital TICKETS…" thread produced four hits (`[31/66]`, `[32/66]`, `[35/66]`, `[36/66]`) in the extractor input, and the "Reverb Dance Comp" reminder produced two (separate timestamps — those may be legitimately distinct). The 5 Gmail search templates in `scripts/build_queries.py` overlap by design (a dance-studio email plausibly matches `school_activities`, `sports_extracurriculars`, and `newsletters_calendars` at once), and Gmail returns each message independently per query — so the union of candidates before extraction is noisier than it needs to be. Agent cost scales roughly linearly with candidate count, so this is real waste on every run.

Cheapest fix: insert a dedup pass in `main.py` between "collect candidates from all 5 Gmail queries" and "hand to the extractor", keyed on Gmail `messageId`. That alone compresses the 4× recital-thread duplication down to 1 per distinct message. Slightly more ambitious: dedupe by `threadId` and keep only the latest message per thread on the theory that the most recent message usually carries the operative date/decision — this would also collapse the genuine-but-redundant 2× reminder case when the earlier reminder is subsumed by the later one. Start with `messageId`-level; promote to `threadId`-level if post-landing stats show remaining noise.

Test plan: extend the existing agent-batching fixtures with a synthetic multi-query-hit input and assert the dedup pass produces exactly one entry per `messageId`. Instrument the logs so the `[N/M]` banner reports `M` post-dedup with a separate "collected X across 5 queries, deduped to M" line — makes future regressions visible in the Actions log.

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