# PLAN: Chat app foundation

Small steps. Every task names ONE file and ONE or two functions; the steps are in that function's
docstring or comment (marked `TODO (task Sxx)`). Find it with `grep -n "task Sxx" <file>` and read
only that function. Each task has its own small test.

Rules for every task:
- Non-interactive: nobody reads questions. Make the edit, run the accept command, fix what fails, stop.
- Only change the function(s) the task names. Don't refactor, rename or move other code.
- Never read a whole large file: `grep -n`, then `sed -n 'a,bp' <file>` for the lines you need.
- Python: only the packages in `chat/requirements.txt` (httpx, not requests). JS: no packages.
- Do not touch tests, `chat/SPEC.md`, `chat/static/css/`, `chat/coder/templates/`, `Makefile`, `docs/`.

Done earlier (T01–T04, T07): settings, database, login, sessions, invites, users, chats, files.
The app is called Dewdrop. `chat/static/js/stream.js` (the writing engine) is done and human-owned: use it, don't edit it.

- [x] S01: In chat/coder/db.py add `add_message_at` and `reset_chat_usage`, and order messages by created then id (`grep -n "task S01" chat/coder/db.py`)
  accept: make chat-test K="test_step_context and test_db_helpers"
- [x] S02: In chat/coder/gateway.py fill in `request_body` and `parse_line` as their docstrings say
  accept: make chat-test K="test_step_gateway and (test_request_body or test_parse_line)"
- [x] S03: In chat/coder/gateway.py fill in `stream_chat` as its docstring says
  accept: make chat-test K="test_step_gateway and (test_stream_chat or test_no_key_no_header)"
- [ ] S04: In chat/coder/gateway.py fill in `complete` as its docstring says
  accept: make chat-test K="test_step_gateway"
- [ ] S05: In chat/coder/context.py fill in `ctx_max` and `window` as their docstrings say
  accept: make chat-test K="test_step_context and test_ctx_max_and_cache"
- [ ] S06: In chat/coder/context.py fill in `context_messages` and `build_messages` as their docstrings say
  accept: make chat-test K="test_step_context and test_context_and_build_messages"
- [ ] S07: In chat/coder/context.py fill in `used` and `info` as their docstrings say
  accept: make chat-test K="test_step_context and test_used_and_info"
- [ ] S08: In chat/coder/context.py fill in `just_before` and `compress` as their docstrings say
  accept: make chat-test K="test_step_context"
- [ ] S09: In chat/coder/answer.py fill in `sse`, `phase` and `make_title` as their docstrings say
  accept: make chat-test K="test_step_answer and test_helpers"
- [ ] S10: In chat/coder/answer.py fill in `run_answer` steps 2–5 of its docstring (skip steps 1, 6, 7 for now; yield a done event)
  accept: make chat-test K="test_step_answer and (test_stream_events_and_store or test_count_without_usage or test_busy or test_gateway_errors)"
- [ ] S11: In chat/coder/answer.py add steps 1, 6 and 7 of the `run_answer` docstring
  accept: make chat-test K="test_step_answer"
- [ ] S12: In chat/coder/app.py fill in the route `api_create_message` as its docstring says (steps 1–4, 6, 7)
  accept: make chat-test K="TestStream"
- [ ] S13: In chat/coder/app.py add step 5 (files) to `api_create_message`: see "S13 details" below
  accept: make chat-test K="TestFiles or TestStream"
- [ ] S14: In chat/coder/app.py fill in `api_compress`, and make `api_get_chat` return `context.info(...)` as "context"
  accept: make chat-test K="TestContext"
- [ ] S15: Make the whole chat suite green (run it, fix only what fails, in the app code)
  accept: make chat-test
- [x] S16: stream.js: splitEvents and readEvents (done by hand: the writing engine)
  accept: node --test chat/tests/js/stream.test.mjs
- [x] S17: stream.js: Typewriter (done by hand: the writing engine)
  accept: node --test chat/tests/js/stream.test.mjs
- [x] S18: stream.js: groupChats and metaText (done by hand: the writing engine)
  accept: node --test chat/tests/js/stream.test.mjs
