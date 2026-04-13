# Kids Schedule — GitHub Actions

Searches Ellen's Gmail for upcoming kids' activities (school events, appointments, sports, academic deadlines) and saves a consolidated schedule as a Gmail draft every Monday morning.

Runs on GitHub Actions — no computer needs to be on.

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

### 4. Push and go

Push this repo to GitHub. The workflow runs automatically every Monday at 6:30 AM Eastern, or you can trigger it manually from the Actions tab.

## Local development

```bash
# Set env vars
export GMAIL_CLIENT_ID="..."
export GMAIL_CLIENT_SECRET="..."
export GMAIL_REFRESH_TOKEN="..."
export ANTHROPIC_API_KEY="..."

# Install
pip install -r requirements.txt

# Run
python main.py              # full run
python main.py --dry-run    # skip creating the draft
python main.py --lookback-days 90  # wider search window
```

## Architecture

```
main.py                  — orchestrator, wires all steps together
gmail_client.py          — Gmail API wrapper (replaces Cowork MCP connector)
agent.py                 — Anthropic API for event extraction (judgment step)
scripts/
  build_queries.py       — date math + Gmail query construction (deterministic)
  process_events.py      — filter, dedupe, sort, render draft body (deterministic)
blocklist.txt            — sender domains excluded from searches
.github/workflows/
  weekly-schedule.yml    — GitHub Actions cron workflow
```

## Cost

- **Gmail API**: free (well within quota)
- **GitHub Actions**: free tier covers this easily (~2 min/week)
- **Anthropic API**: ~$0.20–0.50/run with Sonnet, ~$1–2/month at weekly cadence
