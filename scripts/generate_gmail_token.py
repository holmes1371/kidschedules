#!/usr/bin/env python3
"""Generate a Gmail OAuth refresh token for the weekly workflow.

Runs the browser-based consent flow against the Desktop OAuth client in
`client_secret.json` at the repo root, and prints the resulting refresh
token. Paste the printed value into the `GMAIL_REFRESH_TOKEN` repo
secret (Settings -> Secrets and variables -> Actions).

Usage:
    py scripts/generate_gmail_token.py

Preconditions:
    - `client_secret.json` at the repo root (Desktop-type OAuth client).
    - `google-auth-oauthlib` installed (already in requirements.txt).
    - OAuth app is in "In production" publishing state so the refresh
      token does not expire after 7 days.
"""
from __future__ import annotations

import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

# Must match gmail_client.SCOPES. Kept in sync manually — if you edit
# the scope list in gmail_client.py, edit it here too.
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def main() -> int:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    client_secret_path = os.path.join(repo_root, "client_secret.json")

    if not os.path.exists(client_secret_path):
        print(
            f"client_secret.json not found at {client_secret_path}.\n"
            "Download it from Google Cloud Console -> APIs & Services -> "
            "Credentials -> your OAuth client -> Download JSON, and place "
            "it at the repo root.",
            file=sys.stderr,
        )
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
    creds = flow.run_local_server(port=0)

    if not creds.refresh_token:
        print(
            "Auth succeeded but no refresh_token was returned. This usually "
            "means Google already had a refresh token on file for this "
            "client+account pair. Revoke access at "
            "https://myaccount.google.com/permissions and re-run.",
            file=sys.stderr,
        )
        return 2

    print()
    print("Refresh token (paste into GMAIL_REFRESH_TOKEN secret):")
    print(creds.refresh_token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
