# 23. Separate test landing page for manual `workflow_dispatch` QA runs — f0dea5b / 0822afc / c0bf8e4 / 75b7a5d

Filed and closed 2026-04-27. Every workflow run — scheduled cron and manual `workflow_dispatch` alike — used to overwrite `docs/index.html`, the page Ellen sees. Manual runs that existed purely to verify a fix put experimental output in front of her until the next cron tick replaced it. The pipeline needed a way to route test builds to a separate path so the production page stays untouched.

**Design (`design/test-landing-page.md`).** New `test_output` boolean `workflow_dispatch` input alongside the existing Dry run / Intentional failure / Create draft toggles. Default false → manual run behaves like a cron tick (full prod write, full state writes — useful for "I just landed a feature, push it now without waiting for the next 6:15 ET cron"). True → entire run is sandboxed: writes to `docs/testpage.html`, no production state mutations anywhere. Cron-scheduled runs unaffected.

**Single global toggle, no carve-outs.** When `test_output=true` the run skips ALL persistent writes: `events_state.json` (#4), `prior_events.json` (#13), `sender_stats.json` (#17), all auto-blocklist files (#27), `.filter_audit.json` (#2), the state-branch push step, the Gmail digest draft (#3, #10), and Apps Script POSTs from the rendered page (#32, #6). Reads still happen — the test page reflects current Gmail / current Apps Script ignored+completed state. Carving out exceptions ("write the cache but not the draft") would have been a footgun: a single missed exception silently corrupts the next prod run.

**Test page filename: `docs/testpage.html`** (not `docs/test/index.html`). Flat is simpler — no nested `test/ics/` to manage, URL `/testpage.html` is straightforward, and the artifact layout stays flat.

**Action buttons inert via existing empty-WEBHOOK_URL gate.** main.py forwards `--webhook-url ""` to `process_events.py` in test mode, which trips the existing dev/preview short-circuit at `process_events.py:2125` (POST handler) and `:2427` (#34 refresh-fetch handler). No new code path — reused the empty-webhook gate that already existed for local `dev_render.py` runs.

**Banner on the test page.** Sticky red bar above the page header: "🧪 TEST PAGE — Manual QA build, not live data. Card actions are disabled. Trigger another workflow_dispatch run with `test_output` on to refresh." `<title>` tagged with " (TEST)" so the browser tab is visually distinct.

**Preserve Ellen's prod page via curl-from-live.** `actions/deploy-pages` does a full-replace deploy of the uploaded artifact, so without preservation a test run would wipe `/index.html` from the live site until the next cron tick rebuilt it. New workflow step, gated on `inputs.test_output == 'true'`, runs before `upload-pages-artifact`: curls the live prod `/index.html` into `docs/index.html`, then scrapes `<a href="ics/...">` from the just-fetched page and curls each ICS file into `docs/ics/`. Best-effort: a curl failure logs a warning and proceeds (test page still deploys, prod briefly 404s, next cron heals). State-branch backup of `docs/index.html` + ICS was considered as a more durable alternative but adds maintenance surface; reserved as a fallback if curl reliability becomes an issue.

**Commit trail.**

- `f0dea5b` — Design note + ROADMAP `[~]` flip.
- `0822afc` — `test_output` `workflow_dispatch` input wired through to `main.py`; state-branch save gated; curl-prod step added; `should_create_draft` / `step1b_filter_audit` / `step3b_update_auto_blocklist` / `es.save_state` / `ns.save_stats` all short-circuit; `step4_process_events` forwards `--webhook-url ""` + `--output-target test`, omits `--prior-events` and `--ics-out-dir`; `step5_publish` writes `docs/testpage.html`. 23 new tests across `tests/test_workflow_test_output_gate.py`, `tests/test_main.py`, `tests/test_main_orchestration.py`.
- `c0bf8e4` — `process_events.py --output-target test` renders banner above `.header` plus ` (TEST)` suffix in `<title>`. 8 new render-substring tests. 754 → 785 tests green.
- `75b7a5d` — Close-out: SHAs in ROADMAP body, verification checklist (since-fulfilled).

**Live verification.** Tom ran a `workflow_dispatch test_output=true` dispatch and confirmed 2026-04-27: the test page came up at `/testpage.html` with current data and the red banner; Ellen's `/index.html` was unchanged from the last cron — the curl-prod preservation worked end-to-end.

**Open for future work (explicit non-goals).** Functional buttons on the test page (Tom confirmed not needed; tested elsewhere). Multiple concurrent testpages (only one `testpage.html` slot; if parallel-PR testing becomes a need, a `testpage-{branch}.html` scheme is a follow-up). Auto-cleanup of stale testpage between cron runs (cron writes only prod; the previous testpage persists at `/testpage.html` with its banner warning — add a cron-time `rm docs/testpage.html` step if it ever becomes annoying). Curl-prod fallback to state-branch (revisit only if the live URL becomes unreachable in practice).
