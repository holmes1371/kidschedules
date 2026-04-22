"""Coverage for main.py orchestration helpers listed in ROADMAP.md
'Test coverage gaps' (medium-risk tier).

`test_main.py` already pins `should_create_draft`,
`step2c_load_cache_and_filter`, `_attach_sender_domains` /
`_compute_block_key`, `_reextract_eviction`, `_per_message_counts`,
`_print_outlier_alerts`, and the kwarg wiring from `main` into
`step3`/`step4`. This file covers the remaining orchestration:

- `_load_webhook_url` / `_load_pages_url` — missing, present, unreadable
- `run_script` — returns stdout, tags stderr with the script name
- `_bootstrap_from_future_events` — all five decision branches
- `step5_publish` — dry-run short-circuit, real write, `.nojekyll`
- `step6_create_draft` — gate off, empty-week guard, happy path
- `step1_build_queries`, `step2_search_gmail`,
  `step3b_update_auto_blocklist` — wiring smoke around delegated work

`step1b_filter_audit` and `main()` itself are intentionally left to
the live weekly-cron integration — both are thin orchestration over
helpers that are individually covered, and the stub surface needed
to unit-test them does not pin anything that a drift in the real
helpers would not already break.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import main  # noqa: E402


# ── _load_webhook_url / _load_pages_url ─────────────────────────────────


def test_load_webhook_url_missing_file_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "WEBHOOK_URL_PATH", str(tmp_path / "nope.txt"))
    assert main._load_webhook_url() == ""


def test_load_webhook_url_strips_trailing_whitespace(monkeypatch, tmp_path):
    """File content is stripped — a stray newline from `echo >> file`
    must not leak into the rendered HTML."""
    path = tmp_path / "webhook.txt"
    path.write_text("https://example.com/webhook\n")
    monkeypatch.setattr(main, "WEBHOOK_URL_PATH", str(path))
    assert main._load_webhook_url() == "https://example.com/webhook"


def test_load_webhook_url_oserror_returns_empty(monkeypatch, tmp_path):
    """An unreadable file (e.g. permissions drift on the runner) must
    not crash the pipeline — returning "" degrades to "no Ignore
    button" which is the same shape as "no webhook configured"."""
    path = tmp_path / "webhook.txt"
    path.write_text("irrelevant")
    monkeypatch.setattr(main, "WEBHOOK_URL_PATH", str(path))

    def boom(*_a, **_k):
        raise OSError("simulated read failure")

    monkeypatch.setattr("builtins.open", boom)
    assert main._load_webhook_url() == ""


def test_load_pages_url_missing_file_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "PAGES_URL_PATH", str(tmp_path / "nope.txt"))
    assert main._load_pages_url() == ""


def test_load_pages_url_strips_trailing_whitespace(monkeypatch, tmp_path):
    path = tmp_path / "pages.txt"
    path.write_text("  https://ellen.example/site/  \n")
    monkeypatch.setattr(main, "PAGES_URL_PATH", str(path))
    assert main._load_pages_url() == "https://ellen.example/site/"


def test_load_pages_url_oserror_returns_empty(monkeypatch, tmp_path):
    path = tmp_path / "pages.txt"
    path.write_text("irrelevant")
    monkeypatch.setattr(main, "PAGES_URL_PATH", str(path))

    def boom(*_a, **_k):
        raise OSError("nope")

    monkeypatch.setattr("builtins.open", boom)
    assert main._load_pages_url() == ""


# ── run_script ──────────────────────────────────────────────────────────


def test_run_script_returns_stdout(monkeypatch):
    """Subprocess returns captured stdout verbatim — this is the
    contract step1_build_queries relies on to parse JSON."""
    called = {}

    def fake_run(cmd, capture_output, text, check):
        called["cmd"] = cmd
        called["capture_output"] = capture_output
        called["text"] = text
        called["check"] = check
        return SimpleNamespace(stdout="payload\n", stderr="")

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    out = main.run_script("build_queries.py", ["--today", "2026-04-22"])
    assert out == "payload\n"
    assert called["capture_output"] is True
    assert called["text"] is True
    assert called["check"] is True
    # Command shape: [python_executable, <scripts>/build_queries.py, args...]
    assert called["cmd"][0] == sys.executable
    assert called["cmd"][1].endswith(
        os.path.join("scripts", "build_queries.py")
    )
    assert called["cmd"][2:] == ["--today", "2026-04-22"]


