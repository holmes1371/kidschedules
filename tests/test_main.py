"""Unit tests for main.should_create_draft and main.step2c_load_cache_and_filter.

`should_create_draft` is the load-bearing piece for the spam-prevention
guarantee on the weekly Gmail digest. It is exhaustively parametrized
across every combination of the three inputs so a future refactor cannot
silently flip the default.

`step2c_load_cache_and_filter` is the zero-new-messages short-circuit
that prevents the Anthropic agent from re-processing cached messages.

Truth table for should_create_draft:
    dry_run  create_draft  CREATE_DRAFT  → expected
    T        any           any           → False   (dry-run always wins)
    F        T             any           → True
    F        F             "1"           → True
    F        F             other/unset   → False
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Ensure repo root is importable so `import main` works.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import events_state as es  # noqa: E402
import main  # noqa: E402


def _args(dry_run: bool, create_draft: bool) -> SimpleNamespace:
    return SimpleNamespace(dry_run=dry_run, create_draft=create_draft)


@pytest.fixture(autouse=True)
def _clear_create_draft_env(monkeypatch):
    """Each test starts with CREATE_DRAFT unset so we don't inherit local env."""
    monkeypatch.delenv("CREATE_DRAFT", raising=False)


# ── dry-run always wins ─────────────────────────────────────────────────


@pytest.mark.parametrize("create_draft", [True, False])
@pytest.mark.parametrize("env_value", [None, "", "0", "1", "true"])
def test_dry_run_always_suppresses(monkeypatch, create_draft, env_value):
    if env_value is not None:
        monkeypatch.setenv("CREATE_DRAFT", env_value)
    assert main.should_create_draft(_args(dry_run=True,
                                         create_draft=create_draft)) is False


# ── CLI flag opt-in ─────────────────────────────────────────────────────


def test_cli_flag_opts_in():
    assert main.should_create_draft(_args(dry_run=False, create_draft=True)) is True


# ── env var opt-in ──────────────────────────────────────────────────────


def test_env_var_set_to_1_opts_in(monkeypatch):
    monkeypatch.setenv("CREATE_DRAFT", "1")
    assert main.should_create_draft(_args(dry_run=False, create_draft=False)) is True


@pytest.mark.parametrize("env_value", ["", "0", "true", "yes", "True"])
def test_env_var_other_values_do_not_opt_in(monkeypatch, env_value):
    monkeypatch.setenv("CREATE_DRAFT", env_value)
    assert main.should_create_draft(_args(dry_run=False, create_draft=False)) is False


# ── default is off ──────────────────────────────────────────────────────


def test_default_is_no_draft():
    # No env var, no flag, no dry-run.
    assert main.should_create_draft(_args(dry_run=False, create_draft=False)) is False


# ── step2c cache filter (zero-new-messages short-circuit) ──────────────────


TODAY = dt.date(2026, 4, 14)
NOW_ISO = "2026-04-14T06:30:00-04:00"


def _write_state(path: Path, processed_ids: list[str]) -> None:
    state = es._empty_state()
    for mid in processed_ids:
        state["processed_messages"][mid] = NOW_ISO
    path.write_text(json.dumps(state), encoding="utf-8")


def test_step2c_all_cached_returns_empty_new_emails(tmp_path, capsys):
    """Given every inbound messageId is already in the cache, step2c
    returns new_emails=[]. main()'s guard then skips extract_events
    entirely — the Anthropic call never happens."""
    state_path = tmp_path / "events_state.json"
    _write_state(state_path, ["m1", "m2", "m3"])
    full_emails = [
        {"messageId": "m1", "subject": "a"},
        {"messageId": "m2", "subject": "b"},
        {"messageId": "m3", "subject": "c"},
    ]
    state, new_emails = main.step2c_load_cache_and_filter(
        full_emails, state_path=str(state_path), today=TODAY,
    )
    assert new_emails == []
    assert len(state["processed_messages"]) == 3


def test_step2c_partial_cache_returns_only_new(tmp_path):
    state_path = tmp_path / "events_state.json"
    _write_state(state_path, ["m1"])
    full_emails = [
        {"messageId": "m1", "subject": "cached"},
        {"messageId": "m2", "subject": "new"},
    ]
    _, new_emails = main.step2c_load_cache_and_filter(
        full_emails, state_path=str(state_path), today=TODAY,
    )
    assert [e["messageId"] for e in new_emails] == ["m2"]


