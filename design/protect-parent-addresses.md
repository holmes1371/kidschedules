# Protect parent addresses from auto-blocklist

ROADMAP item #26. Filed 2026-04-24, immediately after item #25's kid_names query landed and was observed to **not** catch Ellen's "Everly volleyball" self-note. Investigation showed the kid_names query was constructed correctly — but every Gmail query (the new one and the prior five) carries `-from:ellen.n.holmes@gmail.com` in its exclusion clause because Ellen's address is in `blocklist_auto.txt` since 2026-04-14.

## How Ellen's address ended up in the auto-blocklist

Origin: `blocklist_auto.txt` row dated 2026-04-14:

```
ellen.n.holmes@gmail.com  # auto 2026-04-14: adult personal email about tax return, no child events
```

The agent (extract_events / irrelevant_senders) saw a tax-related email Ellen sent herself, judged it adult-only, emitted a high-confidence `irrelevant_senders` flag, and `update_auto_blocklist.py` accepted the suggestion. The agent's per-email judgment was fine for that specific email; what's wrong is the policy: **a parent's personal address must never be auto-blocked, because that address is the carrier for self-notes capturing schedule snippets** — exactly the failure mode item #25 was designed to fix.

## Two structural problems

### 1. There is no per-address protection mechanism

`scripts/protected_senders.py::is_protected` reduces every sender to its registrable domain before matching:

```python
if "@" in s:
    s = s.rsplit("@", 1)[1]
```

So a `protected_senders.txt` line like `ellen.n.holmes@gmail.com` is loaded fine, but `is_protected("ellen.n.holmes@gmail.com", patterns)` only ever compares `gmail.com` against the patterns. We can't currently express "protect this *address*, not this *domain*" — and we'd never want to protect the entire `gmail.com` domain because every personal Gmail block currently lives there.

### 2. `update_auto_blocklist.py` doesn't share the protection list

`update_auto_blocklist._is_protected` is a separate hardcoded `PROTECTED_SUFFIXES` constant ([update_auto_blocklist.py:30-41](../scripts/update_auto_blocklist.py)):

```python
PROTECTED_SUFFIXES = (
    "fcps.edu", "pta.org", "ptsa.org", "jackrabbittech.com",
    "teamsnap.com", "signupgenius.com", "myschoolbucks.com",
    "lifetouch.com",
)
```

This is a parallel list to `protected_senders.txt`, also domain-only, and *not synchronized*. Even if we extend `is_protected` to support address-form patterns, the auto-blocklist gating won't notice unless we route it through the shared matcher.

So Ellen's address slipped through both gates: `protected_senders.txt` (read by `build_queries.py` to filter the ignored-senders union) couldn't have protected her even if she'd been listed, and `update_auto_blocklist.PROTECTED_SUFFIXES` doesn't read `protected_senders.txt` at all.

## Decision

Three coordinated changes:

1. **Extend `is_protected` to support address-form patterns.** A `protected_senders.txt` line containing `@` matches the full lowercased address; a line without `@` continues to match the registrable domain (and `*`-prefixed suffix patterns continue to match domain suffixes). Backward-compatible: the existing fcps.edu / *pta.org / etc. patterns keep working.

2. **Unify `update_auto_blocklist._is_protected` with the shared matcher.** Drop `PROTECTED_SUFFIXES`; load `protected_senders.txt` via the shared loader and call `is_protected(address, patterns)`. Single source of truth — one file edit (this design note + the tests) protects both consumers.

3. **Add Ellen + Tom to `protected_senders.txt`** as address-form patterns:

   ```
   ellen.n.holmes@gmail.com
   thomas.holmes1371@gmail.com
   ```

   Under a new comment block titled "Family senders (never auto-block)" so the rationale is on the page. These two addresses send the family's self-notes; auto-blocking them would silently drop schedule signal.

Plus a belt-and-suspenders prompt change:

