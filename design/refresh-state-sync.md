# Cross-device state sync on page refresh

ROADMAP item #34. Closes the UX gap exposed by #32 + the existing
ignore-flow: state changes propagate to the Sheet immediately on click,
but to OTHER devices only after the next cron tick (Mon / Wed / Sat
6:15 ET). Concretely: Ellen ignores or completes an event on her phone,
Tom opens the page on his tablet between cron ticks, he still sees the
old state for hours.

Fix: client-side fetch of the three sheet-backed lists on every page
load, reconcile each card against the fetched lists before localStorage
overlays. Architecture invariant preserved — Sheet is still the single
source of truth; this just shortens cross-device latency from "next
cron tick" to "next refresh."

**Complexity: medium → think hard.** Narrower than #32 (no new files,
no new sheet tabs, no sync helper, no main.py wiring) but the
client-side reconciliation has subtle edge cases — race between
in-flight POST and immediate refresh, lazy localStorage schema
migration, three parallel fetches with partial-failure tolerance. The
JS commit (commit 3 below) is the trickiest part of the feature.

## Resolved decisions (2026-04-27 with Tom)

- **Auth: drop `?secret=` on read.** The three list-shape GETs
  (`?kind=ignored | completed | ignored_senders`) become
  unauthenticated. POST endpoints stay as today (no auth — the
  status quo). The data behind these reads is `(event_id, name,
  date)` tuples, the same metadata already on the public Pages page;
  there is no security loss. Implementation: small `apps_script.gs`
  patch in `doGet` to skip the secret check for these three kinds.
  Existing CI cron callers can keep their `?secret=...&kind=...`
  query strings unchanged — the extra param is silently ignored.
- **Reconciliation: sheet wins on refresh, with a grace-period
  exception for recently-flipped local entries.** localStorage
  entries grow from bare ids (or bare domains) to
  `{id, flipped_at_iso}` (resp. `{domain, flipped_at_iso}`).
  `flipped_at_iso` is the timestamp of the user's click — set on
  optimistic flip, NOT after POST confirms. On fetch resolve:
  - id in fetched list → apply fetched state, drop matching
    localStorage entry (sheet is authoritative; cache is redundant).
  - id NOT in fetched list AND localStorage entry's age
    (`now - flipped_at_iso`) is less than the grace period → keep
    the local flip; the POST may still be in flight or Google's
    sheet→GET propagation lag hasn't caught up.
  - id NOT in fetched list AND localStorage entry is older than
    the grace period → drop it; either the user un-flipped from
    another device, or the entry is stale.
  - Pre-#34 localStorage entries (bare ids without timestamp) are
    handled lazily by the load helpers: each bare entry is treated
    as `flipped_at_iso=""` which is lexically less than any ISO
    timestamp, so the age check always classifies them as stale and
    they're dropped on first reconcile. No migration code, no
    backwards-compat shim — the staleness is exactly correct (a
    bare id from a prior session is definitionally older than any
    grace period).
- **Grace period: 10 seconds.** Covers in-flight POST latency
  (typically <500ms) plus Google's sheet → Apps Script GET
  propagation (typically <2s, occasionally up to ~5s on slow
  paths). 10s is conservative-enough that virtually no in-flight
  click is dropped, short-enough that a true cross-device unflip
  reflects on this device within ~10s of refresh. Constant, not
  configurable — no operator knob exposed.
- **Offline write queue: out of scope.** Tracked separately at #35.
  Today's "POST fails → revert + toast" model means localStorage
  never accumulates un-pushed state; the simple "sheet wins"
  reconciliation is safe under it.

## Architecture

Three-tier client-side state resolution on every page load:

```
1. SSR paints from cron-time JSON          (status quo, fast first paint)
2. localStorage hydration applies overlays  (status quo, this-device flips)
3. Fetch overlays cross-device current state (NEW — this feature)
```

The fetch is fired AFTER hydration so first paint is unblocked. When
each fetch resolves (independent of the other two), its list is
reconciled against the rendered DOM per the rules above.