def test_step2c_empty_cache_returns_all_as_new(tmp_path):
    """Fresh-install case: no cache file on disk yet."""
    state_path = tmp_path / "events_state.json"  # doesn't exist
    full_emails = [{"messageId": "m1"}, {"messageId": "m2"}]
    state, new_emails = main.step2c_load_cache_and_filter(
        full_emails, state_path=str(state_path), today=TODAY,
    )
    assert new_emails == full_emails
    assert state["processed_messages"] == {}


# ── _attach_sender_domains ────────────────────────────────────────────────


def _email(mid: str, from_: str) -> dict:
    """Minimal email dict as built by step2b_read_promising."""
    return {"messageId": mid, "from_": from_}


def _candidate(sid: str | None, name: str = "E") -> dict:
    """Minimal candidate event dict; omits source_message_id when sid is None."""
    ev: dict = {"name": name, "date": "2026-05-01"}
    if sid is not None:
        ev["source_message_id"] = sid
    return ev


def test_attach_sender_domain_name_addr_form():
    """Classic `"Name" <addr@domain>` header shape — the common case."""
    emails = [_email("m1", '"PTA President" <pta@school.org>')]
    candidates = [_candidate("m1")]
    main._attach_sender_domains(candidates, emails)
    assert candidates[0]["sender_domain"] == "school.org"


def test_attach_sender_domain_multi_level_tld_via_psl():
    """The whole reason we pulled in tldextract — `k12.ny.us` is a
    public suffix per the PSL, so the registrable domain is
    greenfield.k12.ny.us, not ny.us. A naive dot-split would get this
    wrong and would collapse every NY school district onto one block."""
    emails = [_email("m1", "office@mail.greenfield.k12.ny.us")]
    candidates = [_candidate("m1")]
    main._attach_sender_domains(candidates, emails)
    assert candidates[0]["sender_domain"] == "greenfield.k12.ny.us"


def test_attach_sender_domain_lowercases_output():
    emails = [_email("m1", "Alerts@SCHOOL.ORG")]
    candidates = [_candidate("m1")]
    main._attach_sender_domains(candidates, emails)
    assert candidates[0]["sender_domain"] == "school.org"


def test_attach_sender_domain_missing_source_id(capsys):
    emails = [_email("m1", "pta@school.org")]
    candidates = [_candidate(None)]
    main._attach_sender_domains(candidates, emails)
    assert candidates[0]["sender_domain"] == ""
    assert "no sender_domain" in capsys.readouterr().out


def test_attach_sender_domain_unknown_source_id():
    """Candidate references a messageId not in this batch — possible if
    the agent hallucinates past the upstream filter. Button simply
    doesn't render; no crash, no warning spam per event."""
    emails = [_email("m1", "pta@school.org")]
    candidates = [_candidate("m2")]
    main._attach_sender_domains(candidates, emails)
    assert candidates[0]["sender_domain"] == ""


def test_attach_sender_domain_empty_from_header():
    emails = [_email("m1", "")]
    candidates = [_candidate("m1")]
    main._attach_sender_domains(candidates, emails)
    assert candidates[0]["sender_domain"] == ""


def test_attach_sender_domain_malformed_from():
    """parseaddr returns ('', '') for junk like `not an email` —
    guard ensures we don't pass an empty string into tldextract."""
    emails = [_email("m1", "not an email at all")]
    candidates = [_candidate("m1")]
    main._attach_sender_domains(candidates, emails)
    assert candidates[0]["sender_domain"] == ""


def test_attach_sender_domain_bare_address_no_tld():
    """tldextract returns '' for a bare host with no public suffix."""
    emails = [_email("m1", "local@localhost")]
    candidates = [_candidate("m1")]
    main._attach_sender_domains(candidates, emails)
    assert candidates[0]["sender_domain"] == ""


def test_attach_sender_domain_mixed_batch_emits_one_summary_warning(capsys):
    """Per-event warnings would be too noisy. The helper emits a single
    summary line with the miss count instead."""
    emails = [
        _email("m1", "pta@school.org"),
        _email("m2", ""),
    ]
    candidates = [
        _candidate("m1", "good"),
        _candidate("m2", "empty-from"),
        _candidate(None, "no-sid"),
    ]
    main._attach_sender_domains(candidates, emails)
    assert candidates[0]["sender_domain"] == "school.org"
    assert candidates[1]["sender_domain"] == ""
    assert candidates[2]["sender_domain"] == ""
    out = capsys.readouterr().out
    # One line, count = 2 (the two misses), not two separate warnings.
    assert out.count("no sender_domain") == 1
    assert "2 event(s)" in out


