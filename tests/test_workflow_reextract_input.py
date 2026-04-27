"""Pin the `reextract` workflow_dispatch input wiring (ROADMAP #4 / #33 QoL).

`main.py` has long supported `--reextract <MESSAGE_ID>` to evict a
single cached extraction so the next run re-processes that message.
The flag had no UI surface — only callable from the CLI — which made
re-verifying #33's PDF extraction against pre-#33 cached messages
awkward (the only way to find IDs was JSON-spelunking on the state
branch).

This test pins:

1. The `reextract` text input is declared on `workflow_dispatch`
   with an empty default. Default-empty is load-bearing: every cron
   run and every manual run that doesn't paste an ID gets `""`, so
   the forwarding shell condition naturally skips the flag.
2. The forwarding shell condition fires only when the input is
   non-empty. A typo here (e.g. always-forward, or "==''" instead of
   "-n") would either pass an empty string to main.py (which would
   then fail at `_reextract_eviction("")`) or skip even valid inputs.
3. The existing `workflow_dispatch` toggles (lookback_days, dry_run,
   intentional_failure, create_draft, test_output) are all still
   declared — defense against an editor accidentally dropping one
   when adding a sibling.

Text-parses the YAML rather than using PyYAML to keep the dev-deps
footprint at just pytest, matching the existing
`test_workflow_cron_gate.py` and `test_workflow_test_output_gate.py`
posture.
"""
from __future__ import annotations

import re
from pathlib import Path


WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent
    / ".github" / "workflows" / "weekly-schedule.yml"
)


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_workflow_has_reextract_text_input():
    """The reextract input is declared with `default: ""` and no
    `type:` (defaulting to string per workflow_dispatch semantics).
    Pin both the declaration and the empty default — a non-empty
    default would make every cron run try to re-extract, which is
    not the intended posture."""
    text = _workflow_text()
    m = re.search(
        r"reextract:\s*\n"
        r"\s*description:\s*\"[^\"]+\"\s*\n"
        r"\s*required:\s*false\s*\n"
        r"\s*default:\s*\"\"",
        text,
    )
    assert m is not None, (
        "Could not find a reextract workflow_dispatch input with "
        "an empty string default. ROADMAP #4 / #33 verification "
        "loop relies on this input; if it was renamed, update this "
        "test deliberately."
    )


def test_run_pipeline_forwards_reextract_only_when_non_empty():
    """The shell ARGS-building block must append `--reextract <value>`
    ONLY when the input is non-empty. Empty default means cron runs
    skip the flag entirely; a manual run with a value pasted in flips
    the conditional. Pin the `-n` test (non-empty check) shape so a
    refactor can't silently always-forward an empty string into
    main.py's `_reextract_eviction` (which warns on unknown IDs but
    is not designed for routine empty-input invocations)."""
    text = _workflow_text()
    pattern = (
        r"if\s+\[\s+-n\s+\"\$\{\{\s*github\.event\.inputs\.reextract\s*\}\}\""
        r"\s+\];\s*then\s*\n"
        r"\s*ARGS=\"\$ARGS --reextract \$\{\{\s*"
        r"github\.event\.inputs\.reextract\s*\}\}\""
    )
    assert re.search(pattern, text) is not None, (
        "Run-pipeline step does not forward --reextract to main.py "
        "behind a non-empty check, or the conditional shape changed. "
        "Without `-n`, an empty default would always forward and "
        "main.py would warn on every cron run."
    )


def test_workflow_dispatch_inputs_unchanged_for_existing_toggles():
    """Defense-in-depth: every input that was already there (the four
    pre-#33 toggles plus the test_output input from #23) is still
    declared. Adding `reextract` should not have removed any of
    them."""
    text = _workflow_text()
    for name in (
        "lookback_days",
        "dry_run",
        "intentional_failure",
        "create_draft",
        "test_output",
        "reextract",
    ):
        assert re.search(rf"\b{name}:\s*\n\s*description:", text) is not None, (
            f"workflow_dispatch input {name!r} is missing. Don't remove "
            f"existing inputs when adding a new one."
        )
