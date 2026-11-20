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
- Rights: `chat` (General profile), `evidence` (Clear Source profile), `admin` (invites and
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
- **Rate limit**: 5 failed logins for the same username, or 20 from the same client address, within
  15 minutes → further login attempts for it get **429** until the window passes (even with the
  right password). A success does not reset other addresses' counters. The client address is
  Cloudflare's `Cf-Connecting-IP` when present, else the connection's address (`X-Forwarded-For` is
  never trusted: a visitor can write it).

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
`error` no assistant message is stored.

### 5.1 Answers keep going when the page goes away
The answer runs as a server task, not inside the request: the response only follows its events. When the client
disconnects (a phone locks or switches apps, the network changes) the answer goes on and is stored as usual.
`GET /api/chats/<id>/live` (session; someone else's chat or nothing running → **204**) replays the running answer's
events from the first one and follows it to the end; a finished answer stays followable for 60 s. The page follows a
chat's live answer when it opens a chat whose last message is yours, and opens the chat again when it becomes
visible after a dropped stream (hidden > 5 s). After 15 s without an event the stream sends a comment line
(`: ping`), so a proxy doesn't hang up on a quiet research step (Cloudflare does after ~100 s). Stop (§11.2) still
ends a running answer. Judge:
`tests/test_accept_live.py`.

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
  Rate limit: the address key of §2 (`ip:<client address>`); 5 failures in 15 minutes → **429**.

Pages: the sign-in page has "Sign in with a passkey"; the invite page offers "Use Face ID / Touch
ID next time" right after the account is made; `GET /account` (session) lists your passkeys with
"Add a passkey" and "Remove". The browser side is `chat/static/js/webauthn.js` (human-owned).

## 11. Round 2: phases, stop, folders, code blocks

### 11.1 Phases and events (changes §5)
- Order: `compressing` (if auto-compress runs) → `thinking` → `waiting` (if busy) → `thinking`
  again (first reasoning chunk, only after `waiting`) → `writing` (first content chunk). The first
  `thinking` = `{"phase": "thinking", "label": "Thinking…"}`, sent right before the first gateway
  call, so the orb clouds over and the label shimmers at once. A phase is never sent twice in a row.
  (Until 2026-11-20 the first phase was `reading`, "Reading your message…".)
- While a phase lasts 5 s or more, the page adds the seconds to its label ("Thinking… · 12 s"); after
  25 s with nothing written yet it adds "the model may still be starting up" (the PC may just have woken).
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
- A client that disconnects no longer stops the answer (§5.1); `run_answer` itself still stores what arrived when
  its generator is closed.

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

## 12. Round 3: profiles, sources and tools

### 12.1 Profile settings (per user, per profile)
Each user has their own settings for each profile they may use (`general` needs `chat`, `evidence`
needs `evidence`; `admin` may use both; otherwise **403**; unknown profile **404**):
`instructions` (0–4000 characters), `web` (bool, default **true**: the model may search the web and
open pages when it helps) and a list of **source cards**:
- `pubmed` (at most one per profile, else **409**): PubMed via the free E-utilities, with `filters`
  `{"types": ⊆ [systematic_review, meta_analysis, rct, guideline, review], "years": 1–50 or null,
  "humans": bool, "languages": ⊆ [english, dutch]}` (unknown values are dropped, never an error).
- `website`: one domain (`https://www.rivm.nl/x` → `rivm.nl`); searched with SearXNG `site:`.
- `file`: a document (PDF via pypdf, text, Markdown, HTML; ≤ 5 MB, ≤ 200 000 characters of text,
  else **413**; unreadable **415**). Its whole text goes into the system prompt.
Every card has a `note` (0–1000 characters: how the model should use it) and `enabled`.
A user's Clear Source starts with two cards (seeded once, deletable): PubMed (reviews,
meta-analyses, RCTs and guidelines, last 10 years, humans) and rivm.nl.

Endpoints (JSON; a source as `{"id", "kind", "title", "domain", "note", "filters", "enabled",
"created", "tokens"}`, `tokens` = ceil(chars/4) of a document's text, else 0):
- `GET /api/profiles/<p>` → `{"profile", "name", "instructions", "web", "sources", "budget"}`;
  `budget` = `{"used", "max", "parts": [{"label", "tokens"}]}`: `used` = ceil(chars/4) of the
  profile's system prompt (§12.2), `max` = the context size (§6), parts = "Instructions" and one
  per enabled document, summing to `used`.
- `PATCH /api/profiles/<p>` `{"instructions"?, "web"?}` → the profile; nothing known or wrong
  types → **400**.
- `POST /api/profiles/<p>/sources` `{"kind": "pubmed"|"website", "url"?, "note"?, "filters"?}` →
  **201** the source; bad url/kind/note → **400**.
- `POST /api/profiles/<p>/documents` (multipart `file`) → **201** the source (title = file name).
- `PATCH /api/profiles/<p>/sources/<id>` `{"note"?, "enabled"?, "filters"?}` → the source;
  someone else's or another profile's → **404**; nothing to change → **400**.
- `DELETE /api/profiles/<p>/sources/<id>` → **204**.
- `GET /profiles`: the profiles page (no session → **303** `/login`), one tab per profile you may use.

### 12.2 The system prompt
The base prompt (§11.4), then "Extra instructions from the user:" + the instructions, then
"Sources you can use:" with a line per enabled pubmed/website card (+ its note) and a line for the
web when `web` is on, plus how to cite (`[n]`), then each enabled document as
`Document <title> (<note>):\n```\n<text>\n```` (no parentheses when the note is empty).

### 12.3 Tools
The chat offers the model tools (OpenAI `tools`) from the profile: `web_search` + `open_page` when
`web` is on; `search_site` (enum of the enabled website domains) + `open_page` for websites;
`search_pubmed` when a PubMed card is enabled. Up to 4 tool rounds per answer; the last call has no
tools, so the model must answer. Each tool call shows a phase: `searching` ("Searching PubMed for
"x"…", "Searching rivm.nl for "x"…", "Searching the web for "x"…") or `reading` ("Reading
www.rivm.nl…"); these may repeat, and `writing` may come again after them.
- Web search goes to SearXNG (`SEARXNG_URL`, setting; empty → the tool answers an error).
- `open_page` fetches only public http(s) pages on ports 80/443 (every address the host resolves
  to must be public: no private, loopback, link-local, CGNAT/Tailscale or reserved ranges, checked
  again on each of at most 3 redirects), HTML or text only, at most 2 MB read and 6000 characters
  of readable text handed on. With `web` off it may only open the profile's website domains.
- Results get numbers per answer (`[1]`, `[2]`, … the same URL keeps its number); the tool result
  tells the model "Cite these as: [n] url". `done` gets `"sources": [{"n", "title", "url", "kind",
  "meta"?}]` and the assistant message stores them (`GET /api/chats/<id>` messages have `sources`).
- A failing tool never fails the answer: its result is "Error: <reason>" for the model.

### 12.4 Sources in the page
Under an answer with sources: stream.js `sourcesHtml` cards; `[n]` in the text becomes a link
(`citeLinks`). The profile menu has "Profile settings…" → `/profiles`.

### 12.5 The profiles page (`profiles.js`)
Tabs `.pf-tab[data-profile]` (`aria-selected` on the open one); `#pf-instructions` and `#pf-web`
(saved as you type, after a short pause); `#pf-sources` with one `article.src-card[data-source]
[data-kind]` per card (a note `textarea[data-note]`, `input[data-enabled]`, `[data-delete]`, and for
PubMed the filters `[data-type]`, `select[data-years]`, `[data-humans]`, `[data-lang]`);
`#add-pubmed`, `#add-website` (asks the address), `#add-doc` (file input). The context bar
`#budget-bar` has one `<i>` per budget part (`--w` = tokens / max) and a line at half;
`#budget-text`, `#budget-legend`, `#budget-hint` explain it; the bar gets class `over` above half.

## 13. Clear Source v2: research by the app, a checker for every answer

Clear Source was called EvidenceHunt until 2026-11-19 (the name is taken). Only the shown name changed: the profile id, the right and the CSS stay `evidence`.

The model is never trusted to stay within the evidence; the app enforces it.

### 13.1 The checker (`chat/coder/verify.py`, pure functions)
- `normalize(text)`: lower case; curly quotes → straight (`‘’`→`'`, `“”„`→`"`); `–—−` → `-`; soft hyphens
  removed; every run of whitespace → one space; stripped.
- `find_quote(quote, text)`: True when the quote appears in the source text after `normalize` on both. An
  ellipsis (`...`, `…` or `[...]`) splits the quote into parts that must all appear, in order. Parts shorter
  than 3 characters are ignored; a quote with no part left is False.
- `quotes(answer)`: every quote in the Markdown answer, in order: each blockquote line group (`> …`, the
  surrounding quote marks stripped) and each inline quote in `"…"` or `“…”` of at least 4 words. Each as
  `{"text", "cites": [n, …]}`: the `[n]` (also `[1, 2]`) right after the quote, or for a blockquote anywhere
  in it.
- `sentences(answer)`: the answer's sentences outside quotes, headings and code, as `{"text", "cites"}`.
- `check(answer, sources)` (sources = `[{"n", "text"}]`) → `{"ok", "quotes": [{"text", "cites", "ok", "why"}],
  "uncited": [sentence texts], "bad_refs": [n…]}`: a quote is ok when it is found in at least one source it
  cites (`why`: `"not in source n"`, `"no source"` when it cites none); `uncited` = sentences of 8+ words with
  no `[n]` (the closing line on evidence strength is allowed without one); `bad_refs` = numbers cited that are
  not a source; `ok` = every quote ok and no bad refs.

### 13.2 The research pipeline (`chat/coder/research.py`), Clear Source only
1. **Plan**: one non-streaming call (`gateway.complete`) with `PLAN_PROMPT` asks for a JSON list of up to 6
   searches `{"source": "pubmed" | "site:<domain>" | "web", "query"}`; `parse_plan(text, cfg)` reads it
   (also inside a ```` ``` ```` fence), keeps only sources the profile enables, drops empty queries, max 6.
   An unusable plan falls back to one PubMed search with the question (when PubMed is enabled).
2. **Search** (`gather`): PubMed → `tools.pubmed_search` (with the card's filters); a site → `tools.web_search`
   with `site=` and then `tools.fetch_page` of the top 2 results; web → `web_search` + top 2 pages. Every source
   gets a number in order, `{"n", "title", "url", "kind", "meta", "text"}` (`text` = the abstract or page
   text, ≤ 4000 characters), duplicates by URL kept once, at most 10. Every step goes into the **trail**:
   `{"kind": "search", "source": "PubMed" | "<domain>" | "the web", "query", "found", "url"}` (`url` = the
   search on PubMed's own site for PubMed, else null) and `{"kind": "read", "title", "url"}` per page read.
   A failing search is a trail step with `"found": 0, "error"`, never an exception.
3. **Nothing found** (no sources): the app answers `NO_EVIDENCE[lang]` itself (Dutch when the question looks
   Dutch: `looks_dutch`), the model is not asked.
4. **Answer**: `evidence_messages(question, sources, cfg)` = a system prompt with the rules (only these
   sources, never own knowledge; short answer first, then the reasoning built from verbatim quotes
   `> "…" [n]`, then how strong the evidence is; say so when the sources do not answer the question) and the
   numbered excerpts; the answer streams like any other (no tools).
5. **Check**: `verify.check(answer, sources)` on the full answer; the report is stored with the message.

Events (in this order): `phase reading` → `phase thinking` "Choosing search terms…" → per search `phase
searching` (its label) and a `trail` event `{"step": {...}}` → `phase reading` "Reading N sources…" →
`phase summarising` "Building the answer from N sources…" → `phase writing` + `delta`s → `verify`
`{"report"}` → `done` (also with `sources`, `trail`, `verify`). Messages in `GET /api/chats/<id>` have
`trail` (list) and `verify` (object or null).

### 13.3 Trail and thinking for every profile
- The General profile's tool calls (§12.3) also send a `trail` event per call and store the trail.
- Reasoning (Think mode) is no longer thrown away: each reasoning chunk is a `thinking` event `{"text"}`;
  the assistant message stores it as `thinking` (text, `""` when none) and the seconds spent as `thought_s`.
- The page (stream.js helpers, app.js wiring): `trailHtml(steps)` → `<details class="trail">` with
  "Searched PubMed for "x" · 5 found ↗" lines; `thoughtHtml(text, secs)` → `<details class="thought">
  <summary>Thought for N s</summary>`; `markVerify(bub, report)` gives each quote a ✓ or ⚠ and adds a note
  under the answer for sentences without a source.

### 13.4 PubMed strictness (design C) and the app icon
- A PubMed card's filters may carry `"preset": "strict" | "balanced" | "broad" | "custom"`.
  `profiles.PRESETS`: strict = systematic reviews, meta-analyses, guidelines, last 10 years, humans;
  balanced = + randomised trials, last 15 years, humans; broad = no type or year filter. `clean_filters`
  keeps the preset; a known preset sets the types/years/humans of that preset (languages stay).
- Each user picks an app icon: `PATCH /api/me {"icon": "aero" | "blossom"}` (default: `blossom` when their
  theme is Blossom, else `aero`), shown by `GET /api/me`. `GET /manifest.json` and `GET /apple-touch-icon.png`
  serve the signed-in user's icon (no session: aero); the pages link those two URLs.

## 14. Clear Source, round 2: MeSH, Dutch guidelines, study types, detailed answers

### 14.1 MeSH (PubMed's medical subject headings)
- The plan (§13.2 step 1) may give a PubMed search a `"mesh": ["<English MeSH heading>", …]` list (max 4).
  `parse_plan` keeps it (strings, stripped, ≤ 80 characters, empty ones dropped) only for PubMed items and only
  when non-empty.
- `tools.mesh_check(client, terms)` checks the model's terms against PubMed's MeSH database and returns the
  official headings that exist, in the order asked, each once: one `esearch` (`db=mesh`) for all terms
  (`"<term>"[MeSH Terms]` joined by ` OR `) and one `esummary` (`db=mesh`) of the ids found; a term counts when
  it equals (case-insensitive) one of a record's `ds_meshterms` (its heading or an entry term); the record's
  heading (`ds_meshterms[0]`) is returned. Made-up terms vanish. Any failure → `[]` (the search goes on
  without MeSH).
- `tools.pubmed_term(query, filters, mesh)`: with headings, the first part becomes
  `(("<h1>"[MeSH Terms] AND "<h2>"[MeSH Terms]) OR (<query>))`: the headings for precision, the words for
  what MeSH does not cover (new drugs, for example).
- `tools.pubmed_search(..., mesh=None, info=None)`: when `info` is a dict, it gets `"term"` (the search
  sent), `"translation"` (PubMed's `querytranslation`) and `"count"` (all hits, int).
- The trail step of a PubMed search gets `"mesh"` (the headings used, a list) and `"translation"`; its `url`
  opens PubMed with the exact term that was sent. The page shows the headings as chips.

### 14.2 Study types and the order of sources
- `tools.study_type(pub_types)` → one label from PubMed's publication types, the first that applies:
  Practice Guideline/Guideline → `"Guideline"`, Meta-Analysis → `"Meta-analysis"`, Systematic Review →
  `"Systematic review"`, Randomized Controlled Trial → `"RCT"`, Clinical Trial (any phase) → `"Clinical trial"`,
  Observational Study → `"Observational study"`, Review → `"Review"`, Case Reports → `"Case report"`; else
  `None`. `pubmed_search` gives every article `"type"` (from all its publication types).
- A PubMed source gets `"type"` and `meta` = type · journal · year (the non-empty ones); a page from a website
  card gets `"site": "<domain>"` (web pages `None`).
- `research.order(sources)`: guidelines first, then the strongest studies: website-card pages (Dutch guideline
  sites), PubMed guidelines, meta-analyses and systematic reviews, RCTs, other PubMed, web pages; stable within
  a group; renumbered from 1. The answer is written from the ordered list; the page shows each source's type as
  a badge.

### 14.3 Default sources: Dutch guidelines
- A new Clear Source profile starts with PubMed and these website cards (`profiles.SEED`): richtlijnendatabase.nl
  (medical specialists' guidelines), farmacotherapeutischkompas.nl, kinderformularium.nl, rivm.nl (incl. the
  LCI guidelines), lareb.nl, ge-bu.nl, jgzrichtlijnen.nl (youth health care, NCJ), palliaweb.nl (palliative care, PZNL),
  thuisarts.nl (NHG's patient information). NHG's own guideline site, NICE and GGZ-standaarden cannot be read by the
  app (a bot check; text loaded by JavaScript), so they are not included.
- A new Clear Source profile starts with general web search **off** (`profiles.WEB_OFF`): open web results let
  advocacy sites in. The add-defaults button below turns it off again too.
- `POST /api/profiles/<profile>/defaults` (X-Coder header) adds the default cards the user does not have yet
  (PubMed by kind, websites by domain) and returns `{"added": n, "sources": [...]}`. The profiles page has a
  button `#add-defaults` for it.

### 14.4 Always detailed answers
The rules (§13.2 step 4) ask for a long, detailed answer in Markdown with `##` headings: a short answer first,
then what the Dutch guidelines say, what the studies show, dosing and special groups (only when the sources
cover them) and how strong the evidence is, each part built from verbatim quotes. The plan prompt asks for
PubMed searches with MeSH headings and the 1–3 guideline sites that fit the question.

## 15. Clear Source v3: the research flow

The model is slow to trust but quick to read: Clear Source now reads every source on its own and keeps only what is
quoted word for word. Four stages, the same four the drop shows (thinking, searching, reading, writing).

### 15.1 Understand (thinking)
The plan (§13.2 step 1) is an object `{"about": "<the question in one sentence>", "searches": [...]}` (the old list
form still works). `parse_plan` reads both; `plan_about(text)` returns the about sentence (whitespace collapsed, ≤ 300
characters, `""` when missing). The trail gets a step `{"kind": "plan", "about", "queries": [...], "mesh": [...]}`.

### 15.2 Search (searching)
- A PubMed search asks for `PUBMED_N` (8) articles. When it finds fewer than `MIN_FOUND` (3) and it used filters or
  MeSH, it is tried once more without both; that trail step has `"broader": true`.
- A website result on PubMed Central (`tools.pmc_id(url)`, e.g. `pmc.ncbi.nlm.nih.gov/articles/PMC123/`) is read
  with `tools.pmc_text` (E-utilities `efetch db=pmc`: the abstract and body paragraphs) instead of the web page.
- Every page the search opens is a trail step `{"kind": "open", "title", "url"}` (with `"error"` when it could not be
  read). Up to `MAX_SOURCES` (20) candidates over all rounds.

### 15.3 Bot checks
`tools.looks_blocked(title, text)`: a "page" that is a bot check (reCAPTCHA, "Checking your browser", Cloudflare,
NHG's "Beveiligingscontrole") makes `fetch_page` fail with "blocked by a bot check"; it never becomes a source.

### 15.4 Read one by one (reading)
Each new candidate is read on its own: `read_one(question, src, settings, complete)` sends `extract_messages` (the
`EXTRACT_PROMPT`, the question, the source) and reads the reply with `parse_quotes` (`{"quotes": [...]}` or a list;
strings of 4+ words, quote marks stripped, each once, max `MAX_PASSAGES` = 4). A passage counts only when
`verify.find_quote` finds it in the source. The trail step: `{"kind": "read", "n", "title", "url", "quotes": k,
"status": "used" | "not relevant" | "error"}`. Used sources keep their `"passages"`.

### 15.5 More rounds, writing and checking (writing)
- `investigate(question, cfg, settings, complete, t, stop, out)` runs the stages and yields their events; fewer than
  `MIN_USED` (3) useful sources after a round → one more round (`MAX_ROUNDS` = 2): `followup_messages` shows the model
  the searches so far and asks for new ones; searches already done are skipped. `out["used"]` = the useful sources,
  ordered (§14.2), max `MAX_USED` (10), numbered from 1; `out["trail"]` = every step.
- The answer is written from the passages only (`evidence_messages` lists them under each source) and checked against
  the full source texts. `public_sources` shows the first passage as the source's quote.
- `verify.summary_sentences(answer)`: in an answer with headings, the sentences before the first heading and under a
  summary heading (short answer, kort antwoord, summary, samenvatting, conclusion, strength, sterkte…) summarise the
  quotes and are not listed as uncited.
- Phases: `thinking` "Understanding the question…" → `searching` per search → `reading` "Reading i of k: <title>…"
  → (`thinking` "Looking further with other words…" → …) → `writing` "Writing the answer from n sources…".

### 15.6 The look (design round 13b)
- The trail of a Clear Source answer shows the four stages, each with the drop (`stream.js trailHtml`); while it
  researches, the current stage's drop moves (`animateTrail`). Searches and reads are rows; broader searches are
  indented.
- The opening summary sits in a box (`.short`); ✓ / ⚠ come right after the quote; numbers are plain and readable.
- Sources under an answer are grouped lists: Guidelines, Studies, Other sources, each with its kind as a word.
- The sources page groups the sources (`profiles.group_of`: guidelines, medicines, research, documents, websites)
  with their names (`profiles.label_of`); each source is a row (a bubble with its letter, name, one line, switch);
  its note and PubMed strictness open below it. Profile API sources carry `"group"` and `"label"`.

## 16. Files in and out

Files go both ways: you attach PDF, Word, Excel, HTML or text files to a message, and the model can make a
file for you (Markdown, text, CSV, HTML, Word, Excel or PDF). Any answer can also be downloaded as a file.
The file work lives in a new module `chat/coder/files.py` (pure functions, no database, no network).

### 16.1 Reading uploads (`files.read_upload`)
- `POST /api/files` (multipart field `file`, a session) takes up to `MAX_UPLOAD` = 10 MB (more → **413**
  `{"error": "That file is too big (max 10 MB)."}`). It stores the original bytes and the text read from them and
  answers **201** `{"id", "name", "size", "kind", "tokens"}` (`tokens` = `profiles.tokens(text)`). A file that can't be
  read → the `FileError`'s status (413 or 415) and `{"error": message}`. Names: last path part, max 120 characters.
- `files.read_upload(name, data)` → `{"kind", "text"}`; `kind` from the extension (lower case):
  `.pdf` → `pdf`, `.docx` → `docx`, `.xlsx` → `xlsx`, `.html`/`.htm` → `html`, `.csv` → `csv`, `.doc`/`.xls` → 415
  "Save it as .docx or .xlsx first.", anything else → `text`.
  - pdf: `pypdf`, at most 300 pages, pages joined by a blank line. Unreadable → 415 "That PDF could not be read.";
    no text → 415 "That PDF has no text (is it a scan?)."
  - docx and xlsx are zip files: more than 2000 entries or more than 50 MB unpacked → 413 "That file is too big
    inside."; a bad zip → 415 "That Word file could not be read." / "That Excel file could not be read."
  - docx (`files.docx_text`, stdlib `zipfile` + `xml.etree`): `word/document.xml`, the body's paragraphs and tables in
    order. A paragraph = its `w:t` texts joined, `w:tab` → a tab, `w:br` → a newline; style `Heading1`..`Heading6` →
    that many `#` and a space in front; a style starting with `List` or a `w:numPr` → `- ` in front; empty paragraphs are skipped. A
    table row = its cells' texts joined by ` | `. Lines joined by `\n`.
  - xlsx (`files.xlsx_text`, `openpyxl` read-only, values not formulas): per sheet `## <title>` and then its rows,
    cells joined by ` | ` (None → empty, trailing empty cells dropped, empty rows skipped), at most 5000 rows in all.
  - html: `tools.html_text(text)` (the title is dropped). text and csv: UTF-8 (a BOM is dropped); not UTF-8 or a NUL
    byte → 415 "Only PDF, Word, Excel, HTML and text files."
  - Text that is empty after strip → 415 "That file has no text."; more than 200 000 characters → 413 "That file is too
    long (max 200 000 characters)."
- The database keeps per file: `kind` (default `text`), `text` (the read text; older rows: their content decoded) and
  `made` (0, or 1 for files the model made, §16.3). `GET /api/files/<id>` sends the original bytes with the kind's
  type (`files.MIME`), `Content-Disposition: attachment; filename="<ascii name>"; filename*=UTF-8''<percent-encoded name>`,
  `X-Content-Type-Options: nosniff` and `Content-Security-Policy: default-src 'none'; sandbox`.
- In a message an attached file adds its **text** (never the raw bytes) after the typed text:
  `\n\nFile <name>:\n<fence>\n<text>\n<fence>` where `<fence>` = `files.fence(text)`: three backticks, or one more
  than the longest run of backticks in the text. Stored messages list their files as `{"id", "name", "size", "kind"}`.

### 16.2 Attached files in Clear Source
- `research.split_files(content)` → `(typed, [{"name", "text"}])`: the file blocks of §16.1 at the end of a user
  message are cut off; `typed` is what is left, stripped.
- `run_evidence` researches the typed question; the files are read first, as sources: `research.file_sources(files)`
  gives per file up to `DOC_PARTS` = 3 parts of `SOURCE_CHARS` characters (split at a blank line or a line end when one is
  in the last 1000 characters of a part), each a source `{"title": name or "name (part i of k)", "url": "", "kind":
  "file", "meta": "Your file", "text", "type": None, "site": None}`. `investigate(..., files=parts)` puts them first in
  the candidates and reads them before the first search (phase reading `Reading your file <name>…`). A file source
  with passages is used like any other; in the sources list its kind is `file` (group "Your files").

### 16.3 Files the model makes (the `make_file` tool)
- Every profile except Clear Source offers the tool `make_file` `{"name": string, "format": one of md, txt, csv,
  html, docx, xlsx, pdf, "content": string}`: "Make a file the user can download. content is Markdown for md, txt, html,
  docx and pdf; CSV (first row = the headers) for csv and xlsx. Use it when the user asks for a file or a document."
- `answer.run_answer` runs it itself (not `tools.run_tool`): `files.render(name, format, content)`; a `FileError` → the
  tool result `Error: <message>`. Otherwise the file is stored (`made` = 1, `text` = the content, `kind` = the
  format), the page gets `event: file` `{"id", "name", "size", "kind"}`, and the model gets `Made <name> (<size>
  bytes). The user sees a download card for it; do not repeat its content.` The phase while it runs: writing,
  `Making <name>…`. The assistant message is linked to the files it made (`message_files`), and `done` carries
  `"files": [{"id", "name", "size", "kind"}]`.

### 16.4 Rendering (`files.render(name, fmt, content)` → `(bytes, mime, filename)`)
- `fmt` not in `FORMATS` → `FileError(400, "Unknown format.")`; content longer than 200 000 characters →
  `FileError(413, "That file is too long.")`. `filename = files.safe_name(name, fmt)`: last path part, a trailing
  `.<fmt>` dropped, characters other than letters, digits, space, `.`, `_`, `-`, `(`, `)` → `_`, spaces collapsed,
  stripped of spaces and dots, max 80 characters, empty → `file`; then `.<fmt>`.
- Markdown is read by `files.blocks(md)` into a list of blocks:
  `{"t": "h", "level": 1-6, "runs"}`, `{"t": "p", "runs"}`, `{"t": "li", "ordered": bool, "runs"}`, `{"t": "code",
  "text"}`, `{"t": "table", "rows": [[runs, ...], ...]}` (the first row is the header; the `---` row is dropped), and
  `{"t": "hr"}`. Runs (`files.runs(text)`) are `{"text", "b", "i", "code", "href"}` from `**bold**`, `*italic*` /
  `_italic_`, `` `code` `` and `[text](http(s)://url)` (other link targets stay plain text).
- md and txt: the content as UTF-8. csv: rows read with `csv` and written again, UTF-8 with a BOM; a cell starting
  with `=`, `+`, `-` or `@` that is not a number gets a `'` in front (no formulas in a spreadsheet you open).
- xlsx (`openpyxl`): one sheet, the CSV rows (max 10 000 rows, 100 columns, else `FileError(413, "Too many rows or
  columns.")`); cells that are numbers become numbers; text cells stay text even when they start with `=` (never a
  formula); the first row is bold.
- html: a complete page (`<!doctype html>`, `<meta charset="utf-8">`, `<title>`, a small inline style) with the blocks;
  every text is escaped and only http(s) links are links: no HTML from the content ever reaches the page.
- docx (`python-docx`): headings (`add_heading`, level 1-6), paragraphs with bold / italic / monospace runs, links as
  their text, list items in the styles `List Bullet` / `List Number`, code blocks in a monospace font, tables in the
  style `Table Grid` with a bold first row, `hr` as an empty paragraph.
- pdf (`fpdf2`, A4): the DejaVu Sans font when `files.FONT_DIR` has `DejaVuSans.ttf` (and Bold/Oblique/Mono when
  there), else Helvetica with characters outside Latin-1 replaced by `?`; headings bigger and bold, list items with
  `•` (`-` with Helvetica) or their number, code in a monospace font, tables with `pdf.table()`.

### 16.5 Download an answer
- `GET /api/messages/<id>/export?format=md|docx|pdf|html` (a session; the message's chat must be yours, else **404**;
  another format → **400**) → the message rendered by `files.render`: its text, and when it has sources a `## Sources`
  list `[n] title — url`. The name is the chat's title. Sent like `GET /api/files/<id>`.

### 16.6 The page (`app.js`; markup and styles are human-owned)
- The paperclip takes the formats of §16.1 (`#file-input` `accept`); files can also be dropped on `#composer` or pasted.
  Each upload is a chip `<span class="file" data-kind=KIND>NAME <small>TOKENS</small><button class="x">×</button></span>`
  in `#file-chips`; a failed upload is a chip `<span class="file err">NAME: ERROR</span>` that goes away after 6 s.
- A `file` event (and an assistant message with files) draws in the answer, after its text:
  `<div class="files-out"><a class="file-card" data-kind=KIND href="/api/files/ID" download>` with
  `<span class="fi">EXT</span><b>NAME</b><small>SIZE</small></a>...</div>` (SIZE like `12 KB`).
- A finished assistant answer's meta row has a `.dl` button that opens `.dl-menu` with links to the export of §16.5:
  Markdown, Word, PDF, HTML.
