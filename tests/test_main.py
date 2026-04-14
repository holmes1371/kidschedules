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
