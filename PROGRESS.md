## Task E04 - check() in chat/coder/verify.py (2026-11-19)

Implemented the `check(answer, sources)` function per SPEC §13.1 docstring.
It validates quotes against source texts, flags uncited 8+ word claims (except last sentence with strength words), detects bad refs, and returns `{"ok", "quotes", "uncited", "bad_refs"}`.

All `test_step_verify` tests pass. `make check` passes.