- [ ] S19: In chat/static/js/app.js fill in `loadChats` as its comment says
  accept: node --check chat/static/js/app.js && grep -q "groupChats(state.chats" chat/static/js/app.js
- [ ] S20: In chat/static/js/app.js fill in `openChat` and `drawMessage` as their comments say
  accept: node --check chat/static/js/app.js && grep -q "md.render(" chat/static/js/app.js && grep -q "welcome" chat/static/js/app.js
- [ ] S21: In chat/static/js/app.js fill in `send` as its comment says
  accept: node --check chat/static/js/app.js && grep -q "readEvents(" chat/static/js/app.js && grep -q "new Typewriter(" chat/static/js/app.js && grep -q "renderFade(" chat/static/js/app.js && grep -q "placeTail(" chat/static/js/app.js
- [ ] S22: In chat/static/js/app.js fill in `drawContext` as its comment says
  accept: node --check chat/static/js/app.js && grep -q -- "--used" chat/static/js/app.js && grep -q -- "--at" chat/static/js/app.js
- [ ] S23: In chat/static/js/app.js fill in `setupFiles` as its comment says
  accept: node --check chat/static/js/app.js && grep -q "/api/files" chat/static/js/app.js
- [ ] S24: In chat/static/js/app.js fill in `start` as its comment says
  accept: node --check chat/static/js/app.js && grep -q "coder-mode" chat/static/js/app.js && grep -q "setTheme" chat/static/js/app.js
- [ ] S25: Write chat/static/js/admin.js: see "S25 details" below
  accept: node --check chat/static/js/admin.js && grep -q "/api/invites" chat/static/js/admin.js && grep -q "/api/users" chat/static/js/admin.js
- [ ] S26: Write chat/README.md: see "S26 details" below
  accept: grep -q "## Running locally" chat/README.md && grep -q "## How it fits together" chat/README.md

## S13 details
In `api_create_message`, at step 5: for each id in `file_ids`: `f = db.get_file(int(id), username)`;
a non-number or None -> return 400 `{"error": "Unknown file."}`. Then for each file add to the text
sent to the model: `content += f"\n\nFile {f['name']}:\n```\n{db.get_file_content(f['id']).decode('utf-8')}\n```"`.
After `db.add_message(...)` (it returns the message id) call `db.add_message_files(msg_id, [ids])`
when there were files. The 413 check stays on the typed text only (before adding files).

## S25 details
`chat/coder/templates/admin.html` has `#invite-new` (checkboxes `name=rights`, input `note`),
`#invite-link`, `#invite-list`, `#user-list`. Every request sends header `X-Coder: 1` (and
`Content-Type: application/json` with a JSON body). On load: GET /api/invites and GET /api/users and
draw them as `<li>` rows (invites: rights, note, expires, "used by X" or a Delete button that calls
DELETE /api/invites/ID; users: username, rights, last login, and a Delete button (DELETE
/api/users/NAME, after confirm()) except for yourself). Submit of `#invite-new`: POST /api/invites
{rights: checked values, note}; show `location.origin + result.url` in `#invite-link` with a Copy
button (navigator.clipboard.writeText), then reload the lists. Escape all text you put in HTML.

## S26 details
`chat/README.md` with: `## What it is` (two sentences: the public chat for the local model; it talks
to the model only through the status service's gateway). `## Running locally` (a bash block:
`make chat-test`, then `cd chat && DATA_DIR=./data SECURE_COOKIES=0 ADMIN_USER=me
ADMIN_PASSWORD=change-me-please GATEWAY_URL=http://localhost:8080/v1 .venv/bin/uvicorn --factory
coder.app:create_app --port 8090`). `## How it fits together`: one line each for
`coder/app.py`, `config.py`, `db.py`, `auth.py`, `gateway.py`, `context.py`, `answer.py`,
`templates/`, `static/js/theme.js`, `stream.js`, `app.js`, `login.js`, `invite.js`, `admin.js`,
`static/css/`, and the path browser → chat → status gateway (`/v1`) → llama-server.
