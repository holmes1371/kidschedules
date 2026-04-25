# Auto-blocklist hardening: N-strikes + sender-stats + TTL decay

ROADMAP item #27. Filed 2026-04-24 alongside the close-out of item #26: *"we should also tighten up the auto-block logic — one errant email shouldn't get people blocked forever. There needs to be a better logic/audit system there."*

Item #26 was the surgical fix for the family-sender failure mode (parents' addresses now in `protected_senders.txt`). Item #27 is the systemic version: make the auto-block layer self-correcting so a single high-confidence misjudgment by the agent — on **any** sender, not just parents — doesn't cause a permanent block.

## Goal

Tom's framing (2026-04-25): *"the idea of this auto-block is so it can get smarter over time; blocking emails that never have anything to do with kids, but also lenient enough that one bad email doesn't get blocked for life."*

The precision/recall tradeoff is asymmetric: a false-positive block costs us silent missed events (the Ellen-tax-email pattern); a false-negative block costs us an extra agent call on a noise email. False positives are far more expensive, so the gating biases toward leniency.

## Why the current gating is insufficient

`update_auto_blocklist.py` (post-#26) runs four gates: `confidence == "high"`, parseable address, not protected, not already in the blocklist union. Any suggestion clearing all four becomes a permanent active block on first sight. The audit step (`step1b_filter_audit`) periodically diffs loose-vs-tight queries to surface false negatives but it does NOT remove auto-block entries — it only recommends manual edits, and even those target `blocklist.txt`, not `blocklist_auto.txt`.

Two structural gaps:

1. **No corroboration requirement.** One agent flag, one block, forever.
2. **No expiry.** Even if a sender's email shape changes — child enrolls in a new program, sender starts emitting kid signal — the block stays.

## Decision: three layered levers

Each lever addresses a distinct failure mode. They compose cleanly: sender-stats prevents blocks on senders that are already known-useful; N-strikes prevents one-shot misjudgments on unknown senders; TTL prevents perpetual blocks of senders whose status changes over time.

### 1. Sender-stats reject (prevention, gate-layer)

Reuse the existing `sender_stats.json` (item #17 newsletter-detection telemetry). When `update_auto_blocklist.main()` is about to add an address, it consults sender_stats keyed by the lowercased address. If `messages_seen >= 3 AND total_events >= 1`, reject the suggestion: this sender has produced real kid events historically, so a single irrelevance flag is almost certainly a per-email judgment, not a per-sender judgment.

Cheapest of the three levers. No new state file. Directly answers "block emails that never have anything to do with kids" — sender-stats is the ground-truth signal for "has this sender ever produced a kid event."

### 2. N-strikes pending ledger (prevention, structural)

A new state file `blocklist_auto_state.json` holds two sections:

- **`pending`**: addresses that have been flagged once but haven't been promoted to active block yet. Keyed by lowercased address; values carry `first_flagged_iso`, `last_flagged_iso`, `flagged_message_ids: [...]`, `reason_samples: [...]`.
- **`active`**: TTL metadata for addresses that ARE in `blocklist_auto.txt`. Keyed by lowercased address; values carry `added_iso`, `last_flagged_iso`, `reason`.

First flag → entry in `pending`. Second flag from a *distinct* `message_id` → promote (add to txt, move to active section, clear from pending). A repeat flag from the same `message_id` (e.g. via `--reextract`) does NOT promote — the strike count is unique-message, not unique-flag.

Promotion threshold N=2 chosen because it eliminates one-shot misjudgments at the cost of one extra cron cycle (~7 days) before a legitimate spam sender is blocked. N=3 doubles that wait without much added precision.

### 3. TTL decay (recovery)

After the suggestion-processing loop in `main()`, a TTL pass:

- **Active entries** with `last_flagged_iso` older than 90 days: drop from the txt and from the `active` section. Audit log records `expired`.
- **Pending entries** with `last_flagged_iso` older than 30 days: drop from `pending` (they never got their second flag in time). Audit log records `aged_out`.

Active TTL is generous — a real spammer keeps emitting and gets re-confirmed, refreshing `last_flagged_iso`. A sender that goes quiet for three months is allowed to fall off, defending against the "sender's status changed" case. Pending TTL is shorter because pending entries are "watching, not blocking" — if no corroboration arrives within a month, drop the suspicion rather than keep it indefinitely.

**Refresh-on-flag**: any fresh flag on an active-blocked address bumps its `last_flagged_iso` to today. This means an actual spammer never expires; only senders that go quiet do.

### Deferred: auto-rescue via filter audit

The roadmap sketches a fourth lever — extend `step1b_filter_audit` to actively *remove* auto-block entries whose loose-query results contain real kid events. Out of scope for v1 because:

- N-strikes prevents the same one-shot misjudgments that auto-rescue would catch, structurally and earlier.
- Auto-rescue requires the audit to take action on production state (currently advisory-only) and to coordinate with `agent.review_stripped_messages`. Higher complexity.
- The failure mode auto-rescue *uniquely* fixes — a sender that got two corroborated agent flags despite producing kid mail elsewhere — is rare given N-strikes already corroborates.

Filed as a follow-up to revisit if v1 telemetry shows blocked senders with real kid events leaking through. Will become a new ROADMAP item if needed; not bundled into 27.

## Decisions locked in

- **Combined state file `blocklist_auto_state.json`** rather than two separate files for pending and active metadata. One file, two top-level keys (`pending`, `active`); single load/save pair; one new entry in workflow state-branch plumbing instead of two.
- **`blocklist_auto.txt` format unchanged.** TTL metadata lives in the JSON sidecar, not in inline comments. Existing `build_queries.load_blocklist` parses the txt without modification.
- **Pending entries are NOT in `blocklist_auto.txt`.** Pending means "watching," not "blocking"; they don't appear in the Gmail exclusion clause. Promotion is the moment they enter the txt.
- **Promotion threshold N=2.** First flag → pending; second distinct-message flag → promote.
- **Sender-stats threshold: `messages_seen >= 3 AND total_events >= 1`.** Reuses `newsletter_stats.PROMOTION_MIN_MESSAGES` for the message count and adds an event-existence guard. Three observed messages with at least one event = "useful sender."
- **TTL: 90 days active, 30 days pending.** Active is generous enough to ride out summer breaks; pending is short enough to keep the ledger from growing unbounded.
- **Refresh-on-flag for active entries.** Any new flag on an active-blocked address resets its TTL clock. Real spammers stay blocked indefinitely; quiet senders age out.
- **`blocklist.txt` (hand-curated) does not participate.** None of the levers touch hand edits. Same posture as today.
- **Pre-existing `blocklist_auto.txt` entries get a synthetic `last_flagged_iso = today` on first run after deploy.** No explicit migration step; the loader materializes missing entries with today's date so the TTL clock starts fresh post-deploy.
- **Audit log extension is additive.** New event-type strings: `pending_added`, `pending_promoted`, `pending_aged_out`, `active_refreshed`, `expired`, `rejected_by_sender_stats`. Existing readers ignore unknown keys.
- **Same-message re-flag does not promote.** N-strikes counts distinct `message_ids`, not distinct flags. `--reextract`-driven re-flagging stays at strike count = 1.
- **Hand-blocked address while pending → drop the pending entry without promotion.** If Tom adds an address to `blocklist.txt` while it's in pending, the next cron's load step finds it in the union of blocklists and clears the pending entry as resolved.
- **`reason_samples` capped at 3 entries per pending entry** to prevent unbounded growth on a sender that gets repeatedly flagged but never reaches N=2 (e.g. one-message-only flags from a high-volume newsletter that always rotates `message_id`).

## Accepted risk

- **One extra cron cycle (~7 days) before a real spam sender is blocked.** First flag goes to pending; the second flag (next week's run) promotes. Cost: a handful of extra agent calls on the same noisy sender across the wait. Bounded.
- **Senders that legitimately stop sending for ~3 months get evicted from the auto-block.** If they resume sending kid-irrelevant noise, they re-enter pending on the next flag. Cost: one cron cycle of agent processing on the resumed-sender's first email. Expected to be rare.
- **`sender_stats.json` is required state for the sender-stats lever.** A wiped or corrupt `sender_stats.json` silently disables that lever (the gate falls through to N-strikes). Same warn-and-fall-back posture as the existing newsletter-stats consumers; tolerable.
- **Auto-rescue gap.** A sender that gets two distinct flags despite producing kid mail elsewhere stays blocked. N-strikes is necessary but not sufficient. Auto-rescue (deferred) is the second-line defense for this case. v1 telemetry will show whether it matters.
- **`blocklist_auto_state.json` schema drift.** A future change that renames a key without bumping `schema_version` could silently start a fresh state. Mitigation: schema-version check in load (warn-and-fall-back like newsletter-stats); test pinning the round-trip.
- **Pre-deploy entries' synthetic `last_flagged_iso = today`.** Existing entries get 90 days of additional life from deploy day even if they were added a year ago. Acceptable: alternative is a heuristic seed date that's always wrong; deferring expiry by 90 days post-deploy is one-time and predictable.

## Module / contract changes

### New: `scripts/auto_blocklist_state.py`

Pure helpers; file I/O only in load/save:

- `load_state(path) -> dict` — schema-version-checked, warn-and-fall-back on corruption. Returns the empty-state dict on missing/corrupt/wrong-version.
- `save_state(path, state, now_iso) -> None` — atomic via tempfile + `os.replace`.
- `add_or_promote(state, addr, message_id, reason, today, *, already_active, already_in_main_blocklist) -> str` — returns one of `"pending_added"`, `"pending_promoted"`, `"active_refreshed"`, `"duplicate_flag"`, `"resolved_by_main_blocklist"`. Mutates `state` in place. Caller separately appends promoted addresses to `blocklist_auto.txt`. The `already_active` and `already_in_main_blocklist` flags are computed by the caller from the txt files; the state module doesn't read them itself.
- `tick_ttl(state, today, *, active_ttl_days=90, pending_ttl_days=30) -> dict` — returns `{"expired": [addr, ...], "aged_out": [addr, ...]}` and prunes the corresponding entries from `state`. Caller separately removes expired addresses from `blocklist_auto.txt`.
- `seed_active_from_legacy(state, txt_addresses, today) -> int` — for any address in the txt that has no `active` entry, seed one with `added_iso = last_flagged_iso = today` and `reason = "legacy entry seeded post-deploy"`. Returns the count seeded. Idempotent.

### Modified: `scripts/update_auto_blocklist.py`

`main()` grows three optional flags and two new gates:

- `--state-file` (default `blocklist_auto_state.json`) — path to the new state file.
- `--sender-stats` (default `sender_stats.json`) — path to read sender history.
- `--active-ttl-days` (default 90), `--pending-ttl-days` (default 30) — overridable for tests.

Suggestion-processing flow becomes: existing four gates (confidence, address shape, protected, dedup) → **new gate**: sender-stats reject → **new gate**: pending-vs-promote via `auto_blocklist_state.add_or_promote`. After the loop, `auto_blocklist_state.tick_ttl` runs. Promoted-and-expired diffs drive both the txt rewrite and the audit log.

`blocklist_auto.txt` becomes rewrite-on-change rather than append-only — TTL expiry needs full rewrite. `_AUTO_HEADER` preserved at the top of the rewritten file.

### Modified: `.github/workflows/weekly-schedule.yml`

`blocklist_auto_state.json` added to:

- the restore block (alongside `blocklist_auto.txt` and `blocklist_auto_audit.jsonl`)
- the save block

### Modified: `tests/test_workflow_state_branch_parity.py`

`PERSISTENT_STATE_FILES` constant grows by one entry: `blocklist_auto_state.json`. Existing parity assertions catch the new file in both restore and save blocks.

## Tests

### `tests/test_auto_blocklist_state.py` (new)

- `test_load_state_missing_file_returns_empty`
- `test_load_state_corrupt_json_returns_empty_with_warning`
- `test_load_state_schema_version_mismatch_returns_empty_with_warning`
- `test_save_state_round_trips`
- `test_save_state_stamps_last_updated_iso`
- `test_add_or_promote_first_flag_lands_in_pending`
- `test_add_or_promote_second_flag_distinct_message_promotes`
- `test_add_or_promote_second_flag_same_message_is_duplicate`
- `test_add_or_promote_third_flag_after_promotion_refreshes_active`
- `test_add_or_promote_appends_message_id_to_pending`
- `test_add_or_promote_caps_reason_samples_at_3`
- `test_add_or_promote_resolves_pending_when_in_main_blocklist`
- `test_tick_ttl_expires_active_after_90_days`
- `test_tick_ttl_does_not_expire_active_inside_window`
- `test_tick_ttl_ages_out_pending_after_30_days`
- `test_tick_ttl_does_not_age_out_pending_inside_window`
- `test_tick_ttl_returns_expired_and_aged_out_lists`
- `test_seed_active_from_legacy_idempotent`
- `test_seed_active_from_legacy_count`

### `tests/test_update_auto_blocklist.py` (extend existing)

- `test_main_rejects_when_sender_stats_show_useful_sender`
- `test_main_does_not_reject_when_sender_stats_below_message_threshold`
- `test_main_does_not_reject_when_sender_stats_total_events_zero`
- `test_main_first_flag_lands_in_pending_not_active`
- `test_main_second_flag_distinct_message_promotes_to_active`
- `test_main_active_refreshed_on_repeat_flag`
- `test_main_ttl_expires_active_entry`
- `test_main_ttl_ages_out_pending_entry`
- `test_main_audit_log_records_new_event_types`
- `test_main_state_file_round_trips_through_main`
- `test_main_hand_blocked_address_clears_pending`
- `test_main_synthetic_last_flagged_for_legacy_active_entries`

### `tests/test_workflow_state_branch_parity.py` (extend)

- `PERSISTENT_STATE_FILES` constant grows by one; existing parity assertions catch the new file in both restore and save blocks.

### Existing tests that need updating

- The `main()`-flow tests in `tests/test_update_auto_blocklist.py` that pin the current "first flag → active block" behavior now expect "first flag → pending." Update assertions to reflect the new semantics. Estimated 6–8 test-method updates.

Total: **~21 new tests + ~6–8 updated**. Net +25–28 in coverage on the gating layer.

## Commit plan

1. **Design note + ROADMAP item 27 flip** to `[~]`. This commit. No behavior change.
2. **Sender-stats reject** in `update_auto_blocklist.py` + 3 tests. Smallest contained gate; lands as standalone hardening even before the pending module arrives.
3. **`scripts/auto_blocklist_state.py` module** (pure helpers) + `tests/test_auto_blocklist_state.py` (~19 unit tests). No integration yet.
4. **`update_auto_blocklist.py` integrates the state module** + workflow state-branch plumbing + parity-test update + extended `tests/test_update_auto_blocklist.py` integration tests. The behavior switch from "first flag → active" to "first flag → pending" lands here.
5. **TTL decay** in `auto_blocklist_state.tick_ttl` + integration in `update_auto_blocklist.main()` + audit log event types + tests. Splits cleanly from #4 because pending+active works without expiry; TTL is additive.
6. **Session summary update** + close-out. Item stays `[~]` pending Tom's live-cron verification across at least two weeks (so a pending → promote cycle can be observed end-to-end).

State-branch cleanup is not required for this feature — the existing `blocklist_auto.txt` entries will get synthetic `last_flagged_iso = today` on first post-deploy run via `seed_active_from_legacy`.

## Non-goals

- **Auto-rescue via filter audit.** Deferred; new ROADMAP item if v1 telemetry shows it's needed.
- **Per-sender TTL configuration.** All active blocks expire on 90 days; all pending on 30. Per-sender custom TTLs add config burden without clear value.
- **Operator UI for inspecting pending entries.** The state file is human-readable JSON; `cat blocklist_auto_state.json | jq` is the inspection path. Not worth a dedicated CLI.
- **Migrating `blocklist.txt` (hand-curated) to TTL.** That file is the operator's contract; TTL on it would be surprising. Out of scope.
- **Special-case logic for "this active entry has nonzero stats."** Sender-stats reject prevents the entry from being added in the first place; if it's already there, expiry will eventually remove it. No retroactive eviction.
- **Promoting on N=3+.** Single-flag → pending; second-flag → promote. Tighter precision via N=3 not adopted; v1 telemetry can revisit.
- **Migrating the existing `blocklist_auto_audit.jsonl` schema.** New event types are additive; old entries remain readable; no version bump needed.
