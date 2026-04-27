"""Pytest suite for gmail_client.

Two layers of coverage. (1) `_extract_body`: the Gmail API returns
message payloads as nested MIME parts with base64url-encoded body
data; the walker prefers text/plain, falls back to text/html, and
recurses into nested multipart subtrees. (2) `_get_credentials` and
the `GmailClient` API wrappers (`search_messages`, `read_message`,
`create_draft`, `get_profile`): the auth stack is monkeypatched and
the Gmail service is stubbed with a chainable fake so tests run with
no network and no real credentials. The wrapper tests pin payload
shape, the header-list → dict flattening, and the
multipart/alternative branch of `create_draft`.

GmailClient.__init__ refreshes OAuth credentials and builds the Gmail
service. The `_extract_body` tests never touch self._service or
self._user, so they bypass __init__ via GmailClient.__new__(GmailClient).
The wrapper tests assign a stub service directly for the same reason.
"""
from __future__ import annotations

import base64
import email
import sys
from pathlib import Path

import pytest

# gmail_client.py lives at the repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import gmail_client  # noqa: E402
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


# ── _get_credentials ────────────────────────────────────────────────────
#
# Three branches: env-var-provided refresh token, local token.json
# fallback, and the RuntimeError when neither is available. These tests
# monkeypatch google.oauth2.Credentials and google.auth.transport.Request
# so nothing hits the network and no real secrets are required.


_ENV_VARS = ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")


def _clear_gmail_env(monkeypatch):
    for v in _ENV_VARS:
        monkeypatch.delenv(v, raising=False)


def test_get_credentials_env_var_path(monkeypatch, tmp_path):
    """All three env vars set → a Credentials object is constructed with
    those values, refresh() is called on it, and it is returned. The
    token.json fallback is skipped even if a file exists."""
    _clear_gmail_env(monkeypatch)
    monkeypatch.setenv("GMAIL_CLIENT_ID", "cid")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "rtoken")

    captured = {}

    class FakeCreds:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = kwargs
            self.refreshed_with = None

        def refresh(self, request):
            self.refreshed_with = request

        @classmethod
        def from_authorized_user_file(cls, *_a, **_k):
            raise AssertionError(
                "from_authorized_user_file must not be called when env "
                "vars are present"
            )

    sentinel_request = object()
    monkeypatch.setattr(gmail_client, "Credentials", FakeCreds)
    monkeypatch.setattr(gmail_client, "Request", lambda: sentinel_request)

    result = gmail_client._get_credentials()

    assert isinstance(result, FakeCreds)
    assert result.refreshed_with is sentinel_request
    assert captured["init_kwargs"] == {
        "token": None,
        "refresh_token": "rtoken",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "cid",
        "client_secret": "csecret",
        "scopes": gmail_client.SCOPES,
    }


def test_get_credentials_token_json_fallback(monkeypatch, tmp_path):
    """No env vars, token.json present → loaded via
    from_authorized_user_file. Not expired, so refresh() is NOT called."""
    _clear_gmail_env(monkeypatch)

    token_path = Path(gmail_client.__file__).resolve().parent / "token.json"
    captured = {}
    refresh_calls = []

    class FakeCreds:
        expired = False
        refresh_token = "rt"

        def refresh(self, request):  # pragma: no cover - guarded by test
            refresh_calls.append(request)

    def fake_from_file(path, scopes):
        captured["path"] = path
        captured["scopes"] = scopes
        return FakeCreds()

    monkeypatch.setattr(
        gmail_client.Credentials, "from_authorized_user_file",
        staticmethod(fake_from_file),
    )
    monkeypatch.setattr(
        gmail_client.os.path, "exists",
        lambda p: p == str(token_path),
    )
    monkeypatch.setattr(gmail_client, "Request", lambda: object())

    result = gmail_client._get_credentials()

    assert isinstance(result, FakeCreds)
    assert captured["path"] == str(token_path)
    assert captured["scopes"] == gmail_client.SCOPES
    assert refresh_calls == []


