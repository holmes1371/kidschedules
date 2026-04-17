# Kid Attribution Derivation (#19)

## Problem

#12 gave us per-kid filter chips (`All` / `Everly` / `Isla`) that hide cards
whose `data-child` attribute points at the un-selected kid. The attribute is
written only when the extractor's `child` field is *exactly* `"Everly"` or
`"Isla"`; anything else (`"6th grade AAP"`, `"All LAES students"`, `""`)
leaves `data-child=""` and the card stays visible under every filter.

That behavior is correct for truly school-wide events, but it misfires
when the extractor *could* have attributed to a kid but chose an audience
string instead — e.g. a "6th grade AAP" event or a "Cuppett Performing
Arts" recital. Tom saw both cases in the 2026-04-16 live run: a 6th grade
AAP card and a Cuppett card that should have carried the Everly / Isla
pill but didn't.

The extractor prompt already instructs it to prefer the kid by grade,
teacher, or activity provider (see `_EXTRACTION_BASE_PROMPT` + the prose
appended from `class_roster.json`). The model follows that inconsistently.

## Why Python, not the prompt

Per the skill-building standing order (two runs with the same inputs →
same output ⇒ Python). The roster mapping is deterministic:

- `"6th grade"` anywhere in an event's text is unambiguously Everly given
  the current roster.
- `"Cuppett Performing Arts Center"` is unambiguously Isla.
- `"Ms. Sahai"` is unambiguously Everly.

There is no judgment, narrative, or out-of-band context the agent brings
to that mapping — it's a lookup against `class_roster.json`. Moving it
into a pure Python helper makes it testable, deterministic, and free of
API cost per run.

The extractor prompt still carries the roster prose (it helps the model
make the same attribution when it *does* set `child` correctly, and it's
the primary signal for kid-specific appointment extraction). The new
Python derivation is a safety net on top.

## Scope

Render-time derivation of `data-child` on event cards. No changes to:

- The extractor prompt or the model
- The `child` field on event dicts (event-ID hash depends on it via
  `events_state.py::_event_id`; mutating `child` would orphan cached
  entries)
- The `class_roster.json` schema (alias extraction is implicit from
  parentheticals)
- The audience-line display text (`"For: 6th grade AAP"` still shows as
  context alongside the kid pill when both signals are present)

## Signal tiers

For each event, we check the kid roster in this priority order and
return the first-matching (kid, tier) pair:

1. **name** — `ev["child"]` case-insensitive equals a kid's first name.
   The existing rule; kept for backwards-compatibility and to avoid
   re-running the grade/activity regex when a clean name match is present.

2. **teacher** — the roster teacher's last name matches as a whole word
   anywhere in the event's combined text (`name` + `source` + `location`
   + `child`). `"Ms. Anita Sahai"` → match on `"Sahai"`.

3. **grade** — the kid's current grade *or* the grade one year ahead
   (rising) matches as a grade token anywhere in the combined text.
   Grade tokens accept multiple surface forms:

   - Ordinal digit: `6th`, `7th`, `3rd`, `4th` (word boundaries)
   - Word form: `sixth`, `seventh`, `third`, `fourth` (word boundaries,
     case-insensitive)
   - `grade N`: bare digit adjacent to the word *grade*
   - `rising Nth grader` / `rising Nth grade`: the "rising" adjective is
     absorbed by the plain `\bNth\b` match on the ordinal it's adjacent
     to, so no separate pattern is needed

   Next-grade matching is the new-this-session addition: in spring the
   agents start to see "7th grade AAP info night" emails that refer to
   Everly's upcoming year. Without rising-grade matching those events
   would fall through.

4. **activity** — any activity string for the kid (or its parenthetical
   alias) appears as a case-insensitive substring of the combined text.
   Parenthetical aliases are extracted at load time:
   `"Born 2 Dance Studio (B2D)"` yields candidate matches
   `"Born 2 Dance Studio"` and `"B2D"`.

5. **school** — the school name (plus known aliases like `LAES` ↔
   `Louise Archer Elementary`) matches. This tier is effectively dormant
   for the current roster because both kids share LAES — the
   distinctiveness filter (below) drops shared signals — but a future
   kid at a different school would auto-activate school matching with
   no code change.

### Distinctiveness

