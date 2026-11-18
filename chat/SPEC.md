# Dewdrop (the chat app): spec

The public chat for the local model, called Dewdrop. It lives in `chat/`, runs as its own
pod, and talks to the model only through the status service's gateway (`/v1`, see
`status/SPEC.md` §11). Status page = Tailscale only; chat = public (Cloudflare Tunnel), so
security matters here.

Human-owned (the agent may not edit): this file, `chat/tests/test_accept_*.py`,
`chat/tests/fake_gateway.py`, `chat/static/css/`, `chat/Dockerfile`, `chat/requirements.txt`,
`Makefile`, `.github/`, `docs/`.

## 0. Stack and layout

- Python 3.11, packages from `chat/requirements.txt` only (FastAPI, Jinja2, httpx, argon2-cffi,
  python-multipart, uvicorn, pytest). That file is human-owned: no other packages.
- Package `chat/coder/`. Entry: `coder.app:create_app(settings: dict | None = None) -> FastAPI`.
  Run with `uvicorn --factory coder.app:create_app`. Tests import it with `PYTHONPATH=chat`.
- SQLite at `<DATA_DIR>/coder.db` (stdlib `sqlite3`, one connection per request or a lock;
  WAL mode). Uploaded files live in the database (they are small).
- Server-rendered pages: Jinja2 templates in `chat/coder/templates/`. Browser code in
  `chat/static/js/` (plain ES modules, no build step). Styles: `chat/static/css/` (owned by the
  human, see §9). Static files are served at `/static/...` from `<package dir>/../static` (the same
  layout in the repo, `chat/coder` + `chat/static`, and in the image, `/app/coder` + `/app/static`).
- Markdown is rendered in the browser with markdown-it 14.1.0, vendored at
  `chat/static/vendor/markdown-it.min.js`, created with `html: false` (raw HTML is escaped)
  and `linkify: true`; links get `target="_blank" rel="noopener noreferrer"`.

## 1. Settings

`create_app(settings)` reads each key from `settings` first, then the environment:

| Key | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `./data` | Folder for `coder.db` (created if missing) |
| `GATEWAY_URL` | `http://agent-status.ai-agent.svc:8080/v1` | OpenAI-compatible base URL |
| `GATEWAY_KEY` | (empty) | Bearer key for the gateway (its `chat` client) |
| `STATUS_URL` | (empty) | Status service root; `GET <STATUS_URL>/api/status` → `gateway.n_ctx` |
| `CTX_DEFAULT` | `32768` | Context size when the status service doesn't say |
| `ADMIN_USER`, `ADMIN_PASSWORD` | (empty) | First admin, created only when there are no accounts |
| `SECURE_COOKIES` | `1` | `0` drops the `Secure` cookie flag (tests, local http) |
| `MODEL_LABEL` | `Qwen3.6 35B-A3B · 4-bit` | Shown under the chat |
| `HARDWARE_LABEL` | `running locally on an RTX 3080 Ti` | Shown under the chat |
| `REPO_URL` | `https://github.com/cecedabro/local-ai-agent` | "how it works" link |
| `WAIT_LIMIT` | `120` | Seconds to keep retrying a busy gateway before giving up |

Settings values in tests are strings or numbers; treat `"0"`/`0` as false.

## 2. Accounts and sessions

- Usernames: `^[a-z0-9][a-z0-9_.-]{1,31}$` (lower-case). Passwords: 10–200 characters.
- Passwords are hashed with argon2id (`argon2-cffi` defaults). Never stored or logged in clear.
- Rights: `chat` (General profile), `evidence` (EvidenceHunt profile), `admin` (invites and
  users; implies every right). Stored per account.
- First admin: at start, if the accounts table is empty and both `ADMIN_USER` and
  `ADMIN_PASSWORD` are set, create that account with right `admin`.
- Sessions: a random id (`secrets.token_urlsafe(32)`) in cookie `coder_session`: `HttpOnly`,
  `SameSite=Lax`, `Path=/`, `Max-Age=2592000` (30 days), `Secure` unless `SECURE_COOKIES` is
  off. The database stores only `sha256(id)`. Logout deletes the session.
- **CSRF rule**: every `POST`, `PATCH` and `DELETE` under `/api/` must carry the header
  `X-Coder: 1`, else **403** `{"error": "missing X-Coder header"}` (checked before anything
  else, also for login). Browsers can't send custom headers cross-site without CORS, and this
  app sends no CORS headers.
- **Rate limit**: 5 failed logins for the same username or from the same client address within
  15 minutes → further login attempts for it get **429** until the window passes (even with the
  right password). A success does not reset other addresses' counters.

Endpoints (JSON in and out; errors are `{"error": "<human sentence>"}`):

- `POST /api/login` `{"username", "password"}` → **200** `{"username", "rights"}` and sets the
  cookie; wrong username or password → **401** (same message for both).
