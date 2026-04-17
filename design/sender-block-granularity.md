# Freemail-aware sender-block granularity

ROADMAP item #20. Follow-up to the original Ignore-sender bundle (items 6+7) after live observation that the domain-level block is too coarse for shared consumer email.

## Problem

Today `main.py::_attach_sender_domains` runs every sender through `tldextract.top_domain_under_public_suffix` and stores the result as `sender_domain`. For `jane.smith@gmail.com` that's `"gmail.com"`. The card renders a button labeled "Ignore sender (gmail.com)" and clicking it writes `"gmail.com"` into the Ignored Senders sheet. `build_queries.py` bakes `-from:gmail.com` into the Gmail query on the next workflow run — blocking every gmail.com sender, not just the one the user clicked on. Screenshot captured on 2026-04-17 shows two unrelated gmail.com senders both rendering the same button, each of which would silently nuke the other.

The fix has to preserve three things:

1. **Institutional domains keep working as today.** One click on an `fcps.edu` or `jackrabbittech.com` card should still block the whole domain — that's usually the intent there, because the whole org is a single distribution.
2. **Protected domains stay protected, full stop.** Nothing in this change is allowed to weaken the guard against blocking `fcps.edu`, `*pta.org`, `teamsnap.com`, etc. The guardrail at `scripts/protected_senders.py::is_protected` has to stay load-bearing through the new granularity.
3. **Today's blocklist/ignored-senders machinery stays intact.** `blocklist.txt` and the Gmail-query exclusion path, added in the original bundle and closed-the-gap commits (`e97f1b0`, `2393d31`), continue to work without reshaping.

## Decisions locked in

- **Freemail-aware.** A known-consumer-email list (gmail.com, yahoo.com, outlook.com, hotmail.com, icloud.com, aol.com, me.com, mac.com, live.com, msn.com, comcast.net, verizon.net, protonmail.com, proton.me, fastmail.com, gmx.com, gmx.us, yandex.com, zoho.com, seed; more can be added with a one-line commit) triggers address-level blocking. Anything else falls back to today's domain-level blocking.
- **File-based list** (`freemail_domains.txt` at repo root), same loader pattern as `protected_senders.txt` — case-insensitive, `#` comments, blank-line tolerant. Deliberately no wildcard support: freemail is a closed set.
- **Two-field model on each event.** `sender_domain` keeps its meaning (registrable domain, used by `is_protected` and general sender grouping). New field `sender_block_key` is the string the Ignore-sender button submits. For freemail, lowercased full address (`alice.smith@gmail.com`); for everything else, equal to `sender_domain`. Clean separation between "what domain is this from" and "what do we block on."
- **Lowercase-only canonicalization.** No dot-stripping on gmail, no plus-addressing canonicalization. The kids-school-alerts corpus does not use those gmail tricks in any observable way, and the bookkeeping cost of per-provider canonicalization rules outweighs the payoff.
- **Address-aware `is_protected`.** The guard learns one new trick: if its input contains an `@`, it splits on `@` and matches the domain part against the patterns. Domain inputs keep behaving exactly as today. This is the load-bearing guarantee that no manipulation of the Ignored Senders sheet — UI click, hand edit, stale row, or bug upstream — can land a protected domain in the Gmail query.
- **Schema bump to `events_state.py` v3.** Existing v2 cache entries lack `sender_block_key`. Rather than a fallback-in-classify that produces mixed-granularity cards during a 120-day decay, blow away on mismatch (matching the v1→v2 policy). One run's worth of 60-day re-extraction is the cost; we already pay that on any prompt bump.
- **Apps Script admits both domain and address shapes.** `DOMAIN_RE` sits unchanged for validation of the sender column on Ignored Events (that column stores what the button payload carried; the new button carries block keys). A sibling `SENDER_RE` admits domain-or-address, wired into `_handleIgnoreSender`, `_handleUnignoreSender`, `_listIgnoredSenders`, and the sender column of `_handleIgnore`. Payload key name stays `domain` for wire-protocol backward compatibility — documented as "block identifier, historically a domain" in the header comment. No JS rename churn; no flag day.
- **Existing sheet rows are Ellen's to clean up.** A `gmail.com` / `yahoo.com` row Ellen accumulated in the last few weeks would keep nuking the whole domain until deleted manually from the Ignored Senders tab. Cleanup is a one-minute task for a sheet that probably carries <20 rows total, and migration code is a lot of surface for a one-time task.

