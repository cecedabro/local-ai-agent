# Backend

The server side of the two services: **Dewdrop** (`chat/`, the public chat app) and **agent-status** (`status/`,
the private status page, job queue and model gateway). Contracts are in `chat/SPEC.md` and `status/SPEC.md`;
this page is the map. How a message flows end to end: `docs/DEWDROP.md`; the research flow: `docs/CLEAR-SOURCE.md`.

## Dewdrop (`chat/coder/`)

Python 3.11, FastAPI, Jinja2, httpx, SQLite. Packages are pinned in `chat/requirements.txt` (argon2, webauthn,
pypdf; python-docx, openpyxl, fpdf2 arrive with loop D). Started by uvicorn with `coder.app:create_app` as a
factory; one process, async throughout.

### Modules

| Module | Responsibility | Notes |
|---|---|---|
| `app.py` | Routes, pages, sessions, security middleware | Every response gets a CSP (`script-src 'self'`, no inline), `nosniff`, `same-origin` referrer. Every non-GET needs the `X-Coder: 1` header (CSRF). |
| `config.py` | `Settings` from the environment | `DATA_DIR`, `GATEWAY_URL`, `GATEWAY_KEY`, `STATUS_URL`, `SEARXNG_URL`, `RP_ID`, `ORIGIN`, `WAIT_LIMIT`, labels |
| `auth.py` | Passwords (argon2id), sessions (random id, only its hash stored, HttpOnly + Secure + SameSite=Strict cookie), login rate limit, invites | First admin seeded from `ADMIN_PASSWORD` |
| `passkeys.py` | WebAuthn registration and login | Challenges stored hashed with an expiry |
| `db.py` | All SQL, one class `DB`, thread-local connections, WAL | Schema below; migrations are `ALTER TABLE` in `init_schema` |
| `gateway.py` | The model client: `stream_chat` (SSE parsing, tool calls, reasoning), `complete` (one-shot), `Busy` / `GatewayError` | Also parses tool calls Qwen writes as text (`text_tool_calls`) |
| `context.py` | Token counting, the window (`compress_at` = 75 %), summarising older messages, the system prompt per profile | |
| `answer.py` | `run_answer`: one General answer, tool rounds (max 4), events, storage; hands Clear Source to `research.run_evidence` | `make_file` (loop D) |
| `profiles.py` | The two profiles, default sources (`SEED`), per-user settings and sources, the profile API shape, document text (PDF) | |
| `tools.py` | Tool specs and runner for General; SearXNG search; `fetch_page` (SSRF-safe); PubMed, MeSH, PubMed Central (`ncbi_get` throttle) | `is_public`, `fetch_page`, `html_text` are human-owned |
| `research.py` | Clear Source: plan, gather, read one by one, second round, write, `focus` for long pages | Prompts at the top of the file |
| `verify.py` | The checker: quotes, sentences, citations, `find_quote`, `check` | Pure functions |
| `files.py` | Files in and out (SPEC §16, loop D) | Pure functions |

### Data model (SQLite, `DATA_DIR/coder.db`)

| Table | Holds |
|---|---|
| `users` | username, argon2 hash, rights (`chat`, `evidence`, `admin`), theme, app icon, created, last login |
| `sessions` | hashed session id → user, expiry |
| `invites` | hashed token, rights, note, expiry, used by |
| `login_failures` | rate-limit window per user and per client address |
| `passkeys`, `webauthn_challenges` | WebAuthn credentials (public key, sign count) and pending challenges |
| `chats` | owner, title, profile, folder, last token usage |
| `folders` | owner, name |
| `messages` | role (`user`, `assistant`, `summary`), content, tokens, speed, stopped, and for answers: sources, trail, verify report, thinking, thought seconds (JSON columns) |
| `files`, `message_files` | uploaded or made files (bytes, and with loop D: kind, extracted text, made-by-model) and which message they belong to |
| `profile_settings` | per user and profile: extra instructions, web on/off, defaults seeded |
| `sources` | per user and profile: PubMed (with filters), websites (domain, note), documents (extracted text), enabled |

Everything is per user: every query that reads a chat, message, file or source checks the owner.

### HTTP API (all JSON unless noted; details in `chat/SPEC.md`)

