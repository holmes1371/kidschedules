# Dedupe candidate messages before agent extraction

ROADMAP item #21. Filed 2026-04-17 (session 10) after Tom spotted a single dance-studio thread producing four hits (`[31/66]`, `[32/66]`, `[35/66]`, `[36/66]`) in the extractor input — and a Reverb Dance Comp reminder producing two more. Agent cost scales with candidate count, so redundant candidates are real spend.

## Problem — and a correction to the ROADMAP entry

The ROADMAP entry originally pitched the fix as "insert a dedup pass keyed on Gmail `messageId`". Discovery showed that dedup already exists — `main.py::step2b_read_promising` has a `seen_ids: set[str]` guard (lines 264–273 at `56dee4e`) that collapses messageId duplicates across the five query result sets before the read_message loop. So by the time the `[i/N]` banner prints, the input is already messageId-unique.

The four dance-studio hits are therefore *four distinct messages in the same thread* — replies to the original "First dibs on Recital TICKETS…" with different `messageId`s but the same `threadId`. Adding a second messageId-level pass would compress nothing.

The actual cheap fix is **threadId-level dedup** — the "slightly more ambitious" option named in the same ROADMAP entry. Gmail's search stub (see `gmail_client.py` lines 95 and 105) already carries `threadId` and the `Date` header, so we have what we need without a schema change.

## Decisions locked in

- **Collapse by `threadId`, keep the latest message per thread.** Most reminder threads restate the operative date/decision in the latest reply; by discarding earlier replies we save agent cost and a `read_message` Gmail-API call per dropped stub. Tom's explicit policy call 2026-04-17: "assume the most recent message has the most relevant information".
- **Sort key is the parsed stub `Date` header.** `email.utils.parsedate_to_datetime` handles RFC 2822; a parse failure or missing header falls back to first-seen order within the thread group (i.e. the earlier-encountered stub wins). No attempt to reach into the full message body for a better timestamp; the stub header is good enough and is already available pre-fetch.
- **Dedup happens in `step2b_read_promising`, after the existing messageId pass, before the `read_message` loop.** Placement choice matters: dropping before the body fetch saves Gmail API calls too, not just the agent step. The helper itself is pure (no I/O) so it's unit-testable without mocks.
- **No cache marking for dropped stubs.** Messages dropped by thread dedup are not recorded in `events_state.processed_messages`. On the next run Gmail returns them again, step2b redrops them cheaply. The alternative (mark-as-processed for dropped) complicates the model — "un-mark when a newer reply arrives and becomes the new latest" is finicky and brittle — for a cost saving that's already small because step2b dedup is cheap.
- **Missing `threadId` → kept as-is.** Safe passthrough. Gmail always returns `threadId` in practice (even for a single-message thread each message has its own threadId equal to its messageId), but the helper doesn't assume this; a stub with empty/missing threadId bypasses the group and is preserved.
- **Orthogonal to the agent-prompt "emit duplicates, downstream dedupes" instruction.** The agent still sees only the surviving (latest-per-thread) message from each collapsed thread. Cross-thread duplicate events continue to reach `process_events.py` and get collapsed there; that path is untouched.

## Accepted risk

Latest-per-thread drops earlier replies. If an earlier message in a thread carries a detail the latest doesn't, that detail won't be extracted on this run. Two things make this acceptable:

1. **Prior-run events persist.** When an earlier reply was new, it was extracted as new-message fodder on its own run; the events it yielded live in `events_state.events` and keep rendering for the full 120-day GC window regardless of what the current run does with the thread.
2. **`--reextract <messageId>` is the escape hatch.** If a digest's outlier-alerts block or a missing item on the page suggests a thread's latest reply was too thin, Tom re-runs the pipeline with the specific messageId forced back through the agent; the thread dedup still runs but the forced message is preserved by the cache-eviction path (see `_reextract_eviction`).

## Helper contract

`_dedupe_by_thread(stubs: list[dict]) -> list[dict]` — pure function, lives in `main.py` alongside the other step-helpers. Contract:

- Input is a list of Gmail search stub dicts, each shaped as produced by `GmailClient.search_messages`: `{"messageId": ..., "threadId": ..., "snippet": ..., "headers": {"From": ..., "Subject": ..., "Date": ...}}`.
- Output is a list of the same shape, containing at most one stub per distinct `threadId`.
- For each threadId group: pick the stub with the latest parsed `Date`. Ties (equal parsed datetime) break on first-seen-order. Unparseable/missing `Date` on a stub makes it lose any comparison against a parseable peer; if every stub in the group is unparseable, first-seen wins.
- Stubs with empty/missing `threadId` are passed through as-is (no grouping, never dropped).
- Output preserves the first-seen order of the *surviving* representative per thread — i.e. if thread A's winner was first encountered before thread B's winner, A appears first in the output.
- Never raises. Malformed inputs are tolerated per the posture elsewhere in `main.py` (warn in log if useful, never crash the pipeline over a weird stub).