def test_run_script_tags_stderr_with_script_name(monkeypatch, capsys):
    """Non-empty stderr is prefixed with `[<script> stderr]:` so a
    noisy diagnostic from a downstream script is attributable in the
    run log. This is the default step3b audit-log channel."""
    def fake_run(*_a, **_k):
        return SimpleNamespace(stdout="ok\n", stderr="warning: stuff\n")

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    main.run_script("diff_search_results.py", None)
    out = capsys.readouterr().out
    assert "[diff_search_results.py stderr]: warning: stuff" in out


def test_run_script_silent_when_stderr_empty(monkeypatch, capsys):
    def fake_run(*_a, **_k):
        return SimpleNamespace(stdout="", stderr="")

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    main.run_script("whatever.py")
    assert capsys.readouterr().out == ""


# ── _bootstrap_from_future_events ───────────────────────────────────────


def _state(events=None, processed=None):
    return {
        "events": dict(events or {}),
        "processed_messages": dict(processed or {}),
    }


def test_bootstrap_noop_when_state_already_has_events(monkeypatch):
    """Cache is already populated — do not reseed. Returns 0 even if
    future_events.json exists and is non-empty."""
    monkeypatch.setattr(main, "FUTURE_EVENTS_PATH", "/nonexistent/path")
    state = _state(events={"e1": {"eventId": "e1"}})
    assert main._bootstrap_from_future_events(state, "2026-04-22T00:00:00Z") == 0
    assert list(state["events"].keys()) == ["e1"]


def test_bootstrap_noop_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "FUTURE_EVENTS_PATH", str(tmp_path / "nope.json"))
    state = _state()
    assert main._bootstrap_from_future_events(state, "2026-04-22T00:00:00Z") == 0
    assert state["events"] == {}


def test_bootstrap_noop_on_malformed_json(monkeypatch, tmp_path):
    """Corrupt future_events.json must not crash the pipeline — the
    legacy file is best-effort; the cache just stays empty and the
    next agent run rebuilds from mail."""
    path = tmp_path / "future_events.json"
    path.write_text("not json")
    monkeypatch.setattr(main, "FUTURE_EVENTS_PATH", str(path))
    state = _state()
    assert main._bootstrap_from_future_events(state, "2026-04-22T00:00:00Z") == 0


def test_bootstrap_noop_on_non_list_or_empty(monkeypatch, tmp_path):
    """future_events.json payload must be a non-empty list. Empty list
    and non-list both short-circuit to 0."""
    path = tmp_path / "future_events.json"
    for payload in ("[]", '{"bank": []}'):
        path.write_text(payload)
        monkeypatch.setattr(main, "FUTURE_EVENTS_PATH", str(path))
        state = _state()
        assert main._bootstrap_from_future_events(
            state, "2026-04-22T00:00:00Z"
        ) == 0


def test_bootstrap_seeds_state_from_legacy_bank(monkeypatch, tmp_path):
    """Happy path: the legacy bank lands in state via stamp+merge.
    The return is the number of events loaded from the file, even if
    stamp/merge dedupes against itself."""
    events_in = [
        {"date": "2026-05-01", "title": "Field day", "kid": "Alice"},
        {"date": "2026-05-02", "title": "Swim meet", "kid": "Bob"},
    ]
    path = tmp_path / "future_events.json"
    path.write_text(json.dumps(events_in))
    monkeypatch.setattr(main, "FUTURE_EVENTS_PATH", str(path))
    state = _state()
    n = main._bootstrap_from_future_events(state, "2026-04-22T00:00:00Z")
    assert n == 2
    # stamp_event_ids + merge_events populate state.events
    assert len(state["events"]) == 2


# ── step5_publish ───────────────────────────────────────────────────────


def test_step5_publish_dry_run_does_not_write(monkeypatch, tmp_path, capsys):
    """Dry run must not touch docs/. The banner is printed either way
    so a log reader can't confuse dry-run with a silent write."""
    monkeypatch.setattr(main, "PAGES_OUTPUT_DIR", str(tmp_path / "docs"))
    main.step5_publish("<html>", {"subject": "Weekly"}, dry_run=True)
    assert not (tmp_path / "docs").exists()
    assert "DRY RUN" in capsys.readouterr().out


