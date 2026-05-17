# 3. Weekly email digest to Gmail drafts, with test-mode toggle — b5200cb … f312d90

After publishing, create a Gmail draft summarizing this-week events with a link to the Pages URL. Built with a three-layer safety model (see `design/weekly-digest-draft.md`):

- `main.py --create-draft` is explicit opt-in; default is no-draft. `CREATE_DRAFT=1` env var is equivalent for workflow plumbing. `--dry-run` always suppresses.
- Workflow sets `CREATE_DRAFT=1` only when `github.event_name == 'schedule'` or the new `create_draft` workflow_dispatch input is true.
- Preview of the rendered digest subject + body prints to stdout on every run regardless of the gate, so local/manual runs can eyeball content without touching Gmail.

Render functions (`digest_subject`, `render_digest_text`, `render_digest_html`) live in `scripts/process_events.py`. Draft is HTML with plain-text alternative (`gmail_client.py::create_draft` now accepts `text_alternative`). Empty-week short-circuit: no draft when `this_week_count == 0`. Pages URL pulled from committed `pages_url.txt` (empty-safe). `should_create_draft` is unit-tested exhaustively across all gate combinations.

Commit trail: c89bd19 (design) · b5200cb (render + CLI + tests) · 2ffc458 (gmail_client) · 4838af0 (pages_url.txt) · 91cd5fb (main wiring + gate tests) · f312d90 (workflow).