The `flipped_at_iso` timestamp lives on each localStorage entry. Save
helpers (`saveIgnored`, `saveIgnoredSenders`, `saveCompleted`) write
`{id, flipped_at_iso: new Date().toISOString()}`. Load helpers tolerate
both old (`["abc12"]`) and new (`[{id:"abc12", flipped_at_iso:"..."}]`)
shapes, normalizing to the new shape internally. The change is purely
additive to localStorage — no destructive migration, no schema-version
field, no key renames.

Three parallel fetches happen in `Promise.allSettled` style — each one
that resolves applies its reconciliation independently. A fetch failure
on any single list does not block reconciliation of the other two; the
failed list silently degrades to "no overlay applied; SSR + hydration
state stands."

## Design Q&A

**Q1 — CORS from `*.github.io`?** Apps Script web apps deployed with
"Anyone" access return `Access-Control-Allow-Origin: *` on simple GETs
and POSTs without preflight. The existing client-side POST (`Content-
Type: text/plain;charset=utf-8`) already navigates Apps Script's
quirks — that's a "simple" content type so no preflight fires. For
the new GETs, no custom headers are needed; the request is a "simple
GET" by CORS rules and should round-trip cleanly. Smoke test in commit
2's verification: Tom curls one of the unauthenticated GET endpoints
from his tablet's browser console after redeploy and confirms a 200 +
body. If preflight does break, fallback options are JSONP-style
(`<script src=...>` with a callback) or a workflow-side proxy — both
are ugly; the design doc reserves them as known-bad backstops only.

**Q2 — Visual flip during the fetch window.** The page renders in
<100ms; fetch returns ~500ms-2s later. A card that the SSR rendered as
not-ignored that the fetch flips to ignored will visibly snap. Default
proposal: render normally, accept the flip. Alternatives — block
rendering until fetch completes, or fade-in cards as their final state
resolves — are both worse UX (white screen / jank). Confirm with Tom
during note review; default stands unless overridden.

**Q3 — Network failure / slow Apps Script.** Fetch errors → silent
degrade with a `console.warn` breadcrumb for debugging; no toast,
no error banner. Ellen is offline-tolerant in practice (she rarely
opens the page in low-signal places), and a "sync failed" toast on
every flaky fetch would be more annoying than informative. Same
posture as the existing webhook POST silent-degrade for dev/preview
runs (where `WEBHOOK_URL` is empty).

**Q4 — Should the fetch happen on every page focus, not just initial
load?** No — over-fetching costs Apps Script quota and adds no value
for this use-case (Ellen + Tom refresh the page when they want fresh
state). Page-focus listening would also subtly conflict with the
in-flight POST grace window if a flip-then-immediate-tab-switch fires
the focus listener mid-POST. Initial page load only.

**Q5 — Interaction with #23 (test landing page).** Refresh-time
fetches hit the production Apps Script regardless of which page is
rendered; a test-mode landing page would show production sheet state.
Probably the right behavior (a tester wants to see what production
looks like), but worth pinning explicitly when #23 is built.

**Q6 — What if the WEBHOOK_URL is empty (dev/preview render)?** The
existing client-side `postAction` early-returns on empty
`WEBHOOK_URL`; mirror this in the new fetch logic. No fetch, no
reconciliation overlay, SSR + hydration state stands.

## Test fixtures

`tests/test_process_events.py` JS-substring asserts (mirror existing
ignore-flow JS-pin pattern):

- `test_render_html_js_refresh_sync_fires_three_fetches` — the inline
  JS contains three GET fetches with `?kind=ignored`,
  `?kind=ignored_senders`, `?kind=completed`.
- `test_render_html_js_refresh_sync_no_secret_in_fetch_url` — the
  fetch URLs do NOT carry a `secret=` query param. Pin the absence so
  a future refactor that re-adds auth to the read path fails CI.
- `test_render_html_js_localstorage_writes_flipped_at_iso` — the
  three save helpers write objects with `flipped_at_iso` keys, not
  bare ids/domains.
