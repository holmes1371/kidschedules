# 8. Bug: "Show ignored (N)" counter doesn't update mid-session — eb0236b

Unignore already decremented via `bumpToggle(-1)` on success, but Ignore had the mirror-image gap. Added `bumpToggle(1)` to the `ignore-btn` branch right after the local `setIgnored` + localStorage push, and `bumpToggle(-1)` in the POST-failure catch alongside the existing `setActive` + `saveIgnored` rollback. Zero-to-one creation (counter appearing when the page built with `ignored_n == 0`) is served by the `bumpToggle` rework in 7cf8cb3. 2 regression tests in `tests/test_process_events.py`.
