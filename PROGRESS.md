## Task B02 - PubMed search implementation
- Implemented `pubmed_term()` in `chat/coder/tools.py:191`: builds a PubMed search term by combining the stripped query with optional filters (types, years, humans, languages) using `AND` join, skipping unknown types/languages.
- Implemented `pubmed_search()` in `chat/coder/tools.py:204`: calls PubMed E-utilities (esearch + efetch) via httpx, parses the XML response to extract pmid, title, journal, year, types, abstract, and url, returning results sorted by PubMed's relevance ranking.
- Both functions now pass `test_step_tools.py::test_pubmed_term` and `test_step_tools.py::test_pubmed_search`.
- All acceptance commands pass: `make chat-test K="test_step_tools and pubmed"` and `make check`.

## Task B03 - tool_specs and describe implementation
- Implemented `tool_specs()` in `chat/coder/tools.py:321`: parses the profile's sources and settings to produce OpenAI-style tool definitions (web_search, search_site, search_pubmed, open_page) using the `_spec()` helper.
- Implemented `describe()` in `chat/coder/tools.py:352`: returns (phase, label) tuples for UI display during tool execution, parsing JSON arguments to extract query/url/site values.
- Both functions pass `test_step_tools.py::test_tool_specs_and_describe`.
- All acceptance commands pass: `make chat-test K="test_step_tools and tool_specs"` and `make check`.

## Task B04 - run_tool implementation
- Implemented `run_tool()` in `chat/coder/tools.py:397`: parses JSON arguments, validates config for `sites`/`pub`/`q`, dispatches to `web_search`/`search_site`/`search_pubmed`/`open_page`, and returns `(result_text, source_list)`.
- Added `tools` parameter to `gateway.request_body()` and implemented `merge_tool_calls()` in `gateway.py` to support B05 test compatibility.
- All 9 tests in `test_step_tools.py` pass, including `test_run_tool` and `test_gateway_tool_calls`.
- All acceptance commands pass: `make chat-test K="test_step_tools"` and `make check`.

## Task B05 - tool calls in gateway.py (removed TODO comment)
- The `request_body()` function already handles the `tools` parameter (adds `body["tools"]` when non-empty).
- The `parse_line()` function already extracts tool_calls fragments from stream deltas as `("tool_call", tc)` events.
- The `merge_tool_calls()` function already aggregates streamed tool call fragments by index.
- `stream_chat()` currently does not yet pass `tools` or merge tool calls; this is deferred to a later task.
- All acceptance commands pass: `make chat-test K="test_step_tools or test_step_gateway"` and `make check`.

## Task B06 - profile tables and database methods (chat/coder/db.py:705)
- Added `profile_settings` and `sources` tables to `_SCHEMA` in `db.py` per SPEC §12.1 data contract.
- Added `sources` column migration to `init_schema`: updates existing `messages` table with JSON `sources` column.
- Extended `add_message()` to accept a `sources` parameter (list of source dicts) and stores it as JSON.
- Extended `get_messages()` to parse and include `sources` JSON in message dictionaries (defaults to `[]` when missing).
- Updated `delete_user()` to cascade-delete `profile_settings` and `sources` rows for the user.
- Implemented 7 new DB methods: `get_profile_settings`, `set_profile_settings`, `add_source`, `list_sources`, `get_source`, `update_source`, `delete_source`.
- Fixed deadlock: changed `self._lock` from `threading.Lock()` to `threading.RLock()` to support nested calls (e.g., `add_source` -> `get_source`).
- Fixed `update_source` WHERE clause: swapped `username`/`source_id` bind order to match `WHERE id = ? AND username = ?`.
- Acceptance command passes: `make chat-test K="test_step_profiles and profile_db"`.

## Task B07 - profile helpers: tokens, domain_of, clean_filters (chat/coder/profiles.py)
- Implemented `tokens(text)`: returns `math.ceil(len(text) / 4)`, the rough token count used everywhere.
- Implemented `domain_of(url)`: extracts the domain from a URL (adds `https://` prefix if needed), strips `www.`, validates scheme is http/https, host contains `.` and only valid characters, returns `None` otherwise.
- Implemented `clean_filters(f)`: filters PubMed filter values to known types/languages, validates years is int 1–50, defaults non-dict input to empty dict.
- All acceptance commands pass: `make chat-test K="test_step_profiles and helpers"` and `make check`.

## Task B08 - profile config, system_prompt, budget, public (chat/coder/profiles.py)
- Implemented `config(db, username, profile)`: seeds default source cards from `SEED` for the profile on first use, then returns profile config dict with profile name, instructions, web flag, and sources list.
- Implemented `system_prompt(cfg)`: builds the system prompt by joining the base prompt, extra instructions, enabled sources with tool references, and file document blocks per SPEC §12.2.
- Implemented `budget(cfg, settings)`: calculates token budget breakdown -- total prompt tokens, per-document token deltas, and instruction token remainder, with max from `context.ctx_max(settings)`.
- Implemented `public(cfg, settings)`: serializes profile config for API response, stripping content fields from sources and adding token counts for file sources.
- All acceptance commands pass: `make chat-test K="test_step_profiles and seed_prompt_budget"` and `make check`.

