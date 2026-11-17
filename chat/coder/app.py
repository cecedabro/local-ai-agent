"""FastAPI application for the coder chat app.

Exposes ``create_app(settings: dict | None = None)`` – the entry-point used by
both uvicorn and the test client.
"""
from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .db import DB

_SCRIPT_RE = r'<script src="{static_prefix}js/{name}" defer></script>'

TITLES: dict[str, str] = {
    "": "chat",
    "login": "Login",
    "invites": "Invites",
}


def _html(title: str, extra: str = "", body_cls: str = "",
          extra_head: str = "", extra_body: str = "") -> str:
    page_title = f"Coder · {title}" if title else "Coder"
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{page_title}</title>
<link rel="stylesheet" href="{static_prefix}css/style.css">
{extra_head}
</head>
<body class="{'app-dark' if os.getenv('THEME') == 'dark' else ''}{(' ' + body_cls) if body_cls else ''}">
<main>
{extra_body}
</main>
{extra}
</body>
</html>"""


@asynccontextmanager
async def _lifespan(app: FastAPI):
    db = DB(app.state.settings.DATA_DIR)
    db.init_schema()
    app.state.db = db
    s = app.state.settings
    if s.ADMIN_USER and s.ADMIN_PASSWORD:
        db.seed_admin(s.ADMIN_USER, s.ADMIN_PASSWORD)
    yield
    db.close()


def create_app(settings: dict | None = None) -> FastAPI:
    """Create the FastAPI application.

    The *settings* dict is merged with environment variables to form the
    final ``Settings`` object.  All values from ``chat/SPEC.md`` §1 are
    supported.
    """
    from .config import Settings
    settings_obj = Settings(settings)

    global static_prefix
    static_prefix = "/static/"

    app = FastAPI(title="Coder Chat", version="0.1.0", lifespan=_lifespan)
    app.state.settings = settings_obj

    # ------------------------------------------------------------------ #
    #  Health check                                                       #
    # ------------------------------------------------------------------ #
    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    # ------------------------------------------------------------------ #
    #  Static files                                                       #
    # ------------------------------------------------------------------ #
    _static_dir = Path(__file__).parent.parent / "static"
    if _static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

    # ------------------------------------------------------------------ #
    #  Pages                                                              #
    # ------------------------------------------------------------------ #
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        session = _session_from_cookie(request, app.state.db)
        if session:
            username, user = session
            chats = app.state.db.list_chats(username)
            return HTMLResponse(_render_index(username, user, chats, str(static_prefix)))
        return RedirectResponse("/login")

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request) -> HTMLResponse:
        return HTMLResponse(_render_login_page(str(static_prefix)))

    @app.post("/login")
    async def login_submit(request: Request) -> Any:
        form = await request.form()
        username = (form.get("username") or "").strip()
        password = (form.get("password") or "").strip()
        db = app.state.db
        s = app.state.settings

        if not username or not password:
            return JSONResponse({"error": "missing username or password"}, status_code=400)

        # Rate limiting
        if db.has_recent_failures(f"user:{username}"):
            return JSONResponse({"error": "too many failures – wait a moment"}, status_code=429)

        user = db.get_user(username)
        if user is None:
            db.record_login_failure(f"user:{username}")
            return JSONResponse({"error": "invalid credentials"}, status_code=401)

        from .auth import verify_password
        if not verify_password(password, user["pw_hash"]):
            db.record_login_failure(f"user:{username}")
            return JSONResponse({"error": "invalid credentials"}, status_code=401)

        db.clear_failures(username)
        db.set_last_login(username)
        session_id = secrets.token_urlsafe(32)
        id_hash = db.create_session(username, session_id)

        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(
            "session", session_id,
            httponly=True,
            samesite="lax",
            secure=s.SECURE_COOKIES,
            max_age=30 * 24 * 3600,
        )
        return resp

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        session = _session_from_cookie(request, app.state.db)
        if session:
            _, user = session
            app.state.db.delete_user_sessions(user)
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie("session")
        return resp

    @app.get("/invites", response_class=HTMLResponse)
    async def invites_page(request: Request) -> HTMLResponse:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return RedirectResponse("/login")
        username, user = session
        if "admin" not in user.get("rights", []):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        invites = app.state.db.list_invites()
        users = app.state.db.list_users()
        return HTMLResponse(_render_invites(username, user, invites, users, str(static_prefix)))

    @app.post("/invites/create")
    async def create_invite(request: Request) -> Any:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return RedirectResponse("/login")
        username, user = session
        if "admin" not in user.get("rights", []):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        form = await request.form()
        rights_str = (form.get("rights") or "chat").split(",")
        note = (form.get("note") or "").strip() or None
        invite = app.state.db.create_invite(rights_str, note)
        return JSONResponse({"token": invite["token"], "rights": invite["rights"]})

    @app.post("/invites/delete/{invite_id}")
    async def delete_invite_route(request: Request, invite_id: int) -> JSONResponse:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        username, user = session
        if "admin" not in user.get("rights", []):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        app.state.db.delete_invite(invite_id)
        return JSONResponse({"ok": True})

    @app.post("/user/delete/{username}")
    async def delete_user_route(request: Request, username: str) -> JSONResponse:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        admin_user = session[0]
        if admin_user == username:
            return JSONResponse({"error": "cannot delete yourself"}, status_code=400)
        app.state.db.delete_user(username)
        return JSONResponse({"ok": True})

    @app.post("/user/theme")
    async def update_theme(request: Request) -> JSONResponse:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, _ = session
        form = await request.form()
        theme = (form.get("theme") or "auto").strip()
        app.state.db.update_user_theme(username, theme)
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------------ #
    #  Chat CRUD API                                                      #
    # ------------------------------------------------------------------ #
    @app.get("/api/chats")
    async def api_chats(request: Request) -> JSONResponse:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, _ = session
        chats = app.state.db.list_chats(username)
        return JSONResponse(chats)

    @app.post("/api/chats", status_code=201)
    async def api_create_chat(request: Request) -> JSONResponse:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, _ = session
        form = await request.form()
        profile = (form.get("profile") or "general").strip()
        chat = app.state.db.create_chat(username, profile)
        return JSONResponse(chat, status_code=201)

    @app.get("/api/chat/{chat_id}")
    async def api_get_chat(request: Request, chat_id: int) -> JSONResponse:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, _ = session
        chat = app.state.db.get_chat(chat_id, username)
        if chat is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(chat)

    @app.post("/api/chat/{chat_id}/title")
    async def api_update_title(request: Request, chat_id: int) -> JSONResponse:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, _ = session
        form = await request.form()
        title = (form.get("title") or "New chat").strip()
        result = app.state.db.update_chat_title(chat_id, username, title)
        if result is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(result)

    @app.delete("/api/chat/{chat_id}")
    async def api_delete_chat(request: Request, chat_id: int) -> JSONResponse:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, _ = session
        app.state.db.delete_chat(chat_id, username)
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------------ #
    #  Messages API                                                       #
    # ------------------------------------------------------------------ #
    @app.get("/api/chat/{chat_id}/messages")
    async def api_messages(request: Request, chat_id: int) -> JSONResponse:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, _ = session
        messages = app.state.db.get_messages(chat_id, username)
        return JSONResponse(messages)

    @app.post("/api/chat/{chat_id}/message")
    async def api_add_message(request: Request, chat_id: int) -> JSONResponse:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, _ = session
        if not app.state.db.get_chat(chat_id, username):
            return JSONResponse({"error": "not found"}, status_code=404)
        form = await request.form()
        role = (form.get("role") or "assistant").strip()
        content = form.get("content") or ""
        tokens = int(form["tokens"]) if form.get("tokens") else None
        tps = float(form["tps"]) if form.get("tps") else None
        msg_id = app.state.db.add_message(chat_id, role, content, tokens, tps)
        return JSONResponse({"id": msg_id}, status_code=201)

    # ------------------------------------------------------------------ #
    #  File API                                                           #
    # ------------------------------------------------------------------ #
    @app.post("/api/files")
    async def api_upload_file(request: Request) -> JSONResponse:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, _ = session
        form = await request.form()
        file_obj = form.get("file")
        if file_obj is None:
            return JSONResponse({"error": "no file"}, status_code=400)
        content = file_obj.file.read()
        name = file_obj.filename or "upload"
        size = len(content)
        file_id = app.state.db.add_file(username, name, size, content)
        return JSONResponse({"id": file_id}, status_code=201)

    @app.get("/api/files/{file_id}")
    async def api_get_file(request: Request, file_id: int) -> JSONResponse:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, _ = session
        f = app.state.db.get_file(file_id, username)
        if f is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(f)

    @app.get("/api/files/{file_id}/content")
    async def api_file_content(request: Request, file_id: int) -> Response:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, _ = session
        f = app.state.db.get_file(file_id, username)
        if f is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        content = app.state.db.get_file_content(file_id)
        if content is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return Response(content=content, media_type="application/octet-stream")

    return app


# -------------------------------------------------------------------- #
#  HTML rendering helpers                                               #
# -------------------------------------------------------------------- #

def _session_from_cookie(request: Request, db: DB):
    """Return ``(username, user_dict)`` if the session cookie is valid, else None."""
    sid = request.cookies.get("session")
    if not sid:
        return None
    # Find session – we try by searching all (small scale)
    # In production we'd index by id_hash
    from .config import Settings
    # We need to look up sessions... for now let's use a simpler approach
    # Store username in a separate lookup or just check id_hash directly
    import hashlib
    id_hash = hashlib.sha256(sid.encode()).hexdigest()
    session = db.get_session(id_hash)
    if session is None:
        return None
    user = db.get_user(session["username"])
    if user is None:
        return None
    return (session["username"], user)


def _render_index(username: str, user: dict, chats: list, static_prefix: str) -> str:
    theme = user.get("theme", "auto")
    return f"""\
