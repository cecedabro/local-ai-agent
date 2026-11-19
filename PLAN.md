# PLAN: Clear Source, rounds 2 and 3

Clear Source (the research profile, id `evidence`) gets better searches and answers (chat/SPEC.md §14): PubMed searches
use MeSH headings that the app checks against PubMed's MeSH database, every PubMed source gets its study type, the
sources are ordered guidelines first, the Dutch guideline sites are default sources (with a button to add them back),
and the trail shows the exact PubMed search. Round 3 (§15) is the research flow: understand the question, search
(broader when too little is found, PubMed Central full text), read every source on its own and keep only word-for-word
passages, a second round with new words when too little is useful, a checker that lets summaries be, and the sources page
grouped. Small steps: every task names one file and a few functions; the steps are
in their docstrings or TODO comments (`grep -n "task Fxx" <file>`). Each task has its own test.

Rules for every task:
- Non-interactive: nobody reads questions. Make the edit, run the accept command, fix what fails, stop.
- Only change what the task names. Don't refactor, rename or move other code. Remove the TODO comment you fill in.
- Never read a whole large file (`chat/coder/app.py` ~1050 lines, `chat/coder/tools.py` ~560): `grep -n`, then `sed -n 'a,bp'`.
- Python: only the packages in `chat/requirements.txt`.
- Do not touch tests, `chat/SPEC.md`, `chat/static/css/`, `chat/static/js/stream.js`, `chat/static/vendor/`,
  `chat/static/icons/`, `chat/coder/templates/`, `Makefile`, `docs/`, the constants `PLAN_PROMPT`, `RULES`, `RANKS`,
  `STUDY_TYPES`, `SEED`, `PUBMED_N`, `MIN_FOUND`, `MIN_USED`, `MAX_ROUNDS`, `MAX_PASSAGES`, the
  prompts (`*_PROMPT`), `SUMMARY_HEADINGS`, `GROUPS`, and in `chat/coder/tools.py` `looks_blocked` and `BOT_CHECK`, and in `chat/coder/tools.py` the functions `html_text`, `is_public` and `fetch_page`.

- [x] F01: In chat/coder/tools.py fill in `study_type` as its docstring says
  accept: make chat-test K="test_step_clear and study_type"
- [x] F02: In chat/coder/tools.py add MeSH to `pubmed_term` and `pubmed_search` (the two `TODO (task F02)` comments)
  accept: make chat-test K="test_step_clear and (pubmed_term_mesh or pubmed_search_mesh) or test_step_tools"
- [x] F03: In chat/coder/tools.py fill in `mesh_check` as its docstring says
  accept: make chat-test K="test_step_clear and mesh_check"
- [x] F04: In chat/coder/research.py do the `TODO (task F04)` comments in `parse_plan` and fill in `plan_about`
  accept: make chat-test K="parse_plan or plan_object"
- [x] F05: In chat/coder/research.py fill in `gather` as its docstring says
  accept: make chat-test K="gather"
- [x] F06: In chat/coder/research.py fill in `rank` and `order` as their docstrings say
  accept: make chat-test K="test_step_clear and order"
- [x] F08: In chat/coder/profiles.py fill in `missing_defaults` and in chat/coder/app.py add the route of `TODO (task F08)`
  accept: make chat-test K="missing_defaults or add_defaults"
- [ ] F09: In chat/static/js/profiles.js wire the #add-defaults button (`TODO (task F09)`)
  accept: node --check chat/static/js/profiles.js && make chat-test K="add_defaults"
- [ ] F11: In chat/coder/tools.py fill in `pmc_id` and `pmc_text` as their docstrings say
  accept: make chat-test K="test_step_flow and pmc"
- [ ] F12: In chat/coder/research.py fill in `extract_messages`, `parse_quotes` and `read_one` as their docstrings say
  accept: make chat-test K="test_step_flow and (extract or read_one)"
- [ ] F13: In chat/coder/research.py fill in `followup_messages` and do the `TODO (task F13)` comments (evidence_messages, public_sources)
  accept: make chat-test K="followup or public_sources or evidence_messages"
- [ ] F14: In chat/coder/research.py fill in `investigate` as its docstring says
  accept: make chat-test K="investigate"
- [ ] F15: In chat/coder/research.py do `TODO (task F15)` in `run_evidence` (run the research with `investigate`)
  accept: make chat-test K="test_step_research or test_step_clear or TestEvidenceV2 or mesh_in_a_real_answer"
- [ ] F16: In chat/coder/verify.py fill in `summary_sentences` and do `TODO (task F16)` in `check`
  accept: make chat-test K="test_step_verify or summary_sentences"
- [ ] F17: In chat/coder/profiles.py fill in `group_of` and `label_of` and do the `TODO (task F17)` comments (profiles.public, app.py _source_json)
  accept: make chat-test
- [ ] F18: In chat/static/js/profiles.js do the `TODO (task F18)` comments (a row per source, sources grouped)
  accept: node --check chat/static/js/profiles.js && make chat-test
- [ ] F19: Make the browser tests pass: run `make chat-browser-test`, read the FAIL line, fix only chat/coder/ or chat/static/js/{app,profiles,icons}.js (see "F19 details")
  accept: make chat-test && make chat-browser-test && node --test chat/tests/js/stream.test.mjs
- [ ] F20: Add a "## Clear Source, rounds 2 and 3" section to chat/README.md (see "F20 details")
  accept: grep -q "## Clear Source, rounds 2 and 3" chat/README.md && grep -q "read_one" chat/README.md && make chat-test

## F19 details
`make chat-browser-test` drives a real headless Chromium against the app (chat/tests/browser/run.mjs; serve.py fakes the
model, SearXNG, PubMed and MeSH). It prints one `ok`/`FAIL <name>: <reason>` line per check. Fix the first FAIL, run
again, repeat. Console errors on the page also fail a check: read them in the FAIL line.

## F20 details
Append to `chat/README.md` a section `## Clear Source, rounds 2 and 3` (8-12 lines): MeSH headings checked by
`tools.mesh_check` and combined with the words (`pubmed_term`); `tools.study_type` and `research.order` (guidelines first);
the Dutch guideline sites as default sources (+ the restore button); the flow in `research.investigate`: understand,
search (broader when too little, PubMed Central full text via `tools.pmc_text`, bot checks skipped), read each source on
its own (`read_one`: only word-for-word passages count), a second round with new words, write from the passages; the
checker lets the summary sections be (`verify.summary_sentences`); the sources page grouped (`profiles.group_of`).
