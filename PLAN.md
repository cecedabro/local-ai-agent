# PLAN: Clear Source, round 2

Clear Source (the research profile, id `evidence`) gets better searches and answers (chat/SPEC.md §14): PubMed searches
use MeSH headings that the app checks against PubMed's MeSH database, every PubMed source gets its study type, the
sources are ordered guidelines first, the Dutch guideline sites are default sources (with a button to add them back),
and the trail shows the exact PubMed search. Small steps: every task names one file and a few functions; the steps are
in their docstrings or TODO comments (`grep -n "task Fxx" <file>`). Each task has its own test.

Rules for every task:
- Non-interactive: nobody reads questions. Make the edit, run the accept command, fix what fails, stop.
- Only change what the task names. Don't refactor, rename or move other code. Remove the TODO comment you fill in.
- Never read a whole large file (`chat/coder/app.py` ~1050 lines, `chat/coder/tools.py` ~560): `grep -n`, then `sed -n 'a,bp'`.
- Python: only the packages in `chat/requirements.txt`.
- Do not touch tests, `chat/SPEC.md`, `chat/static/css/`, `chat/static/js/stream.js`, `chat/static/vendor/`,
  `chat/static/icons/`, `chat/coder/templates/`, `Makefile`, `docs/`, the constants `PLAN_PROMPT`, `RULES`, `RANKS`,
  `STUDY_TYPES` and `SEED`, and in `chat/coder/tools.py` the functions `html_text`, `is_public` and `fetch_page`.

- [x] F01: In chat/coder/tools.py fill in `study_type` as its docstring says
  accept: make chat-test K="test_step_clear and study_type"
- [ ] F02: In chat/coder/tools.py add MeSH to `pubmed_term` and `pubmed_search` (the two `TODO (task F02)` comments)
  accept: make chat-test K="test_step_clear and (pubmed_term_mesh or pubmed_search_mesh) or test_step_tools"
- [ ] F03: In chat/coder/tools.py fill in `mesh_check` as its docstring says
  accept: make chat-test K="test_step_clear and mesh_check"
- [ ] F04: In chat/coder/research.py keep the MeSH headings in `parse_plan` (`TODO (task F04)`)
  accept: make chat-test K="parse_plan"
- [ ] F05: In chat/coder/research.py rewrite `gather` as its docstring says
  accept: make chat-test K="gather"
- [ ] F06: In chat/coder/research.py fill in `rank` and `order` as their docstrings say
  accept: make chat-test K="test_step_clear and order"
- [ ] F07: In chat/coder/research.py do the three `TODO (task F07)` comments (public_sources and run_evidence)
  accept: make chat-test K="test_step_clear and not seed or test_step_research or TestEvidenceV2"
- [ ] F08: In chat/coder/profiles.py fill in `missing_defaults` and in chat/coder/app.py add the route of `TODO (task F08)`
  accept: make chat-test
- [ ] F09: In chat/static/js/profiles.js wire the #add-defaults button (`TODO (task F09)`)
  accept: node --check chat/static/js/profiles.js && make chat-test
- [ ] F10: Make the browser tests pass: run `make chat-browser-test`, read the FAIL line, fix only chat/coder/ or chat/static/js/{app,profiles,icons}.js (see "F10 details")
  accept: make chat-test && make chat-browser-test && node --test chat/tests/js/stream.test.mjs
- [ ] F11: Add a "## Clear Source, round 2" section to chat/README.md (see "F11 details")
  accept: grep -q "## Clear Source, round 2" chat/README.md && grep -q "mesh_check" chat/README.md && make chat-test

## F10 details
`make chat-browser-test` drives a real headless Chromium against the app (chat/tests/browser/run.mjs; serve.py fakes the
model, SearXNG, PubMed and MeSH). It prints one `ok`/`FAIL <name>: <reason>` line per check. Fix the first FAIL, run
again, repeat. Console errors on the page also fail a check: read them in the FAIL line.

## F11 details
Append to `chat/README.md` a section `## Clear Source, round 2` (6-10 lines): MeSH headings from the plan are checked
by `tools.mesh_check` against PubMed's MeSH database and combined with the words (`pubmed_term`); the trail shows the
headings and links to the exact PubMed search; `tools.study_type` labels every PubMed source; `research.order` puts the
guideline sites first, then the strongest studies; the Dutch guideline sites are default sources and the profiles page
can add missing ones back; answers are always detailed, under headings, built from quotes.
