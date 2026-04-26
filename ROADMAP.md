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

**2026-04-25**

- Items 27 and 28 closed `[x]` after Tom's prod verification (commit aa20b4b moved full prose to `COMPLETED.md`).
- Item 29 in flight `[~]` — five commits so far: 8606610 (source + Location: prefix v1), 6cd0f74 (CI fixup), 4467aba (URL linkification v2), 43b4621 (CI fixup), and this commit (link blue + bare-domain `www.` reachability v3). Tom hit "refused to connect" on `https://myschoolbucks.com/`; helper now adds `www.` for fully bare domains (one-dot `label.tld` shape) so apex-only sites are reachable. Link color also changed from muted-inherit to GitHub-dark blue (`#58a6ff`) so it stands out.
- Test delta vs main: +20 passing on Linux/CI (10 unit tests + 10 render-integration). On Windows, the 10 render tests still fail with the same unrelated `%-d` strftime issue.
- Nothing else in flight. Next session moves item-29 prose to `COMPLETED.md` once Tom verifies the source line, location prefix, clickable + reachable URLs, and blue link color all work on the live page.

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

Sketch: add a boolean `workflow_dispatch` input — `test_output`, default false — that the workflow forwards to `scripts/process_events.py` (e.g. `--output-target test`). When set, the script writes `docs/test/index.html` instead of `docs/index.html` and the workflow commits only the test path. Production `index.html` is left alone, and the test build is visitable at `/test/` on the same Pages domain. The test page should render a visible banner so a stale tab or bookmark cannot be mistaken for live data.

Design-note questions to resolve before coding:

- Whether `test_output` should also gate adjacent side effects that touch production state — skip Gmail draft creation (item 3), skip incremental-processed-state writes (item 4), skip "new this week" snapshot updates (item 13). A test run that silently marks Gmail messages as "already processed" or stamps "seen" on events would corrupt the next production run, so the working assumption is to fold all of these under one flag, but confirm scope with Tom.
- Whether to unify this with or supersede the existing digest test-mode flag from item 3, or keep them independent toggles.
- Whether test-output commits should use a distinct commit-message prefix so the history is easy to skim past during regular review.

24\. [x] Bug: `agent.py` defines `AUDIT_SYSTEM_PROMPT` twice — 0ba31c9 — see COMPLETED.md

25\. [x] Catch self-notes / direct kid-name emails (e.g. "Everly volleyball") — 0f4a1d2 / ad8f1e1 / bef3db1 / 656310a / 7d6549b — see COMPLETED.md

26\. [x] Auto-blocklist must never block parents' personal addresses — c829e2a / 437fa6b / 9a3940c / cb64dd6 / 39c48b6 — see COMPLETED.md

27\. [x] Auto-blocklist hardening: one errant agent flag shouldn't permanently block a sender — 6bea35a / e5772cc / 6b8c62a / 87d18f5 / ee90951 / 5d914dc / 4ba172b — see COMPLETED.md

28\. [x] Bug: Ignore-sender button renders for protected address-form senders — 0446ed9 — see COMPLETED.md

### 29. [~] Event-card source line + Location: prefix

