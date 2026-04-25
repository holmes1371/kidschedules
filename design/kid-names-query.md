# Kid-names query template

ROADMAP item #25. Filed 2026-04-24 after Ellen's "Everly volleyball / 8-9am May 4-8" self-note (Ellen-to-Ellen, Subject: `Everly volleyball`, body: `8-9am May 4-8. Sent from my iPhone`) reached the inbox, fell into the 60-day lookback window, and was matched by **none** of the five existing query templates — so it never reached step1b filter audit, never reached the agent, and never landed on the page.

## Why the existing five missed it

- `school_activities` — no `field trip / picture day / spirit day / assembly / parent teacher / open house / school event / fundraiser / book fair / report card`.
- `appointments` — no medical vocabulary.
- `sports_extracurriculars` — covers `practice / game / match / tournament / recital / rehearsal / club meeting / tryout / game day / scrimmage / ballet / dance / swim / gymnastics / karate`. **No volleyball, soccer, basketball, baseball, softball, lacrosse, tennis, track, football, hockey, wrestling.** That is a separate hygiene gap (#25b below).
- `academic_due_dates` — no due/deadline vocabulary.
- `newsletters_calendars` — sender is a personal Gmail (Ellen-to-Ellen), not `school/district/pta/ptsa`; subject is `Everly volleyball`, not `calendar/newsletter/reminder/upcoming/schedule`.

Three structural causes converge: (1) a sports-vocabulary gap, (2) self-notes use intentionally terse bodies that don't carry our keyword set, (3) the highest-precision signal we have — the kid's first name — isn't queried.

## Decision

Add a sixth query template `kid_names`, sourced at runtime from `class_roster.json` keys, OR-joined. Same `after:/before:/exclusion` framing as the other five.

```
kid_names = "(Everly OR Isla)"
```

Why this is the right shape and not a separate "batch pull" path:

- **The existing pipeline already does the right thing** — date window, exclusion clause, category-promotions filter, blocklist union, two-pass dedup (messageId + threadId), step1b filter audit. A 6th template reuses all of that for free; a parallel batch path is more code with no functional benefit.
- **Catches the immediate case** — `Everly` literal is in the subject, so `(Everly OR Isla)` matches the missed email.
- **Catches future self-notes** about either kid regardless of activity vocabulary.
- **Catches coach / teacher emails** that mention a kid by first name but lack our keyword set.
- **Roster-driven** — adding a kid in `class_roster.json` (or renaming one) flows through automatically. No second list to keep in sync with the roster.

## Decisions locked in

- **Source of names: `class_roster.json` keys, via `roster_match.load_roster`.** No separate name list, no hardcoding. Re-uses the loader that already exists for kid-attribution; matches the same crash-loud-on-missing-roster posture (`load_roster` raises on absent/malformed JSON — absence of the roster is a bug, not a recoverable condition).
- **Built dynamically in `main()`, not as a constant in `SEARCH_TEMPLATES`.** The other five templates are static keyword groups; this one is roster-derived. Keeping it out of the `SEARCH_TEMPLATES` dict avoids implying it's editable in the same way and avoids a chicken-and-egg with module-level loading.
- **OR-joined with parentheses, no quoting.** Current names (`Everly`, `Isla`) are single words with no Gmail-search special characters. The builder will defensively wrap any name containing whitespace in double quotes for forward-compatibility (e.g. a future `"Mary Jane"` entry), but the v1 output for the current roster is exactly `(Everly OR Isla)`.
- **Same exclusion clause as the other five.** A blocked sender that happens to mention a kid name is still blocked. A protected sender that mentions a kid name is still protected. The existing `-category:promotions` filter still applies.
- **Empty / one-kid roster handling.** Single-kid roster yields `(Name)` — still a valid Gmail query. Empty roster (no keys) suppresses the `kid_names` query entirely (would otherwise produce `()`, an empty parenthetical that Gmail's parser might not love). Crash-loud only kicks in if the file is missing or non-JSON.
- **`--no-kid-names` opt-out flag.** Mirrors the existing `--no-category-filter` style for diagnostics and tests. Default is on.
- **`--roster` path flag.** Mirrors the existing `--blocklist`, `--auto-blocklist`, etc. paths. Default is `class_roster.json` at repo root via `roster_match._DEFAULT_ROSTER_PATH`. Pass `''` to disable.

## Accepted risk

The kid-names query has lower per-message precision than the keyword-driven five — it pulls in any email mentioning `Everly` or `Isla`, including birthday notes from family, photo subjects, and incidental mentions in unrelated threads. Two things make this acceptable:

1. **Step1b filter audit and the agent's filtering already triage non-event content.** Random "tell Everly happy birthday" notes get dropped at the audit stage; the agent's extraction prompt rejects non-event content. The cost is agent tokens for the triage, which is bounded by the kid-name match volume.
2. **The kid names are uncommon enough that recall noise is bounded.** `Everly` especially is a rare name; `Isla` is more common but still much rarer than `practice` or `appointment`. The 60-day window across a personal Gmail will typically produce a small handful of additional candidates per run.

If volume becomes visibly noisy in the funnel logs (`Total messages across all searches: N`), a follow-up can scope the kid-names query to subject-only or to specific senders. v1 is the unscoped form to maximize recall while we calibrate.

## Helper contract

`build_queries.py` grows two responsibilities:

- **Roster loader at module scope.** Reuses `roster_match.load_roster`. No re-implementation; no caching beyond what the OS does. Called once per `main()` invocation.
- **Kid-names query assembly.** Pure function: `build_kid_names_query(roster: dict) -> str | None`. Returns the query body (no `after/before/exclusion` framing) or `None` if the roster has no kids. Empty/whitespace name keys are filtered out. Names containing spaces or Gmail-significant characters get wrapped in double quotes.

```python
def build_kid_names_query(roster: dict) -> str | None:
    """Return the OR-joined kid-names query body, or None for empty roster.

    Names are case-preserved as written in the roster — Gmail search is
    case-insensitive so this is cosmetic. Names containing whitespace
    are double-quoted.
    """
    names = [n.strip() for n in roster.keys() if n and n.strip()]
    if not names:
        return None
    quoted = [f'"{n}"' if (" " in n or "\t" in n) else n for n in names]
    return "(" + " OR ".join(quoted) + ")"
```

## Wiring in `main()`

After the existing exclusion clause is built, before the `assemble(...)` loop:

```python
roster = roster_match.load_roster(args.roster) if args.roster else {}
kid_names_body = build_kid_names_query(roster) if not args.no_kid_names else None

# ...

queries = {name: assemble(body) for name, body in SEARCH_TEMPLATES.items()}
if kid_names_body:
    queries["kid_names"] = assemble(kid_names_body)

loose_queries = {name: assemble_loose(body) for name, body in SEARCH_TEMPLATES.items()}
if kid_names_body:
    loose_queries["kid_names"] = assemble_loose(kid_names_body)
```

A new key `kid_names_query_body` is added to the JSON output for diagnostics, alongside the existing `exclusions` block. When `kid_names` is suppressed (empty roster, `--no-kid-names`, `--roster ''`) the field is `null` and the `queries` / `loose_queries` dicts simply don't carry the entry — preserves backward compatibility for any consumer iterating over those dicts.

## Pytest additions

`tests/test_build_queries.py` grows a section for kid-names (target: 6 tests):

- `test_kid_names_query_built_from_roster_keys` — two-kid roster, query body is `(Everly OR Isla)` (or order matching roster iteration; pin against insertion order which Python 3.7+ guarantees for dicts).
- `test_kid_names_query_present_in_main_output` — full `main()` run produces a `queries["kid_names"]` value carrying the kid-names body inside the standard `after/before/exclusion` framing.
- `test_kid_names_query_single_kid_degenerate` — one-kid roster yields `(Name)` and is still valid.
- `test_kid_names_query_quotes_multiword_names` — synthetic roster with `"Mary Jane"` key produces `("Mary Jane" OR Other)`.
- `test_kid_names_disabled_via_flag` — `--no-kid-names` drops the query and zeros the diagnostic field.
- `test_kid_names_empty_roster_path_skips_loader` — `--roster ''` skips the loader and drops the query (no crash on the missing-roster path).

Plus one regression pin against the actual missed email shape:

- `test_kid_names_query_matches_everly_volleyball_subject` — given the live `Everly` roster key and the `Everly volleyball` subject literal, assert the constructed query body contains `Everly` so Gmail's full-text matcher would have hit. (Gmail-side match semantics are tested implicitly — the pipeline only constructs the query string here.)

Test count delta: +7.

## Roadmap-adjacent: sports keyword extension (item 25b)

Independent commit, separate diff. Extend `SEARCH_TEMPLATES["sports_extracurriculars"]` with `volleyball / soccer / basketball / baseball / softball / lacrosse / tennis / track / football / hockey / wrestling`. Hygiene only — does not fix the missed-email path (kid_names already covers it), but closes the underlying gap so future emails with these sports words match without needing the kid-name to be present.

## Responsibility table

| Concern | Python | LLM (agent.py) |
|---|---|---|
| Load roster keys | ✅ `roster_match.load_roster` (existing) | — |
| Build kid_names OR-clause | ✅ `build_kid_names_query` (new) | — |
| Apply after/before/exclusion framing | ✅ `build_queries.assemble` (existing) | — |
| Run Gmail query | ✅ `gmail_client.search_messages` (existing) | — |
| Dedupe across templates | ✅ `step2b_read_promising` (existing) | — |
| Triage non-event hits | — | ✅ judgment (existing audit + extract) |

No new LLM calls; no agent-time judgment added.

## Commit plan

1. **Design note + ROADMAP flip.** This commit. Adds this note, flips ROADMAP #25 `[ ]` → `[~]`. No behavior change.
2. **Kid-names query implementation + tests.** `build_queries.py` change + `tests/test_build_queries.py` additions.
3. **Sports keyword extension.** Independent diff, `SEARCH_TEMPLATES["sports_extracurriculars"]` adds the missing sports words. Pin via test that asserts each new word is present in the template body.
4. **Close-out.** After Tom signs off (next session), move prose to `COMPLETED.md`, flip `[~]` → `[x]`, record SHAs.

## Non-goals

- **Subject-only or sender-scoped variants.** v1 is unscoped to maximize recall; revisit only if funnel logs show noise.
- **Body-text scoring or relevance ranking.** Step1b filter audit and the agent already do this work.
- **Kid-name expansion to nicknames or last names.** Roster keys are the canonical handle. If `Everly` is sometimes referred to as `Ev` or `Evie` and that becomes a real failure mode, file a follow-up — don't speculate.
- **Per-kid query split.** A single OR-joined query is cheaper (one Gmail API call) and the dedup pipeline doesn't care which template the hit came from.
- **Separate "batch pull" code path.** Rejected in favor of the 6th template (see Decision section).
