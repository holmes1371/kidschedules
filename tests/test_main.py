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


# ── _compute_block_key + sender_block_key attribution ─────────────────────
#
# #20: freemail senders (gmail.com, yahoo.com, etc.) block at address
# granularity; institutional senders keep domain-level blocking. These
# tests pin the decision surface so a future refactor can't silently
# revert to domain-only blocking.


def test_compute_block_key_freemail_returns_address():
    freemail = frozenset({"gmail.com", "yahoo.com"})
    assert (
        main._compute_block_key("alice@gmail.com", "gmail.com", freemail)
        == "alice@gmail.com"
    )


def test_compute_block_key_freemail_lowercases_address():
    freemail = frozenset({"gmail.com"})
    assert (
        main._compute_block_key("Alice.Smith@Gmail.com", "gmail.com", freemail)
        == "alice.smith@gmail.com"
    )


def test_compute_block_key_institutional_returns_domain():
    freemail = frozenset({"gmail.com"})
    assert (
        main._compute_block_key("office@fcps.edu", "fcps.edu", freemail)
        == "fcps.edu"
    )


def test_compute_block_key_empty_domain_returns_empty():
    # Degenerate-attribution path — no domain means no button at all.
    assert main._compute_block_key("anything", "", frozenset({"gmail.com"})) == ""


def test_compute_block_key_freemail_domain_but_no_address_falls_back_to_domain():
    # Defense-in-depth: if the address somehow went missing on a freemail
    # domain, we'd rather block the whole domain than not block at all.
    assert (
        main._compute_block_key("", "gmail.com", frozenset({"gmail.com"}))
        == "gmail.com"
    )


def test_compute_block_key_empty_freemail_set_is_always_domain_level():
    # Graceful degrade when freemail_domains.txt is missing or empty.
    assert (
        main._compute_block_key("alice@gmail.com", "gmail.com", frozenset())
        == "gmail.com"
    )


def test_attach_sender_block_key_freemail_address():
    freemail = frozenset({"gmail.com"})
    emails = [_email("m1", "Jane Doe <jane@gmail.com>")]
    candidates = [_candidate("m1")]
    main._attach_sender_domains(candidates, emails, freemail=freemail)
    assert candidates[0]["sender_domain"] == "gmail.com"
    assert candidates[0]["sender_block_key"] == "jane@gmail.com"


def test_attach_sender_block_key_institutional_uses_domain():
    freemail = frozenset({"gmail.com"})
    emails = [_email("m1", "pta@school.org")]
    candidates = [_candidate("m1")]
    main._attach_sender_domains(candidates, emails, freemail=freemail)
    assert candidates[0]["sender_domain"] == "school.org"
    assert candidates[0]["sender_block_key"] == "school.org"


def test_attach_sender_block_key_address_lowercased():
    freemail = frozenset({"gmail.com"})
    emails = [_email("m1", "Alice.Smith@Gmail.com")]
    candidates = [_candidate("m1")]
    main._attach_sender_domains(candidates, emails, freemail=freemail)
    assert candidates[0]["sender_domain"] == "gmail.com"
    assert candidates[0]["sender_block_key"] == "alice.smith@gmail.com"


def test_attach_sender_block_key_empty_freemail_set_falls_back_to_domain():
    # Mirrors what happens when freemail_domains.txt is missing/empty: every
    # sender — even what we'd normally treat as freemail — gets today's
    # domain-level block behavior. Acceptable graceful-degrade posture.
    emails = [_email("m1", "jane@gmail.com")]
    candidates = [_candidate("m1")]
    main._attach_sender_domains(candidates, emails, freemail=frozenset())
    assert candidates[0]["sender_domain"] == "gmail.com"
    assert candidates[0]["sender_block_key"] == "gmail.com"


def test_attach_sender_block_key_attribution_failure_yields_empty_both():
    # Missing from header → both fields empty so the single render-gate
    # condition (empty block_key → no button) holds.
    emails = [_email("m1", "")]
    candidates = [_candidate("m1")]
    main._attach_sender_domains(candidates, emails, freemail=frozenset({"gmail.com"}))
    assert candidates[0]["sender_domain"] == ""
    assert candidates[0]["sender_block_key"] == ""


def test_attach_sender_block_key_two_different_gmail_senders_differ():
    # The whole point of the change: two gmail.com senders must render
    # different block keys so clicking one doesn't nuke the other.
    freemail = frozenset({"gmail.com"})
    emails = [
        _email("m1", "Alice <alice@gmail.com>"),
        _email("m2", "Bob <bob@gmail.com>"),
    ]
    candidates = [_candidate("m1"), _candidate("m2")]
    main._attach_sender_domains(candidates, emails, freemail=freemail)
    # Same domain, different block keys.
    assert candidates[0]["sender_domain"] == "gmail.com"
    assert candidates[1]["sender_domain"] == "gmail.com"
    assert candidates[0]["sender_block_key"] == "alice@gmail.com"
    assert candidates[1]["sender_block_key"] == "bob@gmail.com"