<div class="flex">
<aside class="sidebar">
  <div class="brand">Coder</div>
  <div class="sidebar-user">{username}</div>
  <form action="/logout" method="post" style="margin-top:8px;">
    <button class="btn-small">Logout</button>
  </form>
  <hr>
  <div class="sidebar-title">Chats</div>
  <div id="chat-list">
    { ''.join(f'<div class="chat-item" data-id="{c["id"]}">{c["title"]}</div>' for c in chats) }
  </div>
  <div style="margin-top:8px;">
    <a href="/invites" class="btn-small">Invites</a>
  </div>
</aside>
<div class="main-area">
  <div id="chat-area" class="chat-area">
    <div id="welcome" class="welcome">
      <h1>Hello, {username}</h1>
      <p>{_get_welcome_message()}</p>
    </div>
    <div id="messages"></div>
    <div id="input-bar">
      <form id="chat-form">
        <textarea id="prompt" placeholder="Message Coder..." rows="2"></textarea>
        <div class="input-row">
          <button type="submit" class="btn-primary">Send</button>
          <label class="btn-secondary">
            Attach
            <input type="file" name="file" style="display:none;">
          </label>
        </div>
      </form>
    </div>
  </div>
</div>
<script src="{static_prefix}js/app.js" defer></script>
</div>"""


def _render_login_page(static_prefix: str) -> str:
    return f"""\
