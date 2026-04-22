"""Pytest suite for gmail_client.GmailClient._extract_body.

The Gmail API returns message payloads as nested MIME parts with
base64url-encoded body data. _extract_body walks that tree, prefers
text/plain, falls back to text/html, and recurses into nested
multipart subtrees. This suite pins the parts of that contract that
matter to the agent — empty body in, empty body out; non-ASCII bytes
decode without crashing; the plain/html preference does not flip.

GmailClient.__init__ refreshes OAuth credentials and builds the Gmail
service. _extract_body never touches self._service or self._user, so
tests bypass __init__ via GmailClient.__new__(GmailClient) — keeps
the suite hermetic with no monkeypatching of the auth stack.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

# gmail_client.py lives at the repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gmail_client import GmailClient  # noqa: E402


def _b64(text: str) -> str:
    """Encode text the way the Gmail API encodes payload body data."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _b64_bytes(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


@pytest.fixture
def client():
    """Bypass __init__ — _extract_body is independent of credentials."""
    return GmailClient.__new__(GmailClient)


def test_single_part_text_plain_decoded(client):
    payload = {
        "mimeType": "text/plain",
        "body": {"data": _b64("Hello world")},
    }
    assert client._extract_body(payload) == "Hello world"


def test_multipart_text_plain_only(client):
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("plain body")}},
        ],
    }
    assert client._extract_body(payload) == "plain body"


def test_multipart_text_html_only_falls_back(client):
    """text/html is the fallback when no text/plain alternative exists —
    common for HTML-only marketing senders."""
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": _b64("<p>html body</p>")}},
        ],
    }
    assert client._extract_body(payload) == "<p>html body</p>"


def test_multipart_with_both_plain_wins_html_first(client):
    """Plain wins regardless of part order — the agent never has to
    strip tags. Order: html part appears before plain in the payload."""
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": _b64("<p>html</p>")}},
            {"mimeType": "text/plain", "body": {"data": _b64("plain")}},
        ],
    }
    assert client._extract_body(payload) == "plain"


def test_multipart_with_both_plain_wins_plain_first(client):
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("plain")}},
            {"mimeType": "text/html", "body": {"data": _b64("<p>html</p>")}},
        ],
    }
    assert client._extract_body(payload) == "plain"


def test_nested_multipart_recurses_into_subtree(client):
    """multipart/mixed wrapping multipart/alternative is the common
    shape for emails with attachments. The plain text lives two levels
    deep; the recursive walk finds it and ignores the application/pdf
    sibling."""
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _b64("inner plain")}},
                    {"mimeType": "text/html", "body": {"data": _b64("<p>inner html</p>")}},
                ],
            },
            {"mimeType": "application/pdf", "body": {"attachmentId": "abc"}},
        ],
    }
    assert client._extract_body(payload) == "inner plain"


def test_empty_payload_returns_empty_string(client):
    """Defensive: an empty dict (no mimeType, no parts) returns ""
    rather than KeyError-ing."""
    assert client._extract_body({}) == ""


def test_text_plain_with_no_body_data_falls_through_to_html(client):
    """Plain part missing its data blob doesn't satisfy the plain
    branch — the html sibling becomes the body."""
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {}},
            {"mimeType": "text/html", "body": {"data": _b64("<p>html</p>")}},
        ],
    }
    assert client._extract_body(payload) == "<p>html</p>"


def test_non_ascii_bytes_decoded_with_replacement(client):
    """Bodies with bytes that aren't valid UTF-8 must not raise — the
    pipeline ingests whatever Gmail returns. errors='replace' swaps
    the bad byte for U+FFFD."""
    bad_bytes = b"caf\xe9 hello"  # latin-1 'é' — invalid as a UTF-8 lead byte alone
    payload = {
        "mimeType": "text/plain",
        "body": {"data": _b64_bytes(bad_bytes)},
    }
    result = client._extract_body(payload)
    assert "hello" in result
    assert "\ufffd" in result
