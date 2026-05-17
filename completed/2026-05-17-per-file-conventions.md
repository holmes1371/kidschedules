# Per-file conventions: session-log + completed/ post-mortems

> **Bootstrap entry** — not tied to a GitHub issue. Future close-outs follow the `completed/<issue-number>.md` convention; this date-and-topic slug flags it as the directory's seed.

## What was built

Two single-file logs split into per-entry directories to eliminate parallel-agent merge conflicts:

- **Session-log** — the old "Last session summary" block at the bottom of `CLAUDE.md` moved into `.claude/session-log/YYYY-MM-DDTHHMM-<topic-slug>.md`. Cold pickup reads the 1–2 most-recent files at session start (filenames sort newest-last alphabetically). Trim discipline at session start: if `>10` files or any `>14` days old, delete the older ones — durable signal lives elsewhere; session-log is recency-only.
- **Post-mortems** — `COMPLETED.md` close-outs moved into `completed/<issue-number>.md` (one file per issue). No trim; the directory is the durable archive. The 39 legacy pre-migration entries stay in `COMPLETED.md` until a separate migration ships; a stub at the top of `COMPLETED.md` points readers at `completed/`.

`CLAUDE.md` updated in four places to canonize both: the "Where things live" entries (closed-items + session-log), the "Session discipline" rules (replaced "Last session summary refresh" with "Session-log file per session"; replaced "Closed items archive to `COMPLETED.md`" with the per-file equivalent), and the deletion of the `## Last session summary` section at the bottom — durable rules stay in CLAUDE.md, recency-only session state moves to `.claude/session-log/`.

## Trigger

Earlier the same day, two PRs landed on `main` in parallel: PR #15 (this session's dedupe work on `claude/quizzical-lehmann-b11725`) and PR #16 (another agent's Mon/Wed/Sat → daily cron stale-reference cleanup on `claude/suspicious-galileo-bee395`). Both branches diverged from the same base. Both refreshed lines in `CLAUDE.md` — and the merge of #16 came in *while #15 was still open*, then #15 merged after. No conflict this time, but only because the touched regions didn't overlap. The structural fragility was obvious: any two agents touching `CLAUDE.md`'s `## Last session summary` block (or appending to `COMPLETED.md`) the same day produce a real collision.

The fix is the LawTracker pattern, ported here verbatim — see `C:\DevWork\LawTracker\completed\2026-05-08-per-file-conventions.md` for the original treatment. Symmetry between the two splits (single-file log → per-entry directory) is intentional: same shape, same rationale, same CLAUDE.md edit pattern.

## Decisions worth preserving

- **Filename schemes diverge by use case.** Session-log uses `YYYY-MM-DDTHHMM-<topic-slug>` because sessions are recency-ordered and need parallel-write disambiguation; topic slug (not branch slug) because branches get deleted post-merge but the session log lives in git forever — a future agent's cold-pickup read goes "what was this session about?", not "what branch was it on?". Completed/ uses `<issue-number>.md` because issue numbers are unique, sortable, and direct-lookup-friendly (`cat completed/13.md` beats scrolling).
- **Trim asymmetry.** Session-log trims aggressively (>10 files / >14 days → delete) because it's recency-only — the durable signal lives in commits / post-mortems / design notes / closed issues. Completed/ never trims because it IS the durable archive.
- **Legacy `COMPLETED.md` kept, not migrated in this work.** The 39 existing entries stay where they are until a separate migration backlog item ships. During the transition window both files are searchable side-by-side: `grep -r 'pattern' completed/ COMPLETED.md`.
- **`CLAUDE.md` auto-loads at session start**, so the new conventions propagate to all future agents without an explicit teaching step. The agent reads `CLAUDE.md`, sees the new "Where things live" + "Session discipline" entries, and writes per-file naturally.
- **No follow-up tracking issue for the migration itself.** The convention change is meta-workflow, not feature work; the bootstrap entry IS the record. A separate tracked issue exists for the future legacy `COMPLETED.md` migration only.

## Follow-ups

- **Tracked issue (to be filed):** split the 39 legacy `COMPLETED.md` entries into per-file `completed/legacy-NN-<slug>.md`. Mechanical — parse `## Item N` headers, write each section as its own file. Update `CLAUDE.md` to drop the "legacy entries still live in `COMPLETED.md`" carve-out, remove the stub at the top of `COMPLETED.md`, delete the file. Tom's call on prioritization vs. the existing queue.