- `POST /api/logout` → **204**, cookie cleared (`Max-Age=0`).
- `GET /api/me` → **200** `{"username", "rights": [...sorted], "theme"}`; no valid session →
  **401**. `theme` is `auto` until changed.
- `PATCH /api/me` `{"theme": "auto"|"light"|"dark"|"blossom"}` → **200** (same body as GET);
  other values → **400**.

Every `/api/` route except `login`, `invites/accept` and `/healthz` needs a session (**401**).

## 3. Invites (admin)

- `POST /api/invites` `{"rights": [...], "note"?}` (rights ⊆ `chat`, `evidence`, `admin`,
  non-empty, else **400**; non-admin → **403**) → **201** `{"id", "token", "url", "expires",
  "rights", "note"}`. `token` = `secrets.token_urlsafe(24)`, stored as sha256; `url` =
  `/invite#<token>`; valid 7 days, single use.
- `GET /api/invites` (admin) → **200** `[{"id", "rights", "note", "created", "expires",
  "used_by"}]`, newest first, never the token.
- `DELETE /api/invites/<id>` (admin) → **204**.
- `POST /api/invites/accept` `{"token", "username", "password"}` → **201** `{"username",
  "rights"}`, creates the account with the invite's rights, marks the invite used, and logs in
  (sets the cookie). Unknown, used or expired token → **400**; bad username or password →
  **400**; username taken → **409**.
- `GET /api/users` (admin) → **200** `[{"username", "rights", "created", "last_login"}]`.
- `DELETE /api/users/<username>` (admin, not yourself) → **204**; deletes the account, its
  sessions, chats and files.

## 4. Chats

A chat belongs to one user and one profile: `general` (needs `chat`) or `evidence` (needs
`evidence`). Users only ever see their own chats; someone else's id → **404**.

- `GET /api/chats` → **200** `[{"id", "title", "profile", "updated"}]` newest `updated` first.
- `POST /api/chats` `{"profile"?: "general"}` → **201** `{"id", "title": "New chat", "profile",
  "updated"}`; unknown profile → **400**; missing right → **403**.
- `GET /api/chats/<id>` → **200** `{"id", "title", "profile", "updated", "messages": [...],
  "context": {"used", "max", "compress_at"}}`. A message: `{"id", "role": "user"|"assistant"|
  "summary", "content", "created", "files": [{"id", "name", "size"}], "tokens", "tps"}`
  (`tokens`/`tps` null for user messages).
- `PATCH /api/chats/<id>` `{"title"}` (1–120 chars) → **200** the chat (without messages).
- `DELETE /api/chats/<id>` → **204**.
- Title: after the first answer, if the title is still `New chat`, it becomes the first user
  message, whitespace collapsed, cut to 60 characters; when cut, trailing spaces are removed and
  `…` is added.

## 5. Sending a message (streaming)

`POST /api/chats/<id>/messages` `{"content", "mode"?: "quick"|"think", "files"?: [file ids]}`
→ **200** `text/event-stream`. Empty content and no files → **400**; more than 20000 characters
→ **413**; a file id that isn't yours → **400**. The user message is stored before streaming.

The server calls `POST <GATEWAY_URL>/chat/completions` with `Authorization: Bearer
<GATEWAY_KEY>`, body `{"model": "coder", "stream": true, "stream_options": {"include_usage":
true}, "chat_template_kwargs": {"enable_thinking": <mode == "think">}, "messages": [...]}`.
Messages: a system prompt for the profile, then the chat's context (§6), then the new message.
Attached files are added to the user message text as
`` File <name>:\n```\n<content>\n``` `` blocks after the typed text.

Events (`event: <name>\ndata: <json>\n\n`):

| Event | Data | When |
|---|---|---|
| `phase` | `{"phase": "waiting", "label": "Waiting for the model… you're next"}` | gateway answered 503 or 429; retry every 2 s up to `WAIT_LIMIT` s |
| `phase` | `{"phase": "compressing", "label": "Compressing older messages…"}` | auto-compress (§6) runs first |
| `phase` | `{"phase": "thinking", "label": "Thinking…"}` | first `delta.reasoning_content` chunk |
| `phase` | `{"phase": "writing", "label": "Writing the answer…"}` | first `delta.content` chunk |
| `delta` | `{"text": "..."}` | each non-empty `delta.content` (reasoning is never sent or stored) |
| `done` | `{"message_id", "tokens", "tps", "context": {"used", "max", "compress_at"}}` | stream finished; assistant message stored |
| `error` | `{"error": "...", "status": <int>}` | gateway error, or still busy after `WAIT_LIMIT` |

Each phase is sent at most once per answer, in the order they happen. `tokens` = the usage
chunk's `completion_tokens` (or the number of content chunks if the gateway sent no usage);
`tps` = tokens / seconds between first content chunk and the end, 1 decimal, or null. On
`error` no assistant message is stored. If the client disconnects mid-answer, stop reading from
the gateway and store what arrived so far.