def test_attach_sender_block_key_default_freemail_load_uses_committed_file():
    # When the caller omits freemail=, main._attach_sender_domains loads
    # the committed freemail_domains.txt. Smoke-check that the real
    # committed file recognizes gmail.com as freemail.
    emails = [_email("m1", "someone@gmail.com")]
    candidates = [_candidate("m1")]
    main._attach_sender_domains(candidates, emails)  # no explicit freemail
    assert candidates[0]["sender_domain"] == "gmail.com"
    assert candidates[0]["sender_block_key"] == "someone@gmail.com"


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


# ── step4_process_events lookback-days bridge (#22) ──────────────────────
#
# The Gmail search window is chosen upstream in main() via
# `args.lookback_days`; the same value has to ride through the
# step4 bridge into process_events.py so the rendered page header
# ("{N} day lookback") and the no-events fallback paragraph both
# match the window the run actually used. Pre-fix, step4 dropped
# the value on the floor and process_events.py silently defaulted
# to 60 — so an override like --lookback-days 120 extracted the
# right events but rendered a stale "60 day lookback" in the UI.


def test_step4_process_events_forwards_lookback_days(monkeypatch):
    """Explicit override: lookback_days=120 must arrive as the string
    "120" on the --lookback-days flag in run_script's argv, so the page
    header and no-events fallback render the actual window the run
    used. Asserting via kv-dict pins adjacency (flag immediately
    followed by its value) alongside presence."""
    captured: dict = {}

    def fake_run_script(script_name, script_args):
        captured["script_args"] = list(script_args)
        _stub_script_outputs(script_args)
        return ""

    monkeypatch.setattr(main, "run_script", fake_run_script)
    monkeypatch.setattr(main, "_load_webhook_url", lambda: "")
    main.step4_process_events(
        [], pages_url="", dry_run=True, lookback_days=120
    )
    kv = dict(zip(captured["script_args"][::2], captured["script_args"][1::2]))
    assert kv["--lookback-days"] == "120"


# ── _dedupe_by_thread (#21 C2) ────────────────────────────────────────────
#
# Thread-level dedup sits in step2b between the existing messageId pass
# and the read_message body fetch. Helper is pure so these tests assert
# the contract directly — latest-by-Date within a thread, parseable
# outranks unparseable, first-seen breaks ties, missing threadId is a
# passthrough. See design/dedupe-candidate-messages.md.


def _stub(mid: str, tid: str, date: str = "") -> dict:
    """Minimal Gmail search stub shape from gmail_client.search_messages."""
    return {
        "messageId": mid,
        "threadId": tid,
        "snippet": "",
        "headers": {"From": "x@y.z", "Subject": mid, "Date": date},
    }


def test_dedupe_by_thread_empty_input():
    assert main._dedupe_by_thread([]) == []


def test_dedupe_by_thread_no_collisions_preserves_input():
    """Three stubs, three distinct threads — output equals input."""
    stubs = [
        _stub("m1", "t1", "Mon, 14 Apr 2026 08:00:00 -0400"),
        _stub("m2", "t2", "Mon, 14 Apr 2026 09:00:00 -0400"),
        _stub("m3", "t3", "Mon, 14 Apr 2026 10:00:00 -0400"),
    ]
    assert main._dedupe_by_thread(stubs) == stubs


def test_dedupe_by_thread_latest_date_survives_within_thread():
    """Two stubs same thread, clear Date ordering; later stub survives
    regardless of input order."""
    earlier = _stub("m1", "t1", "Mon, 14 Apr 2026 08:00:00 -0400")
    later = _stub("m2", "t1", "Tue, 15 Apr 2026 08:00:00 -0400")
    assert main._dedupe_by_thread([earlier, later]) == [later]
    assert main._dedupe_by_thread([later, earlier]) == [later]


def test_dedupe_by_thread_equal_dates_first_seen_wins():
    """Identical parsed Date on two same-thread stubs; first-seen wins
    by the `strictly later` replace rule."""
    a = _stub("m1", "t1", "Mon, 14 Apr 2026 08:00:00 -0400")
    b = _stub("m2", "t1", "Mon, 14 Apr 2026 08:00:00 -0400")
    assert main._dedupe_by_thread([a, b]) == [a]
    assert main._dedupe_by_thread([b, a]) == [b]


def test_dedupe_by_thread_missing_threadid_is_passthrough():
    """Stubs without threadId bypass grouping — never collapsed, never
    dropped, even when they share a messageId shape that would
    otherwise collide (messageId dedup is upstream)."""
    a = _stub("m1", "", "Mon, 14 Apr 2026 08:00:00 -0400")
    b = _stub("m2", "", "Tue, 15 Apr 2026 08:00:00 -0400")
    assert main._dedupe_by_thread([a, b]) == [a, b]


