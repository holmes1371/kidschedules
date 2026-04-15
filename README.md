# Kids Schedule — GitHub Actions + GitHub Pages

Searches Ellen's Gmail for upcoming kids' activities (school events, appointments, sports, academic deadlines) and publishes a clean schedule to a GitHub Pages site on a recurring schedule. No computer needs to be on — just bookmark the page.

## For future agents

Before touching anything, read `ROADMAP.md` at the repo root. It holds the prioritized QoL backlog, the session-discipline rules (design-note-first, commit at natural boundaries, pytest fixtures updated in step with `process_events.py` changes, ROADMAP status updates at session end), and the completed items with their commit SHAs. Feature design notes live under `design/`.

## Local development

```bash
export GMAIL_CLIENT_ID="..."
export GMAIL_CLIENT_SECRET="..."
export GMAIL_REFRESH_TOKEN="..."
export ANTHROPIC_API_KEY="..."

pip install -r requirements.txt

python main.py              # full run
python main.py --dry-run    # skip publishing
python main.py --lookback-days 90  # wider search window
```

## Architecture

```
main.py                  — orchestrator, wires all steps together
gmail_client.py          — Gmail API wrapper
agent.py                 — Anthropic API for event extraction (judgment step)
events_state.py          — persistent cache of processed messages + events
scripts/
  build_queries.py       — date math + Gmail query construction (deterministic)
  process_events.py      — filter, dedupe, sort, render HTML + text (deterministic)
  apps_script.gs         — Google Apps Script for ignore-button webhook
blocklist.txt            — sender domains excluded from searches
docs/
  index.html             — generated schedule page (served by GitHub Pages)
.github/workflows/
  weekly-schedule.yml    — cron workflow (Mon/Wed/Sat 6:15 AM Eastern)
  tests.yml              — pytest on push + PR
tests/                   — pytest suite covering process_events.py
design/                  — per-feature design notes
```

## Cost

- **Gmail API**: free
- **GitHub Actions**: free tier (a few minutes per run)
- **GitHub Pages**: free
- **Anthropic API**: small per-run cost on Sonnet; incremental-extraction caching (see `design/incremental-extraction.md`) skips messages already processed, so most runs touch the agent only for genuinely new email.
