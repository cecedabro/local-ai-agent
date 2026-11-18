# PLAN: Dewdrop profiles and sources

Round 3 (chat/SPEC.md §12): each user edits their profiles (instructions, web on/off, source cards with
notes: PubMed with filters, websites, documents) and sees how much context they take; the model can
search the web (SearXNG), PubMed and the profile's websites, open pages, and cites numbered sources.
Small steps: every task names one file and one to three functions; the steps are in that function's
docstring or TODO comment (`grep -n "task Bxx" <file>`). Each task has its own test.

Rules for every task:
- Non-interactive: nobody reads questions. Make the edit, run the accept command, fix what fails, stop.
- Only change what the task names. Don't refactor, rename or move other code.
- Never read a whole large file (`chat/coder/app.py` is ~900 lines): `grep -n`, then `sed -n 'a,bp'`.
- Python: only the packages in `chat/requirements.txt` (pypdf reads PDFs, httpx does HTTP).
- Do not touch tests, `chat/SPEC.md`, `chat/static/css/`, `chat/static/js/stream.js`, `chat/static/vendor/`,
  `chat/coder/templates/`, `Makefile`, `docs/`, and in `chat/coder/tools.py` the finished functions
  `html_text`, `is_public` and `fetch_page` (they guard against private addresses).

- [x] B01: In chat/coder/tools.py fill in `web_search` as its docstring says
  accept: make chat-test K="test_step_tools and web_search"
- [x] B02: In chat/coder/tools.py fill in `pubmed_term` and `pubmed_search` as their docstrings say
  accept: make chat-test K="test_step_tools and pubmed"
- [x] B03: In chat/coder/tools.py fill in `tool_specs` and `describe` as their docstrings say
  accept: make chat-test K="test_step_tools and tool_specs"
- [x] B04: In chat/coder/tools.py fill in `run_tool` as its docstring says
  accept: make chat-test K="test_step_tools"
- [x] B05: In chat/coder/gateway.py add tool calls (`TODO (task B05)`)
  accept: make chat-test K="test_step_tools or test_step_gateway"
- [x] B06: In chat/coder/db.py add the profile tables and methods (`TODO (task B06)`)
  accept: make chat-test K="test_step_profiles and profile_db or test_step_round2"
- [ ] B07: In chat/coder/profiles.py fill in `tokens`, `domain_of` and `clean_filters` as their docstrings say
  accept: make chat-test K="test_step_profiles and helpers"
- [ ] B08: In chat/coder/profiles.py fill in `config`, `system_prompt`, `budget` and `public` as their docstrings say
  accept: make chat-test K="test_step_profiles and seed_prompt_budget"
- [ ] B09: In chat/coder/profiles.py fill in `document_text` as its docstring says
  accept: make chat-test K="test_step_profiles and document_text"
- [ ] B10: In chat/coder/answer.py add tool rounds to `run_answer` (`TODO (task B10)`, above the function)
  accept: make chat-test K="test_step_profiles or test_step_answer or test_step_round2"
- [ ] B11: In chat/coder/context.py use the profile's prompt (`TODO (task B11)`) and in chat/coder/config.py add SEARXNG_URL (`TODO (task B11)`)
  accept: make chat-test K="test_step_context or test_step_round2 or TestStream"
- [ ] B12: In chat/coder/app.py add the profile routes and the /profiles page (`TODO (task B12)`)
  accept: make chat-test K="TestProfileApi and (seeds or rights or settings or profiles_page)"
- [ ] B13: In chat/coder/app.py add the source card routes (`TODO (task B13)`)
  accept: make chat-test K="TestProfileApi and sources"
- [ ] B14: In chat/coder/app.py add the documents route (`TODO (task B14)`)
  accept: make chat-test
- [ ] B15: In chat/static/js/app.js show sources under answers (`TODO (task B15)`)
  accept: node --check chat/static/js/app.js && make chat-test
- [ ] B16: In chat/static/js/profiles.js fill in `drawBudget` and `cardHtml` as their comments say
  accept: node --check chat/static/js/profiles.js && make chat-test
- [ ] B17: In chat/static/js/profiles.js fill in `start` as its comment says
  accept: node --check chat/static/js/profiles.js && make chat-test
- [ ] B18: Make the browser tests pass: run `make chat-browser-test`, read the FAIL line, fix only chat/coder/ or chat/static/js/app.js or chat/static/js/profiles.js (see "B18 details")
  accept: make chat-test && make chat-browser-test && node --test chat/tests/js/stream.test.mjs
- [ ] B19: Add a "## Profiles and sources" section to chat/README.md (see "B19 details")
  accept: grep -q "## Profiles and sources" chat/README.md && grep -q "SEARXNG_URL" chat/README.md && make chat-test

## B18 details
`make chat-browser-test` drives a real headless Chromium against the app (chat/tests/browser/run.mjs;
fake model and fake SearXNG in serve.py: a message with "search" makes the model call web_search
first). It prints one `ok`/`FAIL <name>: <reason>` line per check. Fix the first FAIL, run again,
repeat. Console errors on the page also fail a check: read them in the FAIL line.

## B19 details
Append to `chat/README.md` a section `## Profiles and sources` (6–10 lines): the profiles page
(`/profiles`, also from the profile menu), instructions, web on/off, the source cards (PubMed with
filters, websites, documents up to 5 MB) and their notes, the context bar (keep a profile under
half), numbered sources under answers, and that web search needs the setting `SEARXNG_URL` (the
private SearXNG in the cluster; empty = the web tools answer an error).
