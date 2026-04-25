"""Pin the restore↔save file-set parity in weekly-schedule.yml.

The workflow persists seven files on the `state` branch between runs:
`events_state.json`, `prior_events.json`, `sender_stats.json`,
`.filter_audit.json`, `blocklist_auto.txt`, `blocklist_auto_audit.jsonl`,
`blocklist_auto_state.json` (#27 v1).

Drift risk: if one of those files gets added to the restore block but
not the save block (or vice versa), the live weekly cron silently
starts over each run for that slice of state, or worse — it runs with
stale data for a while before the drift is noticed. There is no
runtime signal; the pipeline still completes. Pinning both sides as
text parses against a canonical set makes the drift an unmergeable
test failure instead.

`future_events.json` is in the restore block as a one-time legacy
migration but NOT in the save block — it's being retired, not
persisted. The test knows about and allows this asymmetry.
"""
from __future__ import annotations

import re
from pathlib import Path


WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent
    / ".github" / "workflows" / "weekly-schedule.yml"
)


# Files that must appear on BOTH the restore and save sides of the
# weekly workflow. Keep this list aligned with what the scripts
# actually read/write.
PERSISTENT_STATE_FILES = frozenset({
    ".filter_audit.json",
    "blocklist_auto.txt",
    "blocklist_auto_audit.jsonl",
    "blocklist_auto_state.json",
    "events_state.json",
    "prior_events.json",
    "sender_stats.json",
})

# Files that appear only on the restore side (legacy / one-time).
RESTORE_ONLY_FILES = frozenset({
    "future_events.json",  # retired pre-cache event bank
})


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _restore_filenames() -> set[str]:
    """Collect every basename guarded by an `if [ -f .state/<name> ]`
    in the restore-from-state-branch block. Each such line is the
    workflow asking 'does the state branch carry this file?' — any
    answer it cares about must be matched by a save-side writer."""
    text = _workflow_text()
    return set(re.findall(r'\[\s*-f\s+\.state/([^\s\]]+)\s*\]', text))


def _save_filenames() -> set[str]:
    """Collect every basename listed in the save-to-state-branch
    block's `[ -f <name> ] && FILES="$FILES <name>"` lines."""
    text = _workflow_text()
    # Match the FILES= append lines specifically — other `[ -f ... ]`
    # conditionals (the early short-circuit `if [ ! -f ... ]` block)
    # are not the git-add set.
    pattern = r'\[\s*-f\s+([^\s\]]+)\s*\]\s*&&\s*FILES="\$FILES\s+([^"]+)"'
    matches = re.findall(pattern, text)
    # Both capture groups should be the same basename (the conditional
    # gate and the append value). Pin that too.
    out = set()
    for guard, appended in matches:
        assert guard == appended.strip(), (
            f"Save-side guard/append mismatch: `[ -f {guard} ]` vs "
            f'FILES="$FILES {appended}". One side drifted.'
        )
        out.add(guard)
    return out


def test_restore_block_covers_the_canonical_state_set():
    restore = _restore_filenames()
    missing = PERSISTENT_STATE_FILES - restore
    assert not missing, (
        f"Restore block is missing {sorted(missing)!r}. The scripts "
        f"read these files on every run; without a restore line each "
        f"run starts fresh for that slice of state."
    )


def test_save_block_covers_the_canonical_state_set():
    save = _save_filenames()
    missing = PERSISTENT_STATE_FILES - save
    assert not missing, (
        f"Save block is missing {sorted(missing)!r}. The scripts write "
        f"these files on every run; without a save line the updates "
        f"don't make it back to the state branch and the next run "
        f"sees stale data."
    )


def test_restore_and_save_sets_agree_on_persistent_files():
    """Symmetric parity: anything on one side is on the other, except
    the known RESTORE_ONLY_FILES legacy entries."""
    restore = _restore_filenames()
    save = _save_filenames()

    # Restore includes RESTORE_ONLY_FILES intentionally. Strip them
    # before comparing to save.
    restore_sans_legacy = restore - RESTORE_ONLY_FILES
    extra_restore_only = restore_sans_legacy - save
    extra_save_only = save - restore_sans_legacy

    assert extra_restore_only == set(), (
        f"Files restored but never saved: {sorted(extra_restore_only)!r}. "
        f"If these are legacy / one-time entries, add them to "
        f"RESTORE_ONLY_FILES in this test. Otherwise the save block "
        f"needs a matching writer line."
    )
    assert extra_save_only == set(), (
        f"Files saved but never restored: {sorted(extra_save_only)!r}. "
        f"A write without a corresponding restore means the next run "
        f"doesn't see the file — silent stale-state."
    )


def test_no_unexpected_files_in_persistent_state():
    """If a new file gets added to both blocks, the ROADMAP's 'Test
    coverage gaps' note — and the PERSISTENT_STATE_FILES list here
    — need a matching update. Fail loudly so the parity list stays
    in sync with the workflow."""
    known = PERSISTENT_STATE_FILES | RESTORE_ONLY_FILES
    restore = _restore_filenames()
    save = _save_filenames()
    extras = (restore | save) - known
    assert extras == set(), (
        f"Workflow persists unlisted state file(s): {sorted(extras)!r}. "
        f"Update PERSISTENT_STATE_FILES (or RESTORE_ONLY_FILES if it's "
        f"a legacy / one-time entry) in this test so the parity check "
        f"keeps meaning what it says."
    )