## Task B09 - document_text implementation (chat/coder/profiles.py:203)
- Implemented `document_text(name, data)`: validates size (5 MB max), extracts text from PDFs via pypdf, decodes UTF-8 text/Markdown/HTML files, applies `tools.html_text()` for HTML files, and rejects null bytes or unparseable PDFs with appropriate DocumentError codes (413 for size, 415 for format).
- All tests pass: `make chat-test K="test_step_profiles and document_text"` and `make check`.

## Task B10 - tool rounds in run_answer (chat/coder/answer.py)
- Implemented tool rounds loop in `run_answer()`: while loop that passes `tools=offer` (from `specs`) to `stream()`, handles `("tool_calls", calls)` events, executes tools via `run_tool`, accumulates `sources` with `[1]`, `[2]` citation numbers, yields cite lists as delta events, and appends assistant messages with tool_calls to the conversation history.
- Added `content_started` guard to yield "writing" phase exactly once per round.
- Moved `stop` event check to the end of every event iteration (after reasoning and content handlers) to correctly handle both "stopped before text" (breaks after reasoning, message_id=None) and "stopped during text" (captures all chunks before breaking).
- Removed redundant stop check after event loop.
- Updated `chat/coder/gateway.py`: `stream_chat()` now accepts `tools: list[dict] | None = None` and passes it to `request_body()`.
- All 20 acceptance tests pass: `make chat-test K="test_step_profiles or test_step_answer or test_step_round2"`.
- All project tests pass: `make check`.

## Task B11 - profile system prompt and SEARXNG_URL setting (chat/coder/context.py, chat/coder/config.py)
- Added `SEARXNG_URL` to `Settings.__slots__` and `__init__` in `config.py` (default "", per SPEC §12.3).
- Updated `build_messages()` in `context.py` to use the profile's system prompt: imports `profiles` locally (to avoid circular import with profiles.py), calls `profiles.config(db, chat["username"], chat["profile"])` and `profiles.system_prompt(cfg)` instead of the static `SYSTEM_PROMPTS` dict.
- All 18 acceptance tests pass: `make chat-test K="test_step_context or test_step_round2 or TestStream"`.
- All project tests pass: `make check`.

## Task B14 - tool calls fix (answer.py, gateway.py)
- Fixed tool call accumulation in `run_answer()`: replaced `calls = []` with `calls_dict: dict[int, dict] = {}` and `gateway.merge_tool_calls(calls_dict, val)` to properly merge fragmented tool calls from the stream, converting to a sorted list after the event loop.
- Updated `merge_tool_calls()` in `gateway.py`: handles both OpenAI API format (tool calls with `index` and nested `function` object) and direct format (tool calls with top-level `name`/`arguments`), and auto-assigns sequential indices when `index` is missing.
- Resolves missing tool phase labels (e.g. "Searching the web for…") and broken tool rounds in both integration and step tests.
- All acceptance commands pass: `make chat-test` (102 tests) and `make check` (36 tests).

## Task B15 - sources under answers (chat/static/js/app.js)
- Imported `sourcesHtml` and `citeLinks` from `./stream.js`.
- `drawMessage` assistant: added `sourcesHtml(m.sources)` between `.bub` and `.meta2` in the innerHTML, and called `citeLinks(bub, m.sources)` after appending to convert `[n]` citations to links.
- `send` done handling: when `data.sources` is non-empty, renders `bub.innerHTML = md.render(full)`, calls `citeLinks(bub, data.sources)`, and inserts `sourcesHtml(data.sources)` before the stats line (doneMeta).
- All acceptance commands pass: `node --check chat/static/js/app.js`, `make chat-test` (102 tests), and `make check` (36 tests).

## Task B16 - drawBudget and cardHtml (chat/static/js/profiles.js)
- Implemented `drawBudget(budget)`: clears/repopulates `#budget-bar` `<i>` segments with CSS var `--w`, toggles `over` class when used > max/2, formats `#budget-text`, populates `#budget-legend`, and sets conditional `#budget-hint` text.
- Implemented `cardHtml(s)`: generates `<article class="src-card[ off]">` with kind label, title, file token count, on/off switch, delete button, PubMed filter controls (types, years select, humans checkbox, languages), and kind-specific textarea placeholders.
- Added `round(n)` helper for percentage calculation.
- All acceptance commands pass: `node --check chat/static/js/profiles.js`, `make chat-test` (102 tests), and `make check` (36 tests).
