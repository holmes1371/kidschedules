# Failure notifications via GitHub mobile app

Roadmap item 1. Goal: any real pipeline failure must exit the workflow non-zero so GitHub Actions fires a push notification to Tom's phone.

## Scope

Code-side work only. Tom enables Actions push notifications in the mobile app separately.

Two changes:

1. Make sure real failures propagate out of `main.py`.
2. Add an intentional-failure path so the notification can be tested end-to-end without waiting for something to actually break.

## Audit of current failure propagation

Most paths already do the right thing:

- `run_script` uses `subprocess.run(..., check=True)` → `CalledProcessError` propagates → `main.py` exits non-zero.
- Gmail token expiry: `creds.refresh(Request())` in `gmail_client.py::_get_credentials` raises `RefreshError`. Uncaught. Propagates.
- Missing env vars: `_get_credentials` and `agent.py::_get_client` raise `RuntimeError`. Uncaught.
- `main()` has no top-level try/except, so any uncaught exception produces a traceback + exit 1 — which is exactly what Actions needs.
- Workflow: no `continue-on-error` anywhere; subsequent steps implicitly require prior-step success.

## The one gap

`agent.py::extract_events` has this pattern:

```python
try:
    response = _call_with_retry(...)
except Exception as e:
    print(f"SKIPPING {batch_label}: {e}")
    continue
```

`_call_with_retry` already absorbs transient errors (429/500/503/529/connection/timeout) with exponential backoff up to 3 attempts. Anything that gets past the retry loop is a real failure — auth error, persistent 5xx, unexpected status. The current code swallows it and moves to the next batch. If every batch fails, `extract_events` returns `([], [])` and the pipeline publishes a near-empty page with no signal that anything went wrong.

The roadmap explicitly calls out "Anthropic 5xx" as a must-fail case. Fix: drop that `except`, let the exception propagate.

## Decisions

- **Strict propagation in `extract_events`.** Any unretriable batch failure fails the pipeline. Alternative ("only fail if every batch fails") is more tolerant but harder to reason about and hides partial degradation. Strict is simpler and matches the fail-loud principle.
- **Leave parse-failure handling alone.** `_parse_json_response` returning `None` after repair retry still logs and continues. That's a data-quality issue, not a system failure, and the pipeline can publish meaningful output with one batch's worth of malformed JSON dropped.
- **Leave `review_stripped_messages` tolerant.** Filter-audit review is non-critical. If audit review fails, publishing the schedule should still succeed — the audit just doesn't refresh this run.
- **Intentional-failure via CLI flag + workflow dispatch input.** `main.py --intentional-failure` raises immediately. Workflow adds an `intentional_failure` boolean input under `workflow_dispatch.inputs`, plumbed into the pipeline step same pattern as `dry_run`. Tom triggers it from the mobile app or GitHub UI to confirm the notification fires.
- **No custom exit codes, no logging format changes.** Python's default traceback + exit 1 is what Actions needs.

## Test plan

After merging:

1. Trigger the workflow manually from the GitHub mobile app with `intentional_failure: true`.
2. Wait for the workflow to fail.
3. Confirm the push notification arrives.
4. Flip ROADMAP item 1 from `[~]` to `[x]` with the verification commit SHA.

## Files touched

- `agent.py` — remove the `except Exception: continue` around the batch API call.
- `main.py` — add `--intentional-failure` flag handling at the top of `main()`.
- `.github/workflows/weekly-schedule.yml` — new `intentional_failure` workflow_dispatch input, plumbed into the pipeline step.
- `ROADMAP.md` — mark item 1 `[~]` after code changes, `[x]` after Tom verifies.

No changes to `scripts/process_events.py`, so the pytest-fixture rule (roadmap item 2) doesn't apply to this feature.
