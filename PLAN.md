# PLAN: Dewdrop passkeys

Sign in to Dewdrop with Face ID, Touch ID or a phone (passkeys, chat/SPEC.md §10). Small steps:
every task names one file and one or two functions; the steps are in that function's docstring or
TODO comment (`grep -n "task Pxx" <file>`), or in the "Pxx details" below. Each task has its own test.

Rules for every task:
- Non-interactive: nobody reads questions. Make the edit, run the accept command, fix what fails, stop.
- Only change what the task names. Don't refactor, rename or move other code.
- Never read a whole large file (`chat/coder/app.py` is ~600 lines): `grep -n`, then `sed -n 'a,bp'`.
- Python: only the packages in `chat/requirements.txt` (the `webauthn` package does the crypto).
- Do not touch tests, `chat/SPEC.md`, `chat/static/css/`, `chat/static/js/`, `chat/coder/templates/`,
  `Makefile`, `docs/`. The pages and browser code for passkeys are already written.

- [x] P01: In chat/coder/db.py add the passkey tables and methods, and in chat/coder/config.py the settings RP_ID and ORIGIN (see "P01 details")
  accept: make chat-test K="test_step_passkeys and test_db_and_settings"
- [x] P02: In chat/coder/passkeys.py fill in `user_handle` and `registration_options` as their docstrings say
  accept: make chat-test K="test_step_passkeys and test_registration_options"
- [x] P03: In chat/coder/passkeys.py fill in `verify_registration` as its docstring says
  accept: make chat-test K="test_step_passkeys and test_verify_registration"
- [x] P04: In chat/coder/passkeys.py fill in `authentication_options` and `credential_id` as their docstrings say
  accept: make chat-test K="test_step_passkeys and test_authentication_options"
- [ ] P05: In chat/coder/passkeys.py fill in `verify_authentication` as its docstring says
  accept: make chat-test K="test_step_passkeys"
- [ ] P06: In chat/coder/app.py add the passkey registration, list and delete routes (see "P06 details")
  accept: make chat-test K="TestPasskeyRegister"
- [ ] P07: In chat/coder/app.py add the passkey sign-in routes (see "P07 details")
  accept: make chat-test K="TestPasskeyLogin and not account"
- [ ] P08: In chat/coder/app.py add the page GET /account (see "P08 details")
  accept: make chat-test
- [ ] P09: Make the browser tests pass: run `make chat-browser-test`, read the FAIL line, fix only the app code in chat/coder/
  accept: make chat-test && make chat-browser-test
- [ ] P10: Add a "## Passkeys" section to chat/README.md (see "P10 details")
  accept: grep -q "## Passkeys" chat/README.md && grep -q "RP_ID" chat/README.md && make chat-test

## P01 details
In `chat/coder/db.py` follow the `TODO (task P01)` comment (`grep -n "task P01" chat/coder/db.py`).
In `chat/coder/config.py` add two settings like the others (add them to `__slots__` too):
`RP_ID` (default `"localhost"`) and `ORIGIN` (default `"http://localhost:8090"`).

## P06 details
Add inside `create_app` in `chat/coder/app.py`, next to the other `/api/` routes. Import
`from . import passkeys` at the top (with the other `from . import` line) and use `hashlib`.
Helper (inside `create_app`): `_wa_set(resp, key)` sets cookie `coder_wa` = key with `httponly=True,
samesite="lax", path="/api/passkeys", max_age=300, secure=app.state.settings.SECURE_COOKIES`.
Helper: `_wa_take(request, kind)`: `key = request.cookies.get("coder_wa")`; none -> `None`; else
`return app.state.db.take_challenge(hashlib.sha256(key.encode()).hexdigest(), kind)`.
1. `POST /api/passkeys/register/options`: session via `_require_session(request, app.state.db)`
   (return it if it's a Response); `existing = [bytes(p["credential_id"]) for p in db.list_passkeys(username)]`;
   `js, challenge = passkeys.registration_options(settings, username, existing)`;
   `key = secrets.token_urlsafe(32)`; `db.put_challenge(sha256(key), "register", username, challenge)`;
   return `Response(js, media_type="application/json")` with `_wa_set(resp, key)`.
2. `POST /api/passkeys/register/verify`: session; body = request JSON (`{}` on error);
   `got = _wa_take(request, "register")`; if not got, or `got[1] != username`, or
   `body.get("credential")` is not a dict -> 400 `{"error": "That passkey could not be added. Try again."}`.
   `cred_id, public_key, count = passkeys.verify_registration(settings, body["credential"], got[0])`
   (PasskeyError -> the same 400). Name = `(str(body.get("name") or "").strip() or "Passkey")[:60]`.
   `return JSONResponse(db.add_passkey(username, cred_id, public_key, count, name), status_code=201)`.
3. `GET /api/passkeys`: session; `[{k: p[k] for k in ("id", "name", "created", "last_used")} for p in db.list_passkeys(username)]`.
4. `DELETE /api/passkeys/{passkey_id}` (int): session; `db.delete_passkey(passkey_id, username)`
   False -> 404 `{"error": "not found"}`; else `Response(status_code=204)`.

## P07 details
1. `POST /api/passkeys/login/options` (no session): `js, challenge = passkeys.authentication_options(settings)`;
   new key; `db.put_challenge(sha256(key), "login", None, challenge)`; return the JSON with `_wa_set`.
2. `POST /api/passkeys/login/verify` (no session), in this order:
   - `ipkey = f"ip:{request.client.host if request.client else 'unknown'}"`;
     `db.has_recent_failures(ipkey)` -> 429 `{"error": "Too many tries. Wait 15 minutes."}`
   - body JSON (`{}` on error); `got = _wa_take(request, "login")`; `cred = body.get("credential")`
   - in one `try`: no `got` or `cred` not a dict -> raise `passkeys.PasskeyError`;
     `pk = db.get_passkey(passkeys.credential_id(cred))`, None -> raise PasskeyError;
     `count = passkeys.verify_authentication(settings, cred, got[0], bytes(pk["public_key"]), pk["sign_count"])`
   - `except passkeys.PasskeyError`: `db.record_login_failure(ipkey)` and return 401
     `{"error": "That passkey did not work."}`
   - `user = db.get_user(pk["username"])` (None -> the same 401); `db.touch_passkey(pk["id"], count)`;
     `db.set_last_login(username)`; a new session exactly like `/api/login` does
     (`secrets.token_urlsafe(32)`, `db.create_session`, the `coder_session` cookie with the same flags);
     return `{"username", "rights": user["rights"]}`.

## P08 details
`GET /account` (HTML): no session -> `RedirectResponse("/login", status_code=303)`; else
`templates.TemplateResponse(request, "account.html", {"me": {"username": username}})`
(the template already exists).

## P10 details
Append to `chat/README.md` a section `## Passkeys` (4–6 lines): what they are (sign in with Face
ID, Touch ID or a phone; the password stays as a backup), where you add one (`/account`, or right
after accepting an invite), and that production needs the settings `RP_ID=dewdrop.cedricschippers.dev`
and `ORIGIN=https://dewdrop.cedricschippers.dev` (passkeys only work on that exact domain).