4. **Add a "NEVER flag" line for family/parent personal addresses in `agent.py::_EXTRACTION_BASE_PROMPT`.** The prompt currently lists schools, extracurricular providers, and medical providers as untouchable. Adding "family/parent personal email addresses" reduces the chance the agent emits the flag in the first place, which both saves the gating-layer reject step and avoids a polluted audit log.

And a **state-branch cleanup** — the existing `ellen.n.holmes@gmail.com` row in `blocklist_auto.txt` on `origin/state` will keep filtering Ellen until it's removed. The next cron run won't *re-add* Ellen (gating fix prevents that), but it also won't *evict* an existing row. Manual surgery: edit the file on origin/state and commit. **This step modifies shared production state, so it requires explicit user confirmation per auto-mode policy — not bundled into the code commits.**

## Decisions locked in

- **Schema for address-form patterns: full lowercase address.** A single `@` per line distinguishes address from domain. No regex, no globbing inside the local part — keep it simple. If a future need emerges (e.g. wildcard-local-part), revisit.
- **Case-insensitive matching.** Existing posture in `is_protected` already lowercases inputs and patterns; address-form pins to that.
- **No change to `process_events.py`'s render-time use of `is_protected`.** The Ignore-sender button is suppressed for protected domains; with address-form support, it'll also be suppressed for protected addresses. That's the correct behavior — Ellen and Tom should never be Ignore-sender targets either.
- **`update_auto_blocklist` will read `protected_senders.txt` at runtime** (path passed via existing `--protected-senders` flag pattern, with a sensible default). No environment baking; no caching.
- **The hardcoded `PROTECTED_SUFFIXES` constant is removed entirely.** Listing those domains twice (in `protected_senders.txt` and in the constant) was the parity hazard that produced this bug; eliminating the constant kills the hazard. The protected_senders.txt file already contains all the entries from the constant — verified before deleting.
- **Agent-prompt update is additive only.** A new line in the "NEVER flag" list, no other changes. No fixture / golden-output break expected because the existing extraction tests don't pin against that paragraph's exact wording.

## Accepted risk

- **Ellen sometimes legitimately sends adult-only emails.** A tax confirmation, a personal note, a non-kid update. Under this policy, those will *also* now reach the agent for extraction (cost) and won't be auto-blocked. The agent's per-email judgment will return zero events for them, so they fall out at the extraction stage; the only cost is a few extra agent tokens per Ellen-sent email per run. Bounded — Ellen doesn't send dozens of personal emails per week.
- **A future family member's address will need a manual addition** to `protected_senders.txt`. Not roster-derivable (the roster is the kids, not the parents). One-line text edit when needed; called out in the comment block.
- **The agent could still flag a family address if the prompt-level guardrail drifts.** That's why the gating-layer protection (steps 1–3) is the load-bearing fix; the prompt update (step 4) is hygiene.

## Helper contracts

`is_protected(sender, patterns)` after the change:

- Lowercase `sender` and pattern set as before.
- If `sender` contains `@`, the *full address* form is the comparison key for any pattern that contains `@`; the *domain after the last `@`* is the comparison key for any pattern that doesn't contain `@` (bare-domain or `*`-suffix).
- Patterns containing `@` match by full-address equality, case-insensitive.
- `*`-prefix patterns continue to match by domain-suffix.
- Empty `sender` is never protected.
- A protected_senders.txt line containing `@` IS treated as a literal address, not glob — `*@gmail.com` would parse as a `*`-suffix pattern (not address-form) and is not part of the schema. To protect every Gmail address, list each address explicitly; we don't currently have that need.

`update_auto_blocklist.main()` after the change:

- Reads `protected_senders.txt` at startup via `protected_senders.load_protected_senders`.
- For each suggestion, calls `is_protected(address, patterns)` directly — no longer extracts the domain first. The matcher handles both pattern shapes internally.
- Rejection reason becomes `protected sender ({address})` when an address-form match wins, `protected domain ({domain})` for the existing bare-domain / suffix path. Audit log carries the more specific reason.

