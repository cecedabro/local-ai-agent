# PLAN: Accounts and login

People log in with a name and password instead of pasting the status token; machines keep
their keys. Read `status/SPEC.md` §14 first. Judge: `status/tests/test_accept_accounts.py`
(protected: change the code, never the test).

Rules for every task:
- This run is non-interactive: nobody reads your questions. Never end with a question or a plan;
  make the edits, run the accept command, fix what fails, then stop.
- Each task has a short title below and its exact steps under "Txx details" further down this
  file. Read only your task's details (`grep -n "T03 details" PLAN.md`, then read that block).
- Never read a large file whole (`status/app.py` is ~1700 lines). Use `grep -n` and read only the
  lines you need (`sed -n '120,180p' status/app.py`).
- Python 3.11, standard library only. No f-string with a backslash inside `{...}`.
- Keep the old tests green: `make check` and `python3 -m unittest -q status/tests/test_contract.py`.
- Do not touch `harness/`, `status/static/`, `Makefile`, `status/SPEC.md` or any test file except
  your own `status/tests/test_unit_*.py`.

- [x] T01: Accounts table, password hashing and the first admin (see "T01 details" in PLAN.md)
  accept: python3 -m unittest -q status/tests/test_accept_accounts.py -k test_password_stored_with_scrypt
- [ ] T02: Login, logout and sessions (see "T02 details" in PLAN.md)
  accept: python3 -m unittest -q status/tests/test_accept_accounts.py -k test_login_logout_and_cookie -k test_session_expires -k test_seed_only_when_empty -k NoAccountsTest
- [ ] T03: Login rate limit (see "T03 details" in PLAN.md)
  accept: python3 -m unittest -q status/tests/test_accept_accounts.py -k test_rate_limit
- [ ] T04: Login required for pages and the API (see "T04 details" in PLAN.md)
  accept: python3 -m unittest -q status/tests/test_accept_accounts.py -k test_gates && python3 -m unittest -q status/tests/test_contract.py
- [ ] T05: Invites (see "T05 details" in PLAN.md)
  accept: python3 -m unittest -q status/tests/test_accept_accounts.py -k InviteTest
- [ ] T06: Rights for the queue and the chat (see "T06 details" in PLAN.md)
  accept: python3 -m unittest -q status/tests/test_accept_accounts.py -k RightsTest
- [ ] T07: Account admin API (see "T07 details" in PLAN.md)
  accept: python3 -m unittest -q status/tests/test_accept_accounts.py -k AdminTest -k test_change_password
- [ ] T08: Pages for login, invites and people (see "T08 details" in PLAN.md)
  accept: python3 -m unittest -q status/tests/test_accept_accounts.py
- [ ] T09: README for accounts (see "T09 details" in PLAN.md)
  accept: make status-test && grep -q ADMIN_PASSWORD status/README.md && grep -q "/people" status/README.md

---

## T01 details

In `status/app.py`:
1. Add `import hashlib` (if missing) and `import secrets`. Constants near `MAX_BODY`:
   `RIGHTS = ("admin", "chat", "evidence", "queue")`, `NAME_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{1,31}")`,
   `SESSION_DAYS = 30`, `INVITE_DAYS = 7`, `MIN_PASSWORD = 10`.
2. `SCHEMA` (grep `SCHEMA = `): add the three tables of SPEC §14.2 exactly
   (`users`, `sessions`, `invites`).
3. Module functions (before `class App`):
   - `hash_password(pw: str) -> str`: 16 random bytes salt (`secrets.token_bytes(16)`),
     `hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)`, return
     `f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"`.
   - `check_password(pw: str, stored: str) -> bool`: split on `$`, recompute with the stored
     n, r, p and salt, compare with `hmac.compare_digest`; any parse error → False.
   - `norm_rights(rights) -> list[str] | None`: None unless a list of strings all in `RIGHTS`;
     if `"admin"` is in it return `list(RIGHTS)`; else the sorted unique list.
   - `sha(text: str) -> str`: `hashlib.sha256(text.encode()).hexdigest()`.
4. `make_server(...)` and `App.__init__` get a new keyword `admin: tuple[str, str] | None = None`
   (pass it through). In `App.__init__`, after the schema is created: when `admin` is given and
   `select count(*) from users` is 0, insert the admin with `hash_password`, rights
   `"admin,chat,evidence,queue"` (store rights comma-separated), `created = clock()`; commit.
