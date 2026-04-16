# Unignore-sender button + latency parity

ROADMAP sub-item 14 under 6+7. Small surface, but it resolves a UX gap discovered after sub-item 13 (Ignore-sender sweeps siblings locally):

- A sender-swept card shows an **Unignore event** button even though the event-id is usually not in Ignored Events — only the sender is in Ignored Senders. Clicking Unignore hits `_handleUnignore`, which deletes zero rows, returns `ok`, and the card stays hidden for 1–3s while the pessimistic handler waits on the round-trip. Looks broken.
- There's no way to reverse a sender-ignore from the page. Today the only path is editing the "Ignored Senders" sheet tab by hand.
- Unignore (event) is noticeably slower than Ignore because Ignore is optimistic and Unignore is pessimistic. Flipping Unignore to optimistic costs nothing in practice — the webhook doesn't flake — and matches the feel of Ignore.

## Semantics: two independent tabs, one coherent story

The spreadsheet has two tabs and they are not joined by the server:

- **Ignored Events** — rows of `(timestamp, id, name, date)`. Filtered against candidate events in `process_events.py` after extraction.
- **Ignored Senders** — rows of `(timestamp, domain, source)`. Synced into `ignored_senders.json` and baked into the Gmail query as `-from:domain` by `build_queries.py`, so those senders never get fetched in the first place.

The schema bump under this design adds a **5th column** `sender` to Ignored Events so the Apps Script can bulk-delete events for a sender when the sender is unignored. Existing rows (written before this change) have no sender column — they're left alone; legacy rows can only be cleared by individual unignore-event. This tradeoff was agreed with Tom: "it's fine that the legacy ones won't be in sync but everything going forward will work with the new process."

### Option X: sender-sweep is client-visual only

Sender-sweep (sub-item 13) currently pushes every swept card's event-id into `localStorage["kids_schedule_ignored_ids"]` and relies on the server-side `ignore_sender` row to cover the persistent case. That double-write is redundant — the Gmail query already excludes the sender on the next build, so those events never get fetched. Under this change, sender-sweep stops touching the events list entirely:

- Client-side: a new `localStorage["kids_schedule_ignored_senders"]` key holds a list of domain strings. Sweep adds the domain once; no per-event-id writes.
- Server-side: `ignore_sender` still appends a row to Ignored Senders (unchanged).
- Hydration: on page load, any card whose `data-sender` is in `kids_schedule_ignored_senders` gets `setIgnored` + `data-ignored-reason="sender"`.

This keeps Ignored Events as a pure record of "events the user specifically ignored" and means **Unignore-sender = delete one row from Ignored Senders + one bulk-delete from Ignored Events by sender column**, with no leftover stale event-ids.

## Card contract

Cards already carry `data-event-id` and `data-ignored="1"` when ignored. New attribute:

- `data-ignored-reason="event" | "sender"` — written by the client at ignore time (and on hydration). Distinguishes "individually ignored" from "swept by sender-ignore". Omitted on active cards.

Button visibility rules, enforced by a single CSS rule plus the existing `is_protected` server-side check:

| Card state                  | Ignore event | Unignore event | Ignore sender | Unignore sender (domain) |
|-----------------------------|--------------|----------------|---------------|--------------------------|
| Active, has sender          | ✓            | —              | ✓             | —                        |
| Active, no sender           | ✓            | —              | —             | —                        |
| Ignored (reason=event)      | —            | ✓              | —             | —                        |
| Ignored (reason=sender)     | —            | —              | —             | ✓                        |

