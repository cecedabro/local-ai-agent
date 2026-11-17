# PLAN: Chat app foundation

Build the new public chat app in `chat/` (working name "Coder"). Read `chat/SPEC.md` first; the
section for your task is named in its details below. The judge is `chat/tests/test_accept_core.py`
(protected: change the code, never the tests). Run the task's `accept:` command yourself before you
finish and read the first failing assertion.

Rules for every task:
- This run is non-interactive: nobody reads your questions. Never end with a question or a plan;
  make the edits, run the accept command, fix what fails, then stop.
- Never read a large file whole. Use `grep -n` and read only the lines you need (`sed -n 'a,bp'`).
- Python 3.11. Only the packages in `chat/requirements.txt` (`make chat-test` installs them into
  `chat/.venv`; use `chat/.venv/bin/python` to try things).
- Keep groups from earlier tasks green: before you finish, also run their `make chat-test K=...`.
- Do not touch: `chat/SPEC.md`, `chat/tests/test_accept_core.py`, `chat/tests/fake_gateway.py`,
  `chat/static/css/` (the look is the human's; use the hooks from SPEC §9), `chat/Dockerfile`,
  `chat/requirements.txt`, `Makefile`, `.github/`, `docs/`, `status/`, `harness/`.
- Your own extra tests go in `chat/tests/test_unit_*.py`.

- [x] T01: App skeleton, settings, database and first admin (see "T01 details")
  accept: make chat/.venv/.stamp && cd chat && PYTHONPATH=. .venv/bin/python -c "import tempfile; from fastapi.testclient import TestClient; from coder.app import create_app; c = TestClient(create_app({'DATA_DIR': tempfile.mkdtemp()})); assert c.get('/healthz').json() == {'ok': True}"
- [x] T02: Login, logout, sessions, CSRF header, theme and rate limit (see "T02 details")
  accept: make chat-test K="TestAuth or TestRateLimit or healthz or seeded"
- [ ] T03: Invites and the users API (see "T03 details")
  accept: make chat-test K="TestInvites"
- [ ] T04: Chats API (see "T04 details")
  accept: make chat-test K="TestChats"
- [ ] T05: Sending a message: gateway streaming with phases (see "T05 details")
  accept: make chat-test K="TestStream"
- [ ] T06: Context size and compression (see "T06 details")
  accept: make chat-test K="TestContext"
- [ ] T07: File upload, download and attachments (see "T07 details")
  accept: make chat-test K="TestFiles"
- [ ] T08: Pages, security headers and the vendored markdown renderer (see "T08 details")
  accept: make chat-test
- [ ] T09: Chat page script: chats, streaming, phases, context bar (see "T09 details")
  accept: node --check chat/static/js/app.js && node --check chat/static/js/stream.js && make chat-test
- [ ] T10: Login, invite, admin and theme scripts (see "T10 details")
  accept: for f in chat/static/js/*.js; do node --check "$f" || exit 1; done && make chat-test
- [ ] T11: chat/README.md (see "T11 details")
  accept: grep -q "## Running locally" chat/README.md && grep -q "## How it fits together" chat/README.md && make chat-test

## T01 details
SPEC §0, §1, §2 (first admin only), §8 (`/healthz`). Create the package `chat/coder/`:
`__init__.py`, `app.py` with `create_app(settings=None)`, `config.py` (a `Settings` dataclass built
from the `settings` dict first, then `os.environ`, with the defaults of the §1 table; booleans:
`"0"`, `0`, `""`, `"false"` are false), `db.py` (sqlite3, WAL, `foreign_keys=ON`, a
`threading.Lock`, `init_schema()` creating tables `users(username pk, pw_hash, rights, theme,
created, last_login)`, `sessions(id_hash pk, username, created, expires)`, `invites(id pk,
token_hash, rights, note, created, expires, used_by)`, `chats(id pk, username, title, profile,
created, updated, last_prompt_tokens, last_completion_tokens)`, `messages(id pk, chat_id, role,
content, created, tokens, tps)`, `files(id pk, username, name, size, content blob, created)`,
`message_files(message_id, file_id)`, `login_failures(key, at)`). Times are ISO-8601 UTC strings.
`auth.py` with argon2 `hash_password` / `verify_password`. On startup, seed the first admin (rights
`admin`) when the users table is empty and both settings are set. Mount `/static` from
`Path(coder.__file__).parent.parent / "static"`. `GET /healthz` → `{"ok": true}`. Keep the
settings and the db on `app.state`.

## T02 details
SPEC §2. In `auth.py`: sessions (`secrets.token_urlsafe(32)`, store
`hashlib.sha256(id.encode()).hexdigest()`, 30 days), a FastAPI dependency `current_user` that reads
cookie `coder_session` and raises 401. A middleware that returns 403 `{"error": "missing X-Coder
header"}` for POST/PATCH/DELETE under `/api/` without `X-Coder: 1` (before routing). Routes:
`POST /api/login`, `POST /api/logout`, `GET /api/me`, `PATCH /api/me`. Cookie flags exactly as §2
(`secure` from `SECURE_COOKIES`). Rate limit with the `login_failures` table: keys `user:<name>`
and `ip:<client host>`; 5 rows for either key in the last 15 minutes → 429 before checking the
password. Record a failure on every 401. Rights are stored comma-separated and returned sorted.

## T03 details
SPEC §3. Routes `POST/GET /api/invites`, `DELETE /api/invites/{id}`, `POST /api/invites/accept`,
`GET /api/users`, `DELETE /api/users/{username}` (deleting yourself → 400). A dependency
`require_admin` (403). Accepting an invite logs the new user in (same cookie as login). Also add
`GET /admin`: no session → 303 to `/login`, not admin → 403, admin → 200 with a simple HTML page
(T08 makes it a real template; a placeholder `<h1>Admin</h1>` is fine now).

## T04 details
SPEC §4. Routes `GET/POST /api/chats`, `GET/PATCH/DELETE /api/chats/{id}`. Profile rights:
`general` needs `chat`, `evidence` needs `evidence` (`admin` has both). Someone else's chat → 404.
`GET /api/chats/{id}` returns `messages` (each with `files`, an empty list when none) and
`context` (for now `{"used": 0, "max": 32768, "compress_at": 24576}`; T06 makes it real). Keep
the chat logic in `chats.py`.

## T05 details
SPEC §5. Module `gateway.py`: an async generator `stream_chat(settings, messages, think)` using
`httpx.AsyncClient` (timeout 600 s) that POSTs to `<GATEWAY_URL>/chat/completions` with the body of
§5, parses the `data: ` lines, and yields `("reasoning", text)`, `("content", text)`,
`("usage", dict)`; on 503/429 it raises `Busy`, on other non-200 `GatewayError(status)`. The route
`POST /api/chats/{id}/messages`: validate first (400/413/404 are normal JSON responses, before any
streaming), store the user message, then return a `StreamingResponse` (`text/event-stream`) whose
generator builds the messages (system prompt per profile: general = "You are Coder, a helpful
assistant running locally on Cédric's PC. Answer in the user's language. Use Markdown."; evidence =
"You are EvidenceHunt. Answer only from medical evidence and say when evidence is weak or
missing."), retries `Busy` every 2 s (one `waiting` phase) until `WAIT_LIMIT`, then emits
phases/deltas/done/error exactly as the §5 table. Store the assistant message and the usage numbers
on the chat; set the title (§4) after the first answer.

## T06 details
SPEC §6. `context.py`: `ctx_max(settings)` from `GET <STATUS_URL>/api/status` → `gateway.n_ctx`
(cache 5 min, fallback `CTX_DEFAULT`), `context_messages(chat_id)` (latest summary as a system
message + what follows), `used(chat)`. Fill the real `context` into `GET /api/chats/{id}` and the
`done` event. `POST /api/chats/{id}/compress` uses a non-streaming gateway call (prompt: "Summarise
this conversation so far in a few short paragraphs, keeping names, numbers, decisions and open
questions."). The summary message's `created` must sort just before the oldest kept message.
Auto-compress in the send route when `used >= compress_at` (emit the `compressing` phase first).

## T07 details
SPEC §7. `POST /api/files` (multipart field `file`), `GET /api/files/{id}`. Validate UTF-8 and no
NUL (415), size (413), name = the last part after `/` or `\`, max 120 chars. In the send route the
`files` ids must belong to the user (400); store them in `message_files`, add the file blocks to
the text sent to the model, and return them in each message's `files`.

## T08 details
SPEC §8 and §9. Jinja2 templates in `chat/coder/templates/`: `base.html` (head: charset,
viewport, `<script src="/static/js/theme.js"></script>` first, then the four stylesheets
`/static/css/tokens.css`, `base.css`, `chat.css`, `orb.css`, and a favicon), `login.html`,
`invite.html`, `chat.html`, `admin.html`. Copy the structure and class names of
`docs/design/chat/2026-11-17-chat-mockup.html` (sidebar, top bar, thread, dock, footer line) with
real data hooks; use the orb markup of §9 everywhere. No inline `<script>` blocks and no `style=`
attributes (CSP). A middleware adds the §8 headers to every HTML response. `GET /` without a
session → 303 `/login`. Download markdown-it 14.1.0 to `chat/static/vendor/markdown-it.min.js`
(`curl -fsSL https://cdn.jsdelivr.net/npm/markdown-it@14.1.0/dist/markdown-it.min.js -o ...`) and
commit it. The line under the composer uses `MODEL_LABEL`, `HARDWARE_LABEL` and `REPO_URL`.
Create empty `chat/static/js/theme.js` and `app.js` if they don't exist yet (T09/T10 fill them).

## T09 details
SPEC §5, §6, §9. `chat/static/js/app.js` (ES module, loaded by `chat.html`) and
`chat/static/js/stream.js` (exports `parseSSE(reader, onEvent)` for a `fetch` body stream, and a
`Typewriter` class that buffers deltas and releases about one word per 25 ms, calling back with the
text so far). In `app.js`: load `/api/me` and `/api/chats`, render the sidebar groups (Today,
Yesterday, Last 7 days, Older), open a chat, a welcome screen for an empty chat with 3 example
questions per profile, send with `fetch` (`X-Coder: 1`), show the phase on the orb (`data-phase`)
and in `.phase-label`, render the answer with markdown-it (`html: false, linkify: true`, links open
in a new tab) as the typewriter releases text, set `--fill` while writing, show `.meta2` when
done, Copy and Retry, the context bar (`--used`, `--at`, `#ctx-text`, Compress button), file chips
(upload on pick, remove before sending), Enter to send / Shift+Enter for a new line, the
Quick/Think switch remembered in `localStorage`, new chat, rename (double-click the title), delete.
Any 401 goes to `/login`.

## T10 details
`chat/static/js/theme.js` (a classic script that runs first: reads `localStorage` `coder-theme`,
sets `data-theme` / `data-theme-pref` on `<html>` before paint, follows the device for `auto`,
exposes `window.setTheme(pref)` which also `PATCH`es `/api/me`), `login.js` (form → `/api/login`,
shows the 401/429 message, then goes to `/`), `invite.js` (token from `location.hash`, form →
`/api/invites/accept`), `admin.js` (create an invite with right checkboxes, show the full link to
copy, list and delete invites, list and delete users). Wire the theme switch `.seg.four.theme` in
`chat.html`.

## T11 details
Write `chat/README.md`: `## What it is` (two sentences), `## Running locally` (a bash block:
`make chat-test`, then `cd chat && DATA_DIR=./data SECURE_COOKIES=0 ADMIN_USER=me
ADMIN_PASSWORD=change-me-please GATEWAY_URL=http://localhost:8080/v1 .venv/bin/uvicorn --factory
coder.app:create_app --port 8090`), `## How it fits together` (one line per module in
`chat/coder/` and `chat/static/js/`, and the request path browser → chat → status gateway →
llama-server), `## Settings` (the §1 table, short).
