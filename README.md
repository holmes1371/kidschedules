# Kids Schedule — GitHub Actions + GitHub Pages

Searches Ellen's Gmail for upcoming kids' activities (school events, appointments, sports, academic deadlines) and publishes a clean schedule to a GitHub Pages website every Monday morning.

No computer needs to be on. Just bookmark the page.

## Setup

### 1. Google Cloud (one time)

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (e.g., "Kids Schedule Bot")
3. Enable the **Gmail API** (APIs & Services → Library → search "Gmail API")
4. Configure OAuth consent screen → External → add scope `https://www.googleapis.com/auth/gmail.modify`
5. Create OAuth credentials → Desktop app → download `client_secret.json`

### 2. Generate refresh token (one time)

```bash
pip install google-auth-oauthlib
python -c "
from google_auth_oauthlib.flow import InstalledAppFlow
flow = InstalledAppFlow.from_client_secrets_file(
    r'C:\Users\tholm\Downloads\client_secret.json',
    scopes=['https://www.googleapis.com/auth/gmail.modify'])
creds = flow.run_local_server(port=0)
print('Refresh token:', creds.refresh_token)
print('Client ID:', creds.client_id)
print('Client secret:', creds.client_secret)
"
```

Ellen signs in and clicks "Allow" in the browser that opens.

### 3. GitHub repository secrets

In your GitHub repo → Settings → Secrets and variables → Actions, add:

| Secret name | Value |
|---|---|
| `GMAIL_CLIENT_ID` | From step 2 output |
| `GMAIL_CLIENT_SECRET` | From step 2 output |
| `GMAIL_REFRESH_TOKEN` | From step 2 output |
| `ANTHROPIC_API_KEY` | Your Anthropic API key |

### 4. Enable GitHub Pages

In your repo → Settings → Pages:
- Source: **Deploy from a branch**
- Branch: **main**, folder: **/docs**
- Save

### 5. Push and go

Push this repo to GitHub. The workflow runs automatically every Monday at 6:30 AM Eastern. You can also trigger it manually from the Actions tab.

Your schedule will be live at `https://<your-username>.github.io/kids-schedule-github/`

### 6. Ignore-button webhook (one time, optional)

Each event card on the schedule page has an "Ignore" button. Clicking hides the card in the user's browser; if this webhook is configured, the ignore also sticks across future weekly runs.

1. Go to [sheets.new](https://sheets.new) and rename the new sheet to **Kids Schedule — Ignored Events**.
2. Extensions → Apps Script. Delete the starter code.
3. Paste the contents of `scripts/apps_script.gs`.
4. Generate a random secret (`openssl rand -hex 16` or any generator) and replace `REPLACE_ME_WITH_RANDOM_STRING` in the script with it.
5. Save. Click **Deploy → New deployment**:
   - Type: **Web app**
   - Execute as: **Me**
   - Who has access: **Anyone**
6. Click **Deploy**, authorize when prompted, then copy the **Web app URL** (ends in `/exec`).
7. Paste that URL into `ignore_webhook_url.txt` (in the repo root). Commit and push.
8. In your GitHub repo → Settings → Secrets and variables → Actions, add:

| Secret name | Value |
|---|---|
| `IGNORE_READ_SECRET` | The random string from step 4 |

That's it. The next run (weekly or manual) will sync the ignore list from the sheet before rendering.

**To unignore an event:** delete its row from the Google Sheet. Next run picks up the change.

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
gmail_client.py          — Gmail API wrapper (replaces Cowork MCP connector)
agent.py                 — Anthropic API for event extraction (judgment step)
scripts/
  build_queries.py       — date math + Gmail query construction (deterministic)
  process_events.py      — filter, dedupe, sort, render HTML + text (deterministic)
blocklist.txt            — sender domains excluded from searches
docs/
  index.html             — generated schedule page (served by GitHub Pages)
.github/workflows/
  weekly-schedule.yml    — GitHub Actions cron workflow
```

## Cost

- **Gmail API**: free
- **GitHub Actions**: free tier (~2 min/week)
- **GitHub Pages**: free
- **Anthropic API**: ~$0.20–0.50/run with Sonnet, ~$1–2/month
