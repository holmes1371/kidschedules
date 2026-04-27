"""Gmail API client — replaces the Cowork MCP Gmail connector.

Authenticates via OAuth2 refresh token (stored in env vars for GitHub
Actions, or in a local token.json for development). Provides the same
operations the Cowork skill uses: search, read message, create draft.
"""
from __future__ import annotations

import base64
import json
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

# ROADMAP #33. Per-PDF size cap. PDFs exceeding this are skipped with a
# warning; the email body still flows through the agent normally so a
# too-large attachment degrades to today's behavior rather than failing
# the batch. School newsletters in practice are 100KB–2MB; 8MB is a
# comfortable headroom while well under Anthropic's hard limit (32MB
# per document block, 100 pages).
MAX_PDF_BYTES = 8 * 1024 * 1024


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
        """Read a full message and return id, headers, decoded body text,
        and any PDF attachments under MAX_PDF_BYTES (ROADMAP #33).

        The returned ``pdfs`` field is always a list (possibly empty);
        callers can iterate it without a key check. Oversized PDFs are
        skipped with a stderr warning. Reference-style attachments
        (data lives behind ``attachmentId`` rather than inline) trigger
        a second ``messages.attachments.get`` call to fetch the bytes.
        """
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
        pdfs = self._extract_pdfs(msg.get("payload", {}), message_id)
        return {
            "messageId": message_id,
            "threadId": msg.get("threadId"),
            "headers": headers,
            "snippet": msg.get("snippet", ""),
            "body": body_text,
            "pdfs": pdfs,
        }

    def _extract_pdfs(
        self, payload: dict, message_id: str
    ) -> list[bytes]:
        """Walk a message payload and return all `application/pdf` parts
        as raw bytes (ROADMAP #33).

        Two attachment shapes the Gmail API returns:

        - Inline (small attachments): ``part.body.data`` carries the
          base64url-encoded bytes directly. Decode and return.
        - Reference (large attachments, typically >5MB): ``part.body``
          carries only ``attachmentId`` + ``size``. A second call to
          ``users.messages.attachments.get(messageId, attachmentId)``
          fetches the bytes.

        Per-PDF size cap: ``MAX_PDF_BYTES``. Oversized attachments —
        whether the size is known up front (reference-style) or only
        after decoding (inline) — are skipped with a stderr warning.
        Skip is non-fatal: the body still flows through the agent
        normally for that message.

        Recursive: handles `multipart/mixed` wrapping `multipart/
        related` wrapping `multipart/alternative` (the realistic shape
        from Outlook clients), plus deeper nesting if it ever shows up.
        """
        out: list[bytes] = []
        self._walk_pdf_parts(payload, message_id, out)
        return out

    def _walk_pdf_parts(
        self, part: dict, message_id: str, out: list[bytes]
    ) -> None:
        """Recursive helper for `_extract_pdfs`."""
        mime = part.get("mimeType", "")
        if mime == "application/pdf":
            body = part.get("body", {}) or {}
            size = int(body.get("size", 0) or 0)
            if size and size > MAX_PDF_BYTES:
                fname = part.get("filename") or "(unnamed)"
                print(
                    f"    WARNING: skipping PDF {fname!r} on "
                    f"message {message_id} — {size} bytes "
                    f"exceeds MAX_PDF_BYTES ({MAX_PDF_BYTES})"
                )
                return
            inline_data = body.get("data")
            if inline_data:
                try:
                    decoded = base64.urlsafe_b64decode(inline_data)
                except (TypeError, ValueError) as e:
                    print(
                        f"    WARNING: PDF on message {message_id} "
                        f"failed to base64-decode: {e}"
                    )
                    return
            else:
                attachment_id = body.get("attachmentId")
                if not attachment_id:
                    return
                try:
                    resp = (
                        self._service.users()
                        .messages()
                        .attachments()
                        .get(
                            userId=self._user,
                            messageId=message_id,
                            id=attachment_id,
                        )
                        .execute()
                    )
                except Exception as e:
                    print(
                        f"    WARNING: failed to fetch PDF attachment "
                        f"{attachment_id!r} on message {message_id}: {e}"
                    )
                    return
                fetched_data = (resp or {}).get("data")
                if not fetched_data:
                    return
                try:
                    decoded = base64.urlsafe_b64decode(fetched_data)
                except (TypeError, ValueError) as e:
                    print(
                        f"    WARNING: PDF attachment "
                        f"{attachment_id!r} on message {message_id} "
                        f"failed to base64-decode: {e}"
                    )
                    return
            # Defensive: if the inline-data branch undercounted the size
            # (some Outlook payloads omit ``size`` or report the encoded
            # length), enforce the cap on the decoded bytes too.
            if len(decoded) > MAX_PDF_BYTES:
                fname = part.get("filename") or "(unnamed)"
                print(
                    f"    WARNING: skipping PDF {fname!r} on "
                    f"message {message_id} — decoded {len(decoded)} "
                    f"bytes exceeds MAX_PDF_BYTES ({MAX_PDF_BYTES})"
                )
                return
            out.append(decoded)
            return
        # Multipart container — recurse. Anything else (text/html,
        # text/plain, image/png, etc.) is silently ignored at this
        # layer; `_extract_body` handles the body parts.
        for child in part.get("parts", []) or []:
            self._walk_pdf_parts(child, message_id, out)

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
        self,
        subject: str,
        body: str,
        content_type: str = "text/plain",
        text_alternative: str | None = None,
    ) -> dict[str, Any]:
        """Create a Gmail draft (no recipient) and return its metadata.

        If `text_alternative` is provided, builds a multipart/alternative
        message with `body` as the primary part (honoring `content_type`)
        and `text_alternative` as a plain-text fallback. Otherwise the
        draft is a single-part message, matching prior behavior.
        """
        subtype = "plain" if content_type == "text/plain" else "html"
        if text_alternative is not None:
            mime_msg: MIMEText | MIMEMultipart = MIMEMultipart("alternative")
            mime_msg.attach(MIMEText(text_alternative, "plain"))
            mime_msg.attach(MIMEText(body, subtype))
        else:
            mime_msg = MIMEText(body, subtype)
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
