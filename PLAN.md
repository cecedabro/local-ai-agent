# PLAN: EvidenceHunt v2

The model is never trusted to stay within the evidence; the app enforces it (chat/SPEC.md §13): a checker that
finds every quote word for word in its source, research done by the app (the model only plans the searches and
writes from numbered excerpts, "nothing found" means no answer), the search trail and the thinking block for every
profile, PubMed strictness (design C) and the app icon picker. Small steps: every task names one file and a few
functions; the steps are in their docstrings or TODO comments (`grep -n "task Exx" <file>`). Each task has its own test.

Rules for every task:
- Non-interactive: nobody reads questions. Make the edit, run the accept command, fix what fails, stop.
- Only change what the task names. Don't refactor, rename or move other code.
- Never read a whole large file (`chat/coder/app.py` ~1000 lines, `chat/static/js/app.js` ~700): `grep -n`, then `sed -n 'a,bp'`.
- Python: only the packages in `chat/requirements.txt`.
- Do not touch tests, `chat/SPEC.md`, `chat/static/css/`, `chat/static/js/stream.js`, `chat/static/vendor/`,
  `chat/static/icons/`, `chat/coder/templates/`, `Makefile`, `docs/`, and in `chat/coder/tools.py` the functions
  `html_text`, `is_public` and `fetch_page`.

- [x] E01: In chat/coder/verify.py fill in `normalize` as its docstring says
  accept: make chat-test K="test_step_verify and normalize"
- [x] E02: In chat/coder/verify.py fill in `find_quote` as its docstring says
  accept: make chat-test K="test_step_verify and find_quote"
- [x] E03: In chat/coder/verify.py fill in `cites_in`, `_strip_marks`, `quotes` and `sentences` as their docstrings say
  accept: make chat-test K="test_step_verify and (quotes or sentences)"
- [x] E04: In chat/coder/verify.py fill in `check` as its docstring says
  accept: make chat-test K="test_step_verify"
- [x] E05: In chat/coder/db.py add the new message fields and the user icon (`TODO (task E05)`)
  accept: make chat-test K="test_step_research and db_fields or test_step_profiles or test_step_round2"
- [x] E06: In chat/coder/tools.py fill in `trail_step` as its docstring says
  accept: make chat-test K="test_step_research and trail_step"
- [x] E07: In chat/coder/research.py fill in `looks_dutch`, `enabled_sources`, `plan_messages` and `label` as their docstrings say
  accept: make chat-test K="test_step_research and helpers"
- [x] E08: In chat/coder/research.py fill in `parse_plan` as its docstring says
  accept: make chat-test K="test_step_research and parse_plan"
- [x] E09: In chat/coder/research.py fill in `gather` as its docstring says
  accept: make chat-test K="test_step_research and gather"
- [x] E10: In chat/coder/research.py fill in `evidence_messages` and `public_sources` as their docstrings say
  accept: make chat-test K="test_step_research and evidence_messages"
- [x] E11: In chat/coder/research.py fill in `run_evidence` as its docstring says
  accept: make chat-test K="test_step_research"
- [x] E12: In chat/coder/answer.py do `TODO (task E12)` (EvidenceHunt's pipeline; thinking and trail for every profile)
  accept: make chat-test K="test_step_answer or test_step_round2 or test_step_profiles or TestStream or TestEvidenceV2"
- [x] E13: In chat/coder/profiles.py add PubMed strictness and in chat/coder/app.py clean the filters of a source PATCH (`TODO (task E13)`)
  accept: make chat-test K="test_presets or TestProfileApi"
- [x] E14: In chat/coder/app.py add the app icon to /api/me and the routes /manifest.json and /apple-touch-icon.png (`TODO (task E14)`)
  accept: make chat-test
- [x] E15: In chat/static/js/icons.js fill in `start` as its comment says
  accept: node --check chat/static/js/icons.js && make chat-test
- [ ] E16: In chat/static/js/app.js show thinking, the search trail and the checker's marks (`TODO (task E16)`)
  accept: node --check chat/static/js/app.js && make chat-test && node --test chat/tests/js/stream.test.mjs
- [ ] E17: In chat/static/js/profiles.js add PubMed strictness (design C) and fix the on/off switch (`TODO (task E17)`)
  accept: node --check chat/static/js/profiles.js && make chat-test
- [ ] E18: Make the browser tests pass: run `make chat-browser-test`, read the FAIL line, fix only chat/coder/ or chat/static/js/{app,profiles,icons}.js (see "E18 details")
  accept: make chat-test && make chat-browser-test && node --test chat/tests/js/stream.test.mjs
- [ ] E19: Add a "## EvidenceHunt v2" section to chat/README.md (see "E19 details")
  accept: grep -q "## EvidenceHunt v2" chat/README.md && grep -q "verify.py" chat/README.md && make chat-test

## E18 details
`make chat-browser-test` drives a real headless Chromium against the app (chat/tests/browser/run.mjs; serve.py fakes the
model, SearXNG and PubMed: an EvidenceHunt question gets one real quote and one made-up quote). It prints one
`ok`/`FAIL <name>: <reason>` line per check. Fix the first FAIL, run again, repeat. Console errors on the page also
fail a check: read them in the FAIL line.

## E19 details
Append to `chat/README.md` a section `## EvidenceHunt v2` (6-10 lines): the app does the research (the model only plans
the searches, `chat/coder/research.py`), nothing found means no answer, the checker (`chat/coder/verify.py`) finds every
quote word for word in its source and marks it ✓ or ⚠ in the page, sentences without a source are listed, the search
trail and the thinking block, PubMed strictness (Strict / Balanced / Broad), and the app icon picker on the account page.
