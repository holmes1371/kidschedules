# Undo recently ignored + "Ignore sender" — combined feature

New items 6 and 7 in the ROADMAP backlog. Bundled because they share all their surfaces: Apps Script `doPost` routing, a second Google Sheet tab, client-side button/toggle work in the rendered HTML, and a new workflow sync step. Treating them as one unit avoids two passes over the same code.

Tom's framing locked in:

- **No 5-minute toast.** Ignored events render with `display:none` + an `is_ignored` flag; a header toggle unhides them on demand. Unignore is a persistent per-card button on ignored cards, not a time-boxed affordance. Simpler state, less timer plumbing.
- **"Ignore sender" operates on the registrable domain** (`greenfield.k12.ny.us`, not `office@greenfield.k12.ny.us` or `us`). PSL-aware via `tldextract`.
- **Sender → event mapping is deterministic, not LLM-derived.** The agent returns a `source_message_id` per event; `main.py` looks up the raw `From:` header from the Gmail stubs and runs it through `tldextract`. The LLM's only structured job is to echo back an ID it was handed.
- **Ignored senders live in the Google Sheet alongside ignored events, not in a text file.** The sheet is the single source of truth; the workflow fetches the list each run and writes an `ignored_senders.json` cache file (committed, mirrors the `ignored_events.json` pattern). Rows carry a `source` column (`manual` / `auto-button`) so Ellen can distinguish UI-added entries from ones she seeds by hand. The word "ignored" is used end-to-end (tab / cache / action / kind / script) to match the "Ignore sender" button copy — and to avoid colliding with the unrelated `blocklist.txt` used by the Gmail-search filter. See the "Architecture update (2026-04-15)" section below for the full reasoning.
- **No undo grace window.** If an ignored event ages past its date, cache GC drops it. Accepted; the show-ignored toggle makes recovery easy inside that window and there's no stakeholder for longer-lived recovery.

## Rendering model: "render but hide"

`process_events.py::classify` today drops ignored events into a separate `ignored` bucket that never reaches render. The change: ignored events flow through to `display` as normal, with `is_ignored=True` set on the normalized dict. Placement in the week buckets is unchanged. The `ignored` bucket return value is kept (it still holds dropped-ignored events that the caller counts for logging) but nothing in that bucket is rendered.

`render_html` treats `is_ignored` as purely a style hint:

