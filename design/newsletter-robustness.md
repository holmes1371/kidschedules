# Newsletter Robustness (#17)

## Problem

Newsletter-shaped emails (LAES PTA Sunbeam, FCPS updates, monthly dance
schedules) routinely carry 5–15+ dates per issue. The extractor prompt
already names them as a priority case (`agent.py` rule 8, "Newsletter
calendar items"), and per-batch parsing treats N events drawn from one
`source_message_id` as the normal shape. Two failure modes remain:

1. **Silent under-extraction.** When the agent returns 2 events from a
   newsletter that historically yields 12, the pipeline accepts the short
   list as authoritative. The `source_message_id` lands in
   `processed_messages`, so the next run skips that message entirely and
   the miss is permanent until a human notices.

2. **No re-extraction affordance.** Once a message is marked processed
   there is no supported way to re-run it through the agent short of
   hand-editing `events_state.json`.

A third concern is tactical rather than remedial: newsletters compete for
attention with shorter, more generic emails inside the same 10-email
batch. Giving a newsletter its own batch gives the model more per-message
attention at the cost of additional API calls.

## Why Python, not the prompt

The three deterministic pieces — per-sender event-count bookkeeping,
threshold-based outlier classification, and state-file eviction — have
no judgment component. Two runs with the same `sender_stats.json` and
the same extraction output produce the same alerts. Per the
skill-building standing order, these belong in Python. The agent's
contribution at runtime is unchanged: read emails, emit event dicts.

The batching change is also deterministic once a sender is classified:
newsletter senders get batch-of-1, everyone else gets batch-of-10.

## Scope

Four sub-features land together:

- **A. Learned newsletter detection.** New persistent file
  `sender_stats.json` at the repo root, maintained by a new
  `newsletter_stats.py` module. After repeated exposure, a sender is
  flagged `is_newsletter: true`.
- **B. Per-run outlier flag.** After extraction, senders already flagged
  as newsletter whose current-run event count falls well below their
  rolling median are listed as "possible under-extraction" in the
  Monday Gmail digest draft and the Actions run log.
- **C. `--reextract <message-id>` CLI.** `main.py` gains an argument that
  evicts a Gmail message ID from `processed_messages` and purges all
  events whose `source_message_id` matches, so the next run rebuilds the
  extraction from scratch.
- **D. Newsletter-isolated batching.** `agent.py::extract_events` accepts
  an optional set of newsletter-sender addresses. Emails from those
  senders run one-per-API-call; the rest continue to batch at 10.

No changes to:

- The extractor system prompt (`_EXTRACTION_BASE_PROMPT` in `agent.py`).
  Rule 8 already instructs the model to extract every newsletter date.
- The `events_state.json` schema. The event-ID hash stays
  `(name, date, child)`; the parity test in `tests/test_events_state.py`
  still guards it.
- The `child` field, the render layer, or any card HTML (this is a
  pipeline-integrity feature, not a display feature).
- `protected_senders.txt` behavior. A learned newsletter sender is *not*
  auto-promoted to the protected list; curation stays manual.
- The filter audit or auto-blocklist flow. Newsletter status is a
  sibling concept, not a substitute.

## Data model: `sender_stats.json`

New persistent state file at repo root, synced to the `state` branch by
the workflow. File-per-concern pattern — mirrors `events_state.json` and
`prior_events.json`. Gitignored in `main`.

Shape:

```json
{
  "schema_version": 1,
  "last_updated_iso": "2026-04-17T10:15:00-04:00",
  "senders": {
    "sunbeam@laespta.org": {
      "messages_seen": 14,
      "total_events": 137,
      "per_message_counts": [12, 9, 11, 14, 0, 10, 8, 13, 11, 12],
      "first_seen_iso": "2025-10-04T10:15:00-04:00",
      "last_seen_iso":  "2026-04-17T10:15:00-04:00",
      "is_newsletter": true
    }
  }
}
```

`per_message_counts` is capped at the most recent 10 entries (FIFO).
Newer entries append; older entries fall off. This bounds file growth and
makes the rolling median insensitive to year-old data.

`messages_seen` and `total_events` are lifetime counters; they don't
reset when `per_message_counts` rolls over. They're informational; no
code path depends on them.

## Decisions

### Sender key: lowercased From-address

The key is the lowercased mailbox form of the `From` header (parsed with
`email.utils.parseaddr`, then `.lower()`). Domain keys were rejected:
`fcps.edu` carries both the PTA newsletter (high-yield) and transactional
admin mail (low-yield); domain-level aggregation would dilute the signal
until promotion never fires. Per-address keys keep the sensitivity
pinned to the actual publisher.

Consequence: if a school publishes the same newsletter from two
addresses (`sunbeam@laespta.org` and `news@laespta.org`) the stats
bifurcate. Acceptable — this is an edge case and promotion fires
independently on each.

### Promotion threshold

A sender flips to `is_newsletter: true` the first time both conditions
hold:

- `messages_seen >= 3`
- `median(per_message_counts) >= 5`

Chosen empirically to cover the observed newsletter senders (Sunbeam ≈
11, FCPS monthly updates ≈ 6, Cuppett schedule emails ≈ 8) without
false-positive promoting a low-volume sender that happened to fire once
with six dates.

Demotion is not implemented. A promoted sender that stops publishing is
harmless: it won't appear in new batches and can't trigger a batching
change or an outlier alert. Manual removal from `sender_stats.json` is
the escape hatch if an individual sender becomes problematic.

### Outlier threshold

After a run, for each sender with `is_newsletter: true`:

```
current = event count from this run for this sender
prior_median = median(per_message_counts before this run was folded in)
threshold = max(2, round(prior_median * 0.5))
if current < threshold: emit outlier alert
```

Rationale: dividing by 2 tolerates normal newsletter variance (a summer
issue with 6 dates vs. a September back-to-school issue with 15 dates).
The hard floor of 2 prevents false positives at very low prior medians;
if a sender typically publishes 3 events and this run yielded 1, that is
a signal worth surfacing.

Alerts carry `{sender_key, message_id, prior_median, current_count}` and
are computed per-message-id — a newsletter sender whose current run had
two messages with 12 and 1 events respectively flags only the short
message.

### Outlier flag surface: Monday digest + Actions log

Alerts render in two places:

1. **Monday Gmail digest draft.** A new "⚠️ Possible under-extraction"
   section sits above the event list. Each alert is one line with the
   message ID shown verbatim so it can be copied directly into a
   `--reextract` invocation. Mid-week (Wed/Sat) runs suppress the draft
   entirely (per #10) so the alert surface is Monday-only, which matches
   the review cadence.
2. **Actions run log.** Every run, regardless of cadence, prints the
   same lines to stdout under a `STEP 3c: Outlier alerts` banner. This
   gives post-hoc visibility when a Wed/Sat run under-extracts and the
   Mon draft is a full three days away.

Not rendered on the published site. The schedule page is a live
calendar view, not a status dashboard; a sidebar warning pollutes the
reading surface. Alerts are an operator concern, not a viewer concern.

### Newsletter-isolated batching

`agent.py::extract_events` grows a new kwarg:

```python
def extract_events(
    emails: list[dict[str, Any]],
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 16384,
    newsletter_senders: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
```

Default `None` preserves current behavior (all tests that don't pass the
kwarg continue to batch at 10). When provided, the function partitions
`emails` into:

- `newsletter_emails`: sender mailbox (lowercased, parsed from `from_`)
  is in `newsletter_senders`. Batched at size 1.
- `regular_emails`: everything else. Batched at the existing
  `BATCH_SIZE = 10`.

Both partitions run through the same `_call_with_retry` loop and the
same parse/filter pipeline. The order of API calls is newsletters first,
then regulars, so a parse failure on a cheap regular batch doesn't
gate the expensive newsletter work.

Token cost implication: a single 10-email regular batch becomes (say) 2
batch-of-1 newsletter calls plus 1 batch-of-8 regular call. Roughly
1.5–2× the API-call count on a newsletter-heavy run, but each newsletter
receives the full output-token budget instead of sharing it. Input-token
totals barely change (same bodies either way).

### First-run semantics: missing file ≠ empty dict

Matches the pattern established by `prior_events.json` in #13.

- **File missing.** `newsletter_stats.load_stats(path)` returns an empty
  `stats` dict. No senders are classified as newsletters. No outlier
  alerts. Batching defaults to 10 for everyone. After this run's stats
  are folded in and saved, subsequent runs start to pick up the signal.
- **File present, `senders: {}`.** Legitimate empty-stats state (e.g.,
  after manual edit). Same observable behavior as missing.
- **File present, malformed JSON or wrong shape or wrong schema
  version.** Loader warns to stdout (matches `events_state.py::load_state`
  warning style) and returns empty stats. The next save overwrites the
  bad file.

### Dry-run semantics: read, don't write

`main.py --dry-run` loads `sender_stats.json` and uses it for batching
and alert computation. It does *not* save the updated stats. This
mirrors #13's `prior_events.json` handling: a dry-run is a read-only
inspection of the real pipeline's state, not an alternate history.

Consequence: a Wed/Sat real run that happens after a dry-run still sees
correct rolling counts. A user running `--dry-run --reextract <mid>`
does evict from `events_state.json` in memory but does not persist the
eviction — same read-only posture.

### `--reextract <message-id>` mechanics

Placement: before the Gmail fetch (step 2), between arg parsing and
`step1_build_queries`. The eviction runs against the in-memory state
that `step2c_load_cache_and_filter` later reads.

Operations:

1. Load `events_state.json` via `events_state.load_state`.
2. If `message_id in state["processed_messages"]`: remove it. Log 1 msg
   evicted.
3. Scan `state["events"]`; collect event IDs where
   `ev.get("source_message_id") == message_id`. Remove all such entries.
   Log N events evicted.
4. Save state via `events_state.save_state` (atomic tempfile + rename).

An unknown `message_id` is a warning, not a failure: log "no matching
message in cache" and continue. Empty pipelines shouldn't crash because
a user fat-fingered a hex string.

Interaction with the rest of the pipeline: the evicted message falls
back into `filter_unprocessed` because it's no longer in
`processed_messages`, so the agent re-extracts it. The fresh events
merge into the cache normally. Any events from the *original*
extraction that the re-extraction also produces will win on
`_completeness` (or tie and keep cached); any events the original
missed get inserted fresh; any events from the original that the
re-extraction no longer produces stay purged because step 3 never
re-inserts them.

Single message ID per invocation. A bulk form (`--reextract a,b,c` or
`--reextract @file`) is out of scope until a concrete use case emerges.

### Stats update happens after extraction, from candidates

Per-run per-sender counts come from the candidates list, keyed by
`source_message_id`, with the sender mailbox looked up in the same
`from_by_id` map that `_attach_sender_domains` already builds. This
keeps the stats grounded in what was actually extracted (not in what
was *sent* to the agent, which overcounts when the agent legitimately
returns zero events because the email was unrelated).

Messages that were sent to the agent but yielded zero events still
count for stats bookkeeping. A newsletter that legitimately produced
zero events this week (a quiet summer issue) contributes a 0 to
`per_message_counts` — which is exactly what the rolling median needs
to stay honest. We detect these by starting from the `new_emails` list
(messages sent to the agent) rather than only the candidates list.

## Cache / re-render behaviour

`sender_stats.json` is independent of every other state file. No cross-
file invariants. Cache eviction (`events_state.py::gc_state`) does not
touch stats. A cache-cleared run does not reset stats. `--reextract`
evicts from the event cache without touching stats.

Workflow plumbing (restore from `state` branch on clone, save back at
end) mirrors `prior_events.json` exactly.

## Test fixtures

### `tests/test_newsletter_stats.py` (new)

Unit tests for the pure helpers in `newsletter_stats.py`:

- `test_load_stats_missing_file` — no file → empty stats dict, no error
- `test_load_stats_empty_senders` — `senders: {}` → returns empty
  senders dict
- `test_load_stats_malformed_json` — invalid JSON → warn + empty stats
- `test_load_stats_wrong_schema_version` — version mismatch → warn +
  empty stats
- `test_save_stats_atomic` — `.tmp` file used + renamed
- `test_save_stats_roundtrip` — save then load returns the same structure
- `test_update_counts_new_sender` — unseen sender → seeded with
  messages_seen=1, per_message_counts=[N], is_newsletter=False
- `test_update_counts_existing_sender_appends` — seen sender →
  per_message_counts grows, first_seen preserved, last_seen advances
- `test_update_counts_rolls_at_10` — 11th count pushes the first out
- `test_classify_promotes_at_threshold` — 3 messages with median ≥5
  flips is_newsletter=True
- `test_classify_no_promote_below_message_threshold` — 2 messages with
  median 12 stays False
- `test_classify_no_promote_below_median_threshold` — 5 messages with
  median 4 stays False
- `test_classify_sticky` — promoted sender stays True even if median
  drops
- `test_outlier_below_half_median` — current=3, prior_median=12 → flag
- `test_outlier_at_floor` — current=1, prior_median=2 → flag (floor=2)
- `test_outlier_above_threshold_no_flag` — current=6, prior_median=12 →
  no flag
- `test_outlier_non_newsletter_no_flag` — non-promoted sender never
  generates an alert regardless of delta

### `tests/test_agent.py` (extend)

- `test_extract_events_partitions_newsletter_senders` — mocked API call;
  two newsletter emails + one regular → API called 3 times (two
  batch-of-1 + one batch-of-1 regular) with correct payloads
- `test_extract_events_default_batching_unchanged` — no `newsletter_senders`
  kwarg → existing batching semantics preserved (regression guard)
- `test_extract_events_mixed_batch_sizes` — newsletter-heavy run +
  regular emails batches correctly

### `tests/test_main.py` (extend)

- `test_reextract_purges_message_and_events` — cache with msg-id M
  present + 3 events with `source_message_id=M` → after the eviction
  step, processed_messages lacks M and state.events lacks those 3
- `test_reextract_unknown_message_id_warns` — unknown msg-id → no
  exception, warning logged
- `test_main_loads_sender_stats_if_present` — fixture stats file with a
  promoted sender → extract_events receives that sender in
  `newsletter_senders`
- `test_main_stats_saved_after_extraction` — real run-through →
  sender_stats.json updated to include this run's counts
- `test_main_dry_run_does_not_save_stats` — dry-run → stats file
  unchanged

### `tests/test_process_events.py` (extend)

- `test_digest_under_extraction_block_renders` — `--outlier-alerts` JSON
  with one alert → digest text and HTML both contain the block
- `test_digest_no_block_when_alerts_empty` — empty alerts file → no
  "under-extraction" block in either digest variant

## Files touched

- `design/newsletter-robustness.md` (this file)
- `ROADMAP.md` — flip `### 17. [ ]` → `### 17. [~]`; update session notes
- `newsletter_stats.py` (new, repo root) — load/save/update/classify/
  outlier pure helpers
- `main.py` — `--reextract` arg + pre-fetch eviction; load stats; pass
  `newsletter_senders` to `extract_events`; update stats post-extraction;
  compute outlier alerts; thread alerts to `process_events.py` via new
  `--outlier-alerts` tempfile; save stats
- `agent.py::extract_events` — accept `newsletter_senders`; partition
  emails into newsletter (batch-of-1) and regular (batch-of-10); no
  other behavior change
- `scripts/process_events.py` — accept `--outlier-alerts <path>`; render
  "Possible under-extraction" block in digest text + HTML builders
- `.github/workflows/weekly-schedule.yml` — restore block + save block
  + FILES entry for `sender_stats.json`
- `.gitignore` — add `sender_stats.json`
- `tests/test_newsletter_stats.py` (new)
- `tests/test_agent.py`, `tests/test_main.py`, `tests/test_process_events.py`
  — extensions per the fixtures list above

## Out of scope

- Cross-issue newsletter diffing ("this issue added 2 dates vs. last
  week's issue; this one dropped 1") — per ROADMAP body, YAGNI until a
  concrete miss justifies the infrastructure.
- Auto-promotion of newsletter senders to `protected_senders.txt`.
  Curation stays manual; the learned-status signal lives only in
  `sender_stats.json`.
- Demotion (newsletter → regular) of senders that stop publishing.
- Per-sender overrides (e.g., "always batch-of-1 even if not a
  newsletter"). The newsletter status is the only signal that changes
  batching.
- Bulk `--reextract` input. One message ID per invocation.

## Responsibility table

Per the skill-building standing order. Every mechanical step lives in a
Python script; the runtime agent is limited to interpretation.

| Concern                               | Owner            |
| ------------------------------------- | ---------------- |
| Parse `From` header → sender key      | Python (main.py) |
| Load / save `sender_stats.json`       | Python (newsletter_stats.py) |
| Append per-run counts, roll window    | Python (newsletter_stats.py) |
| Classify sender as newsletter         | Python (newsletter_stats.py) |
| Compute rolling median                | Python (newsletter_stats.py) |
| Compute outlier threshold             | Python (newsletter_stats.py) |
| Emit outlier alert records            | Python (newsletter_stats.py) |
| Partition emails into newsletter/regular | Python (agent.py) |
| Batch size selection                  | Python (agent.py) |
| Evict `message_id` from state         | Python (main.py) |
| Render "under-extraction" digest block | Python (process_events.py) |
| Read email body, emit event dicts     | Agent (at runtime) |
| Everything else                       | Python |

No step in the runtime agent's flow depends on `sender_stats.json`
content. The file is infrastructure, not prompt context.
