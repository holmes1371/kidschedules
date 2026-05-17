# 16. Node 20 → Node 24 action upgrades (before 2026-06-02) — ea081da

Every workflow run was printing:

> Warning: Node.js 20 actions are deprecated. The following actions are running on Node.js 20 and may not work as expected: actions/deploy-pages@v4.

GitHub timeline: Node 24 becomes the default on 2026-06-02 and Node 20 is removed from runners on 2026-09-16 (see https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/). The warning named `actions/deploy-pages@v4`; audit of the other pinned actions in `.github/workflows/weekly-schedule.yml` and `.github/workflows/tests.yml` found `actions/checkout@v4`, `actions/setup-python@v5`, and `actions/upload-pages-artifact@v3` all still on the Node 20 runtime.

Bumps landed in `ea081da`: `actions/checkout` v4→v5, `actions/setup-python` v5→v6, `actions/upload-pages-artifact` v3→v5 (with `include-hidden-files: true` to preserve `docs/.nojekyll` across the v4 breaking change), `actions/deploy-pages` v4→v5. Full scope, the `.nojekyll` gotcha, and the verification plan live in `design/node-24-action-upgrades.md`.

Verified: `tests.yml` green on push (`checkout@v5` + `setup-python@v6`); non-dry `weekly-schedule.yml` run confirmed Pages deployed cleanly with `upload-pages-artifact@v5` + `deploy-pages@v5`, no Node 20 deprecation warnings in the Actions log.

Fallback (not applied — all four actions already have Node 24 majors, documented here for a future feature that pulls in a less-maintained action): if an action has no Node 24 major by 2026-06-02, set `ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION=true` as an env on the step (or on the runner) to keep it on Node 20 past the default flip. This only buys time until 2026-09-16, when Node 20 is removed from runners entirely. The older `FORCE_JAVASCRIPT_ACTIONS_TO_NODE20` env var is the historical predecessor and is not the forward-looking escape hatch.