def test_step5_publish_writes_index_and_nojekyll(monkeypatch, tmp_path):
    """Real write: index.html is produced and .nojekyll is created
    on first run (it must ship for docs/ics/*.ics to route correctly
    under the Actions-based Pages deploy)."""
    out_dir = tmp_path / "docs"
    monkeypatch.setattr(main, "PAGES_OUTPUT_DIR", str(out_dir))
    main.step5_publish("<html>body</html>", {"subject": "Weekly"},
                       dry_run=False)
    assert (out_dir / "index.html").read_text() == "<html>body</html>"
    assert (out_dir / ".nojekyll").exists()


def test_step5_publish_preserves_existing_nojekyll(monkeypatch, tmp_path, capsys):
    """Second run into a populated docs/ — the existing .nojekyll is
    left alone (no Created-docs/.nojekyll log line)."""
    out_dir = tmp_path / "docs"
    out_dir.mkdir()
    (out_dir / ".nojekyll").write_text("")
    monkeypatch.setattr(main, "PAGES_OUTPUT_DIR", str(out_dir))
    main.step5_publish("<html>", {"subject": "S"}, dry_run=False)
    assert "Created docs/.nojekyll" not in capsys.readouterr().out


# ── step6_create_draft ──────────────────────────────────────────────────


class _StubGmail:
    def __init__(self):
        self.draft_calls = []

    def create_draft(self, **kwargs):
        self.draft_calls.append(kwargs)
        return {"draftId": "d99"}


def _digest_meta(this_week_count=3, subject="Weekly digest"):
    return {"digest": {"subject": subject, "this_week_count": this_week_count}}


def test_step6_preview_always_logged(capsys):
    gmail = _StubGmail()
    main.step6_create_draft(
        gmail, _digest_meta(), "plain preview", "<p>html</p>",
        actually_create=False,
    )
    out = capsys.readouterr().out
    assert "plain preview" in out
    assert "--- digest preview (plain text) ---" in out
    assert "--- end preview ---" in out
    assert gmail.draft_calls == []


def test_step6_gate_off_short_circuits_after_preview():
    gmail = _StubGmail()
    main.step6_create_draft(
        gmail, _digest_meta(this_week_count=5), "preview", "<p>html</p>",
        actually_create=False,
    )
    assert gmail.draft_calls == []


def test_step6_empty_week_guard_short_circuits(capsys):
    """Even with the gate on, 0 events this week → no draft. This is
    the 'no-spam empty-week' guard — a nothing-this-week draft is
    spam by another name."""
    gmail = _StubGmail()
    main.step6_create_draft(
        gmail, _digest_meta(this_week_count=0), "preview", "<p>html</p>",
        actually_create=True,
    )
    assert gmail.draft_calls == []
    assert "empty-week guard" in capsys.readouterr().out


def test_step6_happy_path_calls_gmail_create_draft_with_alt():
    """Gate on + non-empty week → gmail.create_draft called with the
    multipart/alternative kwargs (text_alternative + content_type=html).
    Pins the weekly-digest draft shape end-to-end."""
    gmail = _StubGmail()
    main.step6_create_draft(
        gmail, _digest_meta(this_week_count=7, subject="S"),
        "plain body", "<p>html body</p>",
        actually_create=True,
    )
    assert len(gmail.draft_calls) == 1
    call = gmail.draft_calls[0]
    assert call == {
        "subject": "S",
        "body": "<p>html body</p>",
        "content_type": "text/html",
        "text_alternative": "plain body",
    }


# ── step1_build_queries ─────────────────────────────────────────────────


