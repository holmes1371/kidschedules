# 2. Pytest suite for `scripts/process_events.py` — 8375e9c (suite) / 8a9f4b3 (CI)

Cover current behavior: past-event filtering, dedupe, sort order, week grouping, event-ID stability (12-char sha1), HTML body rendering, subject-line construction. Use fixture JSON inputs under `fixtures/`; prefer string-equality or snapshot assertions over structural asserts. Wire into a GitHub Actions check so regressions fail the workflow. Foundational — every subsequent feature extends the fixture set.

26 tests in `tests/test_process_events.py`. Fixtures under `fixtures/test/`, body snapshot under `tests/snapshots/`. CI in `.github/workflows/tests.yml` runs on push + PR. Design note at `design/pytest-suite.md`. Session-discipline block updated to point at `tests/` and note that a red test check blocks merge.
