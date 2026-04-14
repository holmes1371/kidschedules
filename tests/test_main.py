"""Unit tests for main.should_create_draft.

This gate is the load-bearing piece for the spam-prevention guarantee
on the weekly Gmail digest. It is exhaustively parametrized across
every combination of the three inputs so a future refactor cannot
silently flip the default.

Truth table:
    dry_run  create_draft  CREATE_DRAFT  → expected
    T        any           any           → False   (dry-run always wins)
    F        T             any           → True
    F        F             "1"           → True
    F        F             other/unset   → False
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Ensure repo root is importable so `import main` works.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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