def test_get_credentials_token_json_expired_refreshes(monkeypatch):
    """token.json loaded, creds.expired is True and a refresh_token
    exists → refresh() fires before the creds are returned."""
    _clear_gmail_env(monkeypatch)

    token_path = Path(gmail_client.__file__).resolve().parent / "token.json"
    refresh_calls = []

    class FakeCreds:
        expired = True
        refresh_token = "rt"

        def refresh(self, request):
            refresh_calls.append(request)

    sentinel_request = object()
    monkeypatch.setattr(
        gmail_client.Credentials, "from_authorized_user_file",
        staticmethod(lambda *a, **k: FakeCreds()),
    )
    monkeypatch.setattr(
        gmail_client.os.path, "exists",
        lambda p: p == str(token_path),
    )
    monkeypatch.setattr(gmail_client, "Request", lambda: sentinel_request)

    result = gmail_client._get_credentials()

    assert isinstance(result, FakeCreds)
    assert refresh_calls == [sentinel_request]


def test_get_credentials_raises_when_no_source_available(monkeypatch):
    """No env vars AND no token.json → RuntimeError with an actionable
    message. This is the boot-time signal to the operator that the
    pipeline is missing its auth config."""
    _clear_gmail_env(monkeypatch)
    monkeypatch.setattr(gmail_client.os.path, "exists", lambda p: False)

    with pytest.raises(RuntimeError, match="No Gmail credentials found"):
        gmail_client._get_credentials()


# ── GmailClient API wrappers ────────────────────────────────────────────
#
# The Gmail API client is a nested set of resource builders — e.g.
# service.users().messages().list(...).execute(). `_ChainedResource`
# below is a tiny fake that lets a test script canned responses for
# each leaf call while recording the kwargs the wrapper passed in.


class _ChainedResource:
    """Fake Gmail API resource.

    Instantiated with a routing dict: ``{"messages.list": lambda **kw: ...,
    "messages.get": lambda **kw: ..., ...}``. Each entry maps a
    dotted method path to a callable that returns the `execute()` payload.
    Unknown paths raise AssertionError so drift in the wrapper shows up
    as a failing test rather than a silent wrong-method call."""

    def __init__(self, routes, path=""):
        self._routes = routes
        self._path = path
        self._pending_kwargs = None

    def __getattr__(self, name):
        return _ChainedResource(self._routes, _join(self._path, name))

    def __call__(self, **kwargs):
        self._pending_kwargs = kwargs
        return self

    def execute(self):
        route = self._routes.get(self._path)
        if route is None:
            raise AssertionError(
                f"Unexpected Gmail API path: {self._path!r} "
                f"(kwargs={self._pending_kwargs})"
            )
        return route(**(self._pending_kwargs or {}))


def _join(prefix, name):
    # Resource builders (users(), messages(), drafts()) return a new
    # resource with the same path, so the method call — not the accessor
    # — is what advances the path. We treat every accessor as path-append
    # but collapse the no-op ones (users, the resource namespaces) when
    # constructing test routes.
    if not prefix:
        return name
    return f"{prefix}.{name}"


def _client_with_routes(routes):
    client = GmailClient.__new__(GmailClient)
    client._service = _ChainedResource(routes)
    client._user = "me"
    return client


