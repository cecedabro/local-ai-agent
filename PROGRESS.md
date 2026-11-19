## Task E04 - check() in chat/coder/verify.py (2026-11-19)

Implemented the `check(answer, sources)` function per SPEC §13.1 docstring.
It validates quotes against source texts, flags uncited 8+ word claims (except last sentence with strength words), detects bad refs, and returns `{"ok", "quotes", "uncited", "bad_refs"}`.

All `test_step_verify` tests pass. `make check` passes.

## Task E05 - New message fields and user icon in chat/coder/db.py (2026-11-19)

Added `trail`, `verify`, `thinking`, `thought_s` columns to the `messages` table and `icon` column to the `users` table (with `init_schema` migrations).
Updated `add_message()` to accept and serialize these new fields, `get_messages()` to deserialize them, and added `update_user_icon()` method.
All `test_step_research` (db_fields), `test_step_profiles`, and `test_step_round2` tests pass. `make check` passes.

## Task E07 - Research helpers in chat/coder/research.py (2026-11-19)

Implemented `looks_dutch()` (detects Dutch by counting matched words against `_DUTCH` set), `enabled_sources()` (builds ordered list from cfg respecting `enabled` flag), `plan_messages()` (assembles system prompt with joined sources), and `label()` (formats phase label for pubmed/web/site:domain sources). Fixed `PLAN_PROMPT` brace conflict by switching from `.format()` to `.replace()` to avoid `KeyError` from embedded JSON braces.
All `test_step_research` helper tests pass. `make check` passes.
