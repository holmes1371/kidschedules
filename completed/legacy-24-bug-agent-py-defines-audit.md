# 24. Bug: `agent.py` defines `AUDIT_SYSTEM_PROMPT` twice — 0ba31c9

`scripts/agent.py` declared `AUDIT_SYSTEM_PROMPT` twice — first at lines 209–239 with `keep_filtered` verdict labels, then again at lines 242–275 with `keep_blocked`. Python's last-assignment-wins rule meant the second block was the live one and the first was dead, but both were reachable to a reader and a well-meaning future edit to "the prompt" could have landed on the wrong copy.

**Fix.** Deleted the dead first block in 0ba31c9; the live `keep_blocked` prompt is the only definition. No behavior change — Python was already using the second block. `tests/test_agent.py` 66/66 stayed green, including the existing `test_review_stripped_messages_uses_audit_system_prompt` identity pin that locks the prompt to the import — sufficient coverage without adding a redundant unit test.

**Verification.** Tom confirmed live audit-flow behavior unchanged 2026-04-24.
