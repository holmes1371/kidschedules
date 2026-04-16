# Card information redesign (ROADMAP #11)

Item 11 in the backlog. Supersedes the earlier "group by child" split: rather than iterating on the current card layout, reshape what each card says and how. The decisions below were locked in session 2 (2026-04-16) against a visual mockup at `design/card-redesign-mockup.html` — six side-by-side old-vs-new variants covering the shapes the pipeline actually emits (timed appointment, all-day due date, URL-only location, school-wide audience, time-range sports event, ignored card). Open the mockup before implementing; this note captures *why*, the mockup captures *what*.

## Scope

`scripts/process_events.py::render_html` — specifically the dated `_event_card` template, its companion CSS, and a small amount of ingest-side normalization (`build_events`) for strings that should never reach render. No change to `agent.py`, the Gmail-search layer, Apps Script, client JS selectors, text email rendering, or the `.ics` export. The feature is intentionally render-only plus the minimum ingest change to kill legacy TBD placeholders.

## Layout A — locked decisions

1. **Meta strip**: a single top-of-body row carrying identity and when. Format is `[child-chip] Day, Mon DD · Time` where the weekday is the three-letter abbreviation and the date is next to it (e.g. `Thu, Apr 16 · 3:45 PM`). The full-month context still lives on the week section header (`Week of April 13`), so short weekday/date inside the card is not ambiguous. The meta strip also replaces today's `.event-date` line; the previous "Thursday, April 16" on its own line is removed.

2. **Child chip**: a 22px solid-colour circle with the child's bold initial. `Everly → #ec407a` (coral) and `Isla → #5c6bc0` (indigo). The `.child-chip`, `.child-chip.everly`, `.child-chip.isla` classes are new additions to the stylesheet; colours also become `--everly` / `--isla` CSS variables so item #12's filter chips can reuse them. Non-kid `child` values (e.g. `"All LAES students"`, `"6th grade AAP"`) do not get a chip — rendering those as an ambiguous single-letter circle was rejected. Instead they render as a small `<div class="event-audience">For: {child}</div>` below the event name. Empty `child` renders neither a chip nor an audience line.

