"""Pytest suite for scripts/sync_ignored_events.py.

Mirrors test_sync_completed_events.py one-for-one — the two helpers are
structurally identical apart from the GET kind parameter (ignored vs
completed) and the field surface (ignored_at vs completed_at). Behaviour-
level pins keep them aligned. Adds the ROADMAP #37 _drop_past_dated cases
that also live in test_sync_completed_events.py.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import sys

import pytest

import sync_ignored_events as sie


# ─── normalize_rows ──────────────────────────────────────────────────────


def test_normalize_lowercases_and_trims_id():
    rows = [{"id": "  ABC123ABC123  ", "name": "Foo", "date": "2026-04-25",
             "ignored_at": "t0"}]
    out = sie.normalize_rows(rows)
    assert out == [{"id": "abc123abc123", "name": "Foo",
                    "date": "2026-04-25", "ignored_at": "t0"}]


@pytest.mark.parametrize("bad", [
    "",                        # empty
    "abc",                     # too short
    "abcdefabcdef0",           # 13 chars — too long
    "abcdefabcdefg",           # 13 chars / contains 'g'
    "ABCDEFABCDE!",            # invalid char
    "12345678901z",            # contains 'z' (not hex)
    "not a hex id",            # spaces
])
def test_normalize_drops_invalid_id(bad):
    out = sie.normalize_rows([{"id": bad, "name": "x", "date": "y"}])
    assert out == []


def test_normalize_drops_rows_missing_id_key():
    rows = [
        {"name": "no id at all"},                              # no id
        {"id": None, "name": "id is None"},                    # None
        {"id": 42, "name": "non-string id"},                   # non-string
        {"id": "abcdefabcdef", "name": "good"},                # ok
    ]
    out = sie.normalize_rows(rows)
    assert [r["id"] for r in out] == ["abcdefabcdef"]


def test_normalize_dedups_first_wins_on_same_id_after_lowercase():
    rows = [
        {"id": "ABCDEFabcdef", "name": "first",  "ignored_at": "t1"},
        {"id": "abcdefabcdef", "name": "second", "ignored_at": "t2"},
        {"id": "AbCdEfAbCdEf", "name": "third",  "ignored_at": "t3"},
    ]
    out = sie.normalize_rows(rows)
    assert len(out) == 1
    assert out[0]["id"] == "abcdefabcdef"
    assert out[0]["name"] == "first"
    assert out[0]["ignored_at"] == "t1"


def test_normalize_sorts_by_id():
    rows = [
        {"id": "ffffffffffff", "name": "z"},
        {"id": "000000000000", "name": "a"},
        {"id": "888888888888", "name": "m"},
    ]
    out = sie.normalize_rows(rows)
    assert [r["id"] for r in out] == [
        "000000000000", "888888888888", "ffffffffffff"
    ]


def test_normalize_passthrough_and_default_missing_fields():
    rows = [
        {"id": "abcdefabcdef", "name": "Full",
         "date": "2026-04-25", "ignored_at": "2026-04-26T00:00:00Z"},
        {"id": "111111111111"},  # no name, no date, no ignored_at
    ]
    out = sie.normalize_rows(rows)
    by_id = {r["id"]: r for r in out}
    assert by_id["abcdefabcdef"] == {
        "id": "abcdefabcdef", "name": "Full",
        "date": "2026-04-25", "ignored_at": "2026-04-26T00:00:00Z",
    }
    assert by_id["111111111111"] == {
        "id": "111111111111", "name": "", "date": "", "ignored_at": "",
    }


def test_normalize_ignores_non_dict_items():
    rows = ["just a string", 42, None, {"id": "abcdefabcdef"}]
    out = sie.normalize_rows(rows)
    assert [r["id"] for r in out] == ["abcdefabcdef"]


# ─── _drop_past_dated  (ROADMAP #37 Tier 1) ─────────────────────────────


_TODAY = dt.date(2026, 4, 28)


def _row(date_str: str, eid: str = "abcdefabcdef") -> dict:
    return {"id": eid, "name": "n", "date": date_str, "ignored_at": ""}


def test_drop_past_dated_drops_strictly_before_today():
    rows = [_row("2026-04-27"), _row("2025-12-31"), _row("1999-01-01")]
    assert sie._drop_past_dated(rows, _TODAY) == []


def test_drop_past_dated_keeps_today():
    rows = [_row("2026-04-28")]
    assert sie._drop_past_dated(rows, _TODAY) == rows


def test_drop_past_dated_keeps_future():
    rows = [_row("2026-04-29"), _row("2030-01-01")]
    assert sie._drop_past_dated(rows, _TODAY) == rows


def test_drop_past_dated_keeps_empty_date_defensively():
    rows = [_row("")]
    assert sie._drop_past_dated(rows, _TODAY) == rows


def test_drop_past_dated_keeps_malformed_date_defensively():
    rows = [
        _row("not a date"),
        _row("2026/04/27"),    # slashes, not iso
        _row("April 27, 2026"),  # human form
        _row("2026-13-01"),    # bad month
        _row("2026-04-31"),    # bad day for april
    ]
    assert sie._drop_past_dated(rows, _TODAY) == rows


def test_drop_past_dated_strips_whitespace_before_parse():
    rows = [_row("  2026-04-27  "), _row("  2026-04-29  ")]
    out = sie._drop_past_dated(rows, _TODAY)
    assert [r["date"] for r in out] == ["  2026-04-29  "]


def test_drop_past_dated_mixed_bag_realistic():
    rows = [
        _row("2026-04-27", "111111111111"),  # past, drop
        _row("2026-04-28", "222222222222"),  # today, keep
        _row("2026-05-15", "333333333333"),  # future, keep
        _row("",           "444444444444"),  # empty, keep defensively
        _row("garbage",    "555555555555"),  # malformed, keep defensively
    ]
    out = sie._drop_past_dated(rows, _TODAY)
    assert [r["id"] for r in out] == [
        "222222222222", "333333333333", "444444444444", "555555555555"
    ]


# ─── write_if_changed ────────────────────────────────────────────────────


def test_write_if_changed_writes_when_file_absent(tmp_path):
    path = tmp_path / "ignored_events.json"
    rows = [{"id": "abcdefabcdef", "name": "x", "date": "", "ignored_at": ""}]
    wrote = sie.write_if_changed(str(path), rows)
    assert wrote is True
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == rows


def test_write_if_changed_writes_when_content_differs(tmp_path):
    path = tmp_path / "ignored_events.json"
    path.write_text('[{"id":"oldoldoldold"}]\n', encoding="utf-8")
    new_rows = [{"id": "abcdefabcdef", "name": "n",
                 "date": "", "ignored_at": ""}]
    wrote = sie.write_if_changed(str(path), new_rows)
    assert wrote is True
    assert json.loads(path.read_text(encoding="utf-8")) == new_rows


def test_write_if_changed_returns_false_when_identical(tmp_path):
    path = tmp_path / "ignored_events.json"
    rows = [{"id": "abcdefabcdef", "name": "n",
             "date": "", "ignored_at": ""}]
    assert sie.write_if_changed(str(path), rows) is True
    first_bytes = path.read_bytes()
    assert sie.write_if_changed(str(path), rows) is False
    assert path.read_bytes() == first_bytes


def test_write_if_changed_uses_2_space_indent_and_trailing_newline(tmp_path):
    path = tmp_path / "ignored_events.json"
    rows = [{"id": "abcdefabcdef", "name": "n",
             "date": "", "ignored_at": ""}]
    sie.write_if_changed(str(path), rows)
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert '\n    "id"' in text


# ─── _fetch ──────────────────────────────────────────────────────────────


class _FakeResponse:
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
    kind=ignored params are appended to the URL query string."""
    captured = {}

    def fake_urlopen(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return _FakeResponse(json.dumps([{"id": "abcdefabcdef"}]))

    monkeypatch.setattr(sie.urllib.request, "urlopen", fake_urlopen)
    result = sie._fetch("https://exec.example/", secret="s3cret", timeout=5.0)

    assert result == [{"id": "abcdefabcdef"}]
    assert "secret=s3cret" in captured["url"]
    assert "kind=ignored" in captured["url"]
    assert captured["timeout"] == 5.0


def test_fetch_url_with_existing_query_uses_ampersand(monkeypatch):
    captured = {}

    def fake_urlopen(url, timeout):
        captured["url"] = url
        return _FakeResponse("[]")

    monkeypatch.setattr(sie.urllib.request, "urlopen", fake_urlopen)
    sie._fetch("https://exec.example/?debug=1", secret="s", timeout=1.0)

    assert "?debug=1&" in captured["url"]
    assert captured["url"].count("?") == 1


def test_fetch_returns_none_on_network_error(monkeypatch, capsys):
    def boom(*_a, **_k):
        raise ConnectionError("dns died")

    monkeypatch.setattr(sie.urllib.request, "urlopen", boom)
    assert sie._fetch("https://x/", secret="s", timeout=1.0) is None
    err = capsys.readouterr().err
    assert "fetch failed" in err
    assert "dns died" in err


def test_fetch_returns_none_on_non_list_response(monkeypatch, capsys):
    def fake_urlopen(url, timeout):
        return _FakeResponse(json.dumps({"error": "nope"}))

    monkeypatch.setattr(sie.urllib.request, "urlopen", fake_urlopen)
    assert sie._fetch("https://x/", secret="s", timeout=1.0) is None
    assert "response was not a JSON list" in capsys.readouterr().err


def test_fetch_returns_none_on_non_json_body(monkeypatch, capsys):
    def fake_urlopen(url, timeout):
        return _FakeResponse("this is html, not json")

    monkeypatch.setattr(sie.urllib.request, "urlopen", fake_urlopen)
    assert sie._fetch("https://x/", secret="s", timeout=1.0) is None
    assert "fetch failed" in capsys.readouterr().err


# ─── main() CLI ──────────────────────────────────────────────────────────


class _FrozenDate(dt.date):
    """Subclass so monkeypatching sie.dt.date affects sie.dt.date.today()
    without breaking other dt.date methods inside the module."""
    pass


def _freeze_today(monkeypatch, today: dt.date):
    class _D(dt.date):
        @classmethod
        def today(cls):
            return today
    monkeypatch.setattr(sie.dt, "date", _D)


def _run_main(monkeypatch, argv, fetch_result, *, capture_err=False,
              today: dt.date | None = None):
    if today is not None:
        _freeze_today(monkeypatch, today)

    def fake_fetch(url, secret, timeout):
        fake_fetch.calls.append({"url": url, "secret": secret, "timeout": timeout})
        return fetch_result

    fake_fetch.calls = []
    monkeypatch.setattr(sie, "_fetch", fake_fetch)
    monkeypatch.setattr(sys, "argv", argv)

    stdout_buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout_buf)
    stderr_buf = io.StringIO() if capture_err else None
    if capture_err:
        monkeypatch.setattr(sys, "stderr", stderr_buf)

    rc = sie.main()
    out = stdout_buf.getvalue()
    err = stderr_buf.getvalue() if capture_err else None
    return rc, out, err, fake_fetch.calls


