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

**2026-05-01**

- #38 + #39 closed `[x]` — Tom verified live after a workflow_dispatch rebuild; full prose archived in `COMPLETED.md`. 872 → 884 tests green (12 net new).
- #38 caveat: the SparkPost tracking URL Tom flagged still 404s when clicked, but `curl -I` confirmed SparkPost's server itself responds with `Location: https://www.google.com/?!=!` — Advanced Pediatrics' email vendor configured the tracking URL to a malformed destination, nothing the pipeline can fix on our side.
- #33 still code complete `[~]` — 4 SHAs (37aa60f / 51c8a54 / a9ffee7 / 63e86df). Pending Tom's live verification on the next real teacher PDF email.
- Items 30 + 31 still `[~]` pending Tom's live verification on newly-arrived emails.
- #35 / #36 still `[ ]` placeholders.

## For future agents

Read this file at the start of any session where Tom mentions "kids-schedule", "the QoL list", or asks about the next feature. The prioritization below is settled — do not re-debate it without prompting. Work items in order unless Tom explicitly says otherwise.

Session discipline:

- Invoke the `karpathy-guidelines` skill via the Skill tool at the start of every session that touches code. Reading `reference/guidelines.md` directly does not count — the skill-load step is what anchors the discipline for the rest of the session.
- git commits need the -c user.name=... -c user.email=... flags since there's no default identity
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

23\. [x] Separate test landing page for manual `workflow_dispatch` QA runs — f0dea5b / 0822afc / c0bf8e4 / 75b7a5d — see COMPLETED.md

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

32\. [x] "Completed" checkbox on event cards — 4828713 / 732a0de / 3cd394e / 863b2f8 / 2c373fc / caa6566 / 1325465 / 636abe0 / 3667823 — see COMPLETED.md

34\. [x] Cross-device state sync on page refresh (ignore + completed) — 4428700 / e2a8cf1 / 93d257d / e1151c9 — see COMPLETED.md

### 33. [~] Extract events from PDF newsletter attachments on teacher emails — 37aa60f / 51c8a54 / a9ffee7 / 63e86df

Code complete 2026-04-27. Design note: `design/pdf-newsletter-attachments.md`. Four commits:

- `37aa60f` — Design note + ROADMAP `[~]` flip + .eml fixture + `pdf_sender_domains.txt` seed (`fcps.edu`) + `scripts/pdf_sender_domains.py` loader/matcher (delegates to `protected_senders.is_protected`) + 6 unit tests.
- `51c8a54` — `gmail_client.py` PDF attachment fetch. `read_message` now returns a `pdfs: list[bytes]` field (always present, empty when no PDFs). Walks MIME parts recursively for `application/pdf`; handles inline data + reference-style `attachmentId` paths. 8MB cap enforced both via advertised size (skip the second API call when oversized) and decoded-bytes length (defensive against missing-size payloads). Failures are skip-and-warn — body still flows through. 8 new tests including end-to-end against the committed `.eml` fixture.
- `a9ffee7` — `agent.py` content-block plumbing + prompt directive. `_plan_batches` forces batch-of-1 for any email with non-empty `pdfs`, regardless of newsletter-classifier state. `extract_events` builds a list-of-content-blocks payload (one `document` block per PDF, in input order, ahead of a single `text` block) when any email in the batch has PDFs; no-PDF batches keep the string-content path. `_call_with_retry`'s `user_message` kwarg renamed to `user_content` to reflect the dual shape. Prompt extends section #8 (PDFs are processed like email-body content) and the source-field block (PDF edition labels do NOT change source date — pinned with a GOOD/BAD example pair). 10 new tests + 1 prompt-pin.
- `63e86df` — `main.py` sender gating + step2b PDF wiring. `step2b_read_promising` propagates `pdfs` into the per-email dicts; new `_gate_pdfs_by_sender` helper drops `pdfs` to `[]` on non-school senders (using `email.utils.parseaddr` to handle named-form headers like `"Meredith Rohde <mlrohde@fcps.edu>"`). Empty patterns list drops every PDF — safe default. New `PDF_SENDER_DOMAINS_PATH` constant. Cache trade-off documented: when the gating list expands later, previously-cached messages from a newly-eligible sender need `--reextract <messageId>` to surface their PDFs. 6 new tests.

754 → 815 tests green (61 net new across the four commits — 6 sender-domain + 8 gmail + 10 agent (5 plan_batches + 5 extract_events) - 1 reused + 1 prompt + 6 main.py - net counted).

**Pending Tom's live verification.** Verification checklist:

- (a) After the next teacher email with a PDF attachment arrives, trigger `workflow_dispatch test_output=true` and confirm `/testpage.html` shows the events extracted from the PDF (e.g. the bottom Upcoming-Dates block of a Rohde 3rd-grade newsletter).
- (b) Source line on those event cards reads "Rohde, Meredith (Apr 2)" or similar — the email's sent date, NOT the PDF's edition label.
- (c) Workflow log line "PDF gating: N email(s) with eligible PDF(s); M non-school sender PDF(s) dropped" reflects the right counts. A Costco-receipt PDF in a personal email sitting in the lookback window should show up in M, not N.
- (d) Cost telemetry from the agent's per-batch `usage` log line shows the expected token bump on PDF batches (~1.5k–3k extra input tokens per page) and stays within ~$0.05/week at typical cadence.