def test_search_messages_flattens_headers_and_returns_stubs():
    """search_messages does a list() to find message IDs, then a get()
    per id to fetch headers + snippet. Headers come back as a list of
    {name,value} dicts and must be flattened to a {name: value} dict
    the rest of the pipeline expects."""
    def list_route(**kwargs):
        assert kwargs["userId"] == "me"
        assert kwargs["q"] == "subject:field trip"
        assert kwargs["maxResults"] == 5
        return {"messages": [{"id": "m1"}, {"id": "m2"}]}

    def get_route(**kwargs):
        assert kwargs["userId"] == "me"
        assert kwargs["format"] == "metadata"
        assert kwargs["metadataHeaders"] == ["From", "Subject", "Date"]
        payload_by_id = {
            "m1": {
                "threadId": "t1",
                "snippet": "snippet 1",
                "payload": {"headers": [
                    {"name": "From", "value": "a@x.com"},
                    {"name": "Subject", "value": "Hi"},
                    {"name": "Date", "value": "Mon"},
                ]},
            },
            "m2": {
                "threadId": "t2",
                "snippet": "snippet 2",
                "payload": {"headers": [
                    {"name": "From", "value": "b@x.com"},
                    {"name": "Subject", "value": "Bye"},
                    {"name": "Date", "value": "Tue"},
                ]},
            },
        }
        return payload_by_id[kwargs["id"]]

    client = _client_with_routes({
        "users.messages.list": list_route,
        "users.messages.get": get_route,
    })

    result = client.search_messages("subject:field trip", max_results=5)

    assert result == [
        {
            "messageId": "m1",
            "threadId": "t1",
            "snippet": "snippet 1",
            "headers": {"From": "a@x.com", "Subject": "Hi", "Date": "Mon"},
        },
        {
            "messageId": "m2",
            "threadId": "t2",
            "snippet": "snippet 2",
            "headers": {"From": "b@x.com", "Subject": "Bye", "Date": "Tue"},
        },
    ]


def test_search_messages_empty_result_set():
    """List returns {} (no 'messages' key) → wrapper returns []
    without issuing any metadata gets."""
    def list_route(**kwargs):
        return {}

    def get_route(**kwargs):  # pragma: no cover - guarded
        raise AssertionError("get must not be called when list is empty")

    client = _client_with_routes({
        "users.messages.list": list_route,
        "users.messages.get": get_route,
    })

    assert client.search_messages("anything") == []


def test_search_messages_default_max_results_is_25():
    """Default max_results value threads through to the API call."""
    captured = {}

    def list_route(**kwargs):
        captured.update(kwargs)
        return {"messages": []}

    client = _client_with_routes({
        "users.messages.list": list_route,
    })
    client.search_messages("q")

    assert captured["maxResults"] == 25


def test_read_message_returns_full_envelope():
    """read_message does a single full-format get, flattens headers,
    and routes the payload through _extract_body."""
    body_text = "plain body"

    def get_route(**kwargs):
        assert kwargs["userId"] == "me"
        assert kwargs["id"] == "m42"
        assert kwargs["format"] == "full"
        return {
            "threadId": "t42",
            "snippet": "snip",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "sender@x.com"},
                    {"name": "Subject", "value": "Re: event"},
                ],
                "body": {
                    "data": base64.urlsafe_b64encode(
                        body_text.encode("utf-8")
                    ).decode("ascii"),
                },
            },
        }

    client = _client_with_routes({
        "users.messages.get": get_route,
    })

    result = client.read_message("m42")

    assert result == {
        "messageId": "m42",
        "threadId": "t42",
        "headers": {"From": "sender@x.com", "Subject": "Re: event"},
        "snippet": "snip",
        "body": body_text,
        "pdfs": [],
    }


# ─── ROADMAP #33: PDF attachment fetching ────────────────────────────────


_PDF_BYTES = b"%PDF-1.4\n%fake-pdf-bytes-for-tests\n%%EOF"


def _multipart_payload_with_pdf(
    body_text: str = "body",
    pdf_data_b64: str | None = None,
    attachment_id: str | None = None,
    pdf_size: int | None = None,
    pdf_filename: str = "March 25th.pdf",
) -> dict:
    """Hand-craft the realistic Outlook payload shape: multipart/mixed
    wrapping multipart/related wrapping multipart/alternative for the
    body, plus a sibling application/pdf part. Either inline data or
    attachmentId reference.

    Mirrors the structure of fixtures/test/pdf_newsletter_third_grade.eml
    so test code reflects the real Gmail-API response shape.
    """
    pdf_body: dict = {}
    if pdf_data_b64 is not None:
        pdf_body["data"] = pdf_data_b64
    if attachment_id is not None:
        pdf_body["attachmentId"] = attachment_id
    if pdf_size is not None:
        pdf_body["size"] = pdf_size
    return {
        "mimeType": "multipart/mixed",
        "headers": [{"name": "From", "value": "teacher@fcps.edu"}],
        "parts": [
            {
                "mimeType": "multipart/related",
                "parts": [
                    {
                        "mimeType": "multipart/alternative",
                        "parts": [
                            {
                                "mimeType": "text/plain",
                                "body": {"data": _b64(body_text)},
                            },
                        ],
                    },
                ],
            },
            {
                "mimeType": "application/pdf",
                "filename": pdf_filename,
                "body": pdf_body,
            },
        ],
    }