Before matching any event, we compute the set of signal strings that are
unique to one kid. A signal (`"laes"`, `"louise archer elementary"`)
shared across two kids is dropped from both kids' signal sets, so it
never contributes to attribution. This is why `"All LAES students"`
cards still render with `data-child=""` — the only signal in the text
is `LAES`, and it's shared, so no kid wins.

A kid's first name is trivially distinctive (two kids with the same
first name is out of scope). Teachers, grades, and activities happen to
be distinctive in the current roster. If a future roster shares a
teacher or activity across kids, the distinctiveness filter silently
turns that tier off for attribution, which is the safe behavior.

### Tie-break

Matches within the same tier across kids resolve to roster order. In
practice the current roster has no tier-level ties: grades differ,
teachers differ, activities differ. If a future change introduces one,
we prefer the roster-first kid and log a warning once per run.

## Rendering impact

`_event_card` and `_undated_card` in `scripts/process_events.py`
currently branch on `child in ("Everly", "Isla")` three times:

1. Kid pill vs audience line
2. `data-child` attribute
3. (undated card) same two decisions

Under the new logic:

- Pill renders when the derivation produces a slug via *any* tier.
- Audience line renders alongside the pill when the pill was produced by
  tier 2+ (teacher / grade / activity / school) *and* `ev["child"]` has
  content. Example: `child="6th grade AAP"` → E pill + "For: 6th grade
  AAP".
- `data-child` carries the derived slug or `""`.

Tier 1 matches (`child` == `"Everly"`) keep the existing clean look: pill
only, no audience line, no behavior change.

## Cache / re-render behavior

`events_state.json` stores raw event dicts (not rendered HTML) keyed by
12-char event-ID hashes of `(name, date, child)`. Because the new
derivation is pure render-time and does not mutate `child`, no cache
rebuild is needed. The next scheduled run will re-render existing cached
events with the new rules applied.

## Test fixtures

All new tests live in `tests/test_process_events.py` (render snapshots)
and a new `tests/test_roster_match.py` (unit tests for the pure helpers).
Required cases:

- Unit: `_derive_child_slug`
  - `child="Everly"` → (`"everly"`, `"name"`)
  - `child="6th grade AAP"` → (`"everly"`, `"grade"`)
  - `child="rising 7th grader"` → (`"everly"`, `"grade"`) (next grade)
  - `child="sixth grade"` → (`"everly"`, `"grade"`) (word form)
  - `child="grade 3"` → (`"isla"`, `"grade"`) (bare digit + "grade")
  - `child="4th grade"` → (`"isla"`, `"grade"`) (next grade)
  - `source="Cuppett Performing Arts Center (Apr 10)"`, `child=""`
    → (`"isla"`, `"activity"`)
  - `source="Born 2 Dance Studio"`, `child=""` → (`"everly"`,
    `"activity"`)
  - `source="B2D recital"`, `child=""` → (`"everly"`, `"activity"`)
  - `location="Ms. Sahai's classroom"`, `child=""` → (`"everly"`,
    `"teacher"`)
  - `source="Ms. Rohde's reading night"`, `child=""` → (`"isla"`,
    `"teacher"`)
  - `child="All LAES students"` → (`""`, `""`) (school shared, not
    distinctive)
  - `child=""`, no distinctive signals → (`""`, `""`)

- Render snapshots (in `test_process_events.py`):
  - Grade-attributed card renders E pill + "For: 6th grade AAP" +
    `data-child="everly"`
  - Activity-attributed card renders I pill + `data-child="isla"`,
    no audience line (child empty)
  - Existing audience-line test (`"All LAES students"` →
    `data-child=""`) still passes — regression guard

## Files touched

- `design/kid-attribution-derivation.md` (this file)
- `scripts/roster_match.py` (new) — pure helpers: grade aliases,
  grade-advance, activity-alias extraction, school-alias table,
  distinctive-signal builder, `_derive_child_slug`
- `scripts/process_events.py` — call `_derive_child_slug` in
  `_event_card` and `_undated_card`; branch the chip/audience render on
  the returned tier
- `tests/test_roster_match.py` (new) — unit tests for the pure helpers
- `tests/test_process_events.py` — add render snapshots for the new
  grade / activity / teacher cases, regression-guard the "LAES shared"
  behavior
- `ROADMAP.md` — add item #19, flip to `[~]` on plan approval