def test_main_happy_path_writes_and_reports_count(monkeypatch, tmp_path):
    out_path = tmp_path / "ignored_events.json"
    rows = [
        {"id": "abcdefabcdef", "name": "Foo", "date": "2026-04-29",
         "ignored_at": "t1"},
        {"id": "111111111111", "name": "Bar", "date": "2026-04-30",
         "ignored_at": "t2"},
    ]
    rc, stdout, _err, calls = _run_main(monkeypatch, [
        "sync_ignored_events.py",
        "--url", "https://exec.example/",
        "--secret", "s3cret",
        "--out", str(out_path),
    ], fetch_result=rows, today=_TODAY)
    assert rc == 0
    assert len(calls) == 1
    assert calls[0]["url"] == "https://exec.example/"
    assert calls[0]["secret"] == "s3cret"
    assert calls[0]["timeout"] == 15.0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    # Sorted by id: 111... < abc...
    assert [r["id"] for r in payload] == ["111111111111", "abcdefabcdef"]
    assert "Synced 2 ignored event(s)" in stdout


def test_main_drops_past_dated_before_write(monkeypatch, tmp_path):
    """ROADMAP #37 Tier 1 integration: past-dated rows from the GET
    response are filtered out before write_if_changed sees them."""
    out_path = tmp_path / "ignored_events.json"
    rows = [
        {"id": "111111111111", "name": "stale",
         "date": "2026-04-27", "ignored_at": "t1"},  # past, drop
        {"id": "222222222222", "name": "today",
         "date": "2026-04-28", "ignored_at": "t2"},  # today, keep
        {"id": "333333333333", "name": "future",
         "date": "2026-05-15", "ignored_at": "t3"},  # future, keep
        {"id": "444444444444", "name": "undated",
         "date": "", "ignored_at": "t4"},            # undated, keep
    ]
    rc, stdout, _err, _calls = _run_main(monkeypatch, [
        "sync_ignored_events.py",
        "--url", "https://x/",
        "--secret", "s",
        "--out", str(out_path),
    ], fetch_result=rows, today=_TODAY)

    assert rc == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert [r["id"] for r in payload] == [
        "222222222222", "333333333333", "444444444444"
    ]
    assert "Synced 3 ignored event(s)" in stdout
    assert "1 past-dated dropped" in stdout


