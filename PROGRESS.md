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
