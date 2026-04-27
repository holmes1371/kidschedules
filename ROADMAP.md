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

**2026-04-27**

- Items 30 + 31 still `[~]` pending Tom's live verification on newly-arrived emails (cards extracted before the prompt push retain old labels; see each item's "No retroactive fix" callout).
- #32 ("Completed" checkbox on event cards) `[~]` — all 7 commits in plan landed; Tom redeployed Apps Script mid-session after commit 3. Awaiting live verification on the next cron cycle (check a card on phone, confirm checkbox state propagates to tablet after refresh; verify `completed_events.json` populates in CI logs).
- #33 (PDF newsletter attachments) filed 2026-04-26 as a placeholder by Tom; reworded this session into house style. Stays `[ ]` — needs a scoping conversation before any work.
- Nothing else in flight.

## For future agents

Read this file at the start of any session where Tom mentions "kids-schedule", "the QoL list", or asks about the next feature. The prioritization below is settled — do not re-debate it without prompting. Work items in order unless Tom explicitly says otherwise.

Session discipline:

- Invoke the `karpathy-guidelines` skill via the Skill tool at the start of every session that touches code. Reading `reference/guidelines.md` directly does not count — the skill-load step is what anchors the discipline for the rest of the session.
- git commits need the -c user.name=... -c user.email=... flags since there's no default identity
- **Soft-delete convention, not `rm`.** The FUSE mount this repo lives on refuses `unlink` but permits `rename`. `rm` fails with `Operation not permitted` even under `dangerouslyDisableSandbox`; `mv` works. When you need to discard a file — most often a stale `.git/index.lock` or `.git/HEAD.lock` left by an interrupted git op — `mkdir -p .to_delete && mv <file> .to_delete/<tag>-$(date +%Y%m%d-%H%M%S)`. The folder isn't tracked (no `.gitkeep`); agents create it on demand so Tom can select-all-delete inside it without working around a stub file. Tom empties it manually from Windows periodically. Full convention + stale-lock recovery + corrupt-index recovery ritual at `design/soft-delete-convention.md`. Unlink warnings on a successful git commit (`warning: unable to unlink '.git/index.lock': Operation not permitted`) are cosmetic; the commit landed, move on.
- Before starting a non-trivial feature, write a short design note to `design/{feature-name}.md` capturing the scope, the decisions already made, and the test fixtures needed. A fresh session should be able to pick up mid-feature from that note plus the last commit, without re-litigating choices.
- Commit at every natural boundary, not just at feature completion. Half-finished work behind a clear commit message is recoverable; a dirty worktree is not.
- Use the built-in TodoWrite tool as internal scaffolding on multi-step work — refresh at each commit boundary and keep exactly one item `in_progress`. The output is not visible in Tom's current Claude Code UI (the "Tasks" panel maps to session-spawn chips, not TodoWrite items), so do not treat it as a reporting channel; it is a working scratchpad for the agent that survives compaction and mid-session interruptions.
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

**Tom's UX (confirmed 2026-04-27).** New checkbox toggle in the existing `workflow_dispatch` UI alongside Dry run / Intentional failure / Create draft — when flipped on, the entire run writes to the test landing page; when left off, the manual run behaves like a cron tick and updates the normal production page. Two-state toggle, no separate workflow file, no environment variables to remember. Cron-scheduled runs are unaffected and always go to production.

Sketch: add a boolean `workflow_dispatch` input — `test_output`, default false — that the workflow forwards to `scripts/process_events.py` (e.g. `--output-target test`). When set, the script writes `docs/test/index.html` instead of `docs/index.html` and the workflow commits only the test path. Production `index.html` is left alone, and the test build is visitable at `/test/` on the same Pages domain. The test page should render a visible banner so a stale tab or bookmark cannot be mistaken for live data.

Design-note questions to resolve before coding:

- Whether `test_output` should also gate adjacent side effects that touch production state — skip Gmail draft creation (item 3), skip incremental-processed-state writes (item 4), skip "new this week" snapshot updates (item 13), and now also #32's `complete`/`uncomplete` POSTs (a test run shouldn't be able to write rows to the live "Completed Events" sheet). A test run that silently marks Gmail messages as "already processed" or stamps "seen" on events would corrupt the next production run, so the working assumption is to fold all of these under one flag, but confirm scope with Tom.
- Whether to unify this with or supersede the existing digest test-mode flag from item 3, or keep them independent toggles.
- Whether test-output commits should use a distinct commit-message prefix so the history is easy to skim past during regular review.

24\. [x] Bug: `agent.py` defines `AUDIT_SYSTEM_PROMPT` twice — 0ba31c9 — see COMPLETED.md

25\. [x] Catch self-notes / direct kid-name emails (e.g. "Everly volleyball") — 0f4a1d2 / ad8f1e1 / bef3db1 / 656310a / 7d6549b — see COMPLETED.md

26\. [x] Auto-blocklist must never block parents' personal addresses — c829e2a / 437fa6b / 9a3940c / cb64dd6 / 39c48b6 — see COMPLETED.md

27\. [x] Auto-blocklist hardening: one errant agent flag shouldn't permanently block a sender — 6bea35a / e5772cc / 6b8c62a / 87d18f5 / ee90951 / 5d914dc / 4ba172b — see COMPLETED.md

28\. [x] Bug: Ignore-sender button renders for protected address-form senders — 0446ed9 — see COMPLETED.md

29\. [x] Event-card source line + Location: prefix + URL linkification — 8606610 / 6cd0f74 / 4467aba / 43b4621 / 5052b1f / 3fbdf8c — see COMPLETED.md

### 30. [~] Agent should preserve URLs verbatim in event location