def test_read_message_pdfs_empty_when_no_attachment():
    """A plain text-only email (no PDF parts) yields pdfs=[]. Pin the
    contract that the field is ALWAYS a list, never absent — callers
    should be able to iterate without a key check."""
    def get_route(**kwargs):
        return {
            "threadId": "t",
            "snippet": "",
            "payload": {
                "mimeType": "text/plain",
                "headers": [],
                "body": {"data": _b64("body only")},
            },
        }
    client = _client_with_routes({"users.messages.get": get_route})
    result = client.read_message("m1")
    assert result["pdfs"] == []


def test_read_message_pdfs_inline_decodes():
    """Small PDF attachments (typical school newsletter, <5MB) come
    back inline in part.body.data. The walker decodes them and returns
    raw bytes."""
    pdf_b64 = _b64_bytes(_PDF_BYTES)
    payload = _multipart_payload_with_pdf(
        pdf_data_b64=pdf_b64, pdf_size=len(_PDF_BYTES),
    )

    def get_route(**kwargs):
        return {"threadId": "t", "snippet": "", "payload": payload}

    client = _client_with_routes({"users.messages.get": get_route})
    result = client.read_message("m1")

    assert result["pdfs"] == [_PDF_BYTES]
    # Body still extracted alongside the PDF.
    assert result["body"] == "body"


def test_read_message_pdfs_reference_fetches_attachment():
    """Larger attachments (>5MB-ish) come as references — body has only
    attachmentId. The walker calls users.messages.attachments.get to
    fetch the bytes and returns them."""
    pdf_b64 = _b64_bytes(_PDF_BYTES)
    payload = _multipart_payload_with_pdf(
        attachment_id="att-99",
        pdf_size=len(_PDF_BYTES),  # advertised size still under cap
    )
    captured = {}

    def get_route(**kwargs):
        return {"threadId": "t", "snippet": "", "payload": payload}

    def attachments_get_route(**kwargs):
        captured.update(kwargs)
        return {"data": pdf_b64}

    client = _client_with_routes({
        "users.messages.get": get_route,
        "users.messages.attachments.get": attachments_get_route,
    })
    result = client.read_message("m1")

    assert result["pdfs"] == [_PDF_BYTES]
    # The reference fetch was called with the right ids.
    assert captured == {
        "userId": "me",
        "messageId": "m1",
        "id": "att-99",
    }


def test_read_message_pdfs_oversized_skipped_via_advertised_size(capsys):
    """Reference-style PDFs whose advertised size already exceeds
    MAX_PDF_BYTES are skipped without making the second API call —
    avoids transferring multi-MB bytes only to drop them. A warning
    lands on stdout for log-side visibility."""
    payload = _multipart_payload_with_pdf(
        attachment_id="att-big",
        pdf_size=gmail_client.MAX_PDF_BYTES + 1,
    )

    def get_route(**kwargs):
        return {"threadId": "t", "snippet": "", "payload": payload}

    def attachments_get_route(**kwargs):
        raise AssertionError(
            "attachments.get must NOT be called when advertised size "
            "exceeds MAX_PDF_BYTES"
        )

    client = _client_with_routes({
        "users.messages.get": get_route,
        "users.messages.attachments.get": attachments_get_route,
    })
    result = client.read_message("m1")
    assert result["pdfs"] == []
    out = capsys.readouterr().out
    assert "exceeds MAX_PDF_BYTES" in out


