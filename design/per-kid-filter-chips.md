# Per-kid filter chips + teacher roster (ROADMAP #12)

Item 12 in the backlog. Two concerns bundled under one ROADMAP number by the card-redesign session: a client-side per-kid filter affordance (the chips) and a prerequisite that improves the underlying `child` field quality (the roster). The roster lands first so the chips filter accurately-labelled cards; the UI work is orthogonal and can land without the roster if needed, but commit order matters for trust in the affordance.

Decisions below locked in session 5 (2026-04-17) planning. Open the card-redesign mockup at `design/card-redesign-mockup.html` for the `--everly` / `--isla` palette context the chips inherit.

## Scope

**Commit 1 (roster subtask):**

- New file: `class_roster.json` at repo root.
- Edit: `agent.py` loads the roster at module import and injects a small prose block into `EXTRACTION_SYSTEM_PROMPT`.
- Edit: `tests/test_agent.py` gets new cases.
- No changes to `scripts/process_events.py`, UI, Gmail, Apps Script, or anything downstream of extraction.

**Commit 2 (chip row):**

- Edit: `scripts/process_events.py::render_html` — new chip-row block between `.stats` and `.container`, new CSS, new inline JS filter function. Each card body carries a new `data-child="everly|isla|"` attribute.
- Edit: `tests/test_process_events.py` — new cases under a `# ─── per-kid filter chips (#12) ───` banner.
- No new fixture — `fixtures/test/card_redesign.json` already covers Everly + Isla + audience-line + empty-child rows.
- No changes to `agent.py`, ingest, Gmail, Apps Script, or text-email rendering.

## Locked decisions

1. **Filter semantics** — clicking a kid chip hides *only* cards tagged with the other named kid. Cards with no kid chip (audience-line values like "All LAES students" / "6th grade AAP", or empty `child`) stay visible across every filter. Rationale: a school-wide closure is relevant to both kids; hiding it when a specific kid is selected would make the filter feel lossy.

2. **Persistence** — filter state is ephemeral. A cold reload returns to "All". No localStorage plumbing. Rationale: view toggle, not a data decision like ignoring.

3. **Chip set** — exactly three chips: `All`, `Everly`, `Isla`. No chips for audience-line values or other free-text `child` strings. Chip markup is hard-coded in the renderer — it does NOT iterate over the unique children in the current run. Rationale: the chip palette (`--everly` / `--isla`) is tied to a fixed 2-kid family; free-text audience values are an unstable taxonomy.

4. **Empty-week handling** — if a filter empties a week section, the week heading shows alone. Acceptable visual edge case; not worth a CSS `:has()` rule.

5. **Roster shape** — JSON at repo root, keyed by kid name:

   ```json
   {
     "Everly": {
       "teacher": "Ms. Anita Sahai",
       "grade": "6th",
       "school": "Louise Archer Elementary"
     },
     "Isla": {
       "teacher": "Ms. Meredith Rohde",
       "grade": "3rd",
       "school": "Louise Archer Elementary"
     }
   }
   ```

   Fall-update workflow: edit the JSON, commit. No code change needed.

6. **Roster injection style** — JSON is the source of truth; `agent.py` formats prose at module load and appends it to the base prompt. Deterministic script owns formatting; model sees prose (more reliable than embedded JSON for this kind of rule).

   Concretely:

   - Module-level: `_ROSTER_PATH = Path(__file__).parent / "class_roster.json"`.
   - `_load_roster_prose() -> str` reads the JSON and returns a prose block shaped like:

     ```
     Teacher roster (current academic year):
     - Everly is in 6th grade at Louise Archer Elementary; her teacher is Ms. Anita Sahai.
     - Isla is in 3rd grade at Louise Archer Elementary; her teacher is Ms. Meredith Rohde.

     If an email names a teacher without naming the kid, attribute events
     to that teacher's student. If an email names a grade level that
     matches a kid's grade, prefer that kid for the `child` field.
     ```

   - `EXTRACTION_SYSTEM_PROMPT = _BASE_PROMPT + "\n" + _load_roster_prose()` at module load.
   - If `class_roster.json` is missing or unparseable: raise at import time. The file is committed and its absence is a bug, not a condition to paper over with a silent fallback.

   Rationale: prose reads clearer to the model than raw JSON; formatting lives in a pure, tested function; fall updates are "edit JSON, done"; crash-on-missing catches accidental deletions before the next weekly run returns silently-worse extractions.

