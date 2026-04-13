"""Gmail API client — replaces the Cowork MCP Gmail connector.

Authenticates via OAuth2 refresh token (stored in env vars for GitHub
Actions, or in a local token.json for development). Provides the same
operations the Cowork skill uses: search, read message, create draft.
"""
from __future__ import annotations

import base64
import json
import os
from email.mime.text import MIMEText
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def _get_credentials() -> Credentials:
    """Build credentials from environment variables or local token.json."""
    # Prefer env vars (GitHub Actions)
    client_id = os.environ.get("GMAIL_CLIENT_ID")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET")
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN")

    if client_id and client_secret and refresh_token:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )
        creds.refresh(Request())
        return creds

    # Fall back to local token.json (for development / local runs)
    token_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "token.json"
    )
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        return creds

    raise RuntimeError(
        "No Gmail credentials found. Set GMAIL_CLIENT_ID, "
        "GMAIL_CLIENT_SECRET, and GMAIL_REFRESH_TOKEN env vars, "
        "or provide a local token.json file."
    )


class GmailClient:
    """Thin wrapper around the Gmail API matching the operations the
    kids-schedule pipeline needs."""

    def __init__(self) -> None:
        creds = _get_credentials()
        self._service = build("gmail", "v1", credentials=creds)
        self._user = "me"

    def search_messages(
        self, query: str, max_results: int = 25
    ) -> list[dict[str, Any]]:
        """Search Gmail and return a list of message stubs.

        Each stub has: messageId, threadId, snippet, and headers dict
        with From, Subject, Date.
        """
        resp = (
            self._service.users()
            .messages()
            .list(userId=self._user, q=query, maxResults=max_results)
            .execute()
        )
        messages = resp.get("messages", [])
        results = []
        for msg_stub in messages:
            msg_id = msg_stub["id"]
            # Fetch metadata for each message (headers + snippet)
            meta = (
                self._service.users()
                .messages()
                .get(
                    userId=self._user,
                    id=msg_id,
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )
            headers = {}
            for h in meta.get("payload", {}).get("headers", []):
                headers[h["name"]] = h["value"]
            results.append(
                {
                    "messageId": msg_id,
                    "threadId": meta.get("threadId"),
                    "snippet": meta.get("snippet", ""),
                    "headers": headers,
                }
            )
        return results

    def read_message(self, message_id: str) -> dict[str, Any]:
        """Read a full message and return id, headers, and decoded body text."""
        msg = (
            self._service.users()
            .messages()
            .get(userId=self._user, id=message_id, format="full")
            .execute()
        )
        headers = {}
        for h in msg.get("payload", {}).get("headers", []):
            headers[h["name"]] = h["value"]

        body_text = self._extract_body(msg.get("payload", {}))
        return {
            "messageId": message_id,
            "threadId": msg.get("threadId"),
            "headers": headers,
            "snippet": msg.get("snippet", ""),
            "body": body_text,
        }

    def _extract_body(self, payload: dict) -> str:
        """Recursively extract plain-text body from a message payload."""
        # Single-part message
        if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

        # Multipart — prefer text/plain, fall back to text/html
        parts = payload.get("parts", [])
        plain = ""
        html = ""
        for part in parts:
            mime = part.get("mimeType", "")
            if mime == "text/plain" and part.get("body", {}).get("data"):
                plain = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
            elif mime == "text/html" and part.get("body", {}).get("data"):
                html = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
            elif mime.startswith("multipart/"):
                # Recurse into nested multipart
                nested = self._extract_body(part)
                if nested:
                    plain = plain or nested

        return plain or html or ""

    def create_draft(
        self, subject: str, body: str, content_type: str = "text/plain"
    ) -> dict[str, Any]:
        """Create a Gmail draft (no recipient) and return its metadata."""
        mime_msg = MIMEText(body, "plain" if content_type == "text/plain" else "html")
        mime_msg["Subject"] = subject
        raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("utf-8")
        draft = (
            self._service.users()
            .drafts()
            .create(userId=self._user, body={"message": {"raw": raw}})
            .execute()
        )
        return {
            "draftId": draft["id"],
            "messageId": draft.get("message", {}).get("id"),
            "subject": subject,
        }

    def get_profile(self) -> dict[str, Any]:
        """Return basic profile info (email address)."""
        return (
            self._service.users()
            .getProfile(userId=self._user)
            .execute()
        )