def test_read_message_pdfs_oversized_skipped_via_decoded_length(capsys):
    """Defensive cap on the inline branch: some senders' payloads omit
    `size` (or report the encoded length, which is ~33% larger than
    decoded). After decoding, the walker re-checks against the cap so
    a missing-size payload can't sneak past."""
    big_pdf = b"%PDF-1.4\n" + b"X" * (gmail_client.MAX_PDF_BYTES + 100)
    pdf_b64 = _b64_bytes(big_pdf)
    payload = _multipart_payload_with_pdf(
        pdf_data_b64=pdf_b64,
        # Note: no pdf_size provided — simulates an Outlook payload
        # that omits the size hint.
    )

    def get_route(**kwargs):
        return {"threadId": "t", "snippet": "", "payload": payload}

    client = _client_with_routes({"users.messages.get": get_route})
    result = client.read_message("m1")
    assert result["pdfs"] == []
    assert "exceeds MAX_PDF_BYTES" in capsys.readouterr().out


def test_read_message_pdfs_multiple_attachments_all_returned():
    """Edge: a single email carrying multiple PDF attachments (rare
    but realistic — e.g. a teacher attaching both a weekly newsletter
    and a permission slip). All under the cap come through; order
    matches the part order in the payload."""
    pdf_a = b"%PDF-1.4\nA\n%%EOF"
    pdf_b = b"%PDF-1.4\nB\n%%EOF"
    payload = {
        "mimeType": "multipart/mixed",
        "headers": [],
        "parts": [
            {
                "mimeType": "text/plain",
                "body": {"data": _b64("body")},
            },
            {
                "mimeType": "application/pdf",
                "filename": "first.pdf",
                "body": {
                    "data": _b64_bytes(pdf_a),
                    "size": len(pdf_a),
                },
            },
            {
                "mimeType": "application/pdf",
                "filename": "second.pdf",
                "body": {
                    "data": _b64_bytes(pdf_b),
                    "size": len(pdf_b),
                },
            },
        ],
    }

    def get_route(**kwargs):
        return {"threadId": "t", "snippet": "", "payload": payload}

    client = _client_with_routes({"users.messages.get": get_route})
    result = client.read_message("m1")
    assert result["pdfs"] == [pdf_a, pdf_b]


def test_read_message_pdfs_attachment_fetch_failure_warns_and_skips(capsys):
    """Reference-fetch failure (transient API error, permissions
    drift, etc.) → log a warning and skip the PDF. Body still flows
    through; the run continues."""
    payload = _multipart_payload_with_pdf(
        attachment_id="att-fail", pdf_size=1024,
    )

    def get_route(**kwargs):
        return {"threadId": "t", "snippet": "", "payload": payload}

    def attachments_get_route(**kwargs):
        raise RuntimeError("boom")

    client = _client_with_routes({
        "users.messages.get": get_route,
        "users.messages.attachments.get": attachments_get_route,
    })
    result = client.read_message("m1")
    assert result["pdfs"] == []
    assert result["body"] == "body"
    assert "failed to fetch PDF attachment" in capsys.readouterr().out


def test_read_message_pdfs_real_eml_fixture_decodes_intact():
    """End-to-end against the committed .eml fixture: parse the .eml
    with stdlib email, simulate the Gmail-API response shape, and
    confirm read_message extracts the same PDF bytes byte-for-byte
    as the source. Pins the realistic Outlook MIME shape against the
    walker's recursion."""
    fixture_path = (
        Path(__file__).resolve().parent.parent
        / "fixtures" / "test" / "pdf_newsletter_third_grade.eml"
    )
    with fixture_path.open("rb") as f:
        msg = email.message_from_binary_file(f, policy=email.policy.default)

    # Pull out the PDF bytes the way our test will assert against them.
    expected_pdf_bytes: bytes | None = None
    for part in msg.walk():
        if part.get_content_type() == "application/pdf":
            expected_pdf_bytes = part.get_payload(decode=True)
            break
    assert expected_pdf_bytes is not None, "fixture lost its PDF part"

    # Build a Gmail-API-style payload with the same PDF inline.
    payload = {
        "mimeType": "multipart/mixed",
        "headers": [],
        "parts": [
            {
                "mimeType": "text/plain",
                "body": {"data": _b64("body")},
            },
            {
                "mimeType": "application/pdf",
                "filename": "March 25th.pdf",
                "body": {
                    "data": _b64_bytes(expected_pdf_bytes),
                    "size": len(expected_pdf_bytes),
                },
            },
        ],
    }

    def get_route(**kwargs):
        return {"threadId": "t", "snippet": "", "payload": payload}

    client = _client_with_routes({"users.messages.get": get_route})
    result = client.read_message("m1")
    assert result["pdfs"] == [expected_pdf_bytes]


