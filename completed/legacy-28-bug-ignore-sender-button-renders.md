# 28. Bug: Ignore-sender button renders for protected address-form senders — 0446ed9

Filed 2026-04-25 from Tom: noticed during item-27 verification that Ellen's events (her self-notes about the kids' activities — exactly what the kid_names query / item 25 surfaces) were rendering with an "Ignore sender" button despite Ellen being on the protected list (item 26). One fat-finger click would have ignored Ellen's address from the UI side, working around the auto-blocklist protection.

**Root cause.** `process_events.py:895` was calling `is_protected(domain, protected)` — passing the bare registrable domain (e.g. `gmail.com`) for a freemail sender. Address-form patterns added in #26 (`ellen.n.holmes@gmail.com`) only match when the sender is itself an address (per `protected_senders.is_protected`); the bare-domain query never fired and the button rendered. The #26 design note explicitly stated this case was supposed to work as a side-effect of address-form support; the missed integration on the render side wasn't caught at the time because the existing render-integration tests only exercised institutional domains (where `block_key == domain` and the bug is invisible).

**Scope (Tom-confirmed).** Suppress only the Ignore-*sender* button on protected-sender cards; the Ignore-*event* button stays — the user can still hide a single event from a protected sender, they just can't sweep the whole sender by accident.

**Fix (0446ed9).** One-line code change: pass `block_key` (full address for freemail, bare domain otherwise) instead of `domain` to `is_protected`. Both pattern shapes are handled uniformly by the matcher — bare-domain patterns continue to match for institutional senders, address-form patterns now match for freemail senders. Surrounding comment rewritten to explain the corrected semantics and the per-event-ignore distinction.

**Tests.** Two new render-integration tests in `tests/test_protected_senders.py` mirroring the existing institutional pair: protected freemail address (Ignore-sender suppressed AND Ignore-event still rendered — both pinned), unprotected freemail address sharing a protected sender's domain (Ignore-sender kept — address-form protection is per-address, not per-domain).

**Live verification.** Tom verified post-deploy that Ellen's event cards no longer render the Ignore-sender button (but Ignore-event still works); fat-finger gap closed. Tom signed off 2026-04-25.
