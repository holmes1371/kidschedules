# Kids Schedule — Agent Instructions

This file auto-loads at session start. Read it in full before responding when Tom mentions "kids-schedule", "the QoL list", or asks about the next feature. The prioritization in the issue tracker is settled — do not re-debate it without prompting.

## Session start

- Invoke the `karpathy-guidelines` skill via the Skill tool at the start of every session that touches code. Reading `reference/guidelines.md` directly does NOT count — the skill-load step anchors the discipline for the rest of the session.
- For any non-trivial task, **state the plan and stop** before implementing. Wait for explicit approval. Stating a plan and immediately executing it in the same turn is a violation of the rule, even when the plan looks obvious.

## Where things live

- **Active backlog:** GitHub Issues at https://github.com/holmes1371/kidschedules/issues
- **Project board (Kanban):** https://github.com/users/holmes1371/projects/2 (KidsToDo) — Status column tracks Todo / In Progress / Done
- **Closed-item history:** `COMPLETED.md` in repo root (full post-mortem prose for the original 1–39 numbered items that closed before the migration; post-migration close-outs reference GitHub issue numbers instead)
- **Per-feature design notes:** `design/{feature-name}.md`
- **Source code:** `scripts/`, `main.py`, `agent.py`, `gmail_client.py`, `events_state.py`, `apps_script.gs` (under `scripts/`)
- **Tests:** `tests/` — runs on every push + PR via `.github/workflows/tests.yml`. A red test check blocks merge; don't mark a feature done with tests failing.
- **Live site:** https://holmes1371.github.io/kidschedules/ — built by the Mon/Wed/Sat 6:15 ET cron in `.github/workflows/weekly-schedule.yml`

## Item-number convention

**GitHub issue numbers are the canonical identifier.** Reference items as `#7`, `#33`, etc. in commit messages, design notes, and chat. GitHub auto-links the `#N` form to the issue page, so cross-references are clickable. Issue numbers are inherently stable — GitHub never reuses them within a repo.

Pre-migration history in `COMPLETED.md` uses an older "Item N" numbering (1–39) that doesn't align to current GitHub issue numbers (the migration filed the open backlog as new issues #3–#9). When citing a closed-pre-migration item, write `COMPLETED.md item N` to disambiguate from a current GitHub issue. Do NOT renumber `COMPLETED.md` — those numbers are the keys for the historical commit message trail.

The auto-load `MEMORY.md` under `~/.claude/projects/.../memory/` has two pre-migration notes (git commit identity, pre-push pytest rule) that this file supersedes — they're left in place as cross-session backstops; this CLAUDE.md is the canonical source.

## Session discipline

- Git commits need `-c user.name="Tom Holmes" -c user.email="thomas.holmes1371@gmail.com"` flags since there's no default identity. Never use placeholder `.example` TLDs (memory note: those read as impersonation).
- Before starting a non-trivial feature, write a short design note to `design/{feature-name}.md` capturing the scope, the decisions already made, and the test fixtures needed. A fresh session should be able to pick up mid-feature from that note plus the last commit, without re-litigating choices.
- Commit at every natural boundary, not just at feature completion. Half-finished work behind a clear commit message is recoverable; a dirty worktree is not.
- Use the built-in TodoWrite tool as internal scaffolding on multi-step work — refresh at each commit boundary and keep exactly one item `in_progress`. The output is not visible in Tom's UI; it's a working scratchpad that survives compaction and mid-session interruptions.
- **Move an issue to "In Progress" on the board as soon as Tom approves the plan** — before the design note, before any code. The board status is what tells the next agent what's actually in flight.
- **Update the "Last session summary" block at the bottom of this file between each commit during a multi-commit feature**, not just at session end. Single-slot, replace in place. Older sessions' context lives in commit messages, `COMPLETED.md`, and `design/*.md`.
- **Do not move an issue to "Done" without explicit Tom signoff.** When the final code commit lands, leave it in "In Progress", record the SHA(s) in the issue body, and summarize what's pending live verification. Tom verifies and either signs off (next session moves to Done + posts a close-out comment) or returns feedback. Closing on your own reads as premature.
- **Manual-verification checklist on issues.** When an item ships work that needs Tom's manual verification before close (live page check, workflow log inspection, cron-cycle observation, clicking through a UI flow, confirming a side effect in an external service), append a `## Manual verification` section to the issue body with checkbox items. Each box names one concrete check Tom will perform (e.g. `- [ ] Trigger a workflow_dispatch with test_output=true; confirm the new card appears on /testpage.html`, `- [ ] Refresh the live page and confirm the Field Trip card no longer flickers`). Sub-task checkboxes in the scope section track what was built (agent ticks these as it builds); manual-verification checkboxes track what Tom confirmed working — **only Tom ticks the manual-verification boxes, never the agent.** They're his audit trail; ticking them on his behalf defeats the purpose. The issue is ready to close only when both sets are checked. Items that need no manual check (pure refactors, internal helpers verified by tests alone) skip the section. Pre-code design questions go under a different heading like `## Open for next-session discussion` so a glance can tell apart "Tom needs to verify X" (work done, awaiting confirmation) from "Tom needs to decide X" (work not started).
- **Editing GitHub issue bodies via gh.** `gh issue edit --body "..."` (or `--body-file`) overwrites the entire body. Tom may tick manual-verification boxes in the GitHub UI between the agent's edits; pushing a stale local copy back silently un-ticks them. Before each edit: fetch the live body via `"$GH" issue view <N> --json body --jq '.body' > <tmp-path>`, modify only the sections you intend to change (preserve `## Manual verification` content **verbatim** from the fetched copy), then push back with `--body-file <tmp-path>`. On Windows the gh.exe path resolution differs from MSYS `/tmp` (caught in this session) — prefer a repo-local path like `body.md` (gitignore it) or a `mkdir`-on-demand `.migration-tmp/` directory rather than `/tmp`. The agent never writes `[x]` in a manual-verification checkbox itself — only `[ ]` on initial creation, or whatever the fetched body shows on round-trips. The "I'm just preserving what I think is the current state" rationalization is the failure mode; always fetch.
- **Closed items move to `COMPLETED.md` once Tom signs off.** Copy the issue body into `COMPLETED.md` keyed by item number, then close the issue with a "see COMPLETED.md" comment. Original numbers stay stable. When touching territory that overlaps a completed item, read its full entry in `COMPLETED.md` before re-deriving decisions.
- Pre-push: full `pytest tests/ -q` green via the strftime-patch workflow on Windows (memory note `feedback_pre_push_full_render_suite.md`). Do NOT settle for `-k` filtered or single-file runs.
- Any feature that modifies `scripts/process_events.py` must extend the pytest fixtures in step with the change, not after.
- Honor the standing order: deterministic work lives in Python scripts; the agent does only judgment and interpretation. If a feature tempts you to move mechanical work into agent-handled text, push back.
- The site is a live view, not an archive. Old `docs/index.html` commits persist in git history but they are not a feature — do not design affordances for "view prior schedules" or commit versioned weekly snapshots under dated filenames.
- The `Ellen's ToDo` mount is retired. All work happens in `kids-schedule-github/`.

