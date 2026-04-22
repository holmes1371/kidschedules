# Kids Schedule — QoL Roadmap

Authoritative backlog for quality-of-life improvements to the kids-schedule-github pipeline. Edit in place; commit changes alongside code.

Always load the karpathy-guidelines skill before starting anything here.

Closed `[x]` items are archived in `COMPLETED.md` with their full post-mortem prose. Stubs below preserve the original numbering so past session summaries and commit messages still resolve.

## Last session summary

This section holds **exactly one block** — the current/most-recent session — and it MUST be short. The next agent needs a cold pickup, not a recap.

Strict rules for writing it:

1. **≤5 bullets, ≤1 sentence each where possible.** Trim ruthlessly. If a bullet needs a paragraph, the real content belongs in a design note or `COMPLETED.md`; link it.
2. **Only what is open, in-flight, or just-filed.** Do NOT restate design decisions, rationale, or commit-by-commit walkthroughs for closed items — those live in `COMPLETED.md`; the next agent can read them if needed.
3. **No standing guidance here.** FUSE rituals, soft-delete convention, commit discipline — all of that lives in "For future agents" below. Do not duplicate.
4. **No cross-session carry-overs.** If something is still broken session-to-session, file it as a numbered ROADMAP item instead of repeating it here.
5. **Replace in place.** Do not append a new block and archive the old one below.

**2026-04-22**

- Item 24 (`agent.py` duplicate `AUDIT_SYSTEM_PROMPT`) fixed in 0ba31c9; item left at `[~]` pending Tom's manual verification of the live audit flow.
- Working through the Test coverage gaps section in risk-tier order. **High-risk and medium-risk tiers both cleared.** 89 new tests landed across the run — 526 passing on Linux CI. Pre-existing 92 Windows `%-d` strftime failures in `test_process_events.py` / `test_protected_senders.py` are unrelated and unchanged.
- Low-risk tier is intentionally left — each entry is called out in the section as "intentional skip" or "low payoff" (pure CLI utilities, subprocess wrapper, interactive OAuth flow, Apps Script GS file). No further action unless Tom wants them pulled in.
- Nothing else in flight.

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

1\. [x] Failure notifications via GitHub mobile app — c3d2e5b — see COMPLETED.md

2\. [x] Pytest suite for `scripts/process_events.py` — 8375e9c (suite) / 8a9f4b3 (CI) — see COMPLETED.md

3\. [x] Weekly email digest to Gmail drafts, with test-mode toggle — b5200cb … f312d90 — see COMPLETED.md

4\. [x] Incremental extraction — skip already-processed Gmail messages — 008051c … 7528267 — see COMPLETED.md

5\. [x] Per-event `.ics` export button — 52ebd73 … cc7ac82 — see COMPLETED.md

6\. [x] Undo recently ignored + 7. "Ignore sender" (bundled) — see COMPLETED.md

8\. [x] Bug: "Show ignored (N)" counter doesn't update mid-session — eb0236b — see COMPLETED.md

9\. [x] Footer refresh-tempo copy out of date — 756428c / 2640c4b — see COMPLETED.md

10\. [x] Gmail draft gating: Monday runs only — 65c86f3 — see COMPLETED.md

11\. [x] Card information redesign (supersedes per-kid split) — fe6e272 — see COMPLETED.md

12\. [x] Per-kid filter chips — f0976f6 (design note) / fd0c264 (roster subtask) / 399d383 (chips) — see COMPLETED.md

13\. [x] "New this week" badges — 5ab4a01 / ac4ae3b / 4cbfc68 — see COMPLETED.md

14\. [-] Manual "refresh now" button in the UI — descoped 2026-04-17, see "Descoped / on hold" at bottom

15\. [-] Conflict highlighting — descoped 2026-04-17, see "Descoped / on hold" at bottom

16\. [x] Node 20 → Node 24 action upgrades (before 2026-06-02) — ea081da — see COMPLETED.md

