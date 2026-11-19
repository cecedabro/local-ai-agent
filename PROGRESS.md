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

## Task E09 - gather in chat/coder/research.py (2026-11-19)

Implemented `gather(plan, ...)` per SPEC §13.2 step 2. Runs PubMed queries and site/web searches, fetches pages, caps sources at MAX_SOURCES (10) and pages at PAGES_PER_SEARCH (2). Builds complete step dicts before appending to trail and calling on_step.
All `test_step_research::test_gather` and `test_gather_caps_sources` pass. `make check` passes.

## Task E10 - evidence_messages and public_sources in chat/coder/research.py (2026-11-19)

Implemented `evidence_messages(question, sources, cfg)` (SPEC §13.2 step 5) which selects the system message, appends system instructions (evidence rules), builds a history section with user/assistant messages, and injects source references (`_EVIDENCE_SRC`). Implemented `public_sources(sources)` to strip internal fields and return the `n/title/url` list. All `test_step_research` evidence tests pass. `make check` passes.

## Task E11 - run_evidence in chat/coder/research.py (2026-11-19)

Implemented `run_evidence(db, settings, chat, think, *, stream, complete, t, clock, stop)` (SPEC §13.2 step 6). Yields the full EvidenceHunt pipeline as SSE text: `phase` events (reading→thinking→searching→reading→summarising→writing), `trail` events for search steps, streaming `delta`/`thinking` events from the answer model, `verify` event with citation validation report, and a `done` event with message metadata, token counts, tps, context info, sources, and trail. Handles the no-sources fallback path with `NO_EVIDENCE` messages, Busy/GatewayError retries during streaming, stop propagation, and database storage via `add_message`, `update_chat_title`, and `update_chat_usage`. All `test_step_research` evidence tests pass. `make check` passes.
