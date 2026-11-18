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
| `RP_ID` | `localhost` | Passkeys: the site's domain (production: `dewdrop.cedricschippers.dev`) |
| `ORIGIN` | `http://localhost:8090` | Passkeys: the exact origin the browser shows (production: `https://dewdrop.cedricschippers.dev`) |

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

## 10. Passkeys

Log in with Touch ID, Face ID or a phone instead of typing the password (WebAuthn, via the
`webauthn` package). The password stays as a fallback. Module: `chat/coder/passkeys.py`.

- A passkey belongs to one user: table `passkeys(id pk, username, credential_id blob unique,
  public_key blob, sign_count int, name, created, last_used)`. The WebAuthn user handle is the
  username in UTF-8 (`passkeys.user_handle`).
- Challenges are single use and live 5 minutes: table `webauthn_challenges(key_hash pk, kind,
  username, challenge blob, expires)`. The browser holds the key in cookie `coder_wa` (random,
  `HttpOnly`, `SameSite=Lax`, `Path=/api/passkeys`, `Max-Age=300`, `Secure` like the session cookie);
  the database stores only `sha256(key)`. Every verify call deletes the challenge first, whether the
  check passes or not.
- Relying party: id `RP_ID`, name `Dewdrop`, expected origin `ORIGIN`. Registration asks for a
  discoverable credential (`residentKey: required`) with user verification required, ES256 or RS256.

Endpoints (JSON; the CSRF header rule of §2 applies to every POST/DELETE):

- `POST /api/passkeys/register/options` (session) `{}` → **200** the creation options as the
  browser expects them (`webauthn.options_to_json`), with `excludeCredentials` = the user's
  passkeys; sets `coder_wa`.
- `POST /api/passkeys/register/verify` (session) `{"credential": <the browser's JSON>, "name"?}` →
  **201** `{"id", "name", "created"}` (name defaults to "Passkey", max 60 characters); a missing or
  used challenge, a wrong origin or a bad attestation → **400** `{"error"}`.
- `GET /api/passkeys` (session) → **200** `[{"id", "name", "created", "last_used"}]`, oldest first.
- `DELETE /api/passkeys/<id>` (session, own passkey) → **204**; someone else's or unknown → **404**.
- `POST /api/passkeys/login/options` (no session) `{}` → **200** request options with an empty
  `allowCredentials` (the browser offers its own passkeys) and user verification required; sets
  `coder_wa`.
- `POST /api/passkeys/login/verify` (no session) `{"credential": ...}` → **200** `{"username",
  "rights"}` and the session cookie of §2; the signature counter is stored and `last_used` set.
  Unknown credential, bad signature, missing or used challenge → **401** (same message for all).
  Rate limit: the address key of §2 (`ip:<client host>`); 5 failures in 15 minutes → **429**.

Pages: the sign-in page has "Sign in with a passkey"; the invite page offers "Use Face ID / Touch
ID next time" right after the account is made; `GET /account` (session) lists your passkeys with
"Add a passkey" and "Remove". The browser side is `chat/static/js/webauthn.js` (human-owned).

## 11. Round 2: phases, stop, folders, code blocks

### 11.1 Phases and events (changes §5)
- Order: `compressing` (if auto-compress runs) → `reading` → `waiting` (if busy) → `thinking`
  (first reasoning chunk) → `writing` (first content chunk). Each at most once per answer.
  `reading` = `{"phase": "reading", "label": "Reading your message…"}`, sent right before the
  first gateway call, so the orb clouds over and the label shimmers at once.
- Every non-empty content chunk becomes a `delta`, the very first one too.
- `done` = `{"message_id", "tokens", "tps", "context", "title", "stopped"}`: `title` is the
  chat's title after this answer (the page shows it at the top), `stopped` true when §11.2 cut
  the answer short.
- A message in `GET /api/chats/<id>` also has `"stopped": true|false`.
- Attached files are added to the user text exactly as §5 says (`\n\nFile <name>:\n```\n<content>\n````),
  so the page can cut the typed text off at the first `\n\nFile `.

### 11.2 Stop
- `POST /api/chats/<id>/stop` → **204**; not your chat → **404**. If an answer for that chat is
  being written, it stops after the chunk it is handling: the gateway stream is closed (the model
  stops), the text so far is stored as the assistant message with `stopped` true (nothing is
  stored when no text came yet; then `message_id` is null), and `done` follows with
  `"stopped": true`. Without a running answer the call does nothing (no flag stays behind).
- If the client disconnects mid-answer, the same happens without the `done` event.

### 11.3 Folders
A user's chats can live in folders (one level, no nesting of folders).
- `GET /api/folders` → **200** `[{"id", "name", "created"}]`, sorted by name (case-insensitive).
- `POST /api/folders` `{"name"}` (1–60 characters after trimming, else **400**) → **201**
  `{"id", "name", "created"}`.
- `PATCH /api/folders/<id>` `{"name"}` → **200** the folder; bad name **400**; not yours **404**.
- `DELETE /api/folders/<id>` → **204**; its chats are kept, now without a folder; not yours **404**.
- `GET /api/chats` items gain `"folder_id"` (int or null).
- `PATCH /api/chats/<id>` takes `{"title"?, "folder_id"?}` (at least one, else **400**):
  `folder_id` null takes it out of its folder; a folder id that isn't yours → **400**. Returns
  **200** `{"id", "title", "profile", "updated", "folder_id"}`.
- Deleting a user (§3) deletes their folders too.

### 11.4 Code files
- The General system prompt also says: "When you write a complete file, put its file name after
  the language on the opening fence line, for example ```html index.html".
- In the page every fenced code block is coloured (highlight.js 11.11.1, vendored at
  `chat/static/vendor/highlight.min.js`) and has a head with the file name, Copy and Download
  (stream.js `codeBlocks`, `codeActions`). Download saves the block as that file.

### 11.5 Page behaviour (app.js; hooks in §9 and below)
- After sending, the thread scrolls to the bottom at once. While an answer is written the page
  follows it, until the reader scrolls up; then it stays exactly where they are reading and the
  `#jump` button ("↓ Latest") shows; clicking it (or scrolling back down) follows again
  (stream.js `Follow`).
- While an answer is on its way `#send` gets class `stop` (title "Stop"); clicking it calls §11.2.
  A stopped answer shows `<div class="note stopped">Stopped</div>` under its stats line.
- The tiny writing drop fills like the big one (`data-phase="writing"` and `--fill` on it too).
- The sidebar shows folders first (`<details class="folder" open data-folder="ID"><summary>
  <span>NAME</span><small>COUNT</small><button class="more" data-folder-menu="ID">⋯</button>
  </summary> …its .conv items… </details>`), then the date groups for chats without a folder.
  Each `.conv` has `<button class="more" data-chat-menu="ID">⋯</button>` and is `draggable`;
  dropping it on a folder (or on `#convs` outside folders) moves it. `#new-folder` asks for a name.
- The menu `#cmenu` (positioned with the CSS variables `--x`/`--y`): for a chat: Rename, "Move
  to" + one button per folder + "No folder" + "New folder…", Delete (confirm first); for a
  folder: Rename, Delete (confirm; its chats are kept).