3. **Category badge removed**: today each card carries a rounded pill like `[School Activity]` in the details row. Deleted. Category is now signalled exclusively by the coloured left rail (today's inline `border-left: 4px solid {fg}` driven by `CATEGORY_COLORS`). The rail mechanism stays exactly as it is; only the pill markup and the corresponding `.badge` CSS are removed.

4. **Source line removed**: the `<div class="event-meta">{child} · {source}</div>` footer is dropped in full. Sender domain still surfaces via the Ignore-sender button on cards that have one; Gmail search covers any deeper provenance question. The `.event-meta`, `.child`, and `.source` CSS rules become orphans under this change and are removed in the same commit (surgical — only the rules whose markup vanishes).

5. **Location line**: empty location drops the whole `<div class="event-location">` element. The literal string `"Location TBD"` never reaches render under the new rules (see canonical-empty-time below). URL-only and email-only locations are also suppressed — the current agent sometimes emits `"https://drive.google.com/..."` or `"yearbook@louisearcherpta.org"` as a location; neither is useful to a parent scanning the page.

6. **Time rendering — canonical all-day handling**: any of `{"", "Time TBD", "All day", "All day (deadline)"}` (case-insensitive, stripped) becomes the small grey `All day` pill (new `.time.allday` class). Any other string renders verbatim (e.g. `3:45 PM`, `5:30 – 6:45 PM`, `1:30 PM dismissal`) inside a plain `.time` span. The classification lives Python-side in a tiny `_is_all_day(time_str)` helper; nothing downstream (agent, ingest, email) changes. See the next section for the reasoning.

7. **Actions unchanged**: the top action row (`Add to calendar`, `Ignore event` / `Unignore event`) and the bottom action row (`Ignore sender` where applicable) keep their shape, markup, and placement. All existing `data-*` attributes (`data-event-id`, `data-event-name`, `data-event-date`, `data-sender`, `data-ignored`, `data-ignored-reason`) stay on the card root so client JS selectors and the 70+ existing render tests keep working.

## agent.py ripple: canonical empty-time at the render boundary

The extractor's prompt today lists `"All day"` and `"All day (deadline)"` as valid `time` values (agent.py line 77). Due dates come out as the latter; genuinely all-day events come out as the former. A third path exists where `time` comes back empty and `build_events` rewrites it to `"Time TBD"` before it hits render. That's three different strings for the same visual state.

Two ways to unify them: normalize upstream at ingest, or classify downstream at render. **Render-side wins.** Upstream normalization would either mutate `ev["time"]` (breaking the plain-text email, which renders the raw value) or add a parallel `is_all_day` field (a speculative schema bump for a single consumer — violates simplicity). Render-side classification costs one helper function, changes no data shape, leaves the email renderer untouched, and means `agent.py` needs no prompt tweak. The prompt's vocabulary stays exactly as it is; render just knows how to collapse the synonyms.

Concretely: add `_is_all_day(s: str) -> bool` in `process_events.py` that returns `True` for stripped, case-insensitive membership in `{"", "time tbd", "all day", "all day (deadline)"}`. `_event_card` calls it, emits either the pill or the verbatim string. `_undated_card` grows the same conditional. `render_body` / `render_event` — the plain-text path — keep rendering the raw value; text email behaviour is out of scope for this feature and changing it would violate surgical-changes.

## Ingest ripple: kill legacy TBD placeholders

`build_events` (around line 365) currently does:

```python
"time": (ev.get("time") or "").strip() or "Time TBD",
"location": (ev.get("location") or "").strip() or "Location TBD",
```

Both placeholders were sentinels for the old render path that needed a visible string to display. With the new rules they're dead weight: `"Time TBD"` goes through `_is_all_day` to the same pill an empty string produces, and `"Location TBD"` is now suppressed anyway. Change both defaults to empty string. The existing `ev["time"] != "Time TBD"` / `ev["location"] != "Location TBD"` checks in `render_event`, `render_undated`, `_undated_card`, and `build_ics` keep working unchanged (an empty string is `!= "Time TBD"`, so the guards that previously hid the sentinel now hide the empty value instead — identical behaviour).

The one test fixture that seeds `"Time TBD"` / `"Location TBD"` strings directly (`fixtures/test/edge_cases.json` per grep) continues to work — those literals still render correctly under the new logic (all-day pill for "Time TBD"; location line suppressed for "Location TBD"). The test assertions against the *sentinel strings themselves* are the ones that need to shift to asserting on the rendered output.

## URL/email location suppression

`_is_suppressible_location(s)` — returns `True` if the stripped, lowercased value starts with `http://` or `https://`, or matches a conservative email regex (`^[^@\s]+@[^@\s]+\.[a-z]{2,}$`). Called from `_event_card` and `_undated_card`. When `True` the entire `<div class="event-location">` element is omitted. Mixed strings like `"Tysons Pediatrics, 8350 Greensboro Dr"` are preserved. Edge case the helper intentionally does *not* catch: `"camps.fcps.edu"` — a bare domain without a scheme. Those are rare, still legible, and catching them risks matching too aggressively (many real venues have dots).

## CSS changes

Additive / modified:

- New: `.child-chip`, `.child-chip.everly`, `.child-chip.isla`, `.meta-strip`, `.meta-strip .day`, `.meta-strip .sep`, `.time.allday`, `.event-location`, `.event-audience`.
- New CSS variables: `--everly: #ec407a;` and `--isla: #5c6bc0;` at `:root` plus matching `@media (prefers-color-scheme: dark)` overrides (TBD during implementation — start with the light-mode values; dark-mode tweak only if contrast fails during eyeball QA).
- Modified: `.event-name` loses its top margin (meta-strip now owns the top of the card body) — target spacing: `margin: 0.1rem 0 0.2rem` per the mockup.
- Modified: `.event-card` retains its inline `border-left: 4px solid {fg}` mechanism. No migration to `.rail-school` etc. classes — the mockup uses classes for readability but the runtime code already resolves the colour per-event and emitting four extra class names would be pure churn.

Removed:

- `.badge` (category pill).
- `.event-meta`, `.child`, `.source`, `.event-date` (the old date line — meta-strip replaces it).
- Any rule that styled the removed elements.

The `@media (prefers-color-scheme: dark)` block keeps all existing overrides; it gains `--everly` / `--isla` dark-mode values and loses the `.event-meta` / `.badge` overrides that no longer have markup to target.

## Fixture/test plan

Extend `tests/test_process_events.py` alongside the render change — not after. The guideline is in the session-discipline block of ROADMAP.md: tests and fixtures move in the same commit as the behaviour they cover.

Existing render-html assertions that depend on removed markup need to shift:

- Any assertion on `class="badge"`, `class="event-meta"`, `class="source"`, `class="event-date"` gets updated or deleted. Per the grep run during design, the current test file has none — only selectors like `data-event-id`, `data-event-name`, `data-sender`, `class="ignored"`, `class="ignore-sender-btn"` are asserted, all of which persist unchanged.
- `tests/snapshots/basic_body.txt` is the plain-text email body, unchanged by this feature — no snapshot re-record.

New fixtures / fixture extensions:

- **`fixtures/test/card_redesign.json`** (new) — a small mixed fixture covering every variant in the mockup: Everly timed, Isla timed, due date with no location, due date with URL location, school-wide audience (`child: "All LAES students"`), and a sports time-range event. Six rows, mirrors the mockup 1:1 so visual regressions are easy to correlate.
- **`fixtures/test/basic_mixed.json`** — no schema change. The `"Glasgow Middle School / Isla"` stale-data flag noted in the session summary is a separate cleanup pass, not this feature's concern.

New `test_process_events.py` cases (under a `# ─── card redesign (Layout A) ───` banner):

- `test_render_html_meta_strip_format_everly_timed` — asserts the rendered card contains `child-chip everly`, the letter `E`, `Thu, Apr 16`, and `3:45 PM` in the meta strip, and does *not* contain the category badge class or the old `.event-meta` wrapper.
- `test_render_html_meta_strip_isla_with_indigo_chip` — mirror for Isla's chip colour.
- `test_render_html_all_day_pill_for_empty_time` — event with `time: ""` renders `class="time allday"`.
- `test_render_html_all_day_pill_for_all_day_deadline` — event with `time: "All day (deadline)"` renders `class="time allday"`, not the verbatim string.
- `test_render_html_all_day_pill_for_time_tbd_literal` — event with `time: "Time TBD"` renders the pill (guards the upstream ingest change).
- `test_render_html_location_suppressed_when_empty` — event with `location: ""` → no `<div class="event-location">` in that card's slice.
- `test_render_html_location_suppressed_when_url` — event with `location: "https://drive.google.com/..."` → no location div.
- `test_render_html_location_suppressed_when_email` — event with `location: "yearbook@laes.org"` → no location div.
- `test_render_html_audience_line_for_school_wide_child` — event with `child: "All LAES students"` → no child-chip, has `<div class="event-audience">For: All LAES students</div>`.
- `test_render_html_no_audience_line_for_named_kid` — Everly/Isla → chip present, no audience line.
- `test_render_html_no_category_badge_anywhere` — `class="badge"` not in the rendered HTML.
- `test_render_html_no_source_footer` — `class="event-meta"` and `class="source"` not in the rendered HTML.
- `test_build_events_defaults_empty_strings_not_tbd` — ingest-side test: a candidate with missing `time`/`location` keys normalizes to `""`, not `"Time TBD"`/`"Location TBD"`.

Rough count: 12 new tests; 0 existing tests touched (per the grep, all existing renders-html assertions stay green). Final count depends on what surfaces during implementation.

## #12 ripple — chip-colour alignment

Item #12 (per-kid filter chips, next up after #11) adds a chip row at the top of the page that toggles card visibility by child. That chip row should reuse `--everly` and `--isla` — the same palette the redesigned cards wear — so the top-of-page filter and the per-card chip read as the same visual language. The exact shape and placement of #12's chip row is out of scope here; this note just locks the colour variables so #12 doesn't re-pick them.

## Responsibility table

| Concern | Owner | Notes |
|---|---|---|
| Extracting `time` / `location` / `child` from email | `agent.py` | Unchanged. Prompt's "All day" / "All day (deadline)" vocabulary preserved. |
| Defaulting missing `time` / `location` to empty string | `scripts/process_events.py::build_events` | Flip `"Time TBD"` / `"Location TBD"` → `""`. |
| Classifying a time string as "all-day" | `scripts/process_events.py::_is_all_day` | New helper; pure, no I/O. |
| Suppressing URL/email locations | `scripts/process_events.py::_is_suppressible_location` | New helper; pure, no I/O. |
| Classifying `child` into `{named-kid, audience, empty}` | `scripts/process_events.py::_event_card` | Inline logic; no new helper needed — a simple if/elif covers all three paths. |
| Rendering the meta strip, chip, audience line, pill, location | `scripts/process_events.py::_event_card` | All template changes live here. `_undated_card` gets the parallel changes. |
| CSS for new classes + removal of dead rules | `scripts/process_events.py::render_html` style block | Edit in place; no new file. |
| Category colour (left rail) | unchanged — `CATEGORY_COLORS` + inline `style="border-left: ..."` | Rail mechanism stays; badge removal does not touch it. |
| Client JS selectors | `docs/index.html` (rendered from render_html) | No change — all existing `data-*` attributes preserved. |
| Tests and fixtures | `tests/test_process_events.py`, `fixtures/test/card_redesign.json` | New fixture, 12ish new tests. |

## Non-goals

- Reworking the page header, `.stats` row, `Show ignored (N)` toggle, undated section heading, empty state, or footer.
- Changing dark-mode palette beyond adding the two new kid colour variables.
- Rewriting `render_body` / `render_event` / `render_undated` (plain-text email). Those keep today's behaviour including `"Time: All day (deadline)"` verbatim.
- Updating Apps Script, Gmail search, `build_queries.py`, `events_state.py`, `ignored_senders.json`, or any other data-flow surface.
- Migrating the category-rail mechanism from inline `style=` to CSS classes.
- Addressing the stale-data `"Glasgow Middle School / Isla"` row in `fixtures/sample_candidates.json` — separate cleanup after #11 lands.
- Adding per-kid filter chips (that's #12).
- Adding "New this week" badges (that's #13).
- Changing the `.ics` export or its button.

## Commit plan

1. This design note — the mockup already landed in `787abd0`; this commit adds the note and a design-note pointer into ROADMAP #11's body. Status stays `[ ]` per the session-2 rule ("stays `[ ]` until the code commit lands").
2. Implementation in `scripts/process_events.py` (helpers + template + CSS) and `tests/test_process_events.py` (new fixture + cases) in one commit — the change is small enough that splitting template-from-tests would produce an intermediate commit with red tests. Flip ROADMAP #11 to `[~]` with the SHA recorded; Tom verifies by opening `docs/index.html` under `scripts/dev_render.py` against the new fixture and the retired `sample_candidates.json`.
3. Close-out once Tom signs off — next session flips `[~]` → `[x]`.
