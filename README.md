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

## Regenerating the Gmail refresh token

The weekly workflow authenticates via a long-lived refresh token stored as the `GMAIL_REFRESH_TOKEN` repo secret. If the token is revoked or the OAuth app is ever reverted from "In production" back to "Testing" in Google Cloud Console, runs will fail with `invalid_grant: Token has been expired or revoked.`

To mint a fresh token:

1. Confirm the OAuth app (Google Cloud Console → APIs & Services → OAuth consent screen) is in **In production** status. Testing-mode apps expire refresh tokens after 7 days.
2. Place `client_secret.json` at the repo root (download from Google Cloud Console → Credentials → your Desktop OAuth client). Gitignored.
3. `python scripts/generate_gmail_token.py` — opens a browser, click through consent (including the "unverified app → Advanced → Go to [app name]" warning for the `gmail.modify` scope), copy the refresh token it prints.
4. GitHub repo → Settings → Secrets and variables → Actions → update `GMAIL_REFRESH_TOKEN`. Paste and save.
5. Trigger the workflow manually from the Actions tab to confirm.

## Cost

- **Gmail API**: free
- **GitHub Actions**: free tier (a few minutes per run)
- **GitHub Pages**: free
- **Anthropic API**: small per-run cost on Sonnet; incremental-extraction caching (see `design/incremental-extraction.md`) skips messages already processed, so most runs touch the agent only for genuinely new email.
