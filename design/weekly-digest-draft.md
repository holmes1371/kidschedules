# Weekly digest → Gmail draft (with strict safety gates)

Roadmap item 3. A Gmail draft summarizing this week's events, with a link to the Pages URL, created once per cron run. The hard constraint: manual runs, local runs, and any run outside the scheduled cron must never create a draft.

## Safety model — three layers

1. **CLI default is no-draft.** `main.py` adds `--create-draft` (explicit opt-in). Default is off. No `--no-draft` flag — the default *is* no-draft, so a negator would be noise. Env var `CREATE_DRAFT=1` is equivalent for workflow plumbing.
2. **Workflow default is no-draft.** Only the `schedule:` trigger sets `CREATE_DRAFT=1`. A manual `workflow_dispatch` does not create a draft unless the operator explicitly toggles a new `create_draft` input. `dry_run` suppresses drafts even when other flags are set.
3. **Always-on preview logging.** Whether or not we actually call `create_draft`, the rendered subject + text body are printed to stdout. Local runs and manual workflow runs can eyeball the draft content without touching Gmail.

Gate function in `main.py`:

```python
def should_create_draft(args) -> bool:
    if args.dry_run:
        return False
    if args.create_draft:
        return True
    if os.environ.get("CREATE_DRAFT") == "1":
        return True
    return False
```

This is the only place the decision lives. It's unit-tested exhaustively — the load-bearing piece for the spam-prevention guarantee. If the gate is correct, drafts cannot leak.

No `DummyGmailClient` end-to-end test — it would require refactoring `main.py` to accept injected clients, which is more invasive than the benefit justifies. Unit tests on `should_create_draft` + rendering tests cover the real risk.

## Scope of "this week"

Events whose `week_start(date) == week_start(today)` — the Monday-to-Sunday bucket containing today. On Monday cron runs this is the current calendar week. We'll revisit if the one-week scope turns out to be too narrow in practice.

## Draft content

- **Subject:** `Kids' Schedule — Week of April N` (derived from `week_start(today)`).
- **HTML body:** short summary — this-week events grouped by day, each line as `Day, Month N — Event name · Time`. Prominent link to the Pages URL for the full 60-day view. `&amp;/&lt;/&gt;` escaped in event names.
- **Text body:** same structure, plain text. MIME multipart/alternative so Gmail renders the HTML version but clients that prefer plain text see a clean fallback.
- **Recipient:** none. Draft is saved with no `To:` header; Ellen fills in and sends manually. Forces a conscious send step.
- **Empty-week guard:** if `this_week_count == 0`, `main.py` skips the `create_draft` call entirely and logs why. Creating a "nothing this week" draft is spam by another name.

## Where rendering lives

Three new pure functions in `scripts/process_events.py`:

- `render_digest_html(weeks, today, pages_url) -> str`
- `render_digest_text(weeks, today, pages_url) -> str`
- `digest_subject(today) -> str`

Surfaced via new CLI flags on `process_events.py`: `--digest-html-out`, `--digest-text-out`, `--pages-url`. Existing `meta` JSON gains a `digest` block with `{subject, this_week_count}` so `main.py` reads one file for all digest info.

Keeps mechanical work in the script (standing order); `main.py` only orchestrates and gates.

## Pages URL — committed file

New `pages_url.txt` at repo root, same pattern as `ignore_webhook_url.txt`. Contains one line: the GitHub Pages URL. Initially empty; Tom populates it post-merge. Empty/missing file → digest body still renders but without a link. Safe degradation.

## gmail_client.py extension

Current `create_draft` builds a single-part `MIMEText`. Extended signature:

```python
def create_draft(
    subject: str,
    body: str,
    content_type: str = "text/plain",
    text_alternative: str | None = None,
) -> dict[str, Any]
```

If `text_alternative` is provided, build `MIMEMultipart("alternative")` with both parts; otherwise preserve existing single-part behavior. Backward compatible — no existing callers break.

## Files touched

- `scripts/process_events.py` — new render functions + CLI flags + meta additions.
- `tests/test_process_events.py` — new tests for the render functions + empty-week + HTML-escape.
- `fixtures/test/digest_this_week.json` — fixture exercising this-week events.
- `main.py` — `--create-draft` flag, `should_create_draft` function, load `pages_url.txt`, new step 6, always-on preview.
- `tests/test_main.py` (new) — `should_create_draft` unit tests, parametrized across all gate combinations.
- `gmail_client.py` — `text_alternative` kwarg on `create_draft`.
- `pages_url.txt` — new, initially empty.
- `.github/workflows/weekly-schedule.yml` — `CREATE_DRAFT` env var set via expression, `create_draft` workflow_dispatch input.
- `ROADMAP.md` — mark `[x]`.

## Explicit non-goals

- **No end-to-end main.py mocking.** Gate tests + render tests cover the real risk.
- **No Gmail calls during development.** I will not run `python main.py --create-draft` locally. First real call happens in your first manual `workflow_dispatch` with `create_draft: true`, after you've eyeballed the Actions log preview.
- **No customization of draft recipient, CC, signature, etc.** Empty draft. Ellen edits and sends.
- **No multi-week or next-week preview.** One week only. Easy to extend later.

## Commit plan

1. Design note.
2. `process_events.py` render functions + CLI + fixtures + tests (cohesive).
3. `gmail_client.py` extension.
4. `pages_url.txt` (empty).
5. `main.py` wiring + `tests/test_main.py` (`should_create_draft` tests).
6. Workflow changes.
7. ROADMAP close-out.