## 6. Context and compression

- `max` = `gateway.n_ctx` from `GET <STATUS_URL>/api/status` (cached 5 minutes; on any error
  or when `STATUS_URL` is empty: `CTX_DEFAULT`). `compress_at` = `floor(0.75 * max)`.
- `used` = the last answer's `prompt_tokens + completion_tokens` from the usage chunk; before
  any answer (or without usage): `ceil(chars / 4)` of what would be sent.
- The context sent to the model: the latest `summary` message (if any) as a system message
  "Summary of the earlier conversation: …", then every message after it.
- `POST /api/chats/<id>/compress` → **200** `{"context": {...}}`: when there are more than 4
  messages after the latest summary, ask the gateway (non-streaming, `"stream": false`) to
  summarise all of them except the last 4, store the result as a `summary` message placed
  before those 4 (its `created` = just before the oldest kept message), and return the new
  context. With 4 or fewer → **200** unchanged. Summaries are shown in the chat as a collapsed
  "Earlier messages summarised" note.
- Auto: before sending, if `used >= compress_at`, compress first (with the `compressing` phase).

## 7. Files

- `POST /api/files` multipart field `file` → **201** `{"id", "name", "size"}`. Text only: must
  decode as UTF-8 and contain no NUL byte, else **415**; larger than 200 000 bytes → **413**.
  Names are stripped to their last path part, max 120 characters.
- `GET /api/files/<id>` → the file as a download (`Content-Disposition: attachment`,
  `Content-Type: text/plain; charset=utf-8`, `X-Content-Type-Options: nosniff`); not yours →
  **404**.

## 8. Pages

- `GET /healthz` → **200** `{"ok": true}` (no session needed).
- `GET /login`: the sign-in page. `GET /invite`: the page that reads the token from the URL
  fragment and asks for username and password. Both work without a session.
- `GET /`: the chat. Without a session → **303** to `/login`.
- `GET /admin`: invites and users, admin only (others **403**, no session → **303** `/login`).
- All pages: `Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src
  'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'`
  (so: no inline scripts or styles; the theme is applied by a small external script that runs
  first in `<head>`), plus `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin`.
- Under the composer: `<MODEL_LABEL> <HARDWARE_LABEL> · <a href="<REPO_URL>">how it works ↗</a>`.

## 9. UI contract (the hooks the stylesheets rely on)

The look is swappable: everything visual lives in `chat/static/css/` (`tokens.css` colours per
theme, `base.css`, `chat.css` layout, `orb.css` the orb and its phases). Templates and JS must
only use these hooks; the reference markup is `docs/design/chat/2026-11-17-chat-mockup.html`.

- `<html data-theme="light|dark|blossom" data-theme-pref="auto|light|dark|blossom">`, set before
  paint by `/static/js/theme.js` from `/api/me` (cached in `localStorage` key `coder-theme`).
- Layout: `body.app` > `aside.side` (`.brand`, `button.newchat`, `input.search`, `nav.convs` with
  `h4` group headings and `.conv[data-id]` items, `.conv.on` = open, `em.tag` = "EH" for
  evidence chats, `.me`) and `.main` (`.top` with `.title`, `button.profile`, `#model.badge`,
  the theme switch `.seg.four.theme`; `.thread > .col` messages; `.dock` with `.composer`,
  `.ctx`, `.hint`).
- Messages: user = `.u > .msg.user` (+ `.file` chips above it). Assistant = `.ai` > the orb +
  `.content` > `.bub` (rendered markdown, on the page, no bubble) + `.meta2` (the stats line, always
  shown: a tiny orb, `Dewdrop · <tokens> tokens · <tps> tok/s`, Copy, Retry). While writing, a tiny
  orb (`.orb.tiny`) sits at the end of the text (stream.js `placeTail`) and moves into `.meta2` when
  done. Summary = `.note.summary`.
- The orb: `<span class="orb" data-phase="idle"><i></i><i></i><i></i><i></i></span>` everywhere
  (brand, avatars, status). Phases set `data-phase` to `waiting|thinking|searching|reading|
  summarising|writing|compressing|idle`; the label is `.phase-label`. While writing, JS sets
  `--fill` (0–1) on the orb: `0.82 * (1 - exp(-seconds_writing / 2.6))`, rising fast and then ever
  slower, never "done"; back to `idle` the water drains. JS sets no inline styles other than these
  CSS variables and the fade delays of stream.js `renderFade`.
- Context bar: `.ctx .bar > i` with `--used` (0–1) and `.line` with `--at` (the
  `compress_at / max` fraction), `#ctx-text`, `button#compress`.
- Streaming text: stream.js `Typewriter` buffers the deltas and releases letters every frame at a
  steady, gently changing pace; `renderFade` re-renders the markdown and lets the newest letters
  fade in (class `nw`). The welcome screen uses `.orb.marble` (the drifting cloud marble).
