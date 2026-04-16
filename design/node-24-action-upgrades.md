# Node 20 → Node 24 action upgrades

Scope: silence the `Node.js 20 actions are deprecated` warning on every workflow
run ahead of GitHub's 2026-06-02 Node 24 default flip (Node 20 removal on
2026-09-16).

## Bumps

| Action | From | To | Reason |
|---|---|---|---|
| `actions/checkout` | `v4` | `v5` | Node 24 landed in v5.0.0 |
| `actions/setup-python` | `v5` | `v6` | Node 24 landed in v6.2.0 |
| `actions/upload-pages-artifact` | `v3` | `v5` | Latest; v4 was the breaking change re: hidden files (see gotcha below). Jumping straight to v5 — no intermediate v4 step buys us anything |
| `actions/deploy-pages` | `v4` | `v5` | Node 24 landed in v5.0.0 |

Each is pinned to the lowest major that ships Node 24 — no sub-minor pins, since
GitHub's convention for first-party actions is to track the major tag.

## Gotcha: `.nojekyll` and hidden-file exclusion

`actions/upload-pages-artifact@v4` introduced a breaking change — hidden files
(leading `.`) are excluded from the artifact by default. Our `docs/` directory
contains `.nojekyll` at the root, which tells GitHub Pages to skip Jekyll
processing. Without it, Pages would reject paths that look Jekyll-ish
(leading-underscore directories, etc.) and our `docs/ics/*.ics` routing is
likely to misbehave.

`upload-pages-artifact@v5` exposes an `include-hidden-files` input. We set it
to `true` on the Upload Pages artifact step so `.nojekyll` ships with the
artifact. Only this one step needs the flag; nothing else in the pipeline cares
about hidden files.

## Verification plan

1. **`tests.yml` pytest run (automatic on push)** — exercises `checkout@v5` and
   `setup-python@v6`. A red run blocks merge, per session discipline.
2. **`workflow_dispatch` with `dry_run=true` on `weekly-schedule.yml`** — same
   two actions as above, plus the state-branch plumbing. The existing
   `if: github.event.inputs.dry_run != 'true'` guards on the Upload Pages
   artifact step and the `deploy` job mean a dry run does *not* exercise
   `upload-pages-artifact@v5` or `deploy-pages@v5`.
3. **First real (non-dry) run** is the only path that verifies the two Pages
   steps. The Monday cron (`15 10 * * 1`) will do this automatically; Tom can
   also force it manually via a non-dry `workflow_dispatch`.
4. Smoke check after the first real run: the Pages deployment succeeds, the
   published site still renders (`.nojekyll` made it through; `ics/` routes
   work), and the Actions log shows no Node 20 deprecation warnings.

## Fallback: no Node 24 major by the deadline

Not needed for us — all four actions already ship a Node 24 major. But for
future reference: if an action we depend on in a later feature has no Node 24
release by 2026-06-02, setting `ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION=true`
as an env on the step (or on the runner) keeps it on Node 20 past the default
flip. This is a temporary escape hatch, not a long-term pin — Node 20 is
removed from runners on 2026-09-16 regardless.

## Non-goals

- Not auditing every transitive action or reusable workflow — we only have
  four direct pins and they all have Node 24 releases.
- Not holding on sub-minor pins. Tracking the major (`@v5`, `@v6`) matches
  GitHub's recommendation for first-party actions and means future Node 24
  patch releases land automatically.
- Not touching `docs/.nojekyll` itself. The fix lives in the upload step via
  `include-hidden-files: true`.