7. **Commit order** — roster first, chip row second. Separate commits. Clean bisect, and the roster change can be confirmed in isolation before the UI churn.

## Chip-row HTML sketch

A new section inserted between `.stats` and `.container`:

```html
<div class="filter-chips" role="group" aria-label="Filter by child">
  <button class="filter-chip active" type="button" data-filter-child="all">All</button>
  <button class="filter-chip" type="button" data-filter-child="everly">
    <span class="child-chip everly" aria-hidden="true">E</span>Everly
  </button>
  <button class="filter-chip" type="button" data-filter-child="isla">
    <span class="child-chip isla" aria-hidden="true">I</span>Isla
  </button>
</div>
```

Each card body gains a single new attribute — `data-child="everly"`, `data-child="isla"`, or `data-child=""`. `_event_card` and `_undated_card` both already bind a local `child` variable, so threading the attribute is one line each.

## CSS sketch

Added near the existing `.child-chip` rules:

```css
.filter-chips {
  display: flex;
  justify-content: center;
  gap: 0.4rem;
  padding: 0.5rem 1rem;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
}
.filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  background: transparent;
  color: var(--text-secondary);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 0.2rem 0.7rem;
  font-size: 0.8rem;
  font-weight: 500;
  cursor: pointer;
  font-family: inherit;
  line-height: 1.4;
}
.filter-chip:hover {
  background: var(--border);
  color: var(--text);
}
.filter-chip.active {
  background: var(--text);
  color: var(--surface);
  border-color: var(--text);
}
body.filter-everly .event-card[data-child="isla"],
body.filter-isla   .event-card[data-child="everly"] { display: none !important; }
```

### `.show-ignored` interaction note

The existing ignored-toggle uses `display: block !important` to reveal ignored cards. The filter hide rule uses `display: none !important` too — for symmetry and so an Everly-filter still hides an Isla-tagged ignored card even when "Show ignored" is on. Without `!important` the filter hide would lose to `.show-ignored .event-card.ignored { display: block !important; }` in specificity order, which is the wrong behaviour. Covered by a dedicated regression test below.

## JS sketch

Dropped inside the existing IIFE in `render_html`:

```js
var filterChips = document.querySelectorAll('.filter-chip');
filterChips.forEach(function (chip) {
  chip.addEventListener('click', function () {
    var kid = chip.getAttribute('data-filter-child');
    document.body.classList.remove('filter-everly', 'filter-isla');
    if (kid !== 'all') {
      document.body.classList.add('filter-' + kid);
    }
    filterChips.forEach(function (c) {
      c.classList.toggle('active', c === chip);
    });
  });
});
```

Ephemeral by design — no read/write to localStorage.

## Test plan

### Commit 1 (roster) — `tests/test_agent.py`

- `test_extraction_prompt_embeds_roster_prose` — imports `agent`, asserts `agent.EXTRACTION_SYSTEM_PROMPT` contains each of `"Everly"`, `"Isla"`, `"Ms. Anita Sahai"`, `"Ms. Meredith Rohde"`, `"Louise Archer Elementary"`, `"Teacher roster"`.
- `test_roster_prose_formatter_unit` — calls `agent._load_roster_prose()` with no argument or calls a helper like `agent._format_roster_prose(mapping)` with a fabricated two-kid dict and asserts the formatted string shape (one line per kid, teacher + grade + school each appear once per kid, trailing attribution rule present). Keeps the coverage at unit level — no roster-file juggling.
- `test_missing_roster_file_raises` — monkeypatches `agent._ROSTER_PATH` to a nonexistent path, expects `reload(agent)` (or a direct call to `_load_roster_prose`) to raise. Guards the crash-on-missing contract.

### Commit 2 (chip row) — `tests/test_process_events.py`

Under a new `# ─── per-kid filter chips (#12) ───` banner:

- `test_render_html_contains_filter_chip_row` — `class="filter-chips"` present, three `data-filter-child` buttons, labels `All` / `Everly` / `Isla`.
- `test_render_html_filter_chip_row_is_static_not_derived_from_events` — run the renderer against an input set containing *only* Isla events. The chip row still contains all three chips (`data-filter-child="all"`, `="everly"`, `="isla"`). Guards against a future refactor that "helpfully" derives the chip set from the event data.
- `test_render_html_event_cards_carry_data_child_everly` — Everly card renders with `data-child="everly"` on `.event-card`.
- `test_render_html_event_cards_carry_data_child_isla` — Isla card renders with `data-child="isla"`.
- `test_render_html_event_cards_empty_data_child_for_audience` — audience-line card (`child: "All LAES students"`) renders with `data-child=""`.
- `test_render_html_event_cards_empty_data_child_for_empty_child` — empty `child` renders with `data-child=""`.
- `test_render_html_filter_hide_css_uses_important` — CSS contains `display: none !important` under `body.filter-everly` / `body.filter-isla`. Prevents regression of the `.show-ignored` interaction.
- `test_render_html_undated_cards_carry_data_child` — `_undated_card` emits `data-child` the same way as `_event_card`. Guards the parallel change.

Rough counts: 3 roster tests (commit 1) + 8 chip tests (commit 2) = 11 new cases. Target test count after both commits: 238 + 11 = 249.

## Non-goals

- Filter chips for non-kid `child` values (audience labels, grade labels, empty).
- Remembering the filter across page loads.
- Hiding bare week headers when a filter empties the week.
- A counter on each chip (e.g. `Everly (4)`). Simplicity first; add only if Tom asks after eyeballing the live build.
- Any change to the text-email body, ICS export, Apps Script, or Gmail query shape.
- Any change to the protected-senders list.
- Mobile-specific tweaks — the chip row inherits the `.stats` container layout and short chip labels should wrap gracefully at phone widths. Revisit only if Tom flags a visual issue.
- Animating the hide transition — the existing `.event-card` already has `transition: opacity 0.25s ease` for the fade pattern; adding a fade on filter would require threading an opacity step into the JS handler, which is out of scope for this feature.

## Responsibility table

| Concern | Owner | Notes |
|---|---|---|
| Teacher-to-kid mapping source of truth | `class_roster.json` | JSON only; edited by hand for fall updates. |
| Formatting roster into prompt prose | `agent.py::_load_roster_prose` (or `_format_roster_prose`) | Pure function; tested. |
| Crashing on missing/malformed roster | `agent.py` at module import | File is committed; absence is a bug. |
| Agent-side routing of teacher-named / grade-named emails to the right kid | `agent.py` via prompt prose | Judgment under ambiguity — correct place is the extractor prompt. |
| Emitting `data-child` on each card | `scripts/process_events.py::_event_card` and `::_undated_card` | Lowercase of `child` if named kid (`Everly` / `Isla`), else empty. |
| Emitting the chip row HTML | `scripts/process_events.py::render_html` | Hard-coded three-chip row — not derived from input events. |
| Filter CSS (hide rules, chip styling) | `render_html` style block | Reuses `--everly` / `--isla`. |
| Filter click handler / body-class toggle | Inline JS in `render_html` | Vanilla DOM; no dependencies. |
| Filter persistence | None — ephemeral | No localStorage key reserved. |
| `.show-ignored` × filter interaction | Both rules use `display: none !important` / `display: block !important` | Covered by `test_render_html_filter_hide_css_uses_important`. |

## Commit plan

1. This design note — standalone commit. ROADMAP #12 stays `[ ]`.
2. Roster subtask — `class_roster.json` + `agent.py` prose injection + 3 tests. ROADMAP #12 stays `[ ]`.
3. Chip row — renderer + CSS + JS + 8 tests. Flip ROADMAP #12 → `[~]` with SHA. Tom visual-QAs via `scripts/dev_render.py` and against the next live GitHub Pages build.
4. Close-out once Tom signs off — next session flips `[~]` → `[x]`.
