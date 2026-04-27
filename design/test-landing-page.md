# Separate test landing page for `workflow_dispatch` QA runs

ROADMAP item #23. Today every workflow run — cron AND manual
`workflow_dispatch` — overwrites `docs/index.html`, the page Ellen
sees. Manual runs that exist purely to verify a fix put experimental
output in front of her until the next cron tick rebuilds. The pipeline
needs a way to route test builds to a separate path so the production
page stays untouched.

**Complexity: medium.** Touches three layers (workflow YAML, main.py
orchestration, process_events.py rendering) and introduces a new
artifact-preservation step (curl live prod into the upload artifact).
Each layer is small, but the artifact-preservation detail (see Q1
below) is non-obvious and load-bearing.

## Resolved decisions (2026-04-27 with Tom)

- **UI: a new `test_output` checkbox in the existing `workflow_dispatch`
  inputs.** Default false. When false, the manual run behaves
  identically to a cron tick (full pipeline, full state writes,
  updates Ellen's `/index.html`) — that path stays useful for "I just
  landed a feature, push it now without waiting for the next 6:15 ET
  cron." When true, the entire run is sandboxed: writes to
  `docs/testpage.html`, no production state mutations anywhere.
- **Single global toggle, no carve-outs.** When `test_output=true`
  the run skips ALL persistent writes: events_state.json (#4),
  prior_events.json (#13), sender_stats.json (#17), all auto-blocklist
  writes (#27), .filter_audit.json (#2), the state-branch push step,
  the Gmail digest draft (#3, #10). Reads still happen — the test
  page reflects current Gmail / current Apps Script ignored+completed
  state — but nothing the run does survives the run. Carving out
  exceptions ("write the cache but not the draft") is a footgun: a
  single missed exception silently corrupts the next prod run.
- **Test page filename: `docs/testpage.html`** (not `docs/test/index.html`).
  Single file is simpler than a subdirectory — no nested `test/ics/`
  to manage, URL `/testpage.html` is straightforward, and the artifact
  layout stays flat.
- **Action buttons are inert on the test page.** Tom confirmed
  ignore/complete buttons don't need to be functional — those
  features are tested elsewhere; the test page is for verifying
  rendering / extraction / data-shape changes. Mechanism: pass
  `--webhook-url ""` in test mode, which trips the existing
  dev/preview short-circuit at process_events.py:2125 (POST handler)
  and 2427 (refresh-fetch handler from #34). No new code path —
  reusing the empty-webhook gate that already exists for local
  `dev_render.py` runs.
- **Banner on the test page.** Vivid red bar at the top of the page
  body, plain text: "🧪 TEST PAGE — Manual QA build, not live data.
  Card actions disabled. Trigger another QA run to refresh."
  Rendered server-side conditional on `--output-target test`.
- **Cron runs are not affected.** The toggle only exists in the
  `workflow_dispatch` path; the cron schedule fires the workflow
  with empty `inputs`, default `test_output=false`. Verified by
  reading `weekly-schedule.yml` — `github.event.inputs.test_output`
  is unset on a schedule trigger and the gate's
  `'true'`-string comparison is false.

## Architecture

Test-output run, end to end:

```
1. Checkout main           → docs/.nojekyll only (index.html not tracked)
2. Restore state branch    → events_state.json etc. (READ-only this run)
3. Sync Apps Script lists  → ignored/completed/ignored_senders (READ-only)
4. Run main.py --test-output:
   - Full pipeline runs (Gmail search, agent extract, render)
   - Skip all es.save_state / ns.save_stats / prior_events writes
   - Skip step3b auto-blocklist update
   - Force should_create_draft → False (no Gmail draft)
   - Pass --output-target test + --webhook-url "" to process_events.py
5. process_events.py:
   - In test mode: writes docs/testpage.html (not docs/index.html)
   - Renders banner block at top of body
   - Inert buttons via empty WEBHOOK_URL (existing dev/preview path)
   - Skip --ics-out-dir (test page's .ics buttons are inert anyway)
6. Pre-upload curl-prod step (NEW):
   - curl https://<pages-url>/index.html → docs/index.html
   - Scrape <a href="ics/..."> from that HTML, curl each → docs/ics/
   - Failures here are non-fatal (log warning, proceed)
7. Skip state-branch push step (test_output=true gate)
8. upload-pages-artifact uploads docs/{.nojekyll, index.html (preserved
   prod), testpage.html (test build), ics/*.ics (preserved prod)}
9. Deploy → live site has BOTH /index.html (Ellen, unchanged) AND
   /testpage.html (Tom's QA target)
```

The curl-prod step is the load-bearing piece: without it,
`actions/deploy-pages`'s full-replace deploy would wipe Ellen's
prod page when the test artifact is uploaded.

## Design Q&A

**Q1 — Why curl prod instead of committing `docs/` to a branch?**
Two alternatives considered:
- Commit `docs/index.html` + `docs/ics/` to the `state` branch and
  restore on every workflow run. Most durable (no runtime dependency
  on the live URL being reachable). Cost: state-branch maintenance
  surface grows from 7 files to potentially dozens (one ICS per
  event), and the cron run gains a "save docs to state branch" step.
- Re-render prod ALSO in test-output mode without state writes.
  Cleanest in-isolation but means manual `test_output=true` runs
  STILL refresh what Ellen sees — contradicts Tom's "doesn't land
  on the main page Ellen sees" intent.

Curl-prod is the lightest of the three: zero new state-branch files,
zero pipeline duplication. The runtime dependency on the live URL
is fine in practice — Pages's CDN is generally reachable from
Actions runners. Failure mode is a brief 404 on Ellen's `/index.html`
between a failed test-output run and the next cron tick; Pages
recovers automatically on the next cron deploy. Not catastrophic.

**Q2 — What if the pages URL is wrong / missing / unreachable?**
`pages_url.txt` already exists in the repo root (read by
`main.py::_load_pages_url`); empty / missing / 404 / network-fail all
degrade gracefully:
- `curl --fail --max-time 30` returns non-zero → log a warning, write
  no `docs/index.html`, proceed.
- The artifact then ships without an `index.html`. The test page
  still deploys at `/testpage.html` (the `test_output=true` happy
  path). Ellen's prod page briefly 404s.
- Next cron tick (≤ 2 days) fully rebuilds prod. Self-healing.

The curl is best-effort. Not silently swallowing the failure: a
visible warning lands in the run log so Tom can spot it and trigger
a non-test manual run if needed.

**Q3 — ICS file scraping reliability.** The prod page renders
`<a href="ics/<eventid>.ics">` per event. The pre-upload step greps
those hrefs out of the just-fetched `docs/index.html`, dedupes, and
curls each one in a loop. Worst cases:
- Empty list (page has no events) → no curls, fine.
- 404 on an individual ICS file → log + continue (one stale
  download link is acceptable).
- Page format ever changes the href shape → grep returns nothing,
  no curls, ICS download links 404 until next cron. Annoying but
  recoverable; the failure surfaces in the workflow log.

Not investing in a manifest file or structured ICS list. The grep is
1–2 lines of bash and the failure mode is bounded.

**Q4 — Why not test_output=true also skip the upload-pages-artifact
+ deploy steps?** Because the whole point is for Tom to view
`/testpage.html` after the run. Skipping deploy means no test page.
The deploy is fine to run; the artifact is what we control to keep
prod intact.

**Q5 — Persistence of the testpage between runs.** Each test_output
run writes a fresh `docs/testpage.html` and the curl preserves
`docs/index.html`. There is no mechanism that preserves a PRIOR
test page across runs — each test run starts from "no testpage in
artifact" and writes its own. Stale-tab risk is mitigated by:
- Banner copy ("Trigger another QA run to refresh") so a long-stale
  tab tells the user.
- Test page is overwritten on every test_output run, so iterating
  on a fix produces a current testpage every time.

Between cron rebuilds, a previously-deployed testpage from the
last test run STAYS at `/testpage.html` (because the cron rebuild
wouldn't curl-preserve it — cron writes only `index.html`). Tom
gets a stale testpage at `/testpage.html` until the next test
run overwrites it. Fine — banner copy already warns. If this
becomes annoying in practice we can add a "delete testpage on
prod runs" step later.

**Q6 — Interaction with other items.**
- `#3` weekly digest draft: forced off in test mode regardless of
  `--create-draft`. Already gated by `should_create_draft`; we add
  test_output as another guard.
- `#4` events_state cache: read freely on test runs (so the test
  page renders far-future banked events from prior cron runs);
  `es.save_state` is skipped at the end. The (small) cost is that
  agent extractions performed during a test run are not saved —
  the next prod cron will re-extract those messages. Accepted.
- `#13` prior_events / NEW badges: `prior_events.json` is restored
  but never written by a test run, so the badge diff is unaffected.
- `#27` auto-blocklist: `update_auto_blocklist.py` is skipped
  entirely on test runs (step3b short-circuits). Audit log doesn't
  log the empty-suggestions line, breaking the "one line per run"
  invariant only on test runs (which aren't supposed to be visible
  in the audit log anyway). Accepted.
- `#32` complete/uncomplete sheet writes: blocked client-side via
  `WEBHOOK_URL=""`; no POSTs from the test page can reach Apps
  Script.
- `#34` cross-device fetch: same `WEBHOOK_URL=""` short-circuit at
  process_events.py:2427. Test page is frozen at SSR time, no
  refresh-time syncs.

## Test fixtures

`tests/test_workflow_test_output_gate.py` (new file, mirrors the
existing `test_workflow_cron_gate.py` pattern — text-parse the YAML,
no PyYAML dep):

- `test_workflow_has_test_output_input` — the `workflow_dispatch`
  block declares an input named `test_output`, type `boolean`,
  default `false`.
- `test_workflow_test_output_forwarded_to_main` — the run-pipeline
  step's command line includes a conditional `--test-output` arg
  gated on `inputs.test_output == 'true'`.
- `test_workflow_state_branch_push_skipped_in_test_mode` — the
  "Save persistent state" step's `if:` clause includes a
  `inputs.test_output != 'true'` condition.
- `test_workflow_curl_prod_step_present_in_test_mode` — a step
  whose name mentions "preserve" or "curl" runs only when
  `inputs.test_output == 'true'`.

`tests/test_main.py` (additive):

- `test_test_output_skips_es_save_state` — main(--test-output) does
  not call `es.save_state`.
- `test_test_output_skips_prior_events_save` — does not write
  `prior_events.json`.
- `test_test_output_skips_sender_stats_save` — does not write
  `sender_stats.json`.
- `test_test_output_skips_step3b_auto_blocklist` — does not invoke
  `update_auto_blocklist.py`.
- `test_test_output_forces_create_draft_off` —
  `should_create_draft(args)` returns False whenever
  `args.test_output is True`, regardless of `--create-draft` /
  `CREATE_DRAFT=1`.
- `test_test_output_passes_output_target_to_process_events` — the
  step4 invocation includes `--output-target test`.
- `test_test_output_forces_empty_webhook_url` — the step4
  invocation passes `--webhook-url ""` even when
  `ignore_webhook_url.txt` exists.
- `test_test_output_skips_ics_out_dir` — no `--ics-out-dir` arg in
  the test-mode invocation.

`tests/test_process_events.py` (additive — JS/HTML substring asserts
following the existing pin pattern):

- `test_render_writes_testpage_path_in_test_mode` — when
  `--output-target test --html-out docs/testpage.html` is passed,
  output goes to that path.
- `test_render_html_test_mode_includes_banner` — the rendered HTML
  contains the banner text "TEST PAGE — Manual QA build".
- `test_render_html_prod_mode_omits_banner` — banner text is NOT
  present in default `--output-target prod` output.
- `test_render_html_test_mode_no_webhook_url_in_output` — when
  combined with `--webhook-url ""`, the rendered HTML does not
  embed any URL into the `WEBHOOK_URL` JS constant. (Already
  implicitly true via the existing dev-preview path; pin it
  explicitly so the banner-render code can't accidentally leak
  a URL.)

No new fixture files needed — the tests use existing patterns
(workflow YAML text-parse, main.py monkeypatch-based orchestration,
HTML substring asserts).

## Responsibility table

| Concern | Workflow YAML | main.py | process_events.py | Apps Script |
|---|---|---|---|---|
| `test_output` input declaration | ✅ | — | — | — |
| Forward `--test-output` to main.py | ✅ | — | — | — |
| Skip state-branch push step | ✅ | — | — | — |
| Curl prod page + ICS into artifact | ✅ | — | — | — |
| Skip `es.save_state` / `ns.save_stats` etc. | — | ✅ | — | — |
| Force `should_create_draft → False` | — | ✅ | — | — |
| Skip step3b auto-blocklist | — | ✅ | — | — |
| Pass `--output-target test` + empty webhook | — | ✅ | — | — |
| Write to `docs/testpage.html` | — | — | ✅ | — |
| Render banner | — | — | ✅ | — |
| Inert buttons (no POSTs) | — | — | ✅ (via empty WEBHOOK_URL) | — |
| (No Apps Script changes) | — | — | — | — |

No agent calls introduced. Apps Script untouched.

## Commit plan

Three code commits + close-out:

1. **Design note + ROADMAP `[~]` flip + last-session-summary update.**
   This commit. No code.

2. **Workflow + main.py wiring + tests.** Adds `test_output` input,
   forwards `--test-output`, gates state-branch push, adds curl-prod
   pre-upload step. main.py threads the flag through and short-circuits
   each persistent write. Includes `tests/test_workflow_test_output_gate.py`
   (new file) + the test_main.py additions.

3. **process_events.py `--output-target test` + banner + tests.**
   New CLI arg, banner template, output-path branch. Includes the
   test_process_events.py additions. Full `pytest tests/ -q` (will
   be ~754 + new tests) green on the strftime-patched copy before
   push.

4. **ROADMAP close-out: record SHAs, leave `[~]` pending Tom's
   live-verification on a real `workflow_dispatch` test_output=true
   run.** Tom verifies: (a) prod `/index.html` unchanged after the
   test run, (b) `/testpage.html` shows current data with the banner,
   (c) clicking Ignore/Complete on testpage does nothing, (d) ICS
   download links on prod still work post-test-run.

## Open for future work (explicit non-goals)

- **Functional buttons on the test page.** Tom confirmed not needed —
  ignore/complete logic is tested elsewhere. If we ever want
  end-to-end button testing on a test page, a separate Apps Script
  endpoint pointing at a "test" sheet tab would be needed. Out of
  scope.
- **Multiple concurrent testpages.** Only one `testpage.html` slot.
  If Tom wants `testpage-{branch}.html` to test multiple PRs in
  parallel, that's a follow-up.
- **Auto-cleanup of stale testpage.** No mechanism to delete an old
  testpage between cron runs. Cron writes only prod; the previous
  testpage persists at `/testpage.html` with its (stale) banner
  warning. If this becomes annoying, add a cron-time
  `rm docs/testpage.html` step.
- **Curl-prod fallback to state-branch.** If the live URL becomes
  unreachable in practice, we'd add a state-branch backup of
  `docs/index.html`. Not investing pre-emptively.