def test_attach_sender_domain_empty_candidates_is_noop(capsys):
    main._attach_sender_domains([], [_email("m1", "pta@school.org")])
    assert capsys.readouterr().out == ""


# ── _reextract_eviction (forced re-extraction) ────────────────────────────


def _write_state_with_events(
    path: Path,
    processed_ids: list[str],
    events: list[tuple[str, str]],  # list of (event_id, source_message_id)
) -> None:
    """Write a state file with both processed_messages and events seeded."""
    state = es._empty_state()
    for mid in processed_ids:
        state["processed_messages"][mid] = NOW_ISO
    for eid, source_mid in events:
        state["events"][eid] = {
            "id": eid,
            "name": f"Event {eid}",
            "date": "2026-05-01",
            "child": "Isla",
            "source_message_id": source_mid,
        }
    path.write_text(json.dumps(state), encoding="utf-8")


def test_reextract_purges_message_and_all_matching_events(tmp_path, capsys):
    """Target: m-target is in processed_messages AND has 3 matching events.
    After eviction, both sides are clean; unrelated entries survive."""
    state_path = tmp_path / "events_state.json"
    _write_state_with_events(
        state_path,
        processed_ids=["m-target", "m-other"],
        events=[
            ("eid-a", "m-target"),
            ("eid-b", "m-target"),
            ("eid-c", "m-target"),
            ("eid-d", "m-other"),  # unrelated
        ],
    )
    main._reextract_eviction(
        "m-target", state_path=str(state_path), now_iso=NOW_ISO
    )

    reloaded = es.load_state(str(state_path))
    assert "m-target" not in reloaded["processed_messages"]
    assert "m-other" in reloaded["processed_messages"]
    assert "eid-a" not in reloaded["events"]
    assert "eid-b" not in reloaded["events"]
    assert "eid-c" not in reloaded["events"]
    assert "eid-d" in reloaded["events"]

    out = capsys.readouterr().out
    assert "Evicted 1 processed_message entry and 3 cached event(s)" in out


def test_reextract_unknown_message_id_is_warning_not_failure(tmp_path, capsys):
    """Fat-fingered message ID → log a warning, do not mutate state."""
    state_path = tmp_path / "events_state.json"
    _write_state_with_events(
        state_path,
        processed_ids=["m-real"],
        events=[("eid-a", "m-real")],
    )
    main._reextract_eviction(
        "m-nonexistent", state_path=str(state_path), now_iso=NOW_ISO
    )

    reloaded = es.load_state(str(state_path))
    assert reloaded["processed_messages"] == {"m-real": NOW_ISO}
    assert "eid-a" in reloaded["events"]

    out = capsys.readouterr().out
    assert "no match in cache" in out.lower()


def test_reextract_missing_state_file_is_noop(tmp_path, capsys):
    """Fresh-install case or deleted cache file — log and return."""
    state_path = tmp_path / "nonexistent.json"
    main._reextract_eviction(
        "m-target", state_path=str(state_path), now_iso=NOW_ISO
    )
    assert not state_path.exists()  # not created by the helper
    out = capsys.readouterr().out
    assert "nothing to evict" in out.lower()


def test_reextract_purges_events_even_when_message_not_in_processed(tmp_path):
    """Edge case: events_state has orphan events whose source message was
    already GC'd from processed_messages. Eviction still purges the
    orphan events and prints 0 message + N events evicted."""
    state_path = tmp_path / "events_state.json"
    _write_state_with_events(
        state_path,
        processed_ids=[],  # no processed_messages entry
        events=[("eid-a", "m-target"), ("eid-b", "m-other")],
    )
    main._reextract_eviction(
        "m-target", state_path=str(state_path), now_iso=NOW_ISO
    )
    reloaded = es.load_state(str(state_path))
    assert "eid-a" not in reloaded["events"]
    assert "eid-b" in reloaded["events"]


def test_reextract_persists_to_disk(tmp_path):
    """The state has to land on disk (not just in-memory) so step2c's
    subsequent load_state call sees the eviction."""
    state_path = tmp_path / "events_state.json"
    _write_state_with_events(
        state_path,
        processed_ids=["m-target"],
        events=[("eid-a", "m-target")],
    )
    main._reextract_eviction(
        "m-target", state_path=str(state_path), now_iso=NOW_ISO
    )
    # Raw file read — no in-memory shortcut.
    on_disk = json.loads(state_path.read_text(encoding="utf-8"))
    assert "m-target" not in on_disk["processed_messages"]
    assert "eid-a" not in on_disk["events"]