| Area | Routes |
|---|---|
| Pages | `GET /`, `/login`, `/invite`, `/account`, `/profiles`, `/admin`, `/manifest.json`, `/apple-touch-icon.png`, `/healthz` |
| Sign in | `POST /api/login`, `/api/logout`; passkeys: `POST /api/passkeys/register/options`, `…/register/verify`, `…/login/options`, `…/login/verify`; `GET /api/passkeys`, `DELETE /api/passkeys/<id>` |
| You | `GET /api/me`, `PATCH /api/me` (theme, icon) |
| Chats | `GET/POST /api/chats`, `GET/PATCH/DELETE /api/chats/<id>`, `POST /api/chats/<id>/messages` (**streams**, `text/event-stream`), `POST /api/chats/<id>/stop`, `POST /api/chats/<id>/compress` |
| Folders | `GET/POST /api/folders`, `PATCH/DELETE /api/folders/<id>` |
| Files | `POST /api/files`, `GET /api/files/<id>`; loop D adds `GET /api/messages/<id>/export?format=` |
| Profiles | `GET/PATCH /api/profiles/<p>`, `POST /api/profiles/<p>/sources`, `PATCH/DELETE …/sources/<id>`, `POST …/documents`, `POST …/defaults` |
| Admin | `GET/POST /api/invites`, `DELETE /api/invites/<id>`, `POST /api/invites/accept`, `GET /api/users`, `DELETE /api/users/<name>` |

### The answer stream

`POST /api/chats/<id>/messages` stores your message and returns server-sent events:

| Event | When |
|---|---|
| `phase` `{phase, label}` | waiting, compressing, reading, thinking, searching, summarising, writing |
| `thinking` `{text}` | reasoning text (Think mode), shown folded |
| `trail` `{step}` | a plan, search, opened page or read source (the research trail) |
| `delta` `{text}` | answer text |
| `verify` `{report}` | Clear Source's check |
| `file` `{id, name, size, kind}` | a file the model made (loop D) |
| `done` | message id, tokens, speed, context use, title, sources, trail, verify, files |
| `error` `{error, status}` | the model could not answer; nothing is stored |

Stop (`POST …/stop`) sets an event the stream checks; what was written so far is stored as "Stopped". If the browser
disconnects, the server stops reading from the model and stores what arrived.

### Tests

`make chat-test` runs the pytest judge against a fake model (`chat/tests/fake_gateway.py`, which also fakes
SearXNG, PubMed, MeSH and PMC). `make chat-browser-test` drives headless Chromium through the real pages
(`chat/tests/browser/`). `node --test chat/tests/js/stream.test.mjs` covers the rendering engine. Test files named
`test_accept_*` check the HTTP contract; `test_step_*` check single functions for agent tasks. All are protected.

## agent-status (`status/`)

One stdlib-only Python file (`status/app.py`) with its SQLite database on a volume. Tailscale only.

| Area | What it does |
|---|---|
| Page | Server-rendered status page, refreshed every 5 s from `/partial`; quick chat; "Start an agent loop" |
| Runs | Events from `agent-loop` (`/api/events`), run history, commits and lines, imported history (`backfill.py`) |
| Queue | Jobs (`/api/jobs`, claim, heartbeat, finish, cancel, move, requeue) worked by `agent-queue` in the devbox |
| Lease | `/api/lease`: one agent run uses the model at a time; others wait in line |
| Gateway | OpenAI-compatible `/v1/chat/completions` to llama-server. Clients in `GATEWAY_CLIENTS` (`name:lane:key`); lane **interactive** (Dewdrop, the quick chat) goes before **batch** (agents). Prompts are never stored; each request's timing and token counts are |
| Metrics | Token speed and totals from llama-server's `/metrics`, hourly and daily |
| Auth | Reading is open on the tailnet; changing anything needs the status token |

Its tests: `make status-test` (contract) and `make check` (unit tests the agent writes).

## Configuration and secrets

All secrets live in OpenBao (`secret/ai-agent/*`, `secret/dewdrop/*`) and reach the pods through External
Secrets: the gateway keys, the llama-server API key, Dewdrop's admin password and gateway key, the status token, the
GitHub token of the devbox. Nothing secret is in this repo; placeholders are literally `REPLACE_ME`.
