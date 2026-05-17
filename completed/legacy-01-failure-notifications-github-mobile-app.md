# 1. Failure notifications via GitHub mobile app — c3d2e5b

Tom enables Actions push notifications for the repo in the GitHub mobile app. On the code side, verify `main.py` and the workflow exit non-zero on real failures (Gmail token expiry, Anthropic 5xx, unexpected exceptions) so the push actually fires. Add a small dry-run or intentional-failure path to confirm the notification arrives end-to-end.

Audited existing propagation paths (most were already correct). Removed the `except Exception: continue` in `agent.py::extract_events` so post-retry API failures propagate instead of silently returning `([], [])`; parse failures and filter-audit failures remain tolerant by design (see `design/failure-notifications.md`). Added `main.py --intentional-failure` plus a matching `intentional_failure` workflow_dispatch input. Tom verified the mobile push arrives when the intentional-failure run finishes.