def test_step1_build_queries_parses_delegated_json(monkeypatch, capsys):
    """step1 is a thin shim: it runs build_queries.py and parses the
    stdout JSON. Swap run_script with a fake that returns known JSON
    and assert the return is the parsed dict, and that --lookback-days
    is threaded through."""
    captured = {}

    fake_payload = {
        "today_human": "April 22, 2026",
        "email_window": {"after": "2026/02/21", "before": "2026/04/22"},
        "exclusions": {
            "blocklist_size": 3,
            "blocklist_size_main": 1,
            "blocklist_size_auto": 1,
            "blocklist_size_ignored_senders": 1,
        },
        "filter_audit": {"reason": "fresh"},
    }

    def fake_run(script, args):
        captured["script"] = script
        captured["args"] = args
        return json.dumps(fake_payload)

    monkeypatch.setattr(main, "run_script", fake_run)
    result = main.step1_build_queries(lookback_days=90)
    assert result == fake_payload
    assert captured["script"] == "build_queries.py"
    assert "--lookback-days" in captured["args"]
    assert "90" in captured["args"]
    # The committed ignored_senders path gets threaded in so the loader
    # picks up UI-ignored senders even on local runs.
    assert "--ignored-senders" in captured["args"]


# ── step2_search_gmail ──────────────────────────────────────────────────


def test_step2_search_gmail_dispatches_one_search_per_query(capsys):
    """Every named query in config['queries'] produces one
    gmail.search_messages call, and the returned dict is keyed by the
    same names."""
    class Gmail:
        def __init__(self):
            self.calls = []

        def search_messages(self, query, max_results):
            self.calls.append((query, max_results))
            return [{"messageId": f"m-{len(self.calls)}"}]

    gmail = Gmail()
    config = {
        "queries": {
            "school_activities": "q1",
            "sports_extracurriculars": "q2",
        },
        "max_results_per_query": 25,
    }
    result = main.step2_search_gmail(gmail, config)

    assert set(result.keys()) == {"school_activities", "sports_extracurriculars"}
    assert len(result["school_activities"]) == 1
    assert len(result["sports_extracurriculars"]) == 1
    assert gmail.calls == [("q1", 25), ("q2", 25)]


# ── step3b_update_auto_blocklist ────────────────────────────────────────


def test_step3b_always_runs_even_with_empty_suggestions(monkeypatch, capsys):
    """Zero-flag runs still invoke update_auto_blocklist.py so the
    audit log gets a one-line-per-run entry. Empty suggestion lists
    are valuable signal too."""
    called = {}

    def fake_run(script, args):
        called["script"] = script
        called["args"] = args
        # Capture the tempfile payload before the caller unlinks it.
        flag_idx = args.index("--suggestions")
        suggestions_path = args[flag_idx + 1]
        with open(suggestions_path, encoding="utf-8") as f:
            called["payload"] = json.load(f)
        return ""

    monkeypatch.setattr(main, "run_script", fake_run)
    main.step3b_update_auto_blocklist([])

    assert called["script"] == "update_auto_blocklist.py"
    assert called["payload"] == []
    assert "no suggestions this run" in capsys.readouterr().out


def test_step3b_passes_suggestions_through_tempfile(monkeypatch):
    """Non-empty suggestion list lands in a tempfile whose path is
    threaded to update_auto_blocklist.py via --suggestions."""
    captured_payloads = []

    def fake_run(script, args):
        flag_idx = args.index("--suggestions")
        with open(args[flag_idx + 1], encoding="utf-8") as f:
            captured_payloads.append(json.load(f))
        return ""

    monkeypatch.setattr(main, "run_script", fake_run)
    suggestions = [
        {"sender": "marketing@x.com", "reason": "promo"},
        {"sender": "bulk@y.com", "reason": "news"},
    ]
    main.step3b_update_auto_blocklist(suggestions)
    assert captured_payloads == [suggestions]


def test_step3b_cleans_up_tempfile_on_run_script_failure(monkeypatch):
    """If update_auto_blocklist.py raises (e.g. subprocess non-zero),
    the tempfile must still be cleaned up. Pins the finally-unlink
    guard against a drift that leaks tempfiles into the runner."""
    captured_path = {}

    def fake_run(script, args):
        flag_idx = args.index("--suggestions")
        captured_path["path"] = args[flag_idx + 1]
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "run_script", fake_run)
    with pytest.raises(RuntimeError, match="boom"):
        main.step3b_update_auto_blocklist([])
    # The finally: os.unlink removed the tempfile before the exception
    # propagated.
    assert not os.path.exists(captured_path["path"])