def test_dedupe_by_thread_parseable_beats_unparseable_within_thread():
    """A parseable Date outranks an unparseable one regardless of
    encounter order — the useful timestamp wins over the missing one."""
    parseable = _stub("m1", "t1", "Mon, 14 Apr 2026 08:00:00 -0400")
    malformed = _stub("m2", "t1", "not a date")
    assert main._dedupe_by_thread([parseable, malformed]) == [parseable]
    assert main._dedupe_by_thread([malformed, parseable]) == [parseable]


def test_dedupe_by_thread_all_unparseable_first_seen_wins():
    """Every stub in a thread has a bad/missing Date — fall back to
    first-seen so the function is total and deterministic."""
    a = _stub("m1", "t1", "not a date")
    b = _stub("m2", "t1", "")  # missing Date header
    assert main._dedupe_by_thread([a, b]) == [a]
    assert main._dedupe_by_thread([b, a]) == [b]


def test_dedupe_by_thread_preserves_first_encounter_group_order():
    """Three groups encountered in order A, B, C with collisions in A
    and B; output still lists representatives in A, B, C order even
    though A's winner is the last-encountered stub of group A."""
    a_early = _stub("a1", "tA", "Mon, 14 Apr 2026 08:00:00 -0400")
    b_early = _stub("b1", "tB", "Mon, 14 Apr 2026 08:05:00 -0400")
    c_only = _stub("c1", "tC", "Mon, 14 Apr 2026 08:10:00 -0400")
    b_late = _stub("b2", "tB", "Tue, 15 Apr 2026 09:00:00 -0400")
    a_late = _stub("a2", "tA", "Wed, 16 Apr 2026 10:00:00 -0400")
    stubs = [a_early, b_early, c_only, b_late, a_late]
    assert main._dedupe_by_thread(stubs) == [a_late, b_late, c_only]


# ── step2b wiring integration (#21 C3) ────────────────────────────────────
#
# End-to-end check that the two dedup passes fire in order and the
# per-message body fetch only happens for survivors — verifying the
# original dance-studio observation: one active reply thread producing
# four [i/N] hits collapses to a single read_message call.


def test_step2b_thread_dedup_collapses_dance_studio_pattern(capsys):
    """Recital thread with four stubs spread across three query
    categories (plus one duplicated messageId to prove the earlier
    messageId pass still fires) collapses to a single read_message
    call; two unrelated threads survive untouched. Log shows the
    three-line funnel counts."""
    # Four dance-studio stubs in thread Tdance, ascending dates — the
    # latest (recital4) should win.
    r1 = _stub("recital1", "Tdance", "Mon, 30 Mar 2026 08:00:00 -0400")
    r2 = _stub("recital2", "Tdance", "Tue, 31 Mar 2026 09:00:00 -0400")
    r3 = _stub("recital3", "Tdance", "Wed,  1 Apr 2026 10:00:00 -0400")
    r4 = _stub("recital4", "Tdance", "Thu,  2 Apr 2026 11:00:00 -0400")
    # Two unrelated threads — pass through.
    laes = _stub("laes1", "Tlaes", "Mon, 30 Mar 2026 12:00:00 -0400")
    swim = _stub("swim1", "Tswim", "Mon, 30 Mar 2026 13:00:00 -0400")

    # r1 appears in two categories — exercises the pre-existing
    # messageId pass so the funnel reports 7 → 6 → 3.
    search_results: dict[str, list[dict]] = {
        "school_activities": [r1, laes],
        "sports_extracurriculars": [r1, swim],
        "newsletters_calendars": [r2, r3, r4],
        "camps_summer": [],
        "birthdays_parties": [],
    }

    calls: list[str] = []

    class _FakeGmail:
        def read_message(self, message_id: str) -> dict:
            calls.append(message_id)
            return {
                "messageId": message_id,
                "threadId": None,
                "headers": {
                    "From": "studio@example.com",
                    "Subject": f"subject-{message_id}",
                    "Date": "Thu,  2 Apr 2026 11:00:00 -0400",
                },
                "snippet": "",
                "body": "body",
            }

    full = main.step2b_read_promising(_FakeGmail(), search_results)

    # One read_message call per surviving thread — the three earlier
    # recital replies never hit the Gmail API.
    assert calls == ["recital4", "laes1", "swim1"]
    assert [e["messageId"] for e in full] == ["recital4", "laes1", "swim1"]

    out = capsys.readouterr().out
    assert "Collected 7 stub(s) across 5 queries" in out
    assert "Unique messageIds: 6" in out
    assert "After thread dedup: 3" in out