5. `main()` (grep `def main`): pass
   `admin=(os.environ.get("ADMIN_USER", "cedric"), os.environ["ADMIN_PASSWORD"]) if os.environ.get("ADMIN_PASSWORD") else None`.

## T02 details

1. `App.accounts_exist() -> bool`: `select 1 from users limit 1`.
2. `App.session_user(cookie_header: str) -> dict | None`: parse with
   `http.cookies.SimpleCookie` (import `http.cookies`; ignore `CookieError`), take
   `agent_session`, look up `sessions` by `sha(value)`; None if missing, if
   `clock() - seen > SESSION_DAYS * 86400`, or if the user is missing or disabled. Otherwise
   update `sessions.seen` and `users.last_seen` to `clock()` and return
   `{"name": ..., "rights": sorted list}`.
3. `App.login(name, password, addr) -> tuple[int, dict, str | None]` (the third value is the
   new session id): user exists, not disabled and `check_password` → create a session
   (`secrets.token_urlsafe(32)`, store `sha(id)`, name, created, seen) and return
   `200, {"name", "rights"}, id`; else `401, {"error": "wrong name or password"}, None`.
   Leave `addr` unused for now (T03 uses it).
4. `App.logout(cookie_header)`: delete the session row (if any).
5. In the handler (`make_server`, class `Handler`): add a helper
   `send_cookie(code, body, session_id)` like `send` that adds the header
   `Set-Cookie: agent_session=<id>; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=2592000`,
   or `agent_session=; Path=/; Max-Age=0` when `session_id` is `""` (logout).
6. Routes, before the token check in `do_POST` (grep `def do_POST`): `POST /login` (JSON body
   `name`, `password`; use `self.client_address[0]` as `addr`) and `POST /logout` (always 200).
   In `do_GET`: `GET /api/me` → 200 `session_user(...)` or 401; `GET /login` → a minimal page
   for now: `render_login_page()` returning HTML with `<form id="login">` and inputs `name` and
   `password` (T08 makes it nice).

## T03 details

1. In `App.__init__`: `self.failures: dict[str, list[float]] = {}` (client address → times of
   failed logins).
2. `App.limited(addr) -> bool`: drop times older than 15 minutes (`clock() - t >= 900`);
   True when 5 or more remain.
3. `App.failed(addr)`: append `clock()`.
4. In `App.login`: if `limited(addr)` → `429, {"error": "too many attempts, try again later"}, None`
   before checking the password; on a wrong password call `failed(addr)`.

## T04 details

The gate applies only when `app.accounts_exist()`; without accounts nothing changes.
In the handler add `def who(self) -> dict | None` = `app.session_user(self.headers.get("Cookie", ""))`
and `def machine(self) -> bool` = `app.authorized(self.headers.get("Authorization", ""))`.
1. `do_GET`: always allow `/healthz`, `/static/...`, `/login`, `/invite`. For `/`, `/chat`
   (and later `/people`): if accounts exist and not `machine()` and `who()` is None → send
   303 with header `Location: /login` (write a small `redirect(location)` helper). For
   `/api/status`, `/api/status/head`, `/partial`, `/api/me`: same check but answer 401 JSON.
2. `do_POST`: keep the existing token check for every machine endpoint (SPEC §14.4 last row).
   Do NOT change the existing behaviour for requests with the status token.
3. `GET /invite` → a minimal page for now: `render_invite_page()` with `<form id="accept">`
   and two password inputs `password` and `password2` (T08 makes it nice).
4. Check `python3 -m unittest -q status/tests/test_contract.py` still passes (it runs without
   accounts).

## T05 details

1. `App.invite(by: dict, body) -> tuple[int, dict]`: 403 unless `"admin"` in `by["rights"]`;
   400 unless `NAME_RE.fullmatch(name)` and `norm_rights(rights)` is not None and non-empty;
   409 if a user with that name exists or an invite with that name is unused and not expired.
   Create `token = secrets.token_urlsafe(32)`, store `sha(token)`, name, rights (comma-joined),
   created, `expires = clock() + INVITE_DAYS * 86400`. Return
   `201, {"url": "/invite#" + token, "expires": iso(expires)}`.
2. `App.accept(body, addr) -> tuple[int, dict, str | None]`: if `limited(addr)` → 429; find the
   invite by `sha(token)`: missing → 404 (and `failed(addr)`); used or expired → 410 (and
   `failed(addr)`); password shorter than `MIN_PASSWORD` → 400; else insert the user, mark the
   invite used, create a session like `login` does and return `200, {"name", "rights"}, id`.
