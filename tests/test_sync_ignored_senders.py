"""Pytest suite for scripts/sync_ignored_senders.py.

Covers the two pure functions (normalize_rows, write_if_changed),
the network wrapper (`_fetch`), and the CLI entry point (`main`).
`_fetch` is stubbed via monkeypatched `urllib.request.urlopen` — no
real network calls. `main` is driven through its argv and verified
against the on-disk cache file.
"""
from __future__ import annotations

import io
import json
import sys

import pytest

import sync_ignored_senders as sis


# ─── normalize_rows ──────────────────────────────────────────────────────


def test_normalize_lowercases_and_trims_domain():
    rows = [{"domain": "  Foo.COM  ", "source": "manual", "timestamp": "t0"}]
    out = sis.normalize_rows(rows)
    assert out == [{"domain": "foo.com", "source": "manual", "timestamp": "t0"}]


@pytest.mark.parametrize("bad", [
    "",               # empty
    "foo",            # no TLD
    "-foo.com",       # leading hyphen
    ".com",           # no label before dot
    "not a domain",   # spaces
    "foo.c",          # TLD too short
])
def test_normalize_drops_invalid_domain(bad):
    out = sis.normalize_rows([{"domain": bad}])
    assert out == []


def test_normalize_drops_rows_missing_domain_key():
    rows = [
        {"source": "manual"},                       # no domain at all
        {"domain": None},                           # domain is None
        {"domain": 42},                             # non-string domain
        {"domain": "laes.org", "source": "manual"}, # good row
    ]
    out = sis.normalize_rows(rows)
    assert [r["domain"] for r in out] == ["laes.org"]


def test_normalize_dedups_first_wins_on_same_domain_after_lowercase():
    rows = [
        {"domain": "Laes.org", "source": "auto-button", "timestamp": "t1"},
        {"domain": "laes.org", "source": "manual",      "timestamp": "t2"},
        {"domain": "LAES.ORG", "source": "other",       "timestamp": "t3"},
    ]
    out = sis.normalize_rows(rows)
    assert len(out) == 1
    assert out[0] == {"domain": "laes.org", "source": "auto-button", "timestamp": "t1"}


def test_normalize_sorts_alphabetically():
    rows = [
        {"domain": "zebra.io"},
        {"domain": "apple.com"},
        {"domain": "middle.org"},
    ]
    out = sis.normalize_rows(rows)
    assert [r["domain"] for r in out] == ["apple.com", "middle.org", "zebra.io"]


def test_normalize_passthrough_and_default_missing_fields():
    rows = [
        {"domain": "full.org", "source": "manual", "timestamp": "2026-04-15T00:00:00Z"},
        {"domain": "bare.org"},  # no timestamp, no source
    ]
    out = sis.normalize_rows(rows)
    by_d = {r["domain"]: r for r in out}
    assert by_d["full.org"] == {
        "domain": "full.org", "source": "manual",
        "timestamp": "2026-04-15T00:00:00Z",
    }
    assert by_d["bare.org"] == {"domain": "bare.org", "source": "", "timestamp": ""}


def test_normalize_ignores_non_dict_items():
    # Defensive — a malformed Apps Script response shouldn't crash us.
    rows = ["just a string", 42, None, {"domain": "ok.com"}]
    out = sis.normalize_rows(rows)
    assert [r["domain"] for r in out] == ["ok.com"]


# ─── write_if_changed ────────────────────────────────────────────────────


def test_write_if_changed_writes_when_file_absent(tmp_path):
    path = tmp_path / "ignored_senders.json"
    rows = [{"domain": "ok.com", "source": "manual", "timestamp": "t0"}]
    wrote = sis.write_if_changed(str(path), rows)
    assert wrote is True
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == rows


def test_write_if_changed_writes_when_content_differs(tmp_path):
    path = tmp_path / "ignored_senders.json"
    path.write_text('[{"domain":"old.com"}]\n', encoding="utf-8")
    new_rows = [{"domain": "new.com", "source": "", "timestamp": ""}]
    wrote = sis.write_if_changed(str(path), new_rows)
    assert wrote is True
    assert json.loads(path.read_text(encoding="utf-8")) == new_rows


