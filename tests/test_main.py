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