Filed 2026-04-25 from Tom — caught during item-29 verification: a DanceOne waiver event rendered with location `Online (PandaDoc link)`, no actual URL anywhere on the card. The source email almost certainly contained a real PandaDoc URL (that's how PandaDoc sends e-signature requests), but the agent summarized it as the parenthetical `(PandaDoc link)` rather than including the literal URL. Item 29's linkifier needs a URL in the location text to render an anchor; without it, Ellen sees a description of a link but can't click through.

**Fix.** `agent.py::_EXTRACTION_BASE_PROMPT`'s `location` field bullet extended with an explicit URL-preservation directive: include URLs VERBATIM (signup forms, waivers, livestreams, RSVP, e-signature, Google Form, PandaDoc/DocuSign), do NOT summarize as "(form link)" or "(PandaDoc link)" or similar paraphrase. Prompt now carries five concrete GOOD / BAD examples so the model has unambiguous patterns to follow. Single-paragraph addition; no schema change, no parser change.

**Tests.** New `test_extraction_prompt_preserves_urls_in_location_directive` in `tests/test_agent.py` — pins the directive's key phrases (`URL VERBATIM`, `PandaDoc`, `Google Form`, `GOOD:`, `BAD:`) so a future prompt edit that accidentally drops the directive fails CI. Modeled on the existing roster-prose pin pattern.

**No retroactive fix.** The pipeline caches extracted events in `events_state.json` keyed on Gmail `messageId` (item #4); cached entries are NEVER re-processed unless explicitly evicted via `--reextract <MESSAGE_ID>`. So events extracted *before* this prompt change keep their old `location` strings (`"Online (PandaDoc link)"` etc.) — only events extracted from *new* emails benefit from the directive. A `--reextract-all` bulk-flush utility was considered (2026-04-25) and explicitly rejected: the cache holds events up to 120 days old via `processed_messages`, but Gmail search is bounded to the 60-day lookback, so a bulk flush would lose far-future events extracted from 60–120-day-old "save the date" emails. Slow-phase-in is the right trade.

Item stays `[~]` pending Tom's live verification post-deploy on **newly-arrived** signup-form / e-signature / Google Form emails (NOT existing cards on the live page — those keep their old labels). Confirm that the next reminder/announcement email with a URL produces a card whose `Location:` line shows the URL itself rendered as a clickable link.

### 31. [~] Agent should source-date events to the email's actual sent date, not a referenced date

Filed 2026-04-25 from Tom — caught immediately after item-30 close: 5 NEW-badged events appeared today on the live page, all from "LAES PTA Sunbeam" with source dates spanning Mar 15, Mar 22, Apr 19. Tom's reaction: *"how did these get missed?!"* — they looked like weeks-old emails just now showing up.

Diagnosis: today's email was a "last day to register" reminder rolling up multiple camps (all signup deadlines were today). The agent extracted 5 distinct end-date events from that one email, but labeled each event's `source` with the date of the *originally referenced* newsletter (Mar 15, etc.) rather than today's email. The labels are misleading — the actual extraction came from today's email, but the source line on the card reads as if the email arrived weeks ago.

**Fix.** Extend the `source` field bullet in `agent.py::_EXTRACTION_BASE_PROMPT` with an explicit disambiguation: "the email's sent date" means the date THIS specific email was sent (the value on the "Date sent:" line at the top of the email block in the input), NOT a date mentioned in the email body. Includes a concrete GOOD/BAD pair ("LAES PTA Sunbeam (Apr 26)" vs "LAES PTA Sunbeam (Mar 15)") and the user-impact rationale ("the user reads the source date as 'when did this information arrive in my inbox' — getting it wrong makes today's reminder look like a weeks-old email").

**Tests.** New `test_extraction_prompt_pins_source_date_to_email_sent_date` in `tests/test_agent.py` — pins single-line phrases ("Date sent:", "the date THIS specific", "rolls up an older newsletter", and both halves of the GOOD/BAD pair) so a future prompt edit dropping the directive fails CI. Modeled on the #30 pin pattern.

**Bundled with #30 for verification.** Both items are agent-prompt strengthenings landed in the same session (#30: preserve URL strings verbatim; #31: source-date the email's actual send date). They live as separate commits but Tom verifies them together on the next post-deploy cron cycle: pull up a **newly-arrived** event card (NOT a pre-existing one — see #30's "No retroactive fix" callout for why) and confirm (a) URLs appear as clickable links in the location, (b) the source-line date matches when Ellen actually received the email in her inbox.

Item stays `[~]` pending live verification.

### 32. [~] "Completed" checkbox on event cards

Filed 2026-04-27 from Tom. Adds a per-card "completed" affordance so Ellen can mark an event done as it happens, without waiting for the date to pass. Mirrors the ignore-flow architecture surface-for-surface — same 12-hex event-id, same Apps Script POST/GET round-trip, same ephemeral sheet-derived cache, same optimistic toggle + rollback. Full design at `design/completed-events.md`.

**Resolved (2026-04-27):** durable persistence; cross-device sync via Apps Script; completed supersedes ignore (both Ignore-event AND Ignore-sender suppressed via CSS on `.event-card.completed`); ignored cards do NOT show the completed checkbox (CSS-suppressed via the existing `[data-ignored="1"]` selector pattern); retirement unchanged (completed cards retire on the normal date-passed threshold). Sheet-as-single-source-of-truth invariant preserved end-to-end — `completed_events.json` is per-run ephemeral, never committed.

**Commits (7 of 7 landed):**

1. Design note + ROADMAP update — `4828713`
2. `process_events.py` classify + render + CLI + tests — `732a0de`
3. `scripts/apps_script.gs` router + Completed Events tab — `3cd394e` (Tom redeployed Apps Script after this commit)
4. `scripts/sync_completed_events.py` + tests — `863b2f8`
5. `main.py` + workflow YAML wiring — `2c373fc`
6. `process_events.py` inline JS handlers + tests — `caa6566`
7. ROADMAP update + SHAs (this entry).

**Pending verification.** Item stays `[~]` until Tom confirms on a live cron cycle: (a) checking a card on phone propagates to tablet after the next cron rebuild, (b) `completed_events.json` shows the expected row count in CI logs after a cron run, (c) the Ignore-event / Ignore-sender buttons disappear from a completed card and reappear on uncheck, (d) the "Completed Events" sheet tab in the Google Sheet accumulates rows correctly (auto-created on first append).

**Manual deploy step landed mid-session.** Apps Script was redeployed after commit 3, before any client surface started calling the new endpoints. The page rendered as normal between commits 3 and 6 — checkboxes appeared but clicks were no-ops. From commit 6 onward, the full UX is live; the next cron cycle is the first one that exercises the end-to-end flow.

### 33. [ ] Extract events from PDF newsletter attachments on teacher emails

Filed 2026-04-26 from Tom as a placeholder — captured mid-thought so it isn't lost; not yet scoped, no design decisions, no plan. Some teachers (school classrooms, room-parent threads) send their weekly/monthly newsletters as PDF attachments rather than HTML body content. The current pipeline only feeds the email body to the agent, so dates buried in those PDFs are silently dropped — Ellen never sees them as cards.

Open for the next session to talk through with Tom before any code:

- Scope: which teacher senders / how often does this happen / how many events are typically inside one of these PDFs. Tom to surface a concrete recent example before scoping.
- Approach: PDF text extraction inside `scripts/process_events.py` (e.g. `pypdf` / `pdfplumber`) feeding the extracted text into the same agent prompt path, vs. a separate extraction codepath, vs. punt as not-worth-it. Image-only / scanned PDFs (OCR) are a separate question — likely out of scope for v1.
- Interaction with existing items: dedupe (#21), incremental cache (#4), and the source-date directive (#31) all need to behave sensibly when the "source" is a PDF inside an email rather than the email body itself.

No commits, no design note, no `[~]` flip until Tom and the next agent discuss.


## Descoped / on hold

Items parked here aren't dead — they're off the active queue but preserved in case priorities shift. Revive by moving the full prose back under "Backlog" at the original number and flipping `[-]` → `[ ]`.

14\. [-] Manual "refresh now" button in the UI

Descoped 2026-04-17 (session 10). The weekly cron cadence has been sufficient in practice — Tom has not hit a real case of needing a mid-week rebuild since the feature was originally filed, and the threat-model / PAT-rotation overhead no longer looks worth the payoff. Preserving the full scope below in case that changes.

Button in `docs/index.html` that triggers the weekly workflow on demand, so a fresh build can be forced after a late schedule email without waiting for the next scheduled run or opening GitHub. GitHub's `workflow_dispatch` API requires an authenticated call, so the existing Apps Script webhook grows a new `action=refresh` endpoint that holds a fine-grained PAT (scope: `workflow`, single-repo) as a Script Property and POSTs to the dispatches endpoint. Client fires `fetch(APPS_SCRIPT_URL, {method:'POST', body: JSON.stringify({secret, action:'refresh'})})` and shows a "Rebuilding… reload in ~2 min" toast; no live polling.

Threat model accepted: the shared secret is effectively public (embedded in page source on a page with near-zero organic traffic), worst case is a handful of wasted workflow runs. Defense in depth: Apps Script rate-limits to one dispatch per 5 minutes via `PropertiesService`. The workflow's existing `concurrency: {group: pages, cancel-in-progress: false}` already prevents pileups from rapid clicks. PAT rotation: 1-year expiry with a calendar reminder.

15\. [-] Conflict highlighting

Descoped 2026-04-17 (session 10). Same-day multi-kid overlaps are visually obvious on the current card layout — the week grouping already co-locates them — and Tom has not seen a missed-conflict incident that would justify the render complexity. Preserving the scope below in case a kid adds a second activity that creates regular overlaps.

In `process_events.py`, detect overlapping timed events on the same day via interval intersection; flag both cards with a visible conflict marker. Prioritize different-kid overlaps as the high-signal case. Same-day all-day + timed events should NOT be flagged as conflicts — they coexist by design.