def test_create_draft_single_part_plain_text():
    """No text_alternative → a single-part MIMEText message. The raw
    payload is base64url-encoded; we decode it back and assert the
    MIME shape so a future change to the multipart/alternative branch
    cannot silently flip the simple path."""
    captured = {}

    def create_route(**kwargs):
        captured.update(kwargs)
        return {"id": "d1", "message": {"id": "msg1"}}

    client = _client_with_routes({
        "users.drafts.create": create_route,
    })

    result = client.create_draft("Hello", "the body")

    assert result == {
        "draftId": "d1",
        "messageId": "msg1",
        "subject": "Hello",
    }
    raw = captured["body"]["message"]["raw"]
    decoded = base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8")
    mime = email.message_from_string(decoded)
    assert mime["Subject"] == "Hello"
    assert mime.get_content_type() == "text/plain"
    assert mime.get_payload().strip() == "the body"


def test_create_draft_html_content_type_sets_html_subtype():
    """`content_type="text/html"` selects the html MIMEText subtype in
    the single-part path."""
    captured = {}

    def create_route(**kwargs):
        captured.update(kwargs)
        return {"id": "d2", "message": {"id": "msg2"}}

    client = _client_with_routes({
        "users.drafts.create": create_route,
    })

    client.create_draft("S", "<p>hi</p>", content_type="text/html")
    raw = captured["body"]["message"]["raw"]
    decoded = base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8")
    mime = email.message_from_string(decoded)
    assert mime.get_content_type() == "text/html"


def test_create_draft_text_alternative_builds_multipart():
    """text_alternative is not None → multipart/alternative with the
    plain part attached first and the primary body (honoring
    content_type) attached second. This is the HTML-digest shape the
    weekly CREATE_DRAFT pass produces."""
    captured = {}

    def create_route(**kwargs):
        captured.update(kwargs)
        return {"id": "d3", "message": {"id": "msg3"}}

    client = _client_with_routes({
        "users.drafts.create": create_route,
    })

    client.create_draft(
        "Weekly digest",
        "<h1>html body</h1>",
        content_type="text/html",
        text_alternative="plain body",
    )

    raw = captured["body"]["message"]["raw"]
    decoded = base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8")
    mime = email.message_from_string(decoded)
    assert mime.get_content_maintype() == "multipart"
    assert mime.get_content_subtype() == "alternative"
    assert mime["Subject"] == "Weekly digest"
    parts = mime.get_payload()
    assert len(parts) == 2
    # RFC 2046: plain must come first so clients that render the
    # "first acceptable" part still show the fallback when they
    # can't render HTML.
    assert parts[0].get_content_type() == "text/plain"
    assert parts[0].get_payload().strip() == "plain body"
    assert parts[1].get_content_type() == "text/html"
    assert parts[1].get_payload().strip() == "<h1>html body</h1>"


def test_get_profile_passes_userid_and_returns_payload():
    def getProfile_route(**kwargs):
        assert kwargs["userId"] == "me"
        return {"emailAddress": "ellen@example.com", "messagesTotal": 1234}

    client = _client_with_routes({
        "users.getProfile": getProfile_route,
    })

    assert client.get_profile() == {
        "emailAddress": "ellen@example.com",
        "messagesTotal": 1234,
    }