- `test_render_html_js_localstorage_load_tolerates_bare_ids` — the
  load helpers handle pre-#34 localStorage entries (bare strings) by
  normalizing them to objects with empty `flipped_at_iso`.
- `test_render_html_js_grace_period_constant_present` — pin the 10s
  grace period constant so future tweaks are explicit.
- `test_render_html_js_reconcile_drops_id_when_in_fetched_list` —
  pin the rule that an id appearing in the fetched list causes the
  matching localStorage entry to be dropped.
- `test_render_html_js_reconcile_keeps_recent_local_when_not_in_fetched`
  — pin the grace-window logic.
- `test_render_html_js_reconcile_silent_on_fetch_failure` — pin the
  console.warn + no-toast posture.
- `test_render_html_js_fetch_skipped_when_webhook_url_empty` — pin
  the dev-preview short-circuit.

`scripts/apps_script.gs` changes have no automated test (matches the
existing posture); manual smoke test post-deploy is adequate.

No new fixture files needed. The JS lives in `render_html`'s output
string; tests probe it via substring asserts the same way every other
client-side test in this file does today.

## Responsibility table

| Concern | Python | LLM (agent.py) | Apps Script | Client JS |
|---|---|---|---|---|
| Sheet write on flip | — | — | ✅ POST handlers | — |
| Sheet read for sync | — | — | ✅ doGet (auth-relaxed) | — |
| Cron-time SSR seed | ✅ process_events.py | — | — | — |
| Initial paint | ✅ render_html | — | — | — |
| localStorage write/read | — | — | — | ✅ save/load helpers |
| Optimistic flip + rollback | — | — | — | ✅ click/change handlers (status quo) |
| Fetch on page load | — | — | — | ✅ NEW |
| Reconcile fetched vs local | — | — | — | ✅ NEW (grace-period rule) |
| Schema migration (lazy) | — | — | — | ✅ load helpers tolerate bare ids |

No runtime LLM calls introduced. Sheet remains the single source of
truth.

## Commit plan

Four commits at natural boundaries (session discipline: commit at
each boundary, not just at feature completion):

1. **Design note + ROADMAP `[~]` flip + last-session-summary update.**
   This commit. Captures resolved decisions, architecture, the 6
   design Qs above, test fixture list, commit plan. No code.
2. **`scripts/apps_script.gs`: drop `?secret=` on the three list-shape
   GETs.** Single-function patch in `doGet` — skip the secret check
   for `kind in {ignored, ignored_senders, completed}`. POSTs
   unchanged. **Tom redeploys Apps Script after this commit lands**
   (manual deploy step, same pattern as #32 commit 3). Pause point —
   confirm a curl returns the lists without a secret before commit 3.
3. **`scripts/process_events.py` inline JS: fetch + reconcile +
   localStorage schema bump + tests.** All client-side work in one
   commit. Three parallel fetches kicked off after the existing
   hydration block; `reconcileFromFetch` helper applying the
   grace-period rule; save helpers updated to write
   `{id, flipped_at_iso}`; load helpers tolerate both old and new
   shapes. JS-substring tests per the fixture list above. Full
   `pytest tests/ -q` green on the strftime-patched copy before push.
4. **ROADMAP close-out: record 4 SHAs in the #34 stub, leave `[~]`
   pending Tom's live verification.** Verification: refresh tablet
   after a phone-side flip and confirm cross-device propagation
   within ~1s of fetch completion.

## Open for future work (explicit non-goals)

- **Offline write queue (#35).** Failed POSTs still revert + toast;
  no local accumulation of unpushed state. Revisit only if Tom
  actually hits the offline-loss case in practice.
- **Page-focus refetch.** No periodic poll, no focus listener — just
  initial page load. Adding focus refetch is a small client tweak if
  use cases ever demand it; flag separately at that point.
- **Loading indicator during the fetch window.** Page renders
  normally, cards may visibly snap when the fetch lands. Acceptable
  per Q2; revisit if Tom finds the flip distracting in practice.
- **Apps Script GET pagination.** All three lists are small (low
  hundreds of rows max in the worst case for #20-style sender
  histories). No need to paginate; single response per kind.
