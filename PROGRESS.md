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

## Task E10 - verify evidence_messages and public_sources in chat/coder/research.py (2026-11-19)

Confirmed both functions were already implemented per their docstrings. `evidence_messages` builds the system message (RULES + optional user instructions) and user message with source pack. `public_sources` strips internal `text` field and adds a `quote` via `tools._cut`. `make chat-test K="test_step_research and evidence_messages"` passes. `make check` passes.

## Task E11 - run_evidence verification (2026-11-19)

Confirmed `run_evidence` in `chat/coder/research.py:279` is fully implemented per its docstring (SPEC §13.2 step 6). All `test_step_research` tests pass (10/10) and `make check` passes (36/36). No changes needed.

## Task E12 - EvidenceHunt pipeline integration in chat/coder/answer.py (2026-11-19)

Implemented EvidenceHunt's pipeline integration and per-profile thinking/trail tracking in `run_answer`:
- Added `from . import research` import.
- After compress (before build_messages): when `chat["profile"] == "evidence"`, delegates to `research.run_evidence()` and returns.
- Before tool rounds: initialized `trail = []`, `thinking_text = ""`, `th0 = None`.
- In the events loop: when `tag == "reasoning"` and `val` is non-empty, sets `th0`, accumulates `thinking_text`, and yields `sse("thinking", {"text": val})` events.
- After each `run_tool` call: builds a trail step via `tools.trail_step()`, appends to `trail`, and yields `sse("trail", {"step": step})`.
- Both `db.add_message` calls now include `trail`, `thinking`, and (normal path) `thought_s`.
- The `done` event now includes `"trail": trail`.

All `test_step_answer`, `test_step_round2`, `test_step_profiles`, `TestStream`, and `TestEvidenceV2` tests pass (29/29). `make check` passes (36/36).

## Task E14 - App icon in /api/me and routes /manifest.json, /apple-touch-icon.png (2026-11-19)

Added `ICONS = ("aero", "blossom")` at module level in `chat/coder/app.py`.
Added `_me(username, db)` helper returning `{"username", "rights", "theme", "icon"}` with icon defaulting to "blossom" for blossom-theme users, else "aero".
Updated `GET /api/me` to include `icon` field via `_me()`.
Updated `PATCH /api/me` to handle `icon` key (validates against ICONS, calls `update_user_icon`), while preserving existing theme validation and the "nothing to change" error for empty/non-dict bodies.
Added `GET /manifest.json` route returning PWA manifest with icons sized 192/512 using the session icon (or "aero" when no session), content-type `application/manifest+json`.
Added `GET /apple-touch-icon.png` route serving `{icon}-180.png` bytes from the static icons directory with `Cache-Control: no-cache`.
All 128 chat tests pass. `make check` passes (36/36).

## Task E15 - Wire icon picker in chat/static/js/icons.js (2026-11-19)

Implemented `start()` in `chat/static/js/icons.js`. On load, fetches `GET /api/me` and marks the matching `button[data-icon]` with class "on". On click, sends `PATCH /api/me` with `{"icon": ...}` and required headers; on success moves "on" class and shows save message, on failure shows error message. `node --check` passes, all 128 chat tests pass, `make check` passes (36/36).