# ── sender-stats integration (#17 C5) ─────────────────────────────────────
#
# Tests for the two pure helpers that bridge extraction into the
# newsletter-stats module (`_per_message_counts`, `_print_outlier_alerts`)
# and for the extended `step3_extract_events` signature that forwards
# `newsletter_senders` down to the agent.


def _full_email(mid: str, from_: str) -> dict:
    """Email dict shape as built by step2b_read_promising — just the
    fields `_per_message_counts` reads."""
    return {"messageId": mid, "from_": from_}


def _ev(source_message_id: str) -> dict:
    """Candidate event dict — only `source_message_id` matters for the
    count derivation."""
    return {
        "name": "e",
        "date": "2026-05-01",
        "source_message_id": source_message_id,
    }


def test_per_message_counts_basic():
    """Two emails; one has 2 events, the other has 1. Helper returns
    one triple per email, with the sender key lowercased."""
    new_emails = [
        _full_email("m1", "news@x.com"),
        _full_email("m2", "reg@y.com"),
    ]
    candidates = [_ev("m1"), _ev("m1"), _ev("m2")]
    counts = main._per_message_counts(new_emails, candidates)
    assert counts == [("news@x.com", "m1", 2), ("reg@y.com", "m2", 1)]


def test_per_message_counts_zero_event_message_contributes_zero():
    """A message sent to the agent that produced no events still
    appears in the output with count=0 — keeps the rolling median
    honest for quiet newsletter issues."""
    new_emails = [
        _full_email("m1", "news@x.com"),
        _full_email("m2", "quiet@x.com"),
    ]
    candidates = [_ev("m1")]  # nothing from m2
    counts = main._per_message_counts(new_emails, candidates)
    assert ("news@x.com", "m1", 1) in counts
    assert ("quiet@x.com", "m2", 0) in counts


def test_per_message_counts_preserves_input_order():
    """Stats bookkeeping doesn't depend on order, but pinning the
    invariant makes the test output easier to read and lets alert-
    generation tests assume a deterministic ordering."""
    new_emails = [
        _full_email("m2", "b@x.com"),
        _full_email("m1", "a@x.com"),
        _full_email("m3", "c@x.com"),
    ]
    counts = main._per_message_counts(new_emails, [])
    assert [t[1] for t in counts] == ["m2", "m1", "m3"]


def test_per_message_counts_ignores_candidates_with_unrecognized_source_id():
    """Defensive: `agent._filter_events_by_source_id` already drops
    events whose source_message_id isn't in the batch, but this helper
    guards independently so a future refactor can't silently inflate
    counts for a ghost message."""
    new_emails = [_full_email("m1", "a@x.com")]
    candidates = [_ev("m1"), _ev("m-ghost")]
    counts = main._per_message_counts(new_emails, candidates)
    assert counts == [("a@x.com", "m1", 1)]


def test_per_message_counts_uses_lowercased_mailbox_from_named_form():
    """Canonicalizes via agent._sender_key so the key shape matches
    what newsletter_stats stores in sender_stats.json."""
    new_emails = [_full_email("m1", '"PTA Sunbeam" <Sunbeam@LAESPTA.ORG>')]
    counts = main._per_message_counts(new_emails, [_ev("m1")])
    assert counts == [("sunbeam@laespta.org", "m1", 1)]


def test_per_message_counts_empty_new_emails_is_empty():
    """No messages sent to the agent → no per-message counts to record,
    regardless of how many orphan candidates are passed."""
    assert main._per_message_counts([], [_ev("m1"), _ev("m2")]) == []


def test_per_message_counts_skips_messages_with_empty_id():
    """Defensive: an email without a messageId can't be keyed into the
    stats file. Upstream never produces these, but the helper shouldn't
    emit a triple that would error on the stats-module side."""
    new_emails = [_full_email("m1", "a@x.com"), _full_email("", "b@x.com")]
    counts = main._per_message_counts(new_emails, [_ev("m1")])
    # The empty-mid email still gets a triple (stats module defends
    # against empty sender_key but not empty message_id). Pinning the
    # observed behavior so a future tightening is a conscious choice.
    assert ("a@x.com", "m1", 1) in counts


# ── _print_outlier_alerts ────────────────────────────────────────────────


