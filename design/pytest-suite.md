# Pytest suite for `scripts/process_events.py`

Roadmap item 2. Foundational: every subsequent feature that touches `process_events.py` extends this suite.

## Scope

Cover the behaviors the roadmap calls out — past-event filtering, dedupe (both passes), sort order, week grouping, event-ID stability, HTML/body rendering, subject-line construction — plus the guardrails the code already has (empty-name warnings, unknown-category warnings, field defaults, tolerant `_load_ignored_ids`, ignored-ID dropping, 60-day horizon banking).

## Layout

```
kids-schedule-github/
  requirements-dev.txt              # pytest only
  tests/
    __init__.py
    conftest.py                     # tiny helpers: fixture loader, today pin
    test_process_events.py
    snapshots/
      basic_body.txt                # pinned text snapshot
  fixtures/test/                    # test-specific inputs, separate from sample_candidates.json
    basic_mixed.json
    duplicates_exact.json
    duplicates_fuzzy.json
    past_future_banked.json
    ignored.json
    edge_cases.json
  .github/workflows/
    tests.yml                       # new: pytest on push + PR
```

`fixtures/sample_candidates.json` stays untouched — it's the dev-render demo input and should remain stable. Test fixtures live under `fixtures/test/` so I can add purpose-built inputs without bloating the demo file.

## Test list

`_event_id` stability + normalization (3 tests):
- Same inputs → same 12-char hex.
- Case / whitespace in name and child normalize to same ID.
- Different date → different ID.

`classify` paths (6 tests):
- Past date → `past` bucket.
- Empty/malformed date → `undated`.
- Date > horizon → `banked`.
- ID in `ignored_ids` → `ignored_dropped`, not in display.
- Empty name → skipped with warning mentioning "missing name".
- Unknown category → event kept, warning mentions the category string.

Field defaults (1 test): missing time/location/source normalize to `"Time TBD"` / `"Location TBD"` / `"unknown source"`.

`dedupe` (4 tests):
- Pass 1 exact: same normalized name + date → most complete kept.
- Pass 2 fuzzy: subset-token-signature collapse on same-date.
- Pass 2 preserves digit-only tokens: `"Ages 3-5"` vs `"Ages 6-8"` not merged.
- Undated events skip the fuzzy pass.

`group_by_week` (2 tests):
- Events across Mon–Sun bucket correctly; week_start is Monday.
- Same-day events sort by lowercased name.

`render_body` snapshot (1 test): pinned fixture + pinned today → exact text match against `tests/snapshots/basic_body.txt`.

`render_html` substring asserts (2 tests):
- `data-event-id` attributes and ignore-button attrs present on each event card.
- Empty-event-list fixture renders the empty-state copy and no event cards.

Subject-line format (1 test): `meta["subject"] == "Kids' Schedule — April 14, 2026"` given `today=2026-04-14`.

`_load_ignored_ids` tolerance (1 test, parametrized): missing file / malformed JSON / wrong shape all → empty frozenset; valid list of dicts with `id` → frozenset of those IDs.

CLI smoke test (1 test): end-to-end `subprocess` invocation that exercises all CLI args at once and checks stdout + the meta JSON shape. Covers `main()` plumbing without duplicating the unit tests above.

Roughly 22 tests. Starting point; the suite grows as future features extend it.

## Key decisions

- **pytest, no plugins.** Snapshot comparison is plain `assert actual == expected` against a file. Avoids dependency creep.
- **Direct function calls for most tests.** Faster, clearer failures than shelling out. One CLI smoke test covers the argparse/IO plumbing.
- **Today pinning.** The CLI already accepts `--today`; tests that call `classify` directly pass an explicit `dt.date`. No clock patching needed.
- **HTML snapshot: substring asserts, not full snapshot.** `render_html`'s footer calls `dt.datetime.now(LOCAL_TZ)`, which makes a full snapshot flaky. Monkeypatching would work but substring asserts are cheaper and clearer for the specific invariants we care about (event-id attrs, ignore-button attrs, webhook JSON escaping, empty-state copy).
- **Body snapshot IS full.** `render_body` is a pure function of inputs — no clock read — so a full-text snapshot is stable and high-signal.
- **Split requirements files.** `requirements-dev.txt` with pytest only; keeps the weekly cron from installing test deps.
- **CI workflow separate from cron.** `tests.yml` runs on `push` and `pull_request`, failing the check on test regression. Weekly cron stays in `weekly-schedule.yml`.

## Non-scope / deferred

- No type-checker or lint tooling. Out of scope; easy to add later.
- No tests for `agent.py`, `gmail_client.py`, `build_queries.py`, `update_auto_blocklist.py`, `diff_search_results.py`, or `mark_filter_audit.py`. Those modules are independent; they can get their own suites later.
- No coverage reporting. If it matters later, add `pytest-cov` then.

## Commit plan

1. Design note (this file).
2. `requirements-dev.txt` + `tests/` + `fixtures/test/` in one commit — they're cohesive and each file is useless without the others.
3. `.github/workflows/tests.yml` separately so the CI wiring is a clean reviewable change.
4. `ROADMAP.md` update: mark item 2 `[x]` with the test-commit SHA, and add a line to the session-discipline block pointing at `tests/` and `tests.yml`.