def test_main_no_past_dated_omits_drop_suffix(monkeypatch, tmp_path):
    """When nothing is dropped, the count suffix stays clean — no
    '(0 past-dated dropped)' noise in the cron log."""
    out_path = tmp_path / "ignored_events.json"
    rows = [{"id": "abcdefabcdef", "name": "n",
             "date": "2026-05-01", "ignored_at": ""}]
    rc, stdout, _err, _calls = _run_main(monkeypatch, [
        "sync_ignored_events.py",
        "--url", "https://x/",
        "--secret", "s",
        "--out", str(out_path),
    ], fetch_result=rows, today=_TODAY)

    assert rc == 0
    assert "past-dated dropped" not in stdout


def test_main_fetch_failure_leaves_cache_untouched(monkeypatch, tmp_path):
    out_path = tmp_path / "ignored_events.json"
    out_path.write_text(json.dumps([{"id": "abcdefabcdef"}]))
    prior = out_path.read_bytes()

    rc, _out, _err, _calls = _run_main(monkeypatch, [
        "sync_ignored_events.py",
        "--url", "https://exec.example/",
        "--secret", "s",
        "--out", str(out_path),
    ], fetch_result=None)

    assert rc == 0
    assert out_path.read_bytes() == prior


def test_main_reports_no_changes_when_write_is_noop(monkeypatch, tmp_path):
    out_path = tmp_path / "ignored_events.json"
    rows = [{"id": "abcdefabcdef", "name": "",
             "date": "2026-05-01", "ignored_at": ""}]
    out_path.write_text(sie._serialize(rows), encoding="utf-8")

    rc, stdout, _err, _calls = _run_main(monkeypatch, [
        "sync_ignored_events.py",
        "--url", "https://x/",
        "--secret", "s",
        "--out", str(out_path),
    ], fetch_result=[{"id": "abcdefabcdef", "date": "2026-05-01"}],
       today=_TODAY)

    assert rc == 0
    assert "No changes" in stdout


def test_main_custom_timeout_threaded_into_fetch(monkeypatch, tmp_path):
    out_path = tmp_path / "ignored_events.json"
    rc, _out, _err, calls = _run_main(monkeypatch, [
        "sync_ignored_events.py",
        "--url", "https://x/",
        "--secret", "s",
        "--out", str(out_path),
        "--timeout", "45",
    ], fetch_result=[])

    assert rc == 0
    assert calls[0]["timeout"] == 45.0
