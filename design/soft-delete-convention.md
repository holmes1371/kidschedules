# Soft-delete convention

This repo sits on a FUSE mount (virtiofs over a Windows OneDrive path) whose semantics refuse `unlink` but permit `rename`. Running `rm` on any file returns `Operation not permitted`, even under `dangerouslyDisableSandbox` — the restriction is filesystem-level, not sandbox-level. `mv` works normally. Agents that try to delete files fail; agents that rename files succeed.

Under that constraint, this project uses a **soft-delete convention**: when an agent needs to discard a file, it `mv`s the file into `.to_delete/` at the repo root. Tom empties `.to_delete/` manually from Windows when convenient. `.to_delete/` exists in the tree (kept alive by `.to_delete/.gitkeep`); everything else under it is `.gitignore`d.

## The rule

**Never `rm`. Always `mv` into `.to_delete/`.** Include a timestamp and short tag in the destination name so the folder stays browseable. Example:

```
mv .git/index.lock .to_delete/git-index-lock-$(date +%Y%m%d-%H%M%S)
mv stale-draft.md   .to_delete/stale-draft-$(date +%Y%m%d-%H%M%S).md
```

If an operation fails with `Operation not permitted` during an unlink, the cause is always this FUSE restriction. Do not ask for sandbox-disable — it does not help. Soft-delete is the only workaround.

## When git leaves lock files behind

Every git operation briefly creates `.git/index.lock`, `.git/HEAD.lock`, or temp objects like `.git/objects/ab/tmp_obj_XXXXXX`. Git tries to delete each when its operation completes. On this mount those unlink calls silently fail, but the op itself succeeds — the lock / temp file just persists.

**Cosmetic warnings** — if the git output shows lines like:

```
warning: unable to unlink '.git/index.lock': Operation not permitted
warning: unable to unlink '.git/objects/c8/tmp_obj_TMDhPW': Operation not permitted
```

after a commit that otherwise reports `[main 70c172a] ...`, the commit landed. Ignore the warnings, move on.

**Stale locks blocking the next op** — if the next git command fails with `fatal: Unable to create '.git/index.lock': File exists`, the persistent lock from the prior op is blocking you. Recover with:

```
mv /sessions/vibrant-funny-ride/mnt/kids-schedule-github/.git/index.lock \
   /sessions/vibrant-funny-ride/mnt/kids-schedule-github/.to_delete/git-index-lock-$(date +%Y%m%d-%H%M%S)
```

Then retry. Same pattern for `.git/HEAD.lock`.

`.git/objects/*/tmp_obj_*` files can be left in place — they don't block subsequent operations, they just accumulate. Sweep them into `.to_delete/` if they grow excessive.

## When the git index gets corrupted

If a git operation is interrupted hard enough, `.git/index` itself can end up with a zero-filled header (`bad signature 0x00000000`). Symptom: `git status` returns `error: bad signature 0x00000000 / fatal: index file corrupt`. The working tree is unaffected — only the staging area index is lost.

Recovery ritual:

1. `mv .git/index .to_delete/git-index-corrupt-$(date +%Y%m%d-%H%M%S)` — move the broken index aside. Git will recreate a fresh empty index on next operation.
2. `mv .git/index.lock .to_delete/...` and `mv .git/HEAD.lock .to_delete/...` if either exists — otherwise step 3 will fail on the ref lock.
3. `git reset HEAD` — rebuilds the index from HEAD's tree. Unstaged working-tree changes are reported as modifications, which is correct: they survive the reset.
4. Re-stage and retry the commit.

`.git/index.corrupt` / `.git/HEAD.lock.stale` / `.git/index.lock.stale*` left behind from past recoveries are harmless — git doesn't look at them — but tidier to `mv` them into `.to_delete/` too so the `.git/` tree stays clean.

## Scope

The convention applies to *every* file the agent might want to discard: stale locks, one-off scratch files in the working tree, superseded draft design notes, abandoned scripts, anything. Single rule, one folder, no exceptions. Tom's only maintenance task is periodically emptying `.to_delete/` from File Explorer.

## What the agent must not do

- Do not `rm` anything, even with `dangerouslyDisableSandbox`. It will fail, wasting a round trip.
- Do not ask the user to manually delete a file that the agent could have soft-deleted itself.
- Do not leave corrupt-index / stale-lock detritus lying around in `.git/` when the fix is a two-line `mv` dance.
- Do not commit anything from `.to_delete/` — the `.gitignore` rule protects the tree, but don't fight it with `git add -f`.

## Why no cleanup script

A `scripts/empty_to_delete.py` was considered and rejected. The agent can't run it (same FUSE restriction — the script would have to `rm`, which fails). Tom can trivially empty the folder from Windows File Explorer, where unlink works. The convention is asymmetric by design: the agent populates `.to_delete/`, the human empties it. Keep it that way.
