# 27. Auto-blocklist hardening: one errant agent flag shouldn't permanently block a sender — 6bea35a (design + flip) / e5772cc (sender-stats reject) / 6b8c62a (auto_blocklist_state module + 27 unit tests) / 87d18f5 (main() integration + workflow plumbing + agent prompt) / ee90951 (TTL decay + audit log) / 5d914dc (close-out summary) / 4ba172b (explicit --state-file in main.py)

Filed 2026-04-24 from Tom: *"we should also tighten up the auto-block logic — one errant email shouldn't get people blocked forever. There needs to be a better logic/audit system there."* Item 26 above was the surgical fix for the family-sender failure mode (parents' addresses now in `protected_senders.txt`); item 27 is the systemic version — make the auto-block layer self-correcting so a single high-confidence misjudgment by the agent on ANY sender doesn't cause a permanent block.

Pre-fix gating in `update_auto_blocklist.py` accepted any suggestion clearing four basic gates (confidence==high, parseable address, not protected, not already in either blocklist) and immediately wrote it to `blocklist_auto.txt` as a permanent active block. The audit step (`step1b_filter_audit`) periodically diffed loose-vs-tight queries to surface false negatives but did NOT remove auto-block entries — it only recommended manual edits. Two structural gaps: no corroboration requirement, no expiry.

**Three-lever fix.** Each lever addresses a distinct failure mode; they compose cleanly.

- **Sender-stats reject (e5772cc, lever 1, gate-layer prevention):** when `update_auto_blocklist.main()` is about to add an address, it consults `sender_stats.json` (item #17 newsletter telemetry). If `messages_seen >= 3 AND total_events >= 1`, reject — the sender has produced real kid events historically, so a single irrelevance flag is almost certainly a per-email judgment, not a per-sender judgment. Cheapest lever; no new state file. Reuses the newsletter telemetry as ground-truth signal for "useful sender."
- **N-strikes pending ledger (6b8c62a + 87d18f5, lever 2, structural prevention):** new state file `blocklist_auto_state.json` with two top-level sections — `pending` (addresses flagged once, awaiting corroboration) and `active` (TTL metadata for entries actually in `blocklist_auto.txt`). First flag → pending; second flag from a *distinct* `source_message_id` → promote (add to txt, move to active section, clear pending). Same-message re-flag (`--reextract`) bumps `last_flagged_iso` but does NOT advance the strike count — defends against double-counting on operator-driven re-extraction. Promotion threshold N=2 chosen to eliminate one-shot misjudgments at the cost of one extra cron cycle (~7 days) per legitimate spam sender.
- **TTL decay (ee90951, lever 3, recovery):** active entries unflagged for 90 days expire (drop from state AND `blocklist_auto.txt` via full rewrite); pending entries unflagged for 30 days age out. Refresh-on-flag means real spammers stay blocked indefinitely; only quiet senders age out, defending against the "sender's status changed" case. `--active-ttl-days` and `--pending-ttl-days` CLI flags expose the windows for tests.

**Mid-feature scope expansion (87d18f5).** The design note assumed `irrelevant_senders` flags carried `source_message_id`, but only events did. Tom approved extending the agent prompt to require the field in each flag. `update_auto_blocklist` rejects flags missing the field as `"missing source_message_id"` (cleaner than treating empty-string as a real id, which would silently break the duplicate-flag defense).

**Auto-rescue scoped out.** The roadmap sketched a fourth lever — extend `step1b_filter_audit` to actively *remove* auto-block entries whose loose-query results contain real kid events. Deferred: N-strikes prevents the same one-shot misjudgments structurally and earlier; auto-rescue would entangle the audit with production-state mutation. New ROADMAP item if v1 telemetry shows the gap matters.

**Workflow + parity.** `blocklist_auto_state.json` added to `.github/workflows/weekly-schedule.yml`'s state-branch restore + save blocks; `tests/test_workflow_state_branch_parity.py::PERSISTENT_STATE_FILES` extended by one. Pre-existing `blocklist_auto.txt` rows get a synthetic `last_flagged_iso = today` on first post-deploy run via `seed_active_from_legacy` so TTL counts cleanly from deploy day.

**Audit log extension (additive).** `blocklist_auto_audit.jsonl` per-run records gained seven new buckets — `promoted`, `pending_added`, `active_refreshed`, `duplicate_flag`, `resolved_by_main_blocklist`, `expired`, `aged_out`. Existing `added`/`rejected` shape preserved (= promoted + rejected respectively) for backward compat with prior log readers.

**Tom hygiene fix (4ba172b).** Caught post-feature: `main.py::step3b_update_auto_blocklist` was relying on `update_auto_blocklist.py`'s `--state-file` default rather than passing it explicitly, the only path argument doing so. Added `AUTO_BLOCKLIST_STATE_PATH` constant and forwarded it explicitly. No behavior change; consistency hygiene.

**Test coverage.** +43 net (3 sender-stats integration + 27 state-module unit + 6 main()-flow integration + 7 TTL integration). 0 new failures vs main.

**Live verification.** Tom verified post-deploy that the cron correctly seeded legacy entries, new flags entered pending instead of jumping straight to active, and the second-flag promotion path fired across cron cycles. Tom signed off 2026-04-25.

**Follow-up filed (item 28).** Caught during item-27 verification — the Ignore-sender button was rendering for protected freemail addresses. Logical sister bug; landed and verified the same day.

Full design record at `design/auto-blocklist-hardening.md`.
