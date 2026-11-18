# PLAN: Dewdrop round 2

Chat fixes and folders (chat/SPEC.md §11): the reading phase, every chunk on screen, a stop button,
folders in the sidebar, coloured code you can download, and a page that keeps your place while you
read. Small steps: every task names one file and one or two functions; the steps are in that
function's docstring or TODO comment (`grep -n "task Rxx" <file>`). Each task has its own test.

Rules for every task:
- Non-interactive: nobody reads questions. Make the edit, run the accept command, fix what fails, stop.
- Only change what the task names. Don't refactor, rename or move other code.
- Never read a whole large file (`chat/coder/app.py` is ~800 lines, `chat/static/js/app.js` ~450):
  `grep -n`, then `sed -n 'a,bp'`.
- Python: only the packages in `chat/requirements.txt`.
- Do not touch tests, `chat/SPEC.md`, `chat/static/css/`, `chat/static/js/stream.js`, `chat/static/vendor/`,
  `chat/coder/templates/`, `Makefile`, `docs/`. The look, the templates and stream.js are already done.

- [x] R01: In chat/coder/db.py add folders, the folder_id and stopped columns (with the migration) as the `TODO (task R01)` comment says
  accept: make chat-test K="test_step_round2 and (folders_db or migration)"
- [x] R02: In chat/coder/context.py add the file-name sentence to the General prompt (`TODO (task R02)`)
  accept: make chat-test K="test_step_round2 and prompt_names_files"
- [x] R03: In chat/coder/answer.py do `TODO (task R03)` in the `run_answer` docstring (reading phase, every chunk as a delta, title in done)
  accept: make chat-test K="test_step_answer or (test_step_round2 and reading_phase)"
- [x] R04: In chat/coder/answer.py do `TODO (task R04)` in the `run_answer` docstring (the stop event)
  accept: make chat-test K="test_step_answer or (test_step_round2 and stop_event)"
- [x] R05: In chat/coder/answer.py do `TODO (task R05)` in the `run_answer` docstring (store the partial answer when the browser goes away)
  accept: make chat-test K="test_step_answer or test_step_round2"
- [x] R06: In chat/coder/app.py rewrite `api_update_chat` as its docstring says (`TODO (task R06)`)
  accept: make chat-test K="TestFolders and move_chats or test_crud_and_isolation"
- [x] R07: In chat/coder/app.py add the four folder routes (`TODO (task R07)`)
  accept: make chat-test K="TestFolders"
- [x] R08: In chat/coder/app.py add the stop route and the running map (`TODO (task R08)`)
  accept: make chat-test K="TestRound2Answer and (stop_endpoint or done_has_title)"
- [x] R09: In chat/coder/app.py fix the file block in `api_create_message` (`TODO (task R09)`)
  accept: make chat-test
- [x] R10: In chat/static/js/app.js fill in `convHtml` and `loadChats` (`TODO (task R10)`)
  accept: node --check chat/static/js/app.js && make chat-test
- [x] R11: In chat/static/js/app.js fill in `doneMeta` and `drawMessage` (`TODO (task R11)`)
  accept: node --check chat/static/js/app.js && make chat-test
- [x] R12: In chat/static/js/app.js fill in `setBusy`, `stopAnswer` and `send` (`TODO (task R12)`)
  accept: node --check chat/static/js/app.js && make chat-test
- [x] R13: In chat/static/js/app.js fill in `moveChat`, `newFolder`, `openMenu`, `chatMenu`, `folderMenu` and `menuAction` (`TODO (task R13)`)
  accept: node --check chat/static/js/app.js && make chat-test
- [x] R14: In chat/static/js/app.js fill in `setupSidebar` (`TODO (task R14)`)
  accept: node --check chat/static/js/app.js && make chat-test
- [x] R15: Make the browser tests pass: run `make chat-browser-test`, read the FAIL line, fix only chat/static/js/app.js or chat/coder/ (see "R15 details")
  accept: make chat-test && make chat-browser-test && node --test chat/tests/js/stream.test.mjs
- [x] R16: Add a "## Round 2" section to chat/README.md (see "R16 details")
  accept: grep -q "## Round 2" chat/README.md && grep -q "folders" chat/README.md && make chat-test

## R15 details
`make chat-browser-test` drives a real headless Chromium against the app (chat/tests/browser/run.mjs,
fake model in serve.py: a message with "long" gets a long answer, "html" gets a code file). It prints
one `ok`/`FAIL <name>: <reason>` line per check. Fix the first FAIL, run again, repeat. The checks
look at the hooks in chat/SPEC.md §11.5; the reference markup is in the TODO docstrings of app.js.
Console errors on the page also fail a check: read them in the FAIL line.

## R16 details
Append to `chat/README.md` a section `## Round 2` (5–8 lines): the reading phase, the stop button
(and that the partial answer is kept), folders (the ⋯ menu, drag a chat onto a folder), coloured
code blocks with Copy and Download (the model puts the file name on the fence line), and that the
page keeps your place when you scroll up while it writes ("↓ Latest" brings you back).
