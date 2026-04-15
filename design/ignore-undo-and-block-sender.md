# Undo recently ignored + "Ignore sender" — combined feature

New items 6 and 7 in the ROADMAP backlog. Bundled because they share all their surfaces: Apps Script `doPost` routing, a second Google Sheet tab, client-side button/toggle work in the rendered HTML, and a new workflow sync step. Treating them as one unit avoids two passes over the same code.

Tom's framing locked in:

- **No 5-minute toast.** Ignored events render with `display:none` + an `is_ignored` flag; a header toggle unhides them on demand. Unignore is a persistent per-card button on ignored cards, not a time-boxed affordance. Simpler state, less timer plumbing.
- **"Ignore sender" operates on the registrable domain** (`greenfield.k12.ny.us`, not `office@greenfield.k12.ny.us` or `us`). PSL-aware via `tldextract`.
- **Sender → event mapping is deterministic, not LLM-derived.** The agent returns a `source_message_id` per event; `main.py` looks up the raw `From:` header from the Gmail stubs and runs it through `tldextract`. The LLM's only structured job is to echo back an ID it was handed.
- **`blocklist.txt` updates commit to `main`**, not the `state` branch. A blocklist entry is repo configuration, not runtime state; history is useful.
- **No undo grace window.** If an ignored event ages past its date, cache GC drops it. Accepted; the show-ignored toggle makes recovery easy inside that window and there's no stakeholder for longer-lived recovery.

## Rendering model: "render but hide"

`process_events.py::classify` today drops ignored events into a separate `ignored` bucket that never reaches render. The change: ignored events flow through to `display` as normal, with `is_ignored=True` set on the normalized dict. Placement in the week buckets is unchanged. The `ignored` bucket return value is kept (it still holds dropped-ignored events that the caller counts for logging) but nothing in that bucket is rendered.

`render_html` treats `is_ignored` as purely a style hint:

- Card element gets `class="event-card ignored"` and `style="display:none"` and `data-ignored="1"`.
- The `<div class="event-actions">` row emits an **Unignore** button instead of the normal **Ignore** button.
- An **Ignore sender** button is emitted on every card regardless of ignore state (see below — unless the event has no `sender_domain`, in which case it's omitted).

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
1. `POST {action:'block_sender', domain}` to Apps Script.
2. On 2xx: show a confirmation toast — "Blocked {domain}. New events from this sender will stop appearing after the next refresh." No DOM manipulation (blocking takes effect next workflow run when the synced blocklist filters the Gmail search). Button disables to prevent duplicate calls.
3. On non-2xx: toast "Block failed".

### localStorage hydration
Existing pattern: on page load, walk localStorage for ignore entries, flip matching cards to ignored state if the server rendered them as non-ignored (i.e. the ignore hasn't round-tripped through the next workflow run yet). Extend to handle the swap-button-to-Unignore case.

## Apps Script changes

`doPost` becomes an action router. Body shape: `{action: string, ...}`. An absent `action` defaults to `'ignore'` for backward compatibility (cheap insurance; Ellen is the only client but the cost is a single `||` default).

Three actions:

- **`ignore`** — existing append behavior. Same id/name/date validation.
- **`unignore`** — find all rows in the Ignored Events sheet with `id` matching the payload, delete them (loop from bottom up to avoid row-shift bugs). Return `ok` even if no rows matched (idempotent).
- **`block_sender`** — validate `domain` matches `^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$` (conservative registrable-domain shape). Append `[timestamp, domain]` to a second sheet tab "Blocked Senders" in the same spreadsheet. Dedup on the client isn't needed — the workflow-side sync handles it.

`doGet` grows a second route keyed on `?kind=`:

- `?secret=X` (unchanged, defaults to `kind=ignored`) — returns ignored events JSON.
- `?secret=X&kind=blocked_senders` — returns blocked senders JSON: `[{timestamp, domain}, ...]`.

Same `READ_SECRET` for both routes. Reusing the existing secret is fine — it's already gating the same spreadsheet, and this is one script with one deploy.

## Workflow changes

New step after the existing "Sync ignored events from Apps Script" step:

**Sync blocked senders from Apps Script** — curl `${URL}?secret=${IGNORE_READ_SECRET}&kind=blocked_senders`, parse JSON, merge each domain into `blocklist.txt` (dedup case-insensitive, preserve existing manual entries, sort alphabetically for stable diffs). If `blocklist.txt` changed, commit and push on `main` with a conventional message. If unchanged, skip commit.

Merge logic lives in a small helper script `scripts/sync_blocklist.py` so it's testable:

- Read existing `blocklist.txt` lines (preserve blank lines and comments as-is).
- Read incoming domains from Apps Script response.
- Union, case-normalize (lowercase), dedup, re-sort the domain entries while leaving comment blocks in place at the top.
- Write back only if changed.

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

`tests/test_sync_blocklist.py` (new file):

- Merge preserves comment block at top of file.
- Merge dedups case-insensitively (`Foo.com` + `foo.com` → one entry).
- Merge preserves manual entries that aren't in the Apps Script payload.
- Merge returns `unchanged` when no new domains appear.
- Merge sorts domain entries alphabetically.

Apps Script changes have no pytest coverage — same posture as today, and the action router is small enough that visual review + live smoke testing is adequate.

## Commit plan

Commit at each natural boundary, not just at feature completion (session discipline):

1. **Design note + ROADMAP insert** (this commit).
2. **`agent.py` schema bump** — `source_message_id` field, prompt update, parser validation, unit tests.
3. **`main.py` sender-domain attachment** + `tldextract` added to `requirements.txt`. Integration test for the lookup path.
4. **`events_state.py` schema v2** — migration policy, fixtures updated.
5. **`process_events.py` render-but-hide model** — classify/render changes, fixture events for `is_ignored`, Show/Hide toggle markup, Ignore-sender button markup. Snapshot updated.
6. **`scripts/apps_script.gs`** — action router, unignore endpoint, block_sender endpoint, blocked-senders GET route. (No automated tests; manual deploy + smoke.)
7. **`scripts/sync_blocklist.py`** — merge helper + unit tests.
8. **Workflow sync step** — new "Sync blocked senders" step + commit-on-main logic.
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
| Domain case normalization + dedup | ✅ `sync_blocklist.py` | — |
| `blocklist.txt` merge + sort + diff-check | ✅ `sync_blocklist.py` | — |
| Classify ignored vs displayed | ✅ `process_events.py::classify` | — |
| Render card HTML + buttons + toggle count | ✅ `process_events.py::render_html` | — |
| Apps Script row delete / append | ✅ `apps_script.gs` (deterministic code) | — |
| Decide which events are "ignored" | ✅ sheet rows + localStorage | — |

No runtime LLM calls are introduced by this feature beyond the existing extraction path.

## Open for future work

Not doing now (explicit non-goals):

- **Bulk unignore** — no "clear all" button. Individual Unignore per card is fine at current volume (<20 ignored events at any time).
- **Blocked-sender UI on the schedule page** — no list/manage view. Users edit `blocklist.txt` in git if they need to remove a block. Cheap fallback.
- **Soft-block** (block with override) — the blocklist is a hard filter at the Gmail-search level. Un-blocking requires a commit. Acceptable.
- **Unignore notifications** — no server-side audit beyond the Apps Script sheet. If needed later, the existing weekly-digest path can be extended.
