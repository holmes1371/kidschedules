# Incremental extraction — skip already-processed Gmail messages

New item 4. Today every run sends up to 60 days of Gmail messages through the Anthropic agent, even though 99% of them haven't changed since last week. The Anthropic call is the only expensive step in the pipeline — Gmail search and rendering are free. This feature caches extracted events keyed by Gmail message ID so the agent only ever sees messages it hasn't processed before.

Tom's framing locked in: keep the 60-day Gmail search intact (cheap, self-healing), but only send messages to the agent that aren't already in a committed cache. Render from the merged set of cached + newly-extracted events.

## State file — `events_state.json`

Committed to the repo, same pattern as `ignored_events.json` and `future_events.json`. Single top-level file:

```json
{
  "schema_version": 1,
  "last_updated_iso": "2026-04-14T06:30:00-04:00",
  "processed_messages": {
    "<gmail_msg_id>": "2026-04-14T06:30:12-04:00"
  },
  "events": {
    "<12-char event_id>": {
      "name": "...", "date": "2026-05-01", "time": "6:30 PM",
      "location": "...", "category": "School Activity",
      "child": "Isla", "source": "LAES PTA (Apr 6)",
      "first_seen_iso": "2026-04-14T06:30:12-04:00"
    }
  }
}
```

No per-message event attribution. The agent sees batches of ~10 emails and returns events without saying which email each came from; threading that through would cost a prompt change with no correctness benefit. What we care about is (1) don't re-extract a seen message, and (2) keep the events we've already extracted. Both are satisfied by tracking message IDs and event IDs as independent sets. Event IDs are already stable (`sha1(norm_name | date | norm_child)[:12]`) so dedupe works across runs without attribution.

## Cache merge semantics

On each run:

1. Load state. On missing/corrupt file → warn, start empty.
2. Garbage-collect before anything else (see below).
3. Run the full Gmail search as today.
4. `new_emails = [e for e in full_emails if e.messageId not in state.processed_messages]`.
5. Extract events only from `new_emails`. Existing batching / retry / irrelevant-sender flagging unchanged.
6. Merge new events into `state.events` by event ID. On collision, keep the version with higher `completeness` score (reuse the existing scorer from `process_events.py::dedupe`). Ties → keep cached (stable).
7. Record every message in `new_emails` in `processed_messages` with today's timestamp.
8. Hand `list(state.events.values())` to `process_events.py` as the candidate list.
9. Save state atomically (tempfile + `os.replace`).

The existing downstream dedupe/classify/render logic stays unchanged — it just receives a larger, more stable candidate pool instead of a freshly-extracted one each week.

## Garbage collection

Runs at load time, before extraction:

- `processed_messages`: drop entries where `processed_at_iso` is older than `2 × lookback_days` (default 120 days). Those messages can't come back into the Gmail search, so caching them is dead weight. Bounded state size.
- `events`: drop entries where (a) `date` is parseable and strictly before today, or (b) `date` is empty and `first_seen_iso` is older than 120 days. Past events have no reason to stick around; long-stale undated events have no chance of being refreshed.
- GC counts are logged in the step banner for visibility.

## Schema version and cache invalidation

Single top-level `schema_version` integer constant in `scripts/events_state.py`. If the loaded state's version doesn't match the constant, blow the whole file away and start empty. Worst case: one run does a full 60-day re-extraction. Acceptable for a rare event (we only bump on meaningful prompt changes).

## Ignore-button interaction

Unchanged. `ignored_events.json` is filtered against the candidate list in `process_events.py::classify` as today. The cache doesn't need to know about ignore state — we render from `state.events` minus the ignore list. An ignored event stays in the cache but never displays; if it ages out by date or message-GC it leaves naturally.

## `future_events.json` retirement