## Tooling references

**GitHub CLI (Windows):**

```
GH="/c/Program Files/GitHub CLI/gh.exe"
```

The Windows-native gh.exe is NOT on the Bash tool's PATH; always invoke via the full quoted path. Token has scopes `gist`, `project`, `read:org`, `repo`, `workflow`. Owner is `holmes1371` (user, not org).

**Project board IDs** (cached so future sessions skip discovery):

- Owner: `holmes1371` (user)
- Project number: `2`
- Project node ID: `PVT_kwHOAmwjSM4BWa16`
- Status field ID: `PVTSSF_lAHOAmwjSM4BWa16zhRvUOU`
- Status options: Todo `f75ad846`, In Progress `47fc9ee4`, Done `98236657`, Descoped `ab890376`

**Common one-liners:**

```bash
# File a new item (use a plain title; GitHub assigns the issue number)
"$GH" issue create --title "<short descriptive title>" --label "queued" --body-file <body.md>

# Move issue to In Progress (find item-id via item-list)
"$GH" project item-list 2 --owner holmes1371 --format json --limit 50
"$GH" project item-edit --id <ITEM-ID> --project-id PVT_kwHOAmwjSM4BWa16 \
   --field-id PVTSSF_lAHOAmwjSM4BWa16zhRvUOU \
   --single-select-option-id 47fc9ee4

# Close as Done after Tom signoff (also move board status to Done with option id 98236657)
"$GH" issue close <N> --comment "Tom verified live. Full prose archived in COMPLETED.md."
```

**Pre-push pytest (Windows strftime patch):**

```bash
cp scripts/process_events.py scripts/process_events.py.bak && python -c "
src = open('scripts/process_events.py', encoding='utf-8').read()
open('scripts/process_events.py', 'w', encoding='utf-8').write(
    src.replace('%-d', '%#d').replace('%-I', '%#I')
)" && python -m pytest tests/ -q; mv scripts/process_events.py.bak scripts/process_events.py
```

## Last session summary

Single block, ≤5 bullets, replaced in place each session. Only what is open, in-flight, or just-filed.

**2026-05-02**

- ROADMAP.md migration to GitHub Issues + KidsToDo project board complete. 7 issues now exist: #3 + #4 (descoped, closed not-planned), #5 + #6 + #7 (in-progress, pending Tom's live verification), #8 + #9 (queued placeholders).
- "Item N" prefix dropped from issue titles in a follow-up — GitHub issue numbers are now the canonical identifier. Pre-migration `COMPLETED.md` numbering stays intact for the historical commit-message trail.
- Manual-verification checklist convention codified in CLAUDE.md (heading: `## Manual verification`). Two operational rules attached: only Tom ticks manual-verification boxes (never the agent — his audit trail), and `gh issue edit` always fetches the live body first via `gh issue view --json body --jq '.body'` before pushing back, so a UI tick between agent edits doesn't get silently un-ticked. Existing in-flight issues #5 / #6 / #7 use the older `## Verification` heading — same intent, same rules apply; they'll get renamed when next touched.
- CLAUDE.md (this file) introduced as the auto-load briefing. Memory entries `feedback_git_commit_identity.md` + `feedback_pre_push_full_render_suite.md` left in place as backstops.