## Wiring

```python
# main.py::step2b_read_promising, after the existing seen_ids loop,
# before the read_message loop:

print(f"  Collected {total_stubs} stub(s) across {len(search_results)} queries")
print(f"  Unique messageIds: {len(emails_to_read)}")
emails_to_read = _dedupe_by_thread(emails_to_read)
print(f"  After thread dedup: {len(emails_to_read)}")
```

The `[i/N]` banner inside the `read_message` loop already uses `len(emails_to_read)`, so it reports the post-thread-dedup count without a separate change. The one pre-existing line `Unique messages to read: {N}` is replaced by the three new lines above; it was a slight misnomer anyway (those were unique *messageIds*, not unique messages-to-read after all the dedup we now do).

`total_stubs = sum(len(v) for v in search_results.values())` — a one-line sum; no new state kept.

## Logs

Structured so the funnel is visible in the Actions log:

```
STEP 2b: Reading full message bodies
  Collected 88 stub(s) across 5 queries
  Unique messageIds: 66
  After thread dedup: 53
  [1/53] Subject line one ...
  [2/53] Subject line two ...
```

Regression signal: a future change that makes thread dedup a no-op (e.g. if the helper accidentally keyed on messageId instead of threadId) would show identical counts on lines 2 and 3, visible in the next live run.

## Pytest additions

`tests/test_main.py` grows a new section for `_dedupe_by_thread` (7 tests):

- `test_dedupe_by_thread_empty_input` — `[]` in, `[]` out.
- `test_dedupe_by_thread_no_collisions` — each stub in its own thread, output equals input.
- `test_dedupe_by_thread_latest_wins` — two stubs same thread, clear Date ordering; later survives.
- `test_dedupe_by_thread_tiebreaker_first_seen` — two stubs same thread, same parsed Date; first-seen wins.
- `test_dedupe_by_thread_missing_threadid_passthrough` — two stubs with empty threadId both survive.
- `test_dedupe_by_thread_malformed_date_falls_back` — same-thread stubs where the later-seen one has an unparseable Date; the parseable one survives even though it's earlier in encounter order (unparseable loses to parseable).
- `test_dedupe_by_thread_all_malformed_first_seen_wins` — same-thread stubs, all with unparseable Date; first-seen wins.

Plus one integration-level test for the wiring:

- `test_step2b_thread_dedup_collapses_multi_query_thread` — synthetic `search_results` with four stubs sharing a threadId spread across three of the five query categories (simulating the dance-studio case); stubs out the `GmailClient.read_message` call; asserts exactly one `read_message` call and one survivor in the returned full_emails list. (Reuses the `_email` / monkeypatch patterns in the existing `step2b` tests.)

Test count delta: +8 (7 unit + 1 integration).

## Responsibility table

| Concern | Python | LLM (agent.py) |
|---|---|---|
| Fetch stubs from Gmail queries | ✅ `gmail_client.search_messages` | — |
| MessageId-level dedup across queries | ✅ `step2b_read_promising` seen_ids loop (unchanged) | — |
| ThreadId-level dedup / latest-per-thread pick | ✅ `_dedupe_by_thread` (new) | — |
| Parse `Date` header for ordering | ✅ `email.utils.parsedate_to_datetime` | — |
| Fetch full body for survivors | ✅ `gmail_client.read_message` | — |
| Extract events | — | ✅ judgment (unchanged) |
| Event-level dedup across emails | ✅ `process_events.classify` (unchanged) | — |

No new LLM calls; no agent-time judgment added.

## Commit plan

1. **Design note + ROADMAP flip.** This commit. Adds this note, flips `ROADMAP.md` #21 `[ ]` → `[~]`. No behavior change.
2. **`_dedupe_by_thread` helper + unit tests.** Pure function in `main.py`; 7 unit tests in `tests/test_main.py`.
3. **Wiring + integration test.** `step2b_read_promising` calls the helper; three new log lines replace `Unique messages to read: {N}`; 1 integration test.
4. **Close-out.** After Tom signs off (next session), move prose to `COMPLETED.md`, flip `[~]` → `[x]`, record the three feature SHAs.

## Non-goals

- **Cross-thread duplicate-event dedup.** That's `process_events.classify`'s job and already works.
- **Collapse by subject-line similarity.** Threading is already Gmail's job; piggybacking on `threadId` is strictly more reliable than heuristic subject grouping.
- **Preserve both first and last message in a thread.** Rejected in favor of simplicity + Tom's explicit policy call. `--reextract` covers the edge case.
- **Mark dropped stubs as processed in the cache.** Small constant re-fetch cost per run; revisit only if Gmail quota becomes visible.
- **Per-query dedup reporting.** The three-line funnel (stubs / unique messageIds / after thread dedup) is enough; a per-query breakdown would be log noise.
- **Knob to disable thread dedup.** No. If the behavior is wrong for a specific message, `--reextract` handles it; adding a disable flag would mean supporting it forever.