- Card element gets `class="event-card ignored"` and `style="display:none"` and `data-ignored="1"`.
- The `<div class="event-actions">` row emits an **Unignore** button instead of the normal **Ignore** button. Same slot, drop-in replacement — the only difference is the label and the color treatment (see below).
- An **Ignore sender** button is emitted on every card regardless of ignore state (see below — unless the event has no `sender_domain`, in which case it's omitted).

### Unignore button color treatment

The Unignore button uses a solid-green variant so ignored cards stand out visually once the show-ignored toggle is on (at a glance you can tell which cards are ignored and how to restore them). New CSS class `.unignore-btn` with its own palette; the existing `.ignore-btn` is untouched on non-ignored cards.

- Light mode: `background: #0d652d` (matches the Sports & Extracurriculars accent already in the palette), `color: #ceead6`, `border: 1px solid #0d652d`.
- Dark mode: `background: #1e8e3e`, `color: #e6f4ea`, `border: 1px solid #1e8e3e`. Applied via the existing `@media (prefers-color-scheme: dark)` block that today overrides `:root`.
- Hover: `filter: brightness(1.15)` on both modes (simplest cross-mode affordance).

These are new additions to the stylesheet — no existing color variables or text-color rules are modified. Specifically, `.event-name`, `.event-date`, `.event-meta`, and the category-badge colors are out of scope for this feature and stay exactly as they are in prod.

### Header toggle

A header control: **Show ignored (N)** / **Hide ignored (N)**. N = `sum(1 for e in display if e.get("is_ignored"))`. Count is rendered server-side; label toggles client-side. Click flips a class on the card container that overrides `display:none` for `.event-card.ignored`.

When N == 0, the header control is omitted entirely (no "Show ignored (0)" button).

## Client behavior

### Ignore / Unignore
Click on the Ignore button:
1. `POST {action:'ignore', id, name, date}` to Apps Script.
2. On 2xx: add to localStorage (today's behavior, keeps sticking across reloads until next workflow run), add `.ignored` + `display:none` to the card, swap the button to "Unignore".
3. On non-2xx: keep the card untouched, log to console, show a brief "Ignore failed" toast.

Click on the Unignore button:
1. `POST {action:'unignore', id}` to Apps Script.
2. On 2xx: remove from localStorage, remove `.ignored` / `display:none` / `data-ignored`, swap button back to "Ignore", decrement N on the header toggle. Card stays visible in its new non-ignored state.
3. On non-2xx: leave the card's ignored state as-is, log + toast.

### Show/Hide ignored toggle
Pure client-side class flip on a root container. No network call. On initial load the toggle starts in the "hidden" state — ignored cards are hidden per their inline `display:none`. Click adds `.show-ignored` to the container; CSS rule `.show-ignored .event-card.ignored { display: block !important; }` unhides them. Second click removes the class.

### Ignore sender
Click on the Ignore sender button:
1. `POST {action:'ignore_sender', domain}` to Apps Script.
2. On 2xx: show a confirmation toast — "Ignoring {domain}. New events from this sender will stop appearing after the next refresh." No DOM manipulation — the effect lands on the next workflow run when the synced `ignored_senders.json` feeds into the Gmail-search exclusion. Button disables to prevent duplicate calls.
3. On non-2xx: toast "Ignore failed".

### localStorage hydration
Existing pattern: on page load, walk localStorage for ignore entries, flip matching cards to ignored state if the server rendered them as non-ignored (i.e. the ignore hasn't round-tripped through the next workflow run yet). Extend to handle the swap-button-to-Unignore case.

## Apps Script changes

`doPost` becomes an action router. Body shape: `{action: string, ...}`. An absent `action` defaults to `'ignore'` for backward compatibility (cheap insurance; Ellen is the only client but the cost is a single `||` default).

Three actions:

- **`ignore`** — existing append behavior. Same id/name/date validation.
- **`unignore`** — find all rows in the Ignored Events sheet with `id` matching the payload, delete them (loop from bottom up to avoid row-shift bugs). Return `ok` even if no rows matched (idempotent).
- **`ignore_sender`** — lowercase `domain` and validate against `^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$` (conservative registrable-domain shape). Append `[timestamp, domain, 'auto-button']` to a second sheet tab "Ignored Senders" in the same spreadsheet. Dedup on the client isn't needed — the workflow-side sync handles it.

`doGet` grows a second route keyed on `?kind=`:

- `?secret=X` (unchanged, defaults to `kind=ignored`) — returns ignored events JSON.
- `?secret=X&kind=ignored_senders` — returns ignored senders JSON: `[{timestamp, domain, source}, ...]`.

Same `READ_SECRET` for both routes. Reusing the existing secret is fine — it's already gating the same spreadsheet, and this is one script with one deploy.

## Workflow changes

New step after the existing "Sync ignored events from Apps Script" step:

**Sync ignored senders from Apps Script** — curl `${URL}?secret=${IGNORE_READ_SECRET}&kind=ignored_senders`, normalize (lowercase, trim, dedup, sort), and write `ignored_senders.json`. If the file contents changed, commit and push on `main` with a conventional message. If unchanged, skip commit.

Fetch-and-write lives in a small helper script `scripts/sync_ignored_senders.py` so it's testable:

- GET the JSON from Apps Script.
- For each row: lowercase the domain, trim whitespace, skip anything that fails the domain regex.
- Dedup, sort alphabetically.
- Write `ignored_senders.json` (`[{"domain": "...", "source": "...", "timestamp": "..."}, ...]`, first-wins on domain).
- Return `unchanged` when the serialized output matches the file on disk.

Commit step uses the same `x-access-token` pattern as the existing state-branch push.

## Sender-domain attribution

### `agent.py`

Prompt addition:

> For each event you extract, include a `"source_message_id"` field that exactly echoes the `messageId` of the message you drew the event from. If an event synthesizes information from multiple messages, pick the message that contained the dated details (or omit the event rather than guess).

Output schema: `source_message_id: string (16-char hex, matches one of the messageIds in this batch)`.

Parser validation:
- If missing or malformed → drop the event, warn. Don't crash.
- If `source_message_id` doesn't match any messageId in the current batch → drop the event, warn. LLM hallucinated an ID.

Never-crash posture matches the existing `except Exception: continue` policy around parse failures (see `design/failure-notifications.md` — parse failures are tolerant by design).

### `main.py`

After agent extraction, walk each event:
1. Look up `message_stubs[event["source_message_id"]]["headers"]["From"]`.
2. Parse with `email.utils.parseaddr` to get the email address.
3. Run through `tldextract.extract(addr_domain).registered_domain`. Lowercase.
4. Attach as `event["sender_domain"]`.

If lookup fails at any step: `event["sender_domain"] = ""`. Downstream the "Ignore sender" button won't render for that event. No crash.

### `events_state.py`

Schema bump to `schema_version: 2`. `events` entries grow an optional `sender_domain` key. Schema mismatch → blow-away-and-rebuild (existing policy). One run of full 60-day re-extraction is acceptable; we already pay this cost on any prompt bump.

### `process_events.py`

`classify` normalization grows `sender_domain` passthrough. `render_html` uses it for the `data-sender` attribute on the card and decides whether to emit the Ignore sender button. No other logic changes.

## Pytest fixtures

`tests/test_process_events.py` gains:

- Fixture events with `is_ignored=True` and without, asserting render output has the right classes/attributes/buttons.
- Fixture events with `sender_domain` set and empty, asserting the Ignore sender button renders conditionally.
- Snapshot updated to cover the new markup (header toggle, Unignore button, Ignore sender button, `data-sender` / `data-ignored` attributes).
- Classification: ignored events appear in `display` with `is_ignored=True`, the `ignored` bucket remains the count-only return (existing tests updated).

`tests/test_events_state.py` gains:

- Schema v2 load: `sender_domain` round-trips through save/load.
- Schema v1 load → blown away and rebuilt empty (existing behavior, just re-verify after the bump).

`tests/test_agent.py` (or new file if none exists):

- Parser accepts events with valid `source_message_id`.
- Parser drops events with missing `source_message_id` and warns.
- Parser drops events with `source_message_id` not in the input batch.

`tests/test_sync_ignored_senders.py` (new file):

- Normalizes domains to lowercase, trims whitespace.
- Dedups same-domain rows (first-wins on timestamp/source).
- Drops rows that fail the domain regex.
- Returns `unchanged` when the serialized output matches the file on disk.
- Sorts domain entries alphabetically for stable diffs.

Apps Script changes have no pytest coverage — same posture as today, and the action router is small enough that visual review + live smoke testing is adequate.

## Commit plan

Commit at each natural boundary, not just at feature completion (session discipline):

1. **Design note + ROADMAP insert** (this commit).
2. **`agent.py` schema bump** — `source_message_id` field, prompt update, parser validation, unit tests.
3. **`main.py` sender-domain attachment** + `tldextract` added to `requirements.txt`. Integration test for the lookup path.
4. **`events_state.py` schema v2** — migration policy, fixtures updated.
5. **`process_events.py` render-but-hide model** — classify/render changes, fixture events for `is_ignored`, Show/Hide toggle markup, Ignore-sender button markup. Snapshot updated.
6. **`scripts/apps_script.gs`** — action router, unignore endpoint, ignore_sender endpoint, ignored-senders GET route. (No automated tests; manual deploy + smoke.)
7. **`scripts/sync_ignored_senders.py`** — fetch-and-write helper + unit tests.
8. **Workflow sync step** — new "Sync ignored senders" step + commit-on-main logic.
9. **Client JS in `docs/index.html` (rendered by `process_events.py`)** — Unignore button wiring, Show/Hide toggle handler, Ignore-sender button wiring, toast helpers, localStorage hydration updates.
10. **ROADMAP status update + SHAs**, session-close.

Steps 2, 5, 7, 9 each modify `process_events.py` or equivalent pipeline code → fixtures updated in step, not after (session discipline).

## Responsibility table

Following the standing order — deterministic work in scripts, agent (LLM) does only judgment:

| Concern | Python | LLM (agent.py) |
|---|---|---|
| Event extraction from email text | — | ✅ judgment |
| Echo back source `messageId` per event | validates, rejects invalid | ✅ echoes the ID it was given |
| Parse `From:` header → email address | ✅ `email.utils.parseaddr` | — |
| Email address → registrable domain | ✅ `tldextract` | — |
| Domain case normalization + dedup | ✅ `sync_ignored_senders.py` | — |
| `ignored_senders.json` sort + diff-check | ✅ `sync_ignored_senders.py` | — |
| Classify ignored vs displayed | ✅ `process_events.py::classify` | — |
| Render card HTML + buttons + toggle count | ✅ `process_events.py::render_html` | — |
| Apps Script row delete / append | ✅ `apps_script.gs` (deterministic code) | — |
| Decide which events are "ignored" | ✅ sheet rows + localStorage | — |

No runtime LLM calls are introduced by this feature beyond the existing extraction path.

## Architecture update (2026-04-15)

After step 5 landed, Tom pushed back on the original split (ignored events in a sheet, blocked senders in `blocklist.txt`): having two storage shapes for two parallel opt-out lists meant two merge policies, two docs, two code paths for the same user intent. The sheet wins on symmetry and on Ellen-editability (no git round-trip to manage blocks manually).

Separately, the word "blocklist" already has a load-bearing meaning elsewhere in the repo — `blocklist.txt` + `blocklist_auto.txt` are the Gmail-search exclusion files managed by `update_auto_blocklist.py` and the step-1b filter audit. That system predates this feature and is unrelated. Reusing the word for the new sender-level opt-out would have created an ambiguous vocabulary across the codebase. The feature is therefore called "ignored senders" end-to-end (sheet tab, cache file, POST action, GET kind, helper script) so every surface matches the user-facing "Ignore sender" button.

Amended decisions:

- **Single Google Sheet, two tabs.** The existing "Ignored Events" tab is unchanged. A new "Ignored Senders" tab holds `[timestamp, domain, source]` rows. `source` is `"auto-button"` when appended via the schedule page's Ignore-sender button, `"manual"` when Ellen edits the sheet directly. Script code reads every row authoritatively; the column is informational.
- **`ignored_senders.json` is a committed cache file**, not a git-source. Same shape relationship as `ignored_events.json` → the pipeline fetches the sheet, writes the JSON, and commits on `main` when it changes. The file exists so the Gmail-search step has a fast local read and so diffs show ignored-sender churn in git history.
- **No `blocklist.txt` entry for this feature.** The old `blocklist.txt` / `blocklist_auto.txt` pair is left alone — they serve the Gmail-search filter and aren't touched by this work.
- **`sync_ignored_senders.py` is fetch-and-write, not merge.** There's no "manual entries to preserve" problem — the sheet is the sole source, and any manual additions Ellen wants to make happen in the sheet. The helper pulls `?kind=ignored_senders`, normalizes (lowercase, trim, dedup), sorts, and writes `ignored_senders.json`. If the file's contents are unchanged, skip commit. Tests collapse to: normalization correctness + no-change short-circuit.
- **Apps Script `ignore_sender` appends with `source: "auto-button"`.** `doGet?kind=ignored_senders` returns the full rows including source, so Ellen can filter/report in a spreadsheet view if she ever wants to.

Ripple through the commit plan:

- Step 6 (Apps Script) gains the `source` column on append and the `?kind=ignored_senders` GET.
- Step 7 (`sync_ignored_senders.py`) is simpler — no comment-block preservation, no case-insensitive manual merge, no alphabetic resort of a mixed file. Just fetch-normalize-write.
- Step 8 (workflow) still commits on `main` — not because an ignored-senders file is configuration (the previous framing), but because `ignored_senders.json` is a cache that we want versioned alongside `ignored_events.json` for historical forensics.
- Nothing in steps 2–5 is affected; those are already landed.

Responsibility table stays accurate — the table now reads "`sync_ignored_senders.py` owns fetch + normalize + dedup + write", no other changes.

## Open for future work

Not doing now (explicit non-goals):

- **Bulk unignore** — no "clear all" button. Individual Unignore per card is fine at current volume (<20 ignored events at any time).
- **Ignored-sender UI on the schedule page** — no list/manage view. Users edit the "Ignored Senders" sheet tab directly if they need to un-ignore a domain. Cheap fallback.
- **Soft-ignore** (ignore with override) — the ignored-senders list is a hard filter at the Gmail-search level. Un-ignoring requires editing the sheet. Acceptable.
- **Unignore notifications** — no server-side audit beyond the Apps Script sheet. If needed later, the existing weekly-digest path can be extended.
