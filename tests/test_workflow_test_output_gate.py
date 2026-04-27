"""Pin the test_output toggle wiring in weekly-schedule.yml.

ROADMAP #23. Three load-bearing pieces in the workflow:

1. The `test_output` boolean input must exist and default false. If
   the input is removed or the default flips, manual `workflow_dispatch`
   runs would silently revert to overwriting Ellen's prod page.
2. The run-pipeline step must forward `--test-output` to main.py when
   the input is true. A drift in the conditional means main.py would
   never see the flag and would do a real prod write.
3. The "Save persistent state back to state branch" step's `if:`
   clause must include `inputs.test_output != 'true'`. Without it, a
   test run would still push events_state.json / sender_stats.json /
   etc. to the state branch — defeating the test-mode sandboxing.
4. A "Preserve production page for test_output run" step must exist
   and run only when `inputs.test_output == 'true'`. Without it,
   `actions/deploy-pages` does a full-replace deploy with no prod
   index.html and Ellen's page disappears between cron ticks.

Tests text-parse the YAML rather than using PyYAML, matching the
existing `test_workflow_cron_gate.py` and `test_workflow_state_branch_parity.py`
posture (no extra dev-deps).
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


def test_workflow_has_test_output_input():
    text = _workflow_text()
    # The input is declared under workflow_dispatch.inputs as a
    # boolean with default false. Pin all three facts so none
    # can drift independently.
    m = re.search(
        r"test_output:\s*\n"
        r"\s*description:\s*\"[^\"]+\"\s*\n"
        r"\s*required:\s*false\s*\n"
        r"\s*type:\s*boolean\s*\n"
        r"\s*default:\s*false",
        text,
    )
    assert m is not None, (
        "Could not find a test_output workflow_dispatch input declared "
        "as a boolean with default false. ROADMAP #23 requires this "
        "input to gate the test-mode behavior; if you renamed it or "
        "changed its type/default, update this test deliberately."
    )


def test_run_pipeline_forwards_test_output_flag():
    """The shell ARGS-building block must append --test-output when
    inputs.test_output is 'true'. Pin the conditional shape so a future
    edit can't silently break the forwarding."""
    text = _workflow_text()
    pattern = (
        r"if\s+\[\s+\"\$\{\{\s*github\.event\.inputs\.test_output\s*\}\}\""
        r"\s+==\s+\"true\"\s+\];\s*then\s*\n"
        r"\s*ARGS=\"\$ARGS --test-output\""
    )
    assert re.search(pattern, text) is not None, (
        "Run-pipeline step does not forward --test-output to main.py. "
        "Without this, the workflow input is wired but the script never "
        "sees it — every manual run still does a real prod write."
    )


def test_state_branch_save_step_gated_on_test_output():
    """The 'Save persistent state back to state branch' step must run
    only when test_output is NOT true. This is the workflow-level
    safety net for test runs (main.py also skips state writes; we
    layer both)."""
    text = _workflow_text()
    # The step header line is the anchor. The if: clause that follows
    # must mention test_output != 'true'.
    m = re.search(
        r"-\s*name:\s*Save persistent state back to state branch\s*\n"
        r"\s*if:\s*\$\{\{\s*([^}]+)\s*\}\}",
        text,
    )
    assert m is not None, (
        "Could not locate the 'Save persistent state' step's if: clause."
    )
    expr = m.group(1)
    assert "github.event.inputs.test_output != 'true'" in expr, (
        f"State-save step's gate does not exclude test_output runs. "
        f"Got: {expr!r}. Without this clause, test runs push transient "
        f"state to the state branch — defeating ROADMAP #23's "
        f"sandboxing intent."
    )
    # Defense-in-depth: dry_run gate must still be present.
    assert "github.event.inputs.dry_run != 'true'" in expr, (
        f"State-save step's gate dropped the dry_run guard. Got: "
        f"{expr!r}. Restore both clauses."
    )


def test_preserve_prod_step_exists_and_is_gated():
    """A workflow step must exist that preserves the live prod page
    in the artifact when test_output is true. The step's if: must
    include `inputs.test_output == 'true'` (positive gate)."""
    text = _workflow_text()
    # Match the step name flexibly — the test pins the gate, not the
    # exact wording. Anything mentioning 'preserve' + 'test' should
    # be unique enough for #23.
    m = re.search(
        r"-\s*name:\s*Preserve production page[^\n]*\n"
        r"(?:\s*#[^\n]*\n)*"
        r"\s*if:\s*\$\{\{\s*([^}]+)\s*\}\}",
        text,
    )
    assert m is not None, (
        "Could not locate the 'Preserve production page' step. "
        "ROADMAP #23 requires this step to curl the live prod page "
        "into the artifact before upload, otherwise actions/deploy-pages "
        "wipes Ellen's prod page on test runs."
    )
    expr = m.group(1)
    assert "github.event.inputs.test_output == 'true'" in expr, (
        f"Preserve-prod step is not gated on test_output=true. Got: "
        f"{expr!r}. The step must run ONLY in test mode — running it "
        f"on prod runs would overwrite the just-rendered docs/index.html "
        f"with the previous deploy's content."
    )


def test_workflow_dispatch_inputs_unchanged_for_existing_toggles():
    """Defense-in-depth: pin that the existing toggles (dry_run,
    intentional_failure, create_draft) still exist alongside the new
    test_output. A refactor that accidentally removed one of them
    would silently change the workflow's UI."""
    text = _workflow_text()
    for name in ("dry_run", "intentional_failure", "create_draft", "test_output"):
        assert re.search(rf"\b{name}:\s*\n\s*description:", text) is not None, (
            f"workflow_dispatch input {name!r} is missing. Don't remove "
            f"existing toggles when adding a new one."
        )