3. Routes in `do_POST`, before the token check: `POST /api/invites` (needs `who()`; logged out
   and no token → 401) and `POST /api/invites/accept` (no login). (`GET /invite` exists since T04.)

## T06 details

1. Queue rights: the job routes and `/api/runs/<id>/(pause|resume)` in `do_POST` currently
   require the status token. Allow them also when `who()` has `"queue"` in its rights; a
   logged-in user without it → 403; nobody → 401. `/api/runs/<id>/control` stays token-only.
2. Gateway (`Gateway.client_for`, and the handler's `gateway()` method): when the
   `Authorization` key matches nothing, look at the session: with `"chat"` →
   client `("chat:" + name, "interactive")`; logged in without it → 403 in OpenAI's error shape
   `{"error": {"message": "no chat right"}}`; nobody → 401 as now.
3. Machine-only endpoints never accept a session (SPEC §14.4 last row).

## T07 details

All need `"admin"` (else 403; logged out → 401). Times as ISO with `iso()`.
1. `GET /api/users` → list of `{"name", "rights", "created", "last_seen", "disabled"}` sorted
   by name (`disabled` as bool, `rights` as sorted list).
2. `POST /api/users/<name>` with optional `rights` (→ `norm_rights`, None → 400) and
   `disabled` (bool): unknown user → 404; for the admin's own account, dropping `"admin"` or
   `disabled: true` → 409. Disabling deletes that user's sessions. Answer 200 with the user.
3. `POST /api/users/<name>/delete`: own account → 409; unknown → 404; else delete the user and
   their sessions, 200 `{"ok": true}`.
4. `POST /api/me/password` (any logged-in user) `{"old", "new"}`: wrong old → 403; new shorter
   than `MIN_PASSWORD` → 400; else store `hash_password(new)`, 200 `{"ok": true}`.
Use `re.fullmatch(r"/api/users/([a-z0-9_.-]+)(/delete)?", path)` in `do_POST`.

## T08 details

Pages (server-rendered like `render_page`; use the existing `HEAD`, `DEFS`, `card` classes; no
new CSS, the stylesheet is human-owned):
1. `render_login_page()`: header with the title and the theme switch (`theme_switch()`), one
   `card` with `<form id="login">` (inputs `name` with `autocomplete="username"`, `password`
   type password with `autocomplete="current-password"`, a `primary` button "Log in") and
   `<div id="msg" class="small muted">`. Script: on submit `fetch('/login', {method: 'POST',
   headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name, password})})`; on
   200 `location.href = '/'`; on 429 show "Too many attempts, try again in 15 minutes."; else
   "Wrong name or password.".
2. `render_invite_page()`: same frame, `<form id="accept">` with `password` and `password2`
   (both `autocomplete="new-password"`); the script checks they match and are ≥ 10 characters,
   posts `{token: location.hash.slice(1), password}` to `/api/invites/accept`, then goes to `/`.
3. `render_page` and `render_chat_page` get a `user: dict | None` parameter (pass `who()` from
   `do_GET`). When `user` is set: no `<form id="unlock">`; the header shows
   `<span class="muted small">{name}</span>`, a link `<a href="/people">People</a>` only for
   admins, and `<button type="button" id="logout" class="link small">Log out</button>` (script:
   POST `/logout`, then go to `/login`); the add-job card only with `"queue"`; the chat card only
   with `"chat"`; render `<body>` without the `locked` class.
4. Chat script (grep `CHAT_SCRIPT`): send the `Authorization` header only when `tok()` is
   non-empty.
5. `GET /people` (admin; logged out → 303 /login; else 403): `render_people_page(users)` with a
   table of accounts (name, rights, last seen, disabled) with buttons
   `data-user="<name>" data-act="disable|enable|delete"` and `<form id="invite">` (input `name`,
   one checkbox per right in `RIGHTS` with `name="rights" value="<right>"`) whose script posts to
   `/api/invites` and shows `location.origin + url` in a `<code>` the admin can copy.
   Escape every name with `html.escape`.

## T09 details

In `status/README.md` (append; don't rewrite): a section `## Accounts` saying people log in at
`/login`; the first admin comes from `ADMIN_USER` (default `cedric`) and `ADMIN_PASSWORD`,
created only when there are no accounts; admins invite people from `/people` (rights chat,
evidence, queue, admin); machines keep `STATUS_TOKEN` and the gateway keys. Add `ADMIN_USER`
and `ADMIN_PASSWORD` rows to the environment variables table.