## Data model

On each event dict:

| Field | Semantics | Populated by |
|---|---|---|
| `sender_domain` | Registrable domain, lowercased (e.g. `gmail.com`, `fcps.edu`). Empty string when attribution fails. Used by `is_protected`, sender grouping, and newsletter stats (no change from today). | `main.py::_attach_sender_domains` via `tldextract` |
| `sender_block_key` | String the Ignore-sender button submits. For freemail, lowercased full address (`alice@gmail.com`); for everything else, equal to `sender_domain`. Empty string when `sender_domain` is empty. | `main.py::_attach_sender_domains` via `_compute_block_key(addr, domain, freemail_set)` |

`sender_block_key` is optional in the cache (schema v3 carries it; v2 entries that survive the blow-away won't exist). `classify` normalizes to `""` if missing; `render_html` emits no Ignore-sender button when it is empty (same gate as `sender_domain` today).

## Derivation

New helper in `main.py`:

```python
def _compute_block_key(addr: str, domain: str, freemail: frozenset[str]) -> str:
    """Decide what string the Ignore-sender button should submit.

    Freemail domains (gmail.com, yahoo.com, etc.) block one address at
    a time; institutional domains (fcps.edu, jackrabbittech.com) block
    the whole domain. Membership is defined by freemail_domains.txt.
    """
    if not domain:
        return ""
    if not addr or domain not in freemail:
        return domain
    return addr.strip().lower()
```

`_attach_sender_domains` grows a `freemail: frozenset[str]` parameter (defaulting to the loader output). Each event gets both fields stamped. Every existing failure path (missing `source_message_id`, empty `from_`, `parseaddr` returned no address, `tldextract` returned no domain) sets both fields to `""` so the render-time gate remains one condition, not two.

No `main.py` code path beyond `_attach_sender_domains` needs to know about the new field. The rest is pure passthrough through the pipeline.

## Rendering

`scripts/process_events.py`:

- `classify` normalizes `sender_block_key` alongside `sender_domain` — same `.strip()` treatment, no default derivation (an explicit `""` stays `""`).
- `render_html` reads `sender_block_key` for the `data-sender` attribute, the button label `"Ignore sender ({block_key})"`, and the POSTed payload value. The **protected guard still keys on `sender_domain`**, not the block key — the guard is a domain-level semantic and the address form would never match the existing patterns directly. The guard is the protection; everything else is presentation.

Concretely:

```python
block_key = (ev.get("sender_block_key") or "").strip()
domain    = (ev.get("sender_domain")    or "").strip()
sender_attr = f' data-sender="{block_key}"' if block_key else ""
sender_btn_html = ""
if block_key and not is_protected(domain, protected):
    sender_btn_html = (... f'Ignore sender ({block_key})' ...)
```

The existing markup slots (`.event-actions-bottom` container, the button's class and data attribute names) are preserved — only the value shape changes.

Snapshot tests update. Fixture `ignored_and_sender.json` grows a gmail.com sender pair (`alice@gmail.com` + `bob@gmail.com`) so the fixture proves the button label and `data-sender` diverge between the two cards.

## Protected-senders guardrail (the load-bearing guarantee)

`scripts/protected_senders.py::is_protected` learns one branch:

```python
def is_protected(sender: str, patterns: list[str]) -> bool:
    s = (sender or "").strip().lower()
    if not s:
        return False
    # Accept either a domain ('fcps.edu') or an address
    # ('alice@fcps.edu'); for addresses, match on the domain part.
    if "@" in s:
        s = s.rsplit("@", 1)[1]
        if not s:
            return False
    for pat in patterns:
        if pat.startswith("*"):
            suffix = pat[1:]
            if suffix and s.endswith(suffix):
                return True
        elif s == pat:
            return True
    return False
```

Behavior preservation:

- Existing domain inputs (`fcps.edu`, `greenfield.k12.ny.us`, `louisearcherpta.org`) match exactly as today; the `@`-branch is skipped.
- Address inputs (`alice@fcps.edu`, `coach@louisearcherpta.org`, `noreply@teamsnap.com`) get their domain extracted and match the same patterns. No pattern-file changes, no wildcard semantic changes.
- Empty-after-`@` (like `alice@`) hits the early return — never protected-false-positive, never mismatches a legit protected domain.

`build_queries.py` already calls `is_protected` on every entry from `ignored_senders.json` before unioning into the exclusion list. Because that entry list now includes address-shaped strings and the guard now handles them, no code change is needed in `build_queries.py` itself — only a docstring and log-string update. A test pins the guarantee end-to-end: `alice@fcps.edu` in the synthetic ignored_senders input drops out of the exclusion union.

## Schema v3 bump

`events_state.py`:

- `CURRENT_SCHEMA_VERSION` → `3`.
- `load_state` mismatch path unchanged — prints warning, returns empty state. One run of 60-day re-extraction fills the cache back in, with both `sender_domain` and `sender_block_key` populated.
- No per-field migration code. The bump is the migration.

Tests update:

- `test_load_state_wrong_schema_version` and the v1→v2 parallel test grow v2-now-stale assertions.
- A round-trip test writes an event with `sender_block_key` populated and reads it back.

## Apps Script

`scripts/apps_script.gs`:

- New constant `SENDER_RE = /^(?:[^\s@]+@)?[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$/` — matches a bare domain, or a domain prefixed with `<local-part>@`. Case-sensitive-lower by convention; all inputs lowercased before matching.
- `_handleIgnore` — the `sender` column write validates against `SENDER_RE` (was `DOMAIN_RE`). Legacy 4-column rows still survive `_listIgnoredEvents` untouched (that reader doesn't inspect column 5). Column 5's value is now "block identifier (historically a domain)" — comment updated.
- `_handleIgnoreSender` — validates `SENDER_RE`, writes to Ignored Senders as `[timestamp, <block_key>, 'auto-button']`. Sheet column name stays "domain" for continuity; Tom/Ellen knows it now carries block identifiers.
- `_handleUnignoreSender` — validates `SENDER_RE`, deletes rows from Ignored Senders matching column 1, deletes rows from Ignored Events matching column 5. Both matches use lowercased exact equality on the whole string — an `alice@gmail.com` unignore does not match a `gmail.com` row and vice versa. That's correct: a user who ignored at the address level unignores at the address level.
- `_listIgnoredSenders` — admission filter loosens to `SENDER_RE`.
- Payload key stays `payload.domain` for both `ignore_sender` and `unignore_sender`. Header comment updated to name it "block identifier."

No deployment coordination risk: `SENDER_RE` is strictly broader than `DOMAIN_RE`, so old clients that send domain-only payloads still pass; new clients that send address payloads pass on the new deploy and fail gracefully ("bad domain" text response) against a pre-deploy script, matching the existing unrelated-version failure mode.

Apps Script has no unit tests; smoke-verified against the live deploy after the commit lands, same posture as every prior Apps Script change.

## `build_queries.py`

Literal logic stays; only documentation changes:

- `load_ignored_senders` docstring: "Return the block-identifier strings from the ephemeral `ignored_senders.json` cache. Historically these were always domains; since ROADMAP #20 they may also be lowercased email addresses for freemail senders."
- The `--protected-senders` help text mentions the address-form handling.
- The exclusion-meta block key `blocklist_size_ignored_senders` stays the same; the count is in the same semantic unit (entries dropped into the `-from:` clause).

Gmail's search operator accepts both `from:gmail.com` and `from:alice@gmail.com`, so the exclusion clause works unchanged for both shapes. Verified in Gmail docs; no action needed.

## Client JS (rendered by `process_events.py`)

Minimal. The button's `data-sender` attribute value is now sometimes an email address; `postAction({action:'ignore_sender', domain: <value>})` carries whatever the attribute holds. The JS already uses an opaque string — no branching needed. `SENDERS_STORAGE_KEY` now holds mixed strings (domains + addresses); the existing equality-match hydration handles both naturally.

One new validation on the Ignore-sender branch: before POSTing, the JS rejects any value that is missing or empty (today it already does this for domain). The regex that the JS uses for a sanity check on the value is loosened to match `SENDER_RE`'s shape. Minor.

## Existing sheet rows

No migration code. Tom/Ellen are expected to delete any `gmail.com` / `yahoo.com` / etc. rows from the Ignored Senders tab manually after the new version is deployed. The practical impact of leaving them: the next workflow run still bakes those rows into the Gmail query, blocking the whole domain, until the row is removed. This is identical to today's behavior, so deploying the new version is strictly improving: new clicks give address-level precision; old clicks are grandfathered until cleaned up.

The design note explicitly names the manual cleanup so the session notes block carries the instruction forward after rollout.

## Pytest fixtures

`fixtures/test/ignored_and_sender.json` gains two cards for the freemail case:

- `"Parent Freemail A"` — sender_domain `gmail.com`, sender_block_key `parenta@gmail.com`.
- `"Parent Freemail B"` — sender_domain `gmail.com`, sender_block_key `parentb@gmail.com`.

Snapshot-style assertions:

- Both cards render `data-sender="parenta@gmail.com"` / `...B..." respectively — proving the UI can distinguish them.
- Both buttons label with the full address, not the bare domain.
- An institutional card (`greenfield.k12.ny.us`) still renders with the domain as its block key.

`tests/test_process_events.py` additions:

- `test_render_sender_block_key_freemail_address` — freemail card renders the address.
- `test_render_sender_block_key_institutional_domain` — institutional card renders the domain.
- `test_render_sender_block_key_missing_suppresses_button` — empty block_key means no button.
- `test_render_protected_still_suppresses_even_with_block_key` — a hypothetical `alice@fcps.edu` (sender_block_key is address, sender_domain is fcps.edu) still suppresses the button via the domain-keyed protected guard.

`tests/test_main.py` additions:

- `test_attach_sender_block_key_freemail` — event with gmail.com sender gets address-form block_key.
- `test_attach_sender_block_key_institutional` — fcps.edu sender gets domain-form block_key.
- `test_attach_sender_block_key_unknown_freemail_list` — empty freemail list yields domain form for every event (graceful degrade).
- `test_attach_sender_block_key_address_lowercased` — `Alice.Smith@Gmail.com` canonicalizes to `alice.smith@gmail.com`.

`tests/test_protected_senders.py` additions:

- `test_is_protected_address_form_school` — `alice@fcps.edu` → True under the `fcps.edu` pattern.
- `test_is_protected_address_form_wildcard` — `coach@louisearcherpta.org` → True under `*pta.org`.
- `test_is_protected_address_form_unprotected` — `alice@gmail.com` → False.
- `test_is_protected_edge_trailing_at` — `alice@` → False (no domain after the `@`).

`tests/test_events_state.py` additions:

- `test_load_state_v2_blows_away` — a v2 file on disk returns empty state.
- `test_save_load_v3_round_trip_with_block_key` — event written with `sender_block_key` reads back with it intact.

`tests/test_freemail_domains.py` (new):

- Loader returns lowercased entries.
- Dedupes same-domain lines.
- Tolerates missing file (returns empty frozenset).
- Skips `#`-commented and blank lines.

`tests/test_build_queries.py` (augments existing if present, new otherwise):

- Protected-sender filter drops `alice@fcps.edu` from the exclusion union.
- Exclusion union preserves `alice@gmail.com` and `jane@outlook.com` as distinct entries.

Apps Script has no pytest coverage — same posture as today.

## Responsibility table

Following the standing order — all mechanical work is Python, the agent (LLM) does only judgment extraction:

| Concern | Python | LLM (agent.py) |
|---|---|---|
| Extract event + source_message_id | — | ✅ judgment (unchanged) |
| Parse `From:` → email address | ✅ `email.utils.parseaddr` | — |
| Email address → registrable domain | ✅ `tldextract` | — |
| Freemail-or-institutional classification | ✅ `freemail_domains.txt` loader + `_compute_block_key` | — |
| Address lowercasing | ✅ `_compute_block_key` | — |
| Protected-sender guard | ✅ `is_protected` | — |
| Render `data-sender`, button label, POST value | ✅ `process_events.render_html` | — |
| Apps Script row append / delete | ✅ `apps_script.gs` | — |
| Ignored-senders → Gmail exclusion union | ✅ `build_queries.py` + `is_protected` | — |

No new LLM calls. No agent-time judgment added to any code path.

## Commit plan

Commit at each natural boundary, session discipline:

1. **Design note + ROADMAP insert + `[~]` flip.** This commit. Adds `design/sender-block-granularity.md`, inserts item #20 at the top of the open queue in `ROADMAP.md`, flips its status to `[~]`, updates the session-notes block.
2. **`freemail_domains.txt` + loader module + tests.** New root-level text file (seed list), new `scripts/freemail_domains.py` loader module mirroring `protected_senders.py`, unit tests in `tests/test_freemail_domains.py`.
3. **`main.py::_attach_sender_domains` derives `sender_block_key`.** Adds `_compute_block_key`, wires the freemail loader, stamps both fields on each event, extends logging if warranted. Unit tests grow.
4. **`events_state.py` schema v3 bump.** `CURRENT_SCHEMA_VERSION = 3`, existing tests updated for new version, new round-trip test carrying the block key.
5. **`scripts/process_events.py` render wiring.** `classify` passes `sender_block_key` through; `render_html` uses it for the data attribute, button label, and POST value; protected guard stays on `sender_domain`. Fixtures grow freemail pair; snapshot test updated.
6. **`scripts/protected_senders.py::is_protected` accepts address form.** One-branch change plus tests covering bare domain, freemail address, protected address, wildcard address, and the trailing-`@` edge.
7. **`scripts/build_queries.py`** — docstring / log-string update; test asserting the address-form protected filter. (No logic change; the upgrade to `is_protected` does the work.)
8. **`scripts/apps_script.gs`** — `SENDER_RE` constant, swap points, header-comment update. No automated tests; smoke-verify against live deploy.
9. **Close-out** — after Tom signs off, move the full prose from ROADMAP to `COMPLETED.md`, leave a one-line stub at #20 with the SHAs, update session-notes block. (This is a separate commit and happens only after live verification.)

Steps 3, 5, 7 touch `process_events.py`-family files; their fixture/test updates land in step per the session discipline.

## Non-goals

- **google.com as freemail.** Workspace / corporate email accounts living on `google.com` are not freemail and don't belong in this list. If kids' schools notify via `classroom.google.com` or similar and domain-level blocking turns out to be wrong for that, the remediation is to add those specific service domains to `protected_senders.txt` (so the button never shows) rather than to widen the freemail list.
- **Dot-stripping or plus-addressing canonicalization on gmail.** Explicitly deferred. If real collisions appear in logs (Ellen notices the button reappearing after she ignored a sender because of a plus-tag), add canonicalization then.
- **Migration code for existing sheet rows.** One-time manual cleanup, no scripted sweep.
- **New UI for managing the freemail list from the page.** Same stance as the ignored-senders UI: sheet / file is Ellen-editable; she doesn't need a separate UI page for it.
- **Per-event override of granularity.** No "block this gmail domain entirely" escape hatch on a single card. If Ellen wants to block all of some freemail domain, she adds the bare domain row to the Ignored Senders sheet by hand.
