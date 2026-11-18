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