<div class="login-box">
  <h1>Coder</h1>
  <p>Sign in to chat</p>
  <form action="/login" method="post">
    <input type="text" name="username" placeholder="Username" required autofocus>
    <input type="password" name="password" placeholder="Password" required>
    <button type="submit" class="btn-primary">Sign in</button>
  </form>
</div>"""


def _render_invites(username: str, user: dict, invites: list, users: list, static_prefix: str) -> str:
    invite_rows = []
    for i in invites:
        rights = ",".join(i["rights"])
        note = i["note"] or ""
        used = i["used_by"] or ""
        invite_rows.append(
            '<tr><td>{id}</td><td>{rights}</td><td>{note}</td><td>{used}</td>'
            '<td><button data-invite-id="{id}">Delete</button></td></tr>'.format(
                id=i["id"], rights=rights, note=note, used=used))

    user_rows = []
    for u in users:
        rights = ",".join(u["rights"])
        if u["username"] == username:
            action_btn = "-"
        else:
            action_btn = '<button data-user="' + u["username"] + '">Delete</button>'
        user_rows.append(
            '<tr><td>{user}</td><td>{rights}</td><td>{created}</td><td>{action}</td></tr>'.format(
                user=u["username"], rights=rights, created=u["created"], action=action_btn))

    return """\
<div class="flex">
<aside class="sidebar">
  <div class="brand">Coder</div>
  <div class="sidebar-user">{username}</div>
  <form action="/logout" method="post" style="margin-top:8px;">
    <button class="btn-small">Logout</button>
  </form>
  <a href="/" class="btn-small">Back to Chat</a>
  <hr>
</aside>
<div class="main-area">
  <h2>Invites</h2>
  <details id="new-invite">
    <summary>Create invite</summary>
    <form id="invite-form">
      <input type="text" name="rights" value="chat" placeholder="Rights (chat,admin)">
      <input type="text" name="note" placeholder="Note">
      <button type="submit" class="btn-primary">Create</button>
    </form>
  </details>
  <table>
    <thead><tr><th>Token</th><th>Rights</th><th>Note</th><th>Used by</th><th></th></tr></thead>
    <tbody>
      {invite_rows}
    </tbody>
  </table>
  <h2>Users</h2>
  <table>
    <thead><tr><th>Username</th><th>Rights</th><th>Created</th><th></th></tr></thead>
    <tbody>
      {user_rows}
    </tbody>
  </table>
</div>
</div>""".format(username=username, invite_rows="".join(invite_rows), user_rows="".join(user_rows))


def _get_welcome_message() -> str:
    """Return a dynamic welcome message based on the hour."""
    from datetime import datetime
    hour = datetime.now().hour
    if hour < 6:
        return "You're up late. What can I help with?"
    if hour < 12:
        return "Good morning! Ready to get started?"
    if hour < 18:
        return "Good afternoon! How can I assist today?"
    return "Good evening! What would you like to work on?"


static_prefix = "/static/"