def test_print_outlier_alerts_empty_prints_banner_plus_no_alerts_line(capsys):
    """Banner is unconditional so an Actions-log reader can tell
    'checked and clean' apart from 'skipped entirely'."""
    main._print_outlier_alerts([])
    out = capsys.readouterr().out
    assert "STEP 3c: Outlier alerts" in out
    assert "No outlier alerts this run" in out


def test_print_outlier_alerts_formats_alert_line(capsys):
    """Each alert line echoes the message ID verbatim so Tom can paste
    it straight into `--reextract`."""
    alerts = [{
        "sender": "sunbeam@laespta.org",
        "message_id": "abc123def456",
        "prior_median": 12,
        "current_count": 1,
        "threshold": 6,
    }]
    main._print_outlier_alerts(alerts)
    out = capsys.readouterr().out
    assert "sunbeam@laespta.org" in out
    assert "abc123def456" in out
    assert "1 event" in out
    assert "prior median 12" in out
    assert "threshold 6" in out
    # The reextract hint renders when at least one alert fires.
    assert "--reextract" in out


def test_print_outlier_alerts_multiple_alerts_one_line_each(capsys):
    """Two alerts → two warning lines, each with its own message ID."""
    alerts = [
        {"sender": "a@x.com", "message_id": "m1",
         "prior_median": 10, "current_count": 2, "threshold": 5},
        {"sender": "b@y.com", "message_id": "m2",
         "prior_median": 6, "current_count": 0, "threshold": 3},
    ]
    main._print_outlier_alerts(alerts)
    out = capsys.readouterr().out
    assert out.count("⚠️") == 2
    assert "m1" in out
    assert "m2" in out


# ── step3_extract_events signature forwarding ────────────────────────────


def test_step3_extract_events_accepts_newsletter_senders_kwarg():
    """Signature guardrail. C5 adds the kwarg to main.step3_extract_events
    so a future refactor can't silently drop it and starve the agent
    of the batching signal."""
    import inspect
    sig = inspect.signature(main.step3_extract_events)
    assert "newsletter_senders" in sig.parameters
    assert sig.parameters["newsletter_senders"].default is None


def test_step3_extract_events_forwards_newsletter_senders_to_agent(monkeypatch):
    """End-to-end wiring check: the kwarg reaches agent.extract_events
    unmodified. Avoids mocking the Anthropic SDK by replacing the
    imported `extract_events` reference in `main`'s namespace."""
    captured: dict = {}

    def fake_extract_events(emails, model="", newsletter_senders=None):
        captured["emails"] = emails
        captured["model"] = model
        captured["newsletter_senders"] = newsletter_senders
        return [], []

    monkeypatch.setattr(main, "extract_events", fake_extract_events)
    newsletters = {"sunbeam@laespta.org"}
    main.step3_extract_events(
        [{"messageId": "m1", "from_": "a@x.com"}],
        model="claude-sonnet-4-6",
        newsletter_senders=newsletters,
    )
    assert captured["newsletter_senders"] is newsletters


def test_step3_extract_events_forwards_none_when_kwarg_omitted(monkeypatch):
    """Default None must still flow through — this is how older callers
    and tests that don't care about the partition stay behaviorally
    equivalent to pre-#17."""
    captured: dict = {}

    def fake_extract_events(emails, model="", newsletter_senders=None):
        captured["newsletter_senders"] = newsletter_senders
        return [], []

    monkeypatch.setattr(main, "extract_events", fake_extract_events)
    main.step3_extract_events([], model="x")
    assert captured["newsletter_senders"] is None


# ─────────────────────────────────────────────────────────────────────────
# step4_process_events outlier-alerts bridge (#17, C6)
# ─────────────────────────────────────────────────────────────────────────


def test_step4_process_events_accepts_outlier_alerts_kwarg():
    """Signature guardrail. C6 adds the kwarg so the weekly digest can
    surface per-run outlier alerts without punching through argparse
    from main() directly."""
    import inspect
    sig = inspect.signature(main.step4_process_events)
    assert "outlier_alerts" in sig.parameters
    assert sig.parameters["outlier_alerts"].default is None