def test_write_if_changed_returns_false_when_identical(tmp_path):
    path = tmp_path / "ignored_senders.json"
    rows = [{"domain": "ok.com", "source": "manual", "timestamp": "t0"}]
    assert sis.write_if_changed(str(path), rows) is True
    first_bytes = path.read_bytes()
    assert sis.write_if_changed(str(path), rows) is False
    # File bytes must be untouched so git sees no diff.
    assert path.read_bytes() == first_bytes


def test_write_if_changed_uses_2_space_indent_and_trailing_newline(tmp_path):
    path = tmp_path / "ignored_senders.json"
    rows = [{"domain": "ok.com", "source": "manual", "timestamp": "t0"}]
    sis.write_if_changed(str(path), rows)
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    # indent=2 → list opens at col 0, list items at col 2, dict keys at
    # col 4. So object keys show up preceded by exactly four spaces.
    assert '\n    "domain"' in text


# ─── _fetch ──────────────────────────────────────────────────────────────


class _FakeResponse:
    """Minimal stand-in for the urllib.request.urlopen context-manager
    return. `json.load` reads from it directly."""

    def __init__(self, payload_text):
        self._buf = io.StringIO(payload_text)

    def read(self, *a, **k):
        return self._buf.read(*a, **k)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_happy_path_returns_list(monkeypatch):
    """Valid JSON list response → returned verbatim. The secret and
    kind=ignored_senders params are appended to the URL query string."""
    captured = {}

    def fake_urlopen(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return _FakeResponse(json.dumps([{"domain": "ok.com"}]))

    monkeypatch.setattr(sis.urllib.request, "urlopen", fake_urlopen)
    result = sis._fetch("https://exec.example/", secret="s3cret", timeout=5.0)

    assert result == [{"domain": "ok.com"}]
    assert "secret=s3cret" in captured["url"]
    assert "kind=ignored_senders" in captured["url"]
    assert captured["timeout"] == 5.0


def test_fetch_url_with_existing_query_uses_ampersand(monkeypatch):
    """Urls that already carry a `?` parameter must get `&` appended,
    not a second `?`. Apps Script reject URLs with a double-`?`."""
    captured = {}

    def fake_urlopen(url, timeout):
        captured["url"] = url
        return _FakeResponse("[]")

    monkeypatch.setattr(sis.urllib.request, "urlopen", fake_urlopen)
    sis._fetch("https://exec.example/?debug=1", secret="s", timeout=1.0)

    assert "?debug=1&" in captured["url"]
    assert captured["url"].count("?") == 1


def test_fetch_returns_none_on_network_error(monkeypatch, capsys):
    """Any network exception → None, with a stderr breadcrumb so the
    runner log has a fingerprint for the operator."""
    def boom(*_a, **_k):
        raise ConnectionError("dns died")

    monkeypatch.setattr(sis.urllib.request, "urlopen", boom)
    assert sis._fetch("https://x/", secret="s", timeout=1.0) is None
    err = capsys.readouterr().err
    assert "fetch failed" in err
    assert "dns died" in err


def test_fetch_returns_none_on_non_list_response(monkeypatch, capsys):
    """Apps Script must return a JSON list. Any other shape (object,
    bare string, etc.) → None plus a stderr breadcrumb. Cache stays
    untouched in the caller."""
    def fake_urlopen(url, timeout):
        return _FakeResponse(json.dumps({"error": "nope"}))

    monkeypatch.setattr(sis.urllib.request, "urlopen", fake_urlopen)
    assert sis._fetch("https://x/", secret="s", timeout=1.0) is None
    assert "response was not a JSON list" in capsys.readouterr().err


def test_fetch_returns_none_on_non_json_body(monkeypatch, capsys):
    """JSONDecodeError from a non-JSON body is funneled into the same
    fetch-failed path — pins the blanket `except Exception` that
    swallows both network and decode errors the same way."""
    def fake_urlopen(url, timeout):
        return _FakeResponse("this is html, not json")

    monkeypatch.setattr(sis.urllib.request, "urlopen", fake_urlopen)
    assert sis._fetch("https://x/", secret="s", timeout=1.0) is None
    assert "fetch failed" in capsys.readouterr().err


# ─── main() CLI ──────────────────────────────────────────────────────────


def _run_main(monkeypatch, argv, fetch_result, *, capture_err=False):
    """Drive `sis.main()` with the given argv and a canned _fetch
    return (bypassing real network). Returns (exit_code, stdout,
    optionally stderr)."""
    def fake_fetch(url, secret, timeout):
        fake_fetch.calls.append({"url": url, "secret": secret, "timeout": timeout})
        return fetch_result

    fake_fetch.calls = []
    monkeypatch.setattr(sis, "_fetch", fake_fetch)
    monkeypatch.setattr(sys, "argv", argv)

    stdout_buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout_buf)
    stderr_buf = io.StringIO() if capture_err else None
    if capture_err:
        monkeypatch.setattr(sys, "stderr", stderr_buf)

    rc = sis.main()
    out = stdout_buf.getvalue()
    err = stderr_buf.getvalue() if capture_err else None
    return rc, out, err, fake_fetch.calls