`load_protected_senders` is unchanged — already returns lowercased pattern strings opaquely; the new schema is purely a matcher concern.

## Tests

- **`tests/test_protected_senders.py`**:
  - `test_is_protected_matches_address_form_pattern_against_address` — pattern `alice@example.com`, sender `alice@example.com` → True.
  - `test_is_protected_does_not_match_address_form_against_other_address_same_domain` — pattern `alice@example.com`, sender `bob@example.com` → False (load-bearing for Tom-vs-Ellen and for any Gmail user not in the parents list).
  - `test_is_protected_does_not_match_bare_domain_against_address_form_pattern` — pattern `alice@example.com`, sender `example.com` → False (bare-domain queries don't accidentally win against an address pattern).
  - `test_is_protected_address_form_case_insensitive` — pattern `Alice@Example.COM`, sender `ALICE@example.com` → True.
  - `test_is_protected_bare_domain_still_matches_for_address_sender` — pattern `example.com`, sender `alice@example.com` → True (regression pin for the existing matcher behavior; nothing should regress).
  - `test_is_protected_wildcard_suffix_still_works` — pattern `*pta.org`, sender `alice@louisearcherpta.org` → True.

- **`tests/test_update_auto_blocklist.py`**:
  - `test_main_rejects_address_form_protected_sender` — `protected_senders.txt` contains `ellen.n.holmes@gmail.com`; suggestion list has a high-confidence flag for `ellen.n.holmes@gmail.com`; `main()` rejects it with reason starting with `protected sender (`. Audit log line carries the rejection.
  - `test_main_still_rejects_bare_domain_protected_sender` — same flow with a `fcps.edu` domain pattern; ensures domain protection still wins.
  - `test_main_passes_through_unprotected_address` — sender at an unrelated domain still gets added when confidence is high; ensures the unification didn't over-broaden rejection.

- **`tests/test_build_queries.py`** (parity check):
  - `test_address_form_protected_sender_filtered_from_ignored_senders_union` — the existing dropped-protected check (item 20) already filters protected senders out of the Gmail query exclusion; pin that the address-form case is honored.

## Commit plan

1. **Design note + ROADMAP item 26 flip.** This commit. No behavior change.
2. **Matcher extension + tests.** `protected_senders.py::is_protected` grows address-form support; `tests/test_protected_senders.py` adds the 6 new pins.
3. **Unify auto-blocklist gating.** `update_auto_blocklist.py` reads `protected_senders.txt`, drops `PROTECTED_SUFFIXES`; `tests/test_update_auto_blocklist.py` adds the 3 new pins.
4. **Add Ellen + Tom to `protected_senders.txt`.** Belt-and-suspenders agent-prompt update in the same commit (one logical change: protect the parents).
5. **Close-out.** ROADMAP session-summary update + commit. Item stays `[~]` pending state-branch cleanup + Tom's live-cron verification.

State-branch cleanup is a separate, explicitly-confirmed action — not bundled into the commit chain.

## Non-goals

- **Roster-derived parent list.** Roster is the kids. The parents' addresses are configuration, not roster data. Don't conflate.
- **Glob support inside the local part of an address.** Out of scope; not currently needed.
- **Migrating existing `blocklist.txt` entries to address form.** That file is hand-curated; if a row ever needs to be address-specific, the operator can edit it. The auto-blocklist is where the divergence shows up because the agent emits address-form suggestions.
- **A "family senders" allowlist that bypasses the exclusion clause entirely.** The protected_senders mechanism already does this once the matcher supports address form; no parallel mechanism needed.
- **Re-running the agent against historical Ellen-sent emails to back-fill events.** The next cron run will pick up anything in the lookback window after the cleanup; older events outside the window are not worth the complexity.
