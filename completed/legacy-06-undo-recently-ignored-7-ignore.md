# 6. Undo recently ignored + 7. "Ignore sender" (bundled)

Bundled because they share all their surfaces — Apps Script routing, a second Google Sheet tab, client-side button/toggle work in the rendered HTML, and a new workflow sync step. Full design: `design/ignore-undo-and-block-sender.md` (includes locked decisions, 10-step commit plan, responsibility table, non-goals).

Locked model (from the design note, not re-debated): render-but-hide (no 5-minute toast); per-card Unignore button in solid-green variant replaces Ignore on ignored cards; header **Show ignored (N)** toggle; registrable-domain blocking via `tldextract`; LLM echoes `source_message_id` → Python does all sender parsing.

Progress against the 10-step commit plan:

1. [x] Design note + ROADMAP insert — f7f3425 · 82979d6 (palette amendment)
2. [x] `agent.py` schema bump (`source_message_id` field, prompt update, parser validation, 9 unit tests) — 518b4ad
3. [x] `main.py` sender-domain attachment + `tldextract>=5.1.0` added to `requirements.txt` (10 unit tests) — eebae6f
4. [x] `events_state.py` schema v2 (optional `sender_domain` per event; blow-away-and-rebuild on mismatch) — 96795dd
5. [x] `process_events.py` render-but-hide model (classify/render changes, Show/Hide toggle, Ignore-sender button) — 220b083; design amended post-step-5 to standardize on "ignored senders" vocabulary end-to-end (b07bdf7)
6. [x] `scripts/apps_script.gs` action router (`ignore` / `unignore` / `ignore_sender`; `?kind=ignored_senders` GET route; second tab "Ignored Senders") — 9935d60
7. [x] `scripts/sync_ignored_senders.py` fetch-and-write helper + 13 unit tests — 8d51750
8. [x] Workflow "Sync ignored senders" step — a9f070c. `ignored_senders.json` is ephemeral (option A): written to the runner's working dir only, no commit-on-main — matches the existing `ignored_events.json` sibling. Design note updated to reflect this (sections: intro, architecture-update, Workflow changes, Commit plan step 8, ripple-through).
9. [x] Client JS in `docs/index.html` (Unignore, Show/Hide toggle, Ignore sender, toast helpers, localStorage hydration) — 646993c
10. [x] CSS fix for action-row overlap (flex wrapper for Add-to-calendar + Ignore event buttons) — bf34506
11. [x] Gap closure: wire `ignored_senders.json` into `build_queries.py` so UI-clicked Ignore-sender decisions actually exclude those domains from Gmail searches at fetch time — e97f1b0
12. [x] Protected-senders guardrail: `protected_senders.txt` at repo root (seeded from `blocklist.txt`'s NEVER-add list) + shared `scripts/protected_senders.py` loader. Both `process_events.render_html` (suppresses the Ignore-sender button) and `build_queries.main` (filters protected domains out of the ignored_senders union) read the same file — defense in depth so the user can't accidentally block schools, PTAs, team-management platforms, or health providers — 2393d31
13. [x] Ignore-sender sweeps sibling cards locally — 7cf8cb3

    Click-time the handler now `querySelectorAll('.event-card[data-sender="<domain>"]')`, optimistically hides every match via `setIgnored`, persists ids to the shared `localStorage` key, and bumps `Show ignored (N)` by the number newly hidden. Cards already `data-ignored="1"` (server-ignored or event-ignored earlier in the session) are skipped so the counter stays accurate. POST failure reverts the entire sweep (`setActive` each card, drop ids, `bumpToggle(-swept.length)`, toast). Fade is deliberately skipped for the sweep — staggered fades across many cards read as jank — single-card Ignore keeps its 300ms fade. Unignore-sender remains intentionally asymmetric: no per-card affordance; domain stays on the Ignored Senders sheet until edited there. `bumpToggle` also gained a zero-to-one create path so a first local ignore on an `ignored_n == 0` page still surfaces the counter. 4 regression tests in `tests/test_process_events.py`.

    The "intentionally asymmetric" framing was superseded by sub-item 14 once the janky Unignore-event surface on sender-swept cards surfaced in use; the sender-sweep's id-push to Ignored Events also got rolled back under that pass (events now stay out of the events tab when sender-ignored — Gmail-query exclusion is the persistence path).

14. [x] Unignore-sender button + optimistic-Unignore parity — 4feaa2d · f24b81e

    Closes the UX gap where a sender-swept card showed "Unignore event" but clicking it did nothing visible (Ignored Events had no matching id; pessimistic handler masked the POST round-trip). Schema bumped: Ignored Events rows now carry a 5th `sender` column so `action=unignore_sender` can bulk-delete by sender in addition to wiping the Ignored Senders row. Client-side: new `kids_schedule_ignored_senders` localStorage key (sender-sweep writes only the domain now, no per-event-id pushes — keeps Ignored Events a pure individual-ignore record), `data-ignored-reason` attribute dispatches the right Unignore variant, and both Unignore paths are optimistic for latency parity with Ignore. Design note at `design/unignore-sender.md`. Legacy 4-column Ignored Events rows are intentionally left out of the bulk-delete path — cleared individually or by hand in the sheet. 8 new regression tests; 2 sub-item-13 tests updated to reflect the storage-key split.

    **Deploy required before this is live.** Paste `scripts/apps_script.gs` into the bound Apps Script editor (preserve the real `READ_SECRET` — the repo copy is a placeholder), then Deploy → Manage deployments → edit the existing web app deployment → New version → Deploy. Without that step `action=unignore_sender` returns `bad action` and Ignore-event rows keep writing 4 columns; client code is defensive against both, so users just see no behavior change until the redeploy lands.

    **Live smoke test after deploy:** (1) Ignore event → new sheet row has 5 columns incl. sender. (2) Unignore event feels as snappy as Ignore (optimistic). (3) Ignore sender hides every sibling, adds a row to Ignored Senders only — Ignored Events is not touched. (4) Page refresh preserves sender-swept state via `kids_schedule_ignored_senders` localStorage. (5) Unignore sender reveals every card from that domain, incl. any individually-ignored ones (server-side bulk delete by column 5). (6) Protected-sender guard unchanged: cards in `protected_senders.txt` still render without an Ignore-sender button. Pre-existing 4-column rows from sub-item 13's sender-sweep won't be caught by Unignore-sender — expected; clear individually if they drift into view.
