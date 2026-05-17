# Dedupe: same-day name-token-overlap merge signal

GitHub issue [#13](https://github.com/holmes1371/kidschedules/issues/13). Filed 2026-05-17 after Tom spotted two cards on the live page that describe the same deadline but rendered as separate cards.

## The pair that slipped through

Live site, Fri Jun 12 (both all-day):

- "School Supply Kits Order Deadline (2026-2027 School Year)" — `www.shopttkits.com (school ID 93768)` — LAES PTA Sunbeam (Apr 19)
- "School Supply Kits Purchase Deadline (2026-2027)" — `www.shopttkits.com (School ID: 93768)` — LAES PTA Vice President (May 11)

Why each existing pass missed:

- **Pass 1 (exact)**: normalized names differ (`order` vs `purchase`, plus `(2026-2027 school year)` vs `(2026-2027)`).
- **Pass 2(a) [name-token subset]**: card 1 signature `{school, supply, kits, order, deadline, 2026, 2027, year}`, card 2 signature `{school, supply, kits, purchase, deadline, 2026, 2027}`. Card 1 has `order, year`; card 2 has `purchase`. Neither is a subset of the other.
- **Pass 2(b) [same `location` + same `time`]**: location strings differ by punctuation only (`school ID 93768` vs `School ID: 93768`). `_norm` lowercases + collapses whitespace but does not strip punctuation. Equality fails.

## Decisions locked in

- **Add a third merge signal (c) — same date + same time (or both all-day) + name-token overlap ≥ N significant tokens.** Catches the "two newsletter editions reword the same deadline" pattern without needing field extraction beyond what `_name_signature` already produces.
- **Threshold N = 4.** Calibration:
  - Screenshot pair: 6-token overlap (`school, supply, kits, deadline, 2026, 2027`) — comfortably above 4.
  - Plausible realistic counter-case "School Picture Day - Kindergarten" vs "School Picture Day - First Grade" (same day, both all-day): 4 tokens overlap (`school, picture, day, ...`) — borderline. With threshold 4 it merges; with 5 it does not. Picture-day kindergarten/first-grade events typically happen on the same day at the same school so this kind of merge is actually correct most of the time; if it bites in practice, tighten to 5.
  - Counter-case below threshold: "Parent-Teacher Conference - Anna" vs "Parent-Teacher Conference - Ben": 3 tokens (`parent, teacher, conference`) — stays separate.
  - Threshold lives as a module-level constant `_NAME_TOKEN_OVERLAP_THRESHOLD = 4` so it's easy to tune in a follow-up.
- **Time-equality guard mirrors `_same_location_and_time`.** Two events with the same date + high name overlap but different times stay separate. Both-empty time (i.e. both all-day) is treated as equal — same precedent as branch (b). This is the guard that prevents over-merge of distinct same-day classes with shared name fragments (e.g. recurring after-school activities at different time slots).
- **Union-find unchanged.** The new condition ORs into the existing `subset or same_loc_time` check; transitive chains across all three signals still collapse correctly.
- **Out of scope.** URL extraction (Option A from the plan discussion) and roster-stage grade filtering (Tom's adjacent observation — distinct concern, belongs upstream in `roster_match`, not in `dedupe`).

## Accepted risk

Threshold 4 may merge real pairs that share 4 generic tokens (`event, day, parent, deadline` are common). The time-equality guard is the main brake — pairs that share 4 generic tokens but happen at different times stay separate. For all-day pairs (where the guard is loosest), the realistic risk is school-wide events with formulaic naming. The escape hatch is the existing per-event ignore flow: if a wrongly-merged card surfaces, Tom hides one and the other re-renders next cron. If misclassification becomes a pattern, tighten to 5 in a one-line edit.

## Helper contract

`_name_token_overlap(a, b) -> int` — pure function, lives in `process_events.py` alongside `_name_signature` and `_same_location_and_time`.

- Returns `len(_name_signature(a["name"]) & _name_signature(b["name"]))`.
- No side effects, no I/O, no exceptions; both inputs are event dicts with a `"name"` key.

## Test plan

All under `tests/test_process_events.py`, alongside the existing `_same_location_*` tests:

1. **Screenshot case pin** — two events with the exact names/dates from the live site collapse to one card.
2. **Below-threshold counter-case** — "Parent-Teacher Conference - Anna" vs "Parent-Teacher Conference - Ben", same day, both 3:00 PM. 3-token overlap → stay separate.
3. **Time-guard counter-case** — high-overlap names (≥ 4 tokens shared) on the same date but at different times stay separate.
4. **Threshold pin** — explicit assert that `_name_token_overlap` returns the expected count for the screenshot pair (defends the threshold constant from accidental tightening).

Existing tests stay green:

- `test_dedupe_pass2_fuzzy_collapses_subset_names_same_date` — ASL Club ↔ ASL Club Meeting (subset branch).
- `test_dedupe_pass2_preserves_digit_only_tokens` — Swim Ages 3-5 vs Ages 6-8 stay separate. Token overlap is 3 (`swim, ages, htm/pool`) which is below threshold; they also have different times if set, so even at threshold 3 the time-guard would catch them. Pin remains valid.
- `test_dedupe_same_location_and_time_merges_newsletter_edition_pattern` — Oakton Dance Camp pair. Still merges via either branch (b) or the new (c), depending on which fires first; union-find doesn't care.

## Wiring

`scripts/process_events.py::dedupe`, inside the Pass 2 inner loop (~line 681):

```python
same_loc_time = _same_location_and_time(bucket[i], bucket[j])
subset = (
    sigs[i] and sigs[j]
    and (sigs[i] <= sigs[j] or sigs[j] <= sigs[i])
)
high_overlap = (
    len(sigs[i] & sigs[j]) >= _NAME_TOKEN_OVERLAP_THRESHOLD
    and _norm(bucket[i].get("time") or "") == _norm(bucket[j].get("time") or "")
)
if subset or same_loc_time or high_overlap:
    ...
```

Threshold constant declared near the other tunables at the top of the dedupe section.