def test_main_happy_path_writes_and_reports_count(monkeypatch, tmp_path):
    out_path = tmp_path / "ignored_senders.json"
    rows = [{"domain": "laes.org"}, {"domain": "Pta.foo"}]
    rc, stdout, _err, calls = _run_main(monkeypatch, [
        "sync_ignored_senders.py",
        "--url", "https://exec.example/",
        "--secret", "s3cret",
        "--out", str(out_path),
    ], fetch_result=rows)
    assert rc == 0
    assert len(calls) == 1
    assert calls[0]["url"] == "https://exec.example/"
    assert calls[0]["secret"] == "s3cret"
    # Default timeout is 15s; pinned here so an accidental change
    # to 1.5 or 150 doesn't slip in.
    assert calls[0]["timeout"] == 15.0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert [r["domain"] for r in payload] == ["laes.org", "pta.foo"]
    assert "Synced 2 ignored sender(s)" in stdout


def test_main_fetch_failure_leaves_cache_untouched(monkeypatch, tmp_path):
    """_fetch returns None → exit 0, output file NOT written, prior
    cache on disk is preserved byte-for-byte."""
    out_path = tmp_path / "ignored_senders.json"
    out_path.write_text(json.dumps([{"domain": "pre.org"}]))
    prior = out_path.read_bytes()

    rc, _out, _err, _calls = _run_main(monkeypatch, [
        "sync_ignored_senders.py",
        "--url", "https://exec.example/",
        "--secret", "s",
        "--out", str(out_path),
    ], fetch_result=None)

    assert rc == 0
    assert out_path.read_bytes() == prior


def test_main_reports_no_changes_when_write_is_noop(monkeypatch, tmp_path):
    """Second run with identical fetch output → stdout says 'No changes'
    and the file bytes are preserved."""
    out_path = tmp_path / "ignored_senders.json"
    rows = [{"domain": "ok.com", "source": "", "timestamp": ""}]
    # Seed with the exact canonical form so write_if_changed short-circuits.
    out_path.write_text(sis._serialize(rows), encoding="utf-8")

    rc, stdout, _err, _calls = _run_main(monkeypatch, [
        "sync_ignored_senders.py",
        "--url", "https://x/",
        "--secret", "s",
        "--out", str(out_path),
    ], fetch_result=[{"domain": "ok.com"}])

    assert rc == 0
    assert "No changes" in stdout


def test_main_custom_timeout_threaded_into_fetch(monkeypatch, tmp_path):
    """--timeout is passed through to _fetch. Lets the operator bump
    the default when Apps Script is slow without touching code."""
    out_path = tmp_path / "ignored_senders.json"
    rc, _out, _err, calls = _run_main(monkeypatch, [
        "sync_ignored_senders.py",
        "--url", "https://x/",
        "--secret", "s",
        "--out", str(out_path),
        "--timeout", "45",
    ], fetch_result=[])

    assert rc == 0
    assert calls[0]["timeout"] == 45.0