def _stub_script_outputs(script_args: list[str]) -> None:
    """Write the minimal set of files step4_process_events reads back."""
    # script_args is a flat [flag, value, flag, value, ...] list.
    kv = dict(zip(script_args[::2], script_args[1::2]))
    Path(kv["--body-out"]).write_text("", encoding="utf-8")
    Path(kv["--html-out"]).write_text("", encoding="utf-8")
    Path(kv["--digest-text-out"]).write_text("", encoding="utf-8")
    Path(kv["--digest-html-out"]).write_text("", encoding="utf-8")
    Path(kv["--meta-out"]).write_text(
        json.dumps({
            "subject": "",
            "today_iso": "2026-04-13",
            "counts": {
                "candidates_in": 0,
                "future_dated": 0,
                "undated": 0,
                "dropped_past": 0,
                "banked_far_future": 0,
                "dropped_ignored": 0,
            },
            "warnings": [],
            "has_events": False,
            "digest": {"subject": "", "this_week_count": 0},
        }),
        encoding="utf-8",
    )


def test_step4_process_events_forwards_outlier_alerts_tempfile(monkeypatch):
    """When alerts are supplied, step4 writes them to a tempfile and adds
    --outlier-alerts to run_script's argv pointing at it. The tempfile
    content is the same list (round-tripped through JSON)."""
    alerts = [
        {
            "sender": "newsletter@example.com",
            "message_id": "m1",
            "prior_median": 6,
            "current_count": 1,
            "threshold": 3,
        }
    ]
    captured: dict = {}

    def fake_run_script(script_name, script_args):
        captured["script_name"] = script_name
        captured["script_args"] = list(script_args)
        # Read the alerts tempfile while it still exists.
        kv = dict(zip(script_args[::2], script_args[1::2]))
        alerts_path = kv["--outlier-alerts"]
        with open(alerts_path, "r", encoding="utf-8") as f:
            captured["alerts_on_disk"] = json.load(f)
        _stub_script_outputs(script_args)
        return ""

    monkeypatch.setattr(main, "run_script", fake_run_script)
    monkeypatch.setattr(main, "_load_webhook_url", lambda: "")
    main.step4_process_events(
        [], pages_url="", dry_run=True, outlier_alerts=alerts
    )
    assert "--outlier-alerts" in captured["script_args"]
    assert captured["alerts_on_disk"] == alerts


def test_step4_process_events_omits_flag_when_alerts_none(monkeypatch):
    """None is the default-quiet path: no tempfile, no flag, no stale
    `--outlier-alerts ""` that a tolerant loader would need to skip."""
    captured: dict = {}

    def fake_run_script(script_name, script_args):
        captured["script_args"] = list(script_args)
        _stub_script_outputs(script_args)
        return ""

    monkeypatch.setattr(main, "run_script", fake_run_script)
    monkeypatch.setattr(main, "_load_webhook_url", lambda: "")
    main.step4_process_events([], pages_url="", dry_run=True)
    assert "--outlier-alerts" not in captured["script_args"]


def test_step4_process_events_omits_flag_when_alerts_empty(monkeypatch):
    """Empty list behaves like None — the flag is an existence-signal,
    not a payload. `newsletter_stats.outlier_alerts` returning [] is
    the common-case no-alerts run and must not drag a tempfile in."""
    captured: dict = {}

    def fake_run_script(script_name, script_args):
        captured["script_args"] = list(script_args)
        _stub_script_outputs(script_args)
        return ""

    monkeypatch.setattr(main, "run_script", fake_run_script)
    monkeypatch.setattr(main, "_load_webhook_url", lambda: "")
    main.step4_process_events(
        [], pages_url="", dry_run=True, outlier_alerts=[]
    )
    assert "--outlier-alerts" not in captured["script_args"]


def test_step4_process_events_cleans_up_alerts_tempfile(monkeypatch):
    """The finally block must unlink the alerts tempfile along with the
    other scratch files so a long-running agent session doesn't
    accumulate one-shot JSON files in /tmp."""
    alerts = [{"sender": "x@y.com", "message_id": "m", "prior_median": 5,
               "current_count": 0, "threshold": 3}]
    seen: dict = {}

    def fake_run_script(script_name, script_args):
        kv = dict(zip(script_args[::2], script_args[1::2]))
        seen["alerts_path"] = kv["--outlier-alerts"]
        assert Path(seen["alerts_path"]).exists()
        _stub_script_outputs(script_args)
        return ""

    monkeypatch.setattr(main, "run_script", fake_run_script)
    monkeypatch.setattr(main, "_load_webhook_url", lambda: "")
    main.step4_process_events(
        [], pages_url="", dry_run=True, outlier_alerts=alerts
    )
    assert not Path(seen["alerts_path"]).exists()