The `.ignore-sender-btn` is hidden via CSS on ignored cards (it's already present in markup for the active state; hiding instead of re-rendering keeps the delegated router simple). Unignore buttons' label, handler, and payload dispatch off `data-ignored-reason`.

## Latency parity: both unignores optimistic

Ignore is already optimistic. Unignore (event) and Unignore (sender) flip to the same pattern:

1. Immediately `setActive` the card(s), drop id(s) / domain from localStorage, `bumpToggle(-N)`.
2. POST in background.
3. On failure: re-apply `setIgnored`, restore localStorage, `bumpToggle(+N)`, toast "Unignore failed — try again".

Same revert envelope as Ignore's. The pessimistic design in step 9 of the original plan was defensive against webhook flakes; in practice we haven't seen any and a revert is indistinguishable from Ignore's failure path.

## Apps Script changes

`_handleIgnore(payload)`:
- Accept optional `payload.sender` (string, lowercased, validated against `DOMAIN_RE` if non-empty; blank and invalid strings fall through to empty-string rather than rejecting, so the existing 4-column writers keep working).
- Row shape becomes `[timestamp, id, name, date, sender]`. Existing 4-column rows are fine — the Unignore-by-id path reads column 1 (id), and the new Unignore-by-sender path reads column 4 (sender) which returns empty string for legacy rows (no match → not deleted, acceptable per the legacy-drift stance).

`_handleUnignoreSender(payload)`:
- New handler, wired in `doPost` as `action === "unignore_sender"`.
- Validates `payload.domain` against `DOMAIN_RE`.
- Deletes every row from Ignored Senders where column 1 matches the domain (idempotent — zero rows deleted is still `ok`).
- Deletes every row from Ignored Events where column 4 matches the domain (bottom-up row iteration, same pattern as `_handleUnignore`).
- Returns `ok`.

`_listIgnoredEvents`:
- No read-side change required. Python `_load_ignored_ids` only reads `id`. The new sender column can be present or absent in the JSON; downstream doesn't care. Keeping the returned JSON minimal (no new field) keeps the snapshot of the GET shape stable.

## Client changes (process_events.py)

- New constant `SENDERS_STORAGE_KEY = "kids_schedule_ignored_senders"`, plus `loadIgnoredSenders` / `saveIgnoredSenders` helpers mirroring the ids pair.
- `setIgnored(card, reason)` / `setActive(card)` take an explicit reason so the `data-ignored-reason` attribute stays in sync. Default reason if omitted is `"event"` (keeps existing call sites working).
- Card template sets `data-ignored-reason="event"` when `is_ignored` is true in the server render (honoring entries from `ignored_events.json`).
- CSS: `.event-card[data-ignored="1"] .ignore-sender-btn { display: none; }` and a new `.unignore-sender-btn` selector with the same green palette as `.unignore-btn`.
- The single `.unignore-btn` server-rendered button gets swapped client-side to "Unignore sender (domain)" on sender-swept cards via the sweep path; on active cards rendered with `is_ignored=True` (reason=event) it says "Unignore event" as today.
- New `.unignore-sender-btn` class for the sender-ignored variant; click router dispatches on class name.
- Hydration loop now does two passes: event-ignored ids → `setIgnored(card, "event")`; sender-ignored domains → for each card with matching `data-sender`, `setIgnored(card, "sender")`.
- Ignore-sender branch: push `domain` to `SENDERS_STORAGE_KEY` (not per-event-id), sweep siblings via `setIgnored(card, "sender")`, bump counter, POST `ignore_sender`. Revert on failure: `setActive` siblings, drop domain from localStorage, `bumpToggle(-N)`.
- Unignore-event branch: optimistic `setActive` + drop id + `bumpToggle(-1)` + POST `unignore`. Revert on failure.
- New Unignore-sender branch: query all cards with matching `data-sender`, `setActive` each + drop domain + `bumpToggle(-N)` + POST `unignore_sender`. Revert on failure.

## Non-goals

- **Backfilling legacy Ignored Events rows with sender.** Agreed out of scope. A small manual cleanup can happen in the sheet UI if needed.
- **Per-sender counter in the header.** Show ignored (N) stays a flat count of hidden cards regardless of reason.
- **Protected-sender guard on Unignore-sender.** The Unignore-sender button only appears on cards that are already sender-ignored. By construction those cards had an Ignore-sender button that passed the `is_protected` guard. Re-checking at unignore time adds no value.

## Test plan

Substring-level JS wiring assertions in `tests/test_process_events.py`:

- Card template emits `data-ignored-reason` when `is_ignored` is true.
- CSS rule hides `.ignore-sender-btn` on ignored cards.
- Click router branches on `.unignore-sender-btn` and posts `"action": "unignore_sender"`.
- Sender-sweep writes to `kids_schedule_ignored_senders` localStorage key (not `kids_schedule_ignored_ids`).
- Hydration inspects both localStorage keys.
- Unignore-event path is optimistic (setActive called before POST).

Existing sub-item 13 tests that asserted sender-sweep pushes event-ids to `kids_schedule_ignored_ids` get updated to assert the domain-list write instead. That's the intended behavior change under X; the spirit of the test (sender-sweep persists locally so a refresh preserves the sweep) is preserved.

Apps Script changes aren't unit-tested (no test harness for `.gs`); smoke-verified against the live deploy after each change.

## Commit plan

1. **Apps Script** — add sender column to `_handleIgnore` write, add `_handleUnignoreSender`, wire the `unignore_sender` action.
2. **process_events.py** — client JS overhaul + CSS rule + regression tests. Update sub-item 13 tests that assumed ids-only storage.
3. **ROADMAP** — close sub-item 14 under the 6+7 bundle with the Commit B SHA.