The existing `future_events.json` bank is subsumed by the new cache (any event beyond the 60-day horizon is now stored in `state.events` alongside the rest). Retiring it is a clean breakpoint at the end of the commit plan: one commit that (a) bootstraps `events_state.json` with the contents of `future_events.json` if the cache is empty, (b) removes the `--banked-out` / `_save_event_bank` plumbing from `main.py` and `process_events.py`, (c) deletes the file and its workflow commit step. Lossless migration.

## Atomicity and failure modes

- Write: `events_state.json.tmp` → `os.replace`. Partial writes are atomic on POSIX.
- Read: on `JSONDecodeError` or `OSError`, log a workflow warning (`print` to stdout, Actions surfaces it) and start with empty state. Next successful run regenerates the file from live data.
- Agent failure on a message: the message is NOT added to `processed_messages`. Retried next run. No "poisoned" cache state.
- Schema mismatch: handled above. Blow-away-and-rebuild.

## Pytest fixtures

Extending `tests/test_process_events.py` isn't where this belongs — this is a new module. New file `tests/test_events_state.py` with:

- `load_state`: missing file, valid file, corrupt JSON, wrong schema version, legacy `future_events.json` bootstrap.
- `filter_unprocessed`: all-new, all-cached, partial-hit.
- `merge_events`: new wins on more completeness, cached wins on less, identical passes through, collision preserves one entry.
- `gc_state`: processed_messages past the window drop; events with past date drop; undated events past 120 days drop; current events retain.
- `save_state`: atomic write via tempfile + rename (can be asserted by mocking `os.replace`).

Plus one integration test in `tests/test_main.py` (the file exists; item 3 added gate tests) that exercises the "zero new messages" short-circuit: given a fully-populated state and emails that are all in it, assert the agent stub is never called.

## Files touched

- `scripts/events_state.py` — new module: `load_state`, `save_state`, `filter_unprocessed`, `merge_events`, `gc_state`, `CURRENT_SCHEMA_VERSION`.
- `main.py` — new step between `step2b_read_promising` and `step3_extract_events` that loads state, filters, and after `step3` merges + saves. Log cache-hit/cache-miss/GC counts.
- `tests/test_events_state.py` — new file per fixture plan above. State dicts built inline as Python literals; no separate JSON fixture files.
- `tests/test_main.py` — one new integration test for the zero-new-message short-circuit.
- `.github/workflows/weekly-schedule.yml` — add `events_state.json` to the committed outputs list.
- `events_state.json` — the initial file (empty shape, committed at feature-complete).
- `ROADMAP.md` — mark `[x]`.

Plus in the retirement commit:

- `main.py`, `scripts/process_events.py` — remove `--banked-out` plumbing and `_load_event_bank` / `_save_event_bank`.
- `future_events.json` — deleted after bootstrap.
- `.github/workflows/weekly-schedule.yml` — drop the file from commit list.

## Explicit non-goals

- **No prompt change to add source-message attribution.** The cache doesn't need it; YAGNI.
- **No change to the Gmail search window or search queries.** Self-healing behavior stays intact: if a message somehow gets unseen, the next run re-catches it.
- **No eviction of the cache on ignore-list growth.** Ignored events live cheaply in the cache; removing them would just re-extract them next run if the ignore list got edited.
- **No retroactive cleanup of `future_events.json` in the same commit as the cache introduction.** Retirement is its own commit at the end so each step is reversible.

## Commit plan

1. Design note (this file) + ROADMAP insert as item 4, push existing item 4 (.ics export) to 5 and so on.
2. `scripts/events_state.py` module + `tests/test_events_state.py` + fixtures. No wiring yet. All tests pass. Cohesive self-contained unit.
3. `main.py` integration (load/filter/merge/save around step 3) + `tests/test_main.py` zero-new-messages test + workflow commit of `events_state.json`. First live run is the real end-to-end smoke test.
4. (Retirement) Bootstrap `events_state.json` from `future_events.json` on first load where cache empty, remove `--banked-out` plumbing, drop `future_events.json` and its workflow commit step.
5. ROADMAP close-out with commit SHAs.
