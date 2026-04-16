# Monday-only Gmail draft gating

Roadmap item 10. Restrict the weekly Gmail digest draft to Monday cron runs only. Today's workflow sets `CREATE_DRAFT=1` on every `schedule` trigger, which fires three times a week (Mon/Wed/Sat at 10:15 UTC), so Ellen gets three drafts where she wants one.

## Decision — option (b): split the cron

Two cron entries, one gate expression:

```yaml
schedule:
  - cron: "15 10 * * 1"    # Monday — draft ON
  - cron: "15 10 * * 3,6"  # Wed/Sat — draft OFF
```

```yaml
CREATE_DRAFT: ${{ (github.event.schedule == '15 10 * * 1' || github.event.inputs.create_draft == 'true') && '1' || '0' }}
```

`github.event.schedule` is populated by GitHub with the exact cron string of the entry that fired, so the expression can branch on it without any shell-date math.

### Why not option (a)

Option (a) was "keep the single `1,3,6` cron entry, compute day-of-week inside the step with `date -u +%u`." Rejected because it splits the gate logic: half lives in a yaml expression (`env:`) and half in a shell branch (`run:`). Auditing "does this trigger fire a draft?" then requires reading two layers. Option (b) keeps the answer in one place — the `env:` expression — even though it doubles the cron surface. Ellen is the only person who will ever edit this file; the extra cron line is not a real cognitive cost.

## What the Python side does

Nothing changes in `scripts/process_events.py::should_create_draft` or `main.py`. The gate function is already exhaustively unit-tested across all combinations of `--dry-run`, `--create-draft`, and `CREATE_DRAFT` env; the workflow is just feeding it a different `CREATE_DRAFT` value on Wed/Sat. No new test fixtures, no changes to the suite.

## Non-goals

- **Python gate changes.** Out of scope; already covered.
- **DST correction.** Unchanged from today's behavior — both cron entries share the existing UTC drift (fires at 6:15 AM EDT in summer, 5:15 AM EST in winter). The existing comment at the top of the schedule block continues to explain this.
- **Manual opt-in semantics.** The `workflow_dispatch` `create_draft` input stays wired into the expression via the OR branch. A manual run on Wed can still force a draft by toggling that input.
- **Dynamic day-of-week checks in Python.** Kept out to preserve the single-source-of-truth property of `should_create_draft`.

## Edge cases considered

- **Cron-string drift.** If a future edit changes the Monday cron time (e.g. bumps to 10:30 UTC), the gate expression must be updated in lockstep or drafts stop firing silently. A comment next to the expression flags this coupling.
- **Two cron entries, same time.** GitHub Actions supports multiple `cron:` entries; each fires its own workflow run. Concurrency is unaffected — the existing `concurrency` block at the workflow level already serializes overlapping runs.
- **Context value whitespace.** GitHub emits `github.event.schedule` exactly as written in the yaml. Single-space form (`15 10 * * 1`) on both sides keeps the comparison safe.

## Verification plan

No automated test covers the yaml. Verification is manual:

1. Push the change.
2. `workflow_dispatch` with `create_draft: true` → confirm a draft is created (manual opt-in still works).
3. `workflow_dispatch` with defaults → confirm no draft (default-off unchanged).
4. Wait for the next Monday cron → confirm a draft is created.
5. Wait for the following Wed or Sat cron → confirm no draft is created.

Per session-discipline rule, #10 stays at `[~]` in ROADMAP until Tom confirms steps 4 and 5 land correctly. Steps 2 and 3 are easy same-day smoke tests; 4 and 5 require waiting for real cron runs.

## Responsibility table

| Concern | Owner |
|---|---|
| Cron schedule definition | workflow yaml |
| Trigger → `CREATE_DRAFT` value mapping | workflow yaml expression |
| `CREATE_DRAFT` + CLI flags → actually call `create_draft` | `main.py::should_create_draft` (unchanged) |
| Draft rendering | `scripts/process_events.py` (unchanged) |
| Smoke-test against real triggers | Tom, manually |