**Sample fixture: `fixtures/test/pdf_newsletter_third_grade.eml`** (real teacher email from `mlrohde@fcps.edu`, 121KB PDF inside, 1 page, 5 dated events in the bottom Upcoming-Dates block). Reminder for future sessions: this is a sample, not a template — different teachers will format differently.

### 35. [ ] Offline write queue: persist ignore / complete flips locally when offline, sync on reconnect

Filed 2026-04-27 from Tom as a placeholder. **Lower priority than #33 / #34** — captured so it isn't lost, but not actively prioritized; revisit only if Tom + Ellen actually start hitting it in practice (rare given they almost always interact with the page online).

Today's behavior on POST failure (network down, Apps Script timeout, etc.) is: optimistic flip reverts immediately, "Ignore failed — try again" toast, no localStorage entry survives. That posture was deliberate when filed during #32: it keeps the architecture simple and is fine for the online use-case Tom + Ellen actually have.

If we ever need offline support, the shape would be: a local queue of pending POSTs with timestamps, retry-on-reconnect (e.g. `navigator.onLine` event listener), and surface a small "N flips pending sync" indicator so the user knows their changes haven't pushed yet. Reconciliation would extend the #34 timestamp-based model — entries with a still-pending POST stay locally authoritative regardless of the fetch.

No commits, no design note, no `[~]` flip until Tom signals he's hit a real offline-loss scenario.

### 36. [ ] Card color-coding intuitiveness — Ellen can't tell what the colors mean

Filed 2026-04-27 from Tom as a placeholder. Card categories drive the left-border color (the `CATEGORY_COLORS` table in `scripts/process_events.py` maps each `category` to an `(fg, bg)` tuple, and `_event_card` renders `border-left: 4px solid {fg};`). Tom's feedback: the color coding *as it stands* isn't intuitive — Ellen sees a colored stripe but has no reference for what each color means, so the cue is decorative rather than informative.

Open for the next session to talk through with Tom before any code:

- Whether the fix is a category legend (small color-coded key in the page header), explicit text-on-card category labels, a different visual encoding (icon, badge, prefix word), or removing color-coding altogether and using something else entirely. Tom to confirm direction.
- Whether the existing category set (School Activity, Sports & Extracurriculars, Academic Due Date, Appointment, Uncategorized) is the right granularity or should be re-bucketed for Ellen's mental model.
- Interaction with the per-kid filter chips (#12) — the chips already provide one orthogonal axis of card grouping; consider whether category needs a parallel filter or stays as a passive cue.
- Whether this is a single-axis cue (category) or should also encode urgency / deadline-proximity / kid-attribution.

No commits, no design note, no `[~]` flip until Tom and the next agent discuss.

37\. [x] Auto-GC the Ignored Events + Completed Events sheets — 3bd0cae / 1f8e8d8 / 228b082 / 6b48b67 / 018942e — see COMPLETED.md

38\. [x] Bug: location linkifier mis-handles email addresses and trailing URL characters — d4581af — see COMPLETED.md

39\. [x] Bug: card briefly disappears on refresh due to hydration vs reconcile asymmetry — 3c4da6a — see COMPLETED.md

## Descoped / on hold

Items parked here aren't dead — they're off the active queue but preserved in case priorities shift. Revive by moving the full prose back under "Backlog" at the original number and flipping `[-]` → `[ ]`.

14\. [-] Manual "refresh now" button in the UI

Descoped 2026-04-17 (session 10). The weekly cron cadence has been sufficient in practice — Tom has not hit a real case of needing a mid-week rebuild since the feature was originally filed, and the threat-model / PAT-rotation overhead no longer looks worth the payoff. Preserving the full scope below in case that changes.

Button in `docs/index.html` that triggers the weekly workflow on demand, so a fresh build can be forced after a late schedule email without waiting for the next scheduled run or opening GitHub. GitHub's `workflow_dispatch` API requires an authenticated call, so the existing Apps Script webhook grows a new `action=refresh` endpoint that holds a fine-grained PAT (scope: `workflow`, single-repo) as a Script Property and POSTs to the dispatches endpoint. Client fires `fetch(APPS_SCRIPT_URL, {method:'POST', body: JSON.stringify({secret, action:'refresh'})})` and shows a "Rebuilding… reload in ~2 min" toast; no live polling.

Threat model accepted: the shared secret is effectively public (embedded in page source on a page with near-zero organic traffic), worst case is a handful of wasted workflow runs. Defense in depth: Apps Script rate-limits to one dispatch per 5 minutes via `PropertiesService`. The workflow's existing `concurrency: {group: pages, cancel-in-progress: false}` already prevents pileups from rapid clicks. PAT rotation: 1-year expiry with a calendar reminder.

15\. [-] Conflict highlighting

Descoped 2026-04-17 (session 10). Same-day multi-kid overlaps are visually obvious on the current card layout — the week grouping already co-locates them — and Tom has not seen a missed-conflict incident that would justify the render complexity. Preserving the scope below in case a kid adds a second activity that creates regular overlaps.

In `process_events.py`, detect overlapping timed events on the same day via interval intersection; flag both cards with a visible conflict marker. Prioritize different-kid overlaps as the high-signal case. Same-day all-day + timed events should NOT be flagged as conflicts — they coexist by design.