17\. [x] Robust handling of multi-event newsletter emails — 2f68501 / 85ae9fa / 89fe4be / bcee931 / 191edaf / 00d0a19 / 3d4bcaa — see COMPLETED.md

18\. [x] Ignore affordance for undated "Needs Verification" cards — 41505aa / aade8aa — see COMPLETED.md

19\. [x] Deterministic kid attribution from grade / teacher / activity — eb65f8a (design note) / 2ee6a17 (module + unit tests) / ad145ba (wiring + render tests) — see COMPLETED.md

20\. [x] Freemail-aware sender-block granularity — f855dee / 745957a / d5820c2 / 563354c / bf9fe35 / 8170081 / 03b44c5 / e448a8a — see COMPLETED.md

21\. [x] Dedupe candidate messages before agent extraction — 9882a1c / 775f173 / 44283b6 — see COMPLETED.md

22\. [x] Bug: page header "N day lookback" ignores `--lookback-days` CLI value — 563827d — see COMPLETED.md

### 23. [ ] Separate test landing page for manual `workflow_dispatch` QA runs

Every workflow run — scheduled cron and manual `workflow_dispatch` alike — currently overwrites `docs/index.html`, the page Ellen uses. Manual runs that exist purely to verify a fix (like the recent #22 live-QA dispatch) put experimental output in front of her until the next cron tick replaces it. The pipeline needs a way to route test builds to a separate path so the production page stays untouched.

Sketch: add a boolean `workflow_dispatch` input — `test_output`, default false — that the workflow forwards to `scripts/process_events.py` (e.g. `--output-target test`). When set, the script writes `docs/test/index.html` instead of `docs/index.html` and the workflow commits only the test path. Production `index.html` is left alone, and the test build is visitable at `/test/` on the same Pages domain. The test page should render a visible banner so a stale tab or bookmark cannot be mistaken for live data.

Design-note questions to resolve before coding:

- Whether `test_output` should also gate adjacent side effects that touch production state — skip Gmail draft creation (item 3), skip incremental-processed-state writes (item 4), skip "new this week" snapshot updates (item 13). A test run that silently marks Gmail messages as "already processed" or stamps "seen" on events would corrupt the next production run, so the working assumption is to fold all of these under one flag, but confirm scope with Tom.
- Whether to unify this with or supersede the existing digest test-mode flag from item 3, or keep them independent toggles.
- Whether test-output commits should use a distinct commit-message prefix so the history is easy to skim past during regular review.

### 24. [~] Bug: `agent.py` defines `AUDIT_SYSTEM_PROMPT` twice

`scripts/agent.py` declares `AUDIT_SYSTEM_PROMPT` at lines 209–239 and again at lines 242–275 with substantively different content — different verdict labels (`keep_filtered` vs `keep_blocked`), different system instructions. Python's last-assignment-wins rule means the second definition is the live one and the first is dead, but both are reachable to a reader and a well-meaning future edit to "the prompt" could land on the wrong copy. Fix: delete the dead first block; verify the live audit flow's behavior is unchanged via the existing `step1b_filter_audit` integration (or pin it with a unit test if one doesn't exist).

In progress: dead first block deleted from `agent.py` in 0ba31c9; the live `keep_blocked` prompt is now the only definition. No behavior change (Python was already using the second block). `tests/test_agent.py` 66/66, including the existing `test_review_stripped_messages_uses_audit_system_prompt` identity pin that locks the prompt to the import — treating that as sufficient coverage rather than adding a redundant unit test. Pending Tom's manual verification of the live audit flow before flip to `[x]`.

## Test coverage gaps

Inventory of where the pytest suite is silent. Not prioritized against the feature backlog above — pull from this when there is slack between feature work, or when a regression in one of these areas would be costly enough to pre-empt. Risk tiers reflect blast radius if the untested code silently breaks, not implementation difficulty. Filed 2026-04-22 from a full source/test survey; revisit and prune as items get covered.

**High risk — silent failure would corrupt production state or block the weekly run**

- [x] `gmail_client.py`: only `_extract_body` is tested; `_get_credentials`, `search_messages`, `read_message`, `create_draft` (incl. the `text_alternative` multipart branch) and `get_profile` have zero coverage. A regression here breaks the entire pipeline at the fetch boundary with no unit-test signal. — covered by `tests/test_gmail_client.py` (_get_credentials three-branch coverage via monkeypatched `Credentials`/`Request`; API wrappers via a chainable stub service).
- [x] `scripts/build_queries.load_audit_state`: untested. Date math, threshold defaulting, and tolerance for malformed `.filter_audit.json` all gate whether step1b runs — a bug here either triggers spurious audits or silently skips them. — covered by `tests/test_build_queries.py` (9 new tests: missing-file, malformed-JSON, missing-field, invalid-ISO, fresh/at-threshold/past-threshold, custom threshold, default threshold).
- [x] `.github/workflows/weekly-schedule.yml` CREATE_DRAFT gate: the `github.event.schedule == '15 10 * * 1'` string match is paired with the cron line `15 10 * * 1` in the same file; a typo on either side silently disables the Monday digest with no test catching it. Pin via a workflow-parsing test that asserts the cron string the workflow runs on matches the cron string the gate checks. — covered by `tests/test_workflow_cron_gate.py` (parses the YAML as text with regex to avoid a PyYAML dep; pins Monday-cron ⇆ gate literal, gate uniqueness, and the gate ≠ Wed/Sat cron).
- [x] `.filter_audit.json` schema parity: `scripts/mark_filter_audit.py` writes the file and `scripts/build_queries.load_audit_state` reads it, with no shared schema and no parity test. A divergence (renamed key, type drift) breaks the audit cadence silently. — covered by `tests/test_filter_audit_parity.py` (6 round-trip tests: writer output parses as `fresh` same-day, threshold override propagates, existing-threshold preservation on rewrite, elapsed-threshold reads as `stale:`, corrupt-file recovery via writer).

**Medium risk — orchestration and integration coverage**

- [x] `main.py` orchestration functions with no direct test: `_load_webhook_url`, `_load_pages_url`, `run_script`, `step1_build_queries`, `step1b_filter_audit`, `step2_search_gmail`, `_bootstrap_from_future_events`, `step3b_update_auto_blocklist`, `step5_publish`, `step6_create_draft`, `main()`. — covered by `tests/test_main_orchestration.py` (26 tests). `step1b_filter_audit` and `main()` intentionally left to the live weekly-cron integration — both are thin orchestration over helpers that are now individually covered, and the stub surface needed to unit-test them does not pin anything a drift in the real helpers wouldn't already break.
- [x] `scripts/sync_ignored_senders.py`: `_fetch` (urlopen wrapper) and `main()` CLI are untested; only `normalize_rows` and `write_if_changed` have coverage. — covered by `tests/test_sync_ignored_senders.py` (9 new tests: `_fetch` happy path, query-string append for URLs with existing `?`, network error / non-list / non-JSON all return None with stderr breadcrumbs; `main` happy path, fetch-failure graceful degrade preserves cache bytes, no-changes short-circuit, `--timeout` propagation).
- [x] `scripts/update_auto_blocklist.main()`: intentionally out of scope per the test docstring, with the live workflow as the integration test. Worth re-evaluating whether that posture still pays — a botched auto-block run mutates a tracked file. — re-evaluated and reversed: covered by `tests/test_update_auto_blocklist.py` (13 new `main()` tests, each guardrail branch pinned: missing/malformed/non-list suggestions exit 1; happy-path header+trailer; confidence/address/protected/dedup rejections; non-dict suggestion defense; reason truncation + `#` stripping; stderr summary; optional audit-log). Docstring updated.
- [x] `.ics` filename routing on Pages: `build_ics` and `write_ics_files` are unit-tested, but nothing pins the URL shape the rendered HTML expects against what the writer produces. A divergence breaks the per-event `.ics` button silently. — covered by `tests/test_ics_url_filename_parity.py` (5 tests). Pinned: renderer's href f-string literal `ics/{ev['id']}.ics`; only one such href-format in process_events.py; main.py's `os.path.join(PAGES_OUTPUT_DIR, "ics")` last segment equals the renderer's segment; writer produces `{event_id}.ics` filenames; round-trip synthesis — the URL the renderer emits for a given event points at the exact file the writer produced. Source-text inspection avoids calling `render_html` (whose `%-d` strftime is POSIX-only).
- [x] State-branch save/restore in `weekly-schedule.yml`: persists `events_state.json`, `prior_events.json`, `sender_stats.json`, `.filter_audit.json`, `blocklist_auto.txt`, `blocklist_auto_audit.jsonl`. No test asserts the workflow's checkout/commit blocks reference the same set the scripts read/write. — covered by `tests/test_workflow_state_branch_parity.py` (4 tests). Regex over the YAML text (no PyYAML dep) extracts both the restore-block `[ -f .state/<file> ]` guards and the save-block `[ -f <file> ] && FILES="$FILES <file>"` pairs, asserts both sets equal the canonical `PERSISTENT_STATE_FILES`, and flags any new file that gets added to either side without also being added to the parity list in the test.

**Low risk — thin scripts or intentional skips**

- `scripts/diff_search_results.py`: pure CLI diff utility for filter audit. Tolerance of the `_messages` shape and totals math could regress quietly.
- `scripts/dev_render.py`: thin subprocess wrapper around `process_events.py`; low payoff.
- `scripts/generate_gmail_token.py`: interactive OAuth flow; intentional skip.
- `scripts/apps_script.gs`: Google Apps Script, cannot be unit-tested in pytest; intentional.

How to use this list: when picking a target, prefer the high-risk items (each is a credible regression source on a path that runs unattended every week), followed by the schema-parity and workflow-gate items in medium. The orchestration coverage in `main.py` is the largest in raw line count but lower in marginal value — most of the underlying work is already tested through `process_events.py` and the helpers; the orchestration is mostly glue.

## Descoped / on hold

Items parked here aren't dead — they're off the active queue but preserved in case priorities shift. Revive by moving the full prose back under "Backlog" at the original number and flipping `[-]` → `[ ]`.

14\. [-] Manual "refresh now" button in the UI

Descoped 2026-04-17 (session 10). The weekly cron cadence has been sufficient in practice — Tom has not hit a real case of needing a mid-week rebuild since the feature was originally filed, and the threat-model / PAT-rotation overhead no longer looks worth the payoff. Preserving the full scope below in case that changes.

Button in `docs/index.html` that triggers the weekly workflow on demand, so a fresh build can be forced after a late schedule email without waiting for the next scheduled run or opening GitHub. GitHub's `workflow_dispatch` API requires an authenticated call, so the existing Apps Script webhook grows a new `action=refresh` endpoint that holds a fine-grained PAT (scope: `workflow`, single-repo) as a Script Property and POSTs to the dispatches endpoint. Client fires `fetch(APPS_SCRIPT_URL, {method:'POST', body: JSON.stringify({secret, action:'refresh'})})` and shows a "Rebuilding… reload in ~2 min" toast; no live polling.

Threat model accepted: the shared secret is effectively public (embedded in page source on a page with near-zero organic traffic), worst case is a handful of wasted workflow runs. Defense in depth: Apps Script rate-limits to one dispatch per 5 minutes via `PropertiesService`. The workflow's existing `concurrency: {group: pages, cancel-in-progress: false}` already prevents pileups from rapid clicks. PAT rotation: 1-year expiry with a calendar reminder.

15\. [-] Conflict highlighting

Descoped 2026-04-17 (session 10). Same-day multi-kid overlaps are visually obvious on the current card layout — the week grouping already co-locates them — and Tom has not seen a missed-conflict incident that would justify the render complexity. Preserving the scope below in case a kid adds a second activity that creates regular overlaps.

In `process_events.py`, detect overlapping timed events on the same day via interval intersection; flag both cards with a visible conflict marker. Prioritize different-kid overlaps as the high-signal case. Same-day all-day + timed events should NOT be flagged as conflicts — they coexist by design.