Filed 2026-04-25 from Tom: event cards lacked any source attribution despite every event dict carrying a `source` field (the agent's curated label, e.g. *"LAES newsletter (Apr 6)"*). The card markup never rendered it — only the digest email did. Tom asked: add a `From:` line to the card so it's clear where each event came from, and while we're at it add a `Location:` prefix to the location for visual consistency with the existing `For:` line, **except** when the location is already a fully-formed street address (which is self-evidently a location).

**Source line.** New `<div class="event-source">From: {ev["source"]}</div>` rendered between the audience (`For:`) line and the location line on both dated and undated cards. Truncated at 80 characters with CSS ellipsis; full string lives in the `title=` tooltip so a long label stays inspectable on hover. Always rendered when `source` is non-empty (the agent's default fallback is `"unknown source"`, so practically every card gets a line).

**Location prefix.** Plain venues now render as `Location: School Gym`, `Location: Online`, `Location: Mr. Patel's Classroom`. Fully-formed addresses (`Fredericksburg Convention Center, 2371 Carlson Way`, `Tysons Pediatrics, 8350 Greensboro Dr`) skip the prefix because the address is already self-evidently a location. Detection heuristic is a single regex matching a digit-prefixed token followed anywhere by a US street-suffix word (`Way` | `St` | `Street` | `Ave` | `Avenue` | `Rd` | `Road` | `Blvd` | `Boulevard` | `Dr` | `Drive` | `Ln` | `Lane` | `Pkwy` | `Parkway` | `Cir` | `Circle` | `Ct` | `Court` | `Ter` | `Terrace` | `Pl` | `Place` | `Hwy` | `Highway`), case-insensitive. False-friend cases handled correctly: "Way Cool Studio" (no leading digit), "Park Lane" (no digit), "Bldg A, Room 215" (no street suffix word) all keep the prefix.

**Tests.** 6 pure-function unit tests for `_is_address_like` (full addresses match; venue-only / room-only / suffix-without-digit don't; case-insensitive; empty/whitespace handled). 5 render-integration tests exercising both dated and undated cards: source line rendered, source truncation + title preserves full string, location prefix added for plain venue, prefix omitted for address-shape location. The render-integration tests fail on Windows + Python 3.14 with the same pre-existing `%-d` strftime issue that takes down the other render tests in this file; CI (Linux) runs them green.

**Source date enforcement deferred.** Tom asked *"even better if it can add the date from which it was sourced."* The agent already includes dates in most source labels (`(Apr 6)` etc.) per its existing prompt. Strict per-source date enforcement would be a 1-paragraph prompt change; held as a follow-up if production data shows enough date-less labels to bother.

**Location URL linkification (v2 follow-up, this session).** Tom's second ask: *"if the location is a website - can it be presented as a hyperlink so Ellen can just click on the link from the event?"* and follow-up *"even for something like this: Location: MySchoolBucks (myschoolbucks.com)..."* — both URL-only locations and URLs embedded inside location text now render as clickable `<a href>` anchors with `target="_blank"` and `rel="noopener noreferrer"`. The linkifier (`_linkify_inline_urls`) detects http(s)://-shaped URLs and bare domains alike via a single regex. URL-only locations were previously suppressed entirely by `_is_suppressible_location` — that branch is removed; URL-only locations now render as a single anchor inside the `event-location` div with the `Location:` prefix (URL-only is not address-like). False-friend pins prevent "Mt. Vernon High School", "Dr. Smith's office", and "version 1.0" from being mis-detected.

**Bare-domain `www.` reachability (v3 follow-up).** `myschoolbucks.com` was getting a `https://myschoolbucks.com` href and Tom hit "refused to connect" — the site only serves at `www.myschoolbucks.com`. Helper `_href_for_bare_domain` now adds `www.` for fully bare domains (one-dot `label.tld` shape, no scheme, no leading `www.`). Already-subdomained URLs (`camps.fcps.edu` = 2 dots) and explicit-scheme URLs are NOT modified — those are intentional references where prepending `www` would break routing or override the source. Visible link text always preserves what the agent emitted; only the `href` is modified for reachability. Limitation: dot-counting heuristic doesn't distinguish multi-segment TLDs (`example.co.uk` would be miscoded as already-subdomained). Acceptable for the codebase's US-centric use case; revisit if a `.co.uk`-style bare domain ever shows up.

**Link styling.** CSS for `.event-location a` ships traditional dark-theme link blue (`#58a6ff`, GitHub-dark style) so the link is unambiguous against the muted location text. Hover bumps a shade brighter (`#79b8ff`) for affordance.

Item stays `[~]` pending Tom's live verification post-deploy: load the page, confirm (a) a `From: ...` line under each card, (b) `Location:` prefix on plain venues but not on full street addresses, (c) URLs (full and bare-domain) render as clickable underlined links.

## Descoped / on hold

Items parked here aren't dead — they're off the active queue but preserved in case priorities shift. Revive by moving the full prose back under "Backlog" at the original number and flipping `[-]` → `[ ]`.

14\. [-] Manual "refresh now" button in the UI

Descoped 2026-04-17 (session 10). The weekly cron cadence has been sufficient in practice — Tom has not hit a real case of needing a mid-week rebuild since the feature was originally filed, and the threat-model / PAT-rotation overhead no longer looks worth the payoff. Preserving the full scope below in case that changes.

Button in `docs/index.html` that triggers the weekly workflow on demand, so a fresh build can be forced after a late schedule email without waiting for the next scheduled run or opening GitHub. GitHub's `workflow_dispatch` API requires an authenticated call, so the existing Apps Script webhook grows a new `action=refresh` endpoint that holds a fine-grained PAT (scope: `workflow`, single-repo) as a Script Property and POSTs to the dispatches endpoint. Client fires `fetch(APPS_SCRIPT_URL, {method:'POST', body: JSON.stringify({secret, action:'refresh'})})` and shows a "Rebuilding… reload in ~2 min" toast; no live polling.

Threat model accepted: the shared secret is effectively public (embedded in page source on a page with near-zero organic traffic), worst case is a handful of wasted workflow runs. Defense in depth: Apps Script rate-limits to one dispatch per 5 minutes via `PropertiesService`. The workflow's existing `concurrency: {group: pages, cancel-in-progress: false}` already prevents pileups from rapid clicks. PAT rotation: 1-year expiry with a calendar reminder.

15\. [-] Conflict highlighting

Descoped 2026-04-17 (session 10). Same-day multi-kid overlaps are visually obvious on the current card layout — the week grouping already co-locates them — and Tom has not seen a missed-conflict incident that would justify the render complexity. Preserving the scope below in case a kid adds a second activity that creates regular overlaps.

In `process_events.py`, detect overlapping timed events on the same day via interval intersection; flag both cards with a visible conflict marker. Prioritize different-kid overlaps as the high-signal case. Same-day all-day + timed events should NOT be flagged as conflicts — they coexist by design.