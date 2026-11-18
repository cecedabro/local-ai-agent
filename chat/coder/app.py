"""FastAPI application for the coder chat app.

Exposes ``create_app(settings: dict | None = None)`` – the entry-point used by
both uvicorn and the test client.
"""
from __future__ import annotations

import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import hashlib

from . import answer, context, gateway, passkeys
from .config import Settings
from .db import DB

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

    app = FastAPI(title="Coder Chat", version="0.1.0", lifespan=_lifespan)
    app.state.settings = settings_obj
    app.state.running: dict[int, asyncio.Event] = {}

    # ------------------------------------------------------------------ #
    #  Security middleware (CSRF + headers)                               #
    # ------------------------------------------------------------------ #
    CSP = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        response = await call_next(request)
        response.headers["content-security-policy"] = CSP
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["referrer-policy"] = "same-origin"
        return response

    @app.middleware("http")
    async def csrf_middleware(request: Request, call_next):
        if request.method in ("POST", "PATCH", "DELETE"):
            path = request.url.path
            if path.startswith("/api/"):
                if request.headers.get("X-Coder") != "1":
                    return JSONResponse({"error": "missing X-Coder header"}, status_code=403)
        return await call_next(request)

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
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        s = _session_from_cookie(request, app.state.db)
        if not s:
            return RedirectResponse("/login", status_code=303)
        username, user = s
        me = {"username": username, "rights": sorted(user.get("rights", []))}
        return templates.TemplateResponse(request, "chat.html", {"me": me, "settings": app.state.settings})

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {})

    @app.get("/invite", response_class=HTMLResponse)
    async def invite_page(request: Request):
        return templates.TemplateResponse(request, "invite.html", {})

    @app.get("/account", response_class=HTMLResponse)
    async def account_page(request: Request):
        s = _session_from_cookie(request, app.state.db)
        if not s:
            return RedirectResponse("/login", status_code=303)
        username, _ = s
        return templates.TemplateResponse(request, "account.html", {"me": {"username": username}})

    async def _handle_login(request: Request) -> Any:
        # Try JSON body first, fall back to form
        if request.headers.get("content-type", "").startswith("application/json"):
            body = await request.json()
            username = (body.get("username") or "").strip()
            password = (body.get("password") or "").strip()
        else:
            form = await request.form()
            username = (form.get("username") or "").strip()
            password = (form.get("password") or "").strip()

        if not username or not password:
            return JSONResponse({"error": "missing username or password"}, status_code=400)

        # Rate limiting
        if app.state.db.has_recent_failures(f"user:{username}"):
            return JSONResponse({"error": "too many failures – wait a moment"}, status_code=429)

        user = app.state.db.get_user(username)
        if user is None:
            app.state.db.record_login_failure(f"user:{username}")
            return JSONResponse({"error": "invalid credentials"}, status_code=401)

        from .auth import verify_password
        if not verify_password(password, user["pw_hash"]):
            app.state.db.record_login_failure(f"user:{username}")
            return JSONResponse({"error": "invalid credentials"}, status_code=401)

        app.state.db.clear_failures(username)
        app.state.db.set_last_login(username)
        session_id = secrets.token_urlsafe(32)
        app.state.db.create_session(username, session_id)

        return session_id, username, user

    @app.post("/api/login")
    async def api_login(request: Request) -> JSONResponse:
        result = await _handle_login(request)
        if isinstance(result, Response):
            return result  # type: ignore[return-value]
        session_id, username, user = result
        resp = JSONResponse({"username": username, "rights": user["rights"]})
        resp.set_cookie(
            "coder_session", session_id,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=30 * 24 * 3600,
            secure=app.state.settings.SECURE_COOKIES,
        )
        return resp

    @app.post("/api/logout")
    async def api_logout(request: Request) -> Response:
        session = _session_from_cookie(request, app.state.db)
        if session:
            username, _ = session
            app.state.db.delete_user_sessions(username)
        resp = Response(status_code=204)
        resp.delete_cookie("coder_session", path="/")
        return resp

    @app.get("/api/me")
    async def api_me_get(request: Request) -> JSONResponse:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, user = session
        theme = user.get("theme", "auto")
        rights = sorted(user.get("rights", []))
        return JSONResponse({"username": username, "rights": rights, "theme": theme})

    @app.patch("/api/me")
    async def api_me_patch(request: Request) -> JSONResponse:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, _ = session
        body = await request.json()
        theme = (body.get("theme") or "auto").strip()
        if theme not in ("auto", "light", "dark", "blossom"):
            return JSONResponse({"error": "invalid theme"}, status_code=400)
        app.state.db.update_user_theme(username, theme)
        user = app.state.db.get_user(username)
        return JSONResponse({"username": username, "rights": sorted(user["rights"]), "theme": theme})

    @app.get("/api/invites")
    async def api_invites_get(request: Request) -> JSONResponse:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, user = session
        if "admin" not in user.get("rights", []):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        invites = app.state.db.list_invites()
        return JSONResponse(invites)

    @app.get("/api/chats")
    async def api_chats(request: Request) -> JSONResponse:
        r = _require_session_and_csrf(request, app.state.db)
        if isinstance(r, Response):
            return r
        username, _ = r
        chats = app.state.db.list_chats(username)
        return JSONResponse(chats)

    @app.post("/api/chats", status_code=201)
    async def api_create_chat(request: Request) -> JSONResponse:
        r = _require_session_and_csrf(request, app.state.db)
        if isinstance(r, Response):
            return r
        username, user = r
        try:
            body = await request.json()
        except Exception:
            body = {}
        profile = (body.get("profile") or "general").strip()
        if profile not in ("general", "evidence"):
            return JSONResponse({"error": "invalid profile"}, status_code=400)
        if not user.get("admin") and "admin" not in user.get("rights", []):
            required_right = profile if profile == "evidence" else "chat"
            if required_right not in user.get("rights", []):
                return JSONResponse({"error": "forbidden"}, status_code=403)
        chat = app.state.db.create_chat(username, profile)
        return JSONResponse(chat, status_code=201)

    @app.get("/api/chats/{chat_id}")
    async def api_get_chat(request: Request, chat_id: str) -> JSONResponse:
        r = _require_session_and_csrf(request, app.state.db)
        if isinstance(r, Response):
            return r
        username, _ = r
        chat = _get_chat_or_404(app.state.db, chat_id, username)
        if chat is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        messages = app.state.db.get_messages(chat_id, username)
        chat["messages"] = messages
        chat["context"] = context.info(app.state.db, chat, app.state.settings)
        return JSONResponse(chat)

    @app.patch("/api/chats/{chat_id}")
    async def api_update_chat(chat_id: int, request: Request) -> JSONResponse:
        """Rename a chat and/or move it to a folder (SPEC §4, §11.3)."""
        r = _require_session(request, app.state.db)
        if isinstance(r, Response):
            return r
        username, _ = r
        try:
            body = await request.json()
        except Exception:
            body = None
        if not isinstance(body, dict) or ("title" not in body and "folder_id" not in body):
            return JSONResponse({"error": "Nothing to change."}, status_code=400)
        chat = app.state.db.get_chat(chat_id, username)
        if chat is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if "title" in body:
            title = str(body.get("title") or "").strip()
            if not title or len(title) > 120:
                return JSONResponse({"error": "A title needs 1 to 120 characters."}, status_code=400)
            app.state.db.update_chat_title(chat_id, username, title)
        if "folder_id" in body:
            fid = body["folder_id"]
            if fid is not None and (isinstance(fid, bool) or not isinstance(fid, int) or
                                    app.state.db.get_folder(fid, username) is None):
                return JSONResponse({"error": "That folder does not exist."}, status_code=400)
            app.state.db.set_chat_folder(chat_id, username, fid)
        c = app.state.db.get_chat(chat_id, username)
        return JSONResponse({k: c[k] for k in ("id", "title", "profile", "updated", "folder_id")})

    async def _folder_name(request: Request) -> str | None:
        """Return stripped folder name or None if invalid."""
        try:
            body = await request.json()
        except Exception:
            return None
        if not isinstance(body, dict):
            return None
        name = str(body.get("name") or "").strip()
        if not name or len(name) > 60:
            return None
        return name

    @app.get("/api/folders")
    async def api_list_folders(request: Request) -> JSONResponse:
        r = _require_session_and_csrf(request, app.state.db)
        if isinstance(r, Response):
            return r
        username, _ = r
        return JSONResponse(app.state.db.list_folders(username))

    @app.post("/api/folders", status_code=201)
    async def api_create_folder(request: Request) -> JSONResponse:
        r = _require_session_and_csrf(request, app.state.db)
        if isinstance(r, Response):
            return r
        username, _ = r
        name = await _folder_name(request)
        if name is None:
            return JSONResponse({"error": "A folder name needs 1 to 60 characters."}, status_code=400)
        return JSONResponse(app.state.db.create_folder(username, name), status_code=201)

    @app.patch("/api/folders/{folder_id:int}")
    async def api_update_folder(folder_id: int, request: Request) -> JSONResponse:
        r = _require_session_and_csrf(request, app.state.db)
        if isinstance(r, Response):
            return r
        username, _ = r
        if app.state.db.get_folder(folder_id, username) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        name = await _folder_name(request)
        if name is None:
            return JSONResponse({"error": "A folder name needs 1 to 60 characters."}, status_code=400)
        app.state.db.rename_folder(folder_id, username, name)
        f = app.state.db.get_folder(folder_id, username)
        return JSONResponse({k: f[k] for k in ("id", "name", "created")})

    @app.delete("/api/folders/{folder_id:int}")
    async def api_delete_folder(folder_id: int, request: Request) -> Response:
        r = _require_session_and_csrf(request, app.state.db)
        if isinstance(r, Response):
            return r
        username, _ = r
        if not app.state.db.delete_folder(folder_id, username):
            return JSONResponse({"error": "not found"}, status_code=404)
        return Response(status_code=204)

    @app.post("/api/chats/{chat_id:int}/stop")
    async def api_stop_chat(chat_id: int, request: Request) -> Response:
        r = _require_session(request, app.state.db)
        if isinstance(r, Response):
            return r
        username, _ = r
        chat = app.state.db.get_chat(chat_id, username)
        if chat is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        ev = app.state.running.get(chat_id)
        if ev is not None:
            ev.set()
        return Response(status_code=204)

    @app.delete("/api/chats/{chat_id}")
    async def api_delete_chat(chat_id: str, request: Request) -> Response:
        r = _require_session_and_csrf(request, app.state.db)
        if isinstance(r, Response):
            return r
        username, _ = r
        chat = _get_chat_or_404(app.state.db, chat_id, username)
        if chat is None:
            return Response(status_code=404)
        app.state.db.delete_chat(chat_id, username)
        return Response(status_code=204)

    # ------------------------------------------------------------------ #
    #  Messages API                                                       #
    # ------------------------------------------------------------------ #
    @app.post("/api/files")
    async def api_upload_file(request: Request) -> Response:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, _ = session
        form = await request.form()
        file_obj = form.get("file")
        if file_obj is None:
            return JSONResponse({"error": "no file"}, status_code=400)
        content = file_obj.file.read()
        filename = file_obj.filename or "upload"
        name = os.path.basename(filename).replace("\\", "/")
        while "/" in name and name != name.split("/", 1)[-1]:
            name = name.rsplit("/", 1)[-1]
        name = name[:120]
        if not name:
            name = "upload"
        size = len(content)
        if size > 200000:
            return JSONResponse({"error": "file too large"}, status_code=413)
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            return JSONResponse({"error": "file must be valid UTF-8"}, status_code=415)
        if b"\x00" in content:
            return JSONResponse({"error": "file must not contain NUL bytes"}, status_code=415)
        file_id = app.state.db.add_file(username, name, size, content)
        return JSONResponse({"id": file_id, "name": name, "size": size}, status_code=201)

    @app.get("/api/files/{file_id}")
    async def api_get_file(request: Request, file_id: int) -> Response:
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
        resp = Response(content=content)
        resp.headers["Content-Disposition"] = f'attachment; filename="{f["name"]}"'
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    # ── POST /api/chats/{chat_id}/messages (streaming) ─────────────────────

    @app.post("/api/chats/{chat_id}/messages")
    async def api_create_message(chat_id: int, request: Request) -> Response:
        """Send a message and stream the answer (SPEC §5).

        TODO (task S12). In this order:
          1. session: r = _require_session(request, db); if it is a Response, return it
          2. chat = db.get_chat(chat_id, username); None -> 404 {"error": "Chat not found."}
          3. body = await request.json() (on error: {}); content = str(body.get("content") or "");
             file_ids = body.get("files") or []
          4. not content.strip() and not file_ids -> 400 {"error": "Type a message first."}
             len(content) > 20000 -> 413 {"error": "That message is too long."}
           5. for each file_id in file_ids: f = db.get_file(file_id, username);
              if f is None: return 400 {"error": "One or more files not found."}
           6. for each fid in file_ids: fc = db.get_file_content(fid); append "File <name>:\n```\n<fc>\n```" to content;
              msg_id = db.add_message(chat_id, "user", content);
              db.add_message_files(msg_id, file_ids)
          7. return StreamingResponse(answer.run_answer(db, settings, chat, body.get("mode") == "think"),
                                      media_type="text/event-stream")
        """
        db = app.state.db
        settings = app.state.settings
        r = _require_session(request, db)
        if isinstance(r, Response):
            return r
        username, _ = r
        chat = db.get_chat(chat_id, username)
        if chat is None:
            return JSONResponse({"error": "Chat not found."}, status_code=404)
        try:
            body = await request.json()
        except Exception:
            body = {}
        content = str(body.get("content") or "")
        file_ids = body.get("files") or []
        if not content.strip() and not file_ids:
            return JSONResponse({"error": "Type a message first."}, status_code=400)
        if len(content) > 20000:
            return JSONResponse({"error": "That message is too long."}, status_code=413)
        for fid in file_ids:
            if not db.get_file(fid, username):
                return JSONResponse({"error": "One or more files not found."}, status_code=400)
        if file_ids:
            for fid in file_ids:
                f = db.get_file(fid, username)
                fc = db.get_file_content(fid)
                content += f'\n\nFile {f["name"]}:\n```\n{fc.decode()}\n```'
        msg_id = db.add_message(chat_id, "user", content)
        if file_ids:
            db.add_message_files(msg_id, file_ids)
        stop = asyncio.Event()
        app.state.running[chat_id] = stop
        async def events():
            try:
                async for chunk in answer.run_answer(db, settings, chat, body.get("mode") == "think", stop=stop):
                    yield chunk
            finally:
                if app.state.running.get(chat_id) is stop:
                    del app.state.running[chat_id]
        return StreamingResponse(events(), media_type="text/event-stream")

    # ── POST /api/chats/{chat_id}/compress ─────────────────────────────────

    @app.post("/api/chats/{chat_id}/compress")
    async def api_compress(chat_id: int, request: Request) -> Response:
        """Compress now (SPEC §6).

        TODO (task S14). Session (like above), chat or 404, then
        await context.compress(db, chat, settings, gateway.complete), reload the chat with db.get_chat,
        and return {"context": context.info(db, chat, settings)}.
        """
        db = app.state.db
        settings = app.state.settings
        r = _require_session(request, db)
        if isinstance(r, Response):
            return r
        username, _ = r
        chat = db.get_chat(chat_id, username)
        if chat is None:
            return JSONResponse({"error": "Chat not found."}, status_code=404)
        await context.compress(db, chat, settings, gateway.complete)
        chat = db.get_chat(chat_id, username)
        return JSONResponse({"context": context.info(db, chat, settings)})

    # ── POST /api/invites ─────────────────────────────────────────────────

    @app.post("/api/invites", status_code=201)
    async def api_create_invite(request: Request) -> Response:
        result = _require_admin(request, app.state.db)
        if isinstance(result, Response):
            return result
        csrf = _require_csrf(request)
        if isinstance(csrf, Response):
            return csrf
        _, user = result
        try:
            body = await request.json()
        except Exception:
            body = {}
        rights = body.get("rights", [])
        if not isinstance(rights, list) or len(rights) == 0:
            return JSONResponse({"error": "rights must be a non-empty list"}, status_code=400)
        valid_rights = {"chat", "evidence", "admin"}
        for r in rights:
            if r not in valid_rights:
                return JSONResponse({"error": f"invalid right: {r}"}, status_code=400)
        note = body.get("note")
        invite = app.state.db.create_invite(rights, note)
        token = invite["token"]
        return JSONResponse({
            "id": invite["id"],
            "token": token,
            "url": f"/invite#{token}",
            "expires": invite["expires"],
            "rights": invite["rights"],
            "note": note
        }, status_code=201)

    # ── DELETE /api/invites/{invite_id} ───────────────────────────────────

    @app.delete("/api/invites/{invite_id}")
    async def api_delete_invite(invite_id: int, request: Request) -> Response:
        result = _require_admin(request, app.state.db)
        if isinstance(result, Response):
            return result
        csrf = _require_csrf(request)
        if isinstance(csrf, Response):
            return csrf
        app.state.db.delete_invite(invite_id)
        return Response(status_code=204)

    # ── POST /api/invites/accept ──────────────────────────────────────────

    @app.post("/api/invites/accept", status_code=201)
    async def api_accept_invite(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        token = (body.get("token") or "").strip()
        new_username = (body.get("username") or "").strip()
        new_password = (body.get("password") or "").strip()
        if not token or not new_username or not new_password:
            return JSONResponse({"error": "token, username, and password required"}, status_code=400)
        import re
        username_regex = re.compile(r'^[a-z0-9][a-z0-9_.\-]{1,31}$')
        if not username_regex.match(new_username):
            return JSONResponse({"error": "invalid username format"}, status_code=400)
        if len(new_password) < 10 or len(new_password) > 200:
            return JSONResponse({"error": "password must be 10-200 characters"}, status_code=400)
        import hashlib
        invite = app.state.db.get_invite_by_token_hash(hashlib.sha256(token.encode()).hexdigest())
        if invite is None:
            return JSONResponse({"error": "invalid token"}, status_code=400)
        from datetime import datetime, timezone
        if datetime.fromisoformat(invite["expires"]) < datetime.now(timezone.utc):
            return JSONResponse({"error": "invite expired"}, status_code=400)
        if invite.get("used_by") is not None:
            return JSONResponse({"error": "invite already used"}, status_code=400)
        if app.state.db.get_user(new_username) is not None:
            return JSONResponse({"error": "username taken"}, status_code=409)
        from .auth import hash_password
        pw_hash = hash_password(new_password)
        app.state.db.create_user(new_username, pw_hash, invite["rights"])
        app.state.db.use_invite(invite["token_hash"], new_username)
        app.state.db.set_last_login(new_username)
        session_id = secrets.token_urlsafe(32)
        app.state.db.create_session(new_username, session_id)
        resp = JSONResponse({"username": new_username, "rights": invite["rights"]}, status_code=201)
        resp.set_cookie(
            "coder_session", session_id,
            httponly=True, samesite="lax", path="/",
            max_age=30 * 24 * 3600,
            secure=app.state.settings.SECURE_COOKIES,
        )
        return resp

    # ── GET /api/users ────────────────────────────────────────────────────

    @app.get("/api/users")
    async def api_list_users(request: Request) -> JSONResponse:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, user = session
        if not _is_admin(user):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        users = app.state.db.list_users()
        return JSONResponse(users)

    # ── DELETE /api/users/{username} ──────────────────────────────────────

    @app.delete("/api/users/{target_username}")
    async def api_delete_user(target_username: str, request: Request) -> Response:
        result = _require_admin(request, app.state.db)
        if isinstance(result, Response):
            return result
        csrf = _require_csrf(request)
        if isinstance(csrf, Response):
            return csrf
        username, _ = result
        if target_username == username:
            return JSONResponse({"error": "cannot delete yourself"}, status_code=400)
        app.state.db.delete_user(target_username)
        return Response(status_code=204)

    # ── GET /admin ────────────────────────────────────────────────────────

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page(request: Request) -> HTMLResponse:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return RedirectResponse("/login", status_code=303)
        username, user = session
        if not _is_admin(user):
            return HTMLResponse("<h1>403 Forbidden</h1>", status_code=403)
        return templates.TemplateResponse(request, "admin.html", {})

    # ------------------------------------------------------------------ #
    #  Passkey registration, list and delete (P06)                        #
    # ------------------------------------------------------------------ #

    def _wa_set(resp: Response, key: str) -> None:
        resp.set_cookie(
            "coder_wa", key,
            httponly=True,
            samesite="lax",
            path="/api/passkeys",
            max_age=300,
            secure=app.state.settings.SECURE_COOKIES,
        )

    def _wa_take(request: Request, kind: str):
        key = request.cookies.get("coder_wa")
        if not key:
            return None
        return app.state.db.take_challenge(hashlib.sha256(key.encode()).hexdigest(), kind)

    @app.post("/api/passkeys/register/options")
    async def passkeys_register_options(request: Request) -> Response:
        db = app.state.db
        settings = app.state.settings
        s = _session_from_cookie(request, db)
        if not s:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, _ = s
        existing = [bytes(p["credential_id"]) for p in db.list_passkeys(username)]
        js, challenge = passkeys.registration_options(settings, username, existing)
        key = secrets.token_urlsafe(32)
        db.put_challenge(hashlib.sha256(key.encode()).hexdigest(), "register", username, challenge)
        resp = Response(js, media_type="application/json")
        _wa_set(resp, key)
        return resp

    @app.post("/api/passkeys/register/verify", status_code=201)
    async def passkeys_register_verify(request: Request) -> Response:
        db = app.state.db
        settings = app.state.settings
        s = _session_from_cookie(request, db)
        if not s:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, _ = s
        try:
            body = await request.json()
        except Exception:
            body = {}
        got = _wa_take(request, "register")
        if not got or got[1] != username or not isinstance(body.get("credential"), dict):
            return JSONResponse({"error": "That passkey could not be added. Try again."}, status_code=400)
        try:
            cred_id, public_key, count = passkeys.verify_registration(settings, body["credential"], got[0])
        except passkeys.PasskeyError:
            return JSONResponse({"error": "That passkey could not be added. Try again."}, status_code=400)
        name = (str(body.get("name") or "").strip() or "Passkey")[:60]
        return JSONResponse(db.add_passkey(username, cred_id, public_key, count, name), status_code=201)

    @app.get("/api/passkeys")
    async def passkeys_list(request: Request) -> JSONResponse:
        s = _session_from_cookie(request, app.state.db)
        if not s:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, _ = s
        return JSONResponse([{k: p[k] for k in ("id", "name", "created", "last_used")} for p in app.state.db.list_passkeys(username)])

    @app.delete("/api/passkeys/{passkey_id}")
    async def passkeys_delete(passkey_id: int, request: Request) -> Response:
        s = _session_from_cookie(request, app.state.db)
        if not s:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, _ = s
        if not app.state.db.delete_passkey(passkey_id, username):
            return JSONResponse({"error": "not found"}, status_code=404)
        return Response(status_code=204)

    @app.post("/api/passkeys/login/options")
    async def passkeys_login_options(request: Request) -> Response:
        settings = app.state.settings
        db = app.state.db
        js, challenge = passkeys.authentication_options(settings)
        key = secrets.token_urlsafe(32)
        db.put_challenge(hashlib.sha256(key.encode()).hexdigest(), "login", None, challenge)
        resp = Response(js, media_type="application/json")
        _wa_set(resp, key)
        return resp

    @app.post("/api/passkeys/login/verify")
    async def passkeys_login_verify(request: Request) -> JSONResponse:
        db = app.state.db
        settings = app.state.settings
        ipkey = f"ip:{request.client.host if request.client else 'unknown'}"
        if db.has_recent_failures(ipkey):
            return JSONResponse({"error": "Too many tries. Wait 15 minutes."}, status_code=429)
        try:
            body = await request.json()
        except Exception:
            body = {}
        got = _wa_take(request, "login")
        cred = body.get("credential")
        try:
            if not got or not isinstance(cred, dict):
                raise passkeys.PasskeyError("missing challenge or credential")
            cid = passkeys.credential_id(cred)
            pk = db.get_passkey(cid)
            if pk is None:
                raise passkeys.PasskeyError("passkey not found")
            count = passkeys.verify_authentication(settings, cred, got[0], bytes(pk["public_key"]), pk["sign_count"])
        except passkeys.PasskeyError:
            db.record_login_failure(ipkey)
            return JSONResponse({"error": "That passkey did not work."}, status_code=401)
        user = db.get_user(pk["username"])
        if user is None:
            return JSONResponse({"error": "That passkey did not work."}, status_code=401)
        db.touch_passkey(pk["id"], count)
        db.set_last_login(pk["username"])
        session_id = secrets.token_urlsafe(32)
        db.create_session(pk["username"], session_id)
        resp = JSONResponse({"username": pk["username"], "rights": user["rights"]})
        resp.set_cookie(
            "coder_session", session_id,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=30 * 24 * 3600,
            secure=app.state.settings.SECURE_COOKIES,
        )
        return resp

    return app


# -------------------------------------------------------------------- #
#  HTML rendering helpers                                               #
# -------------------------------------------------------------------- #

def _session_from_cookie(request: Request, db: DB):
    """Return ``(username, user_dict)`` if the session cookie is valid, else None."""
    sid = request.cookies.get("coder_session")
    if not sid:
        return None
    import hashlib
    id_hash = hashlib.sha256(sid.encode()).hexdigest()
    session = db.get_session(id_hash)
    if session is None:
        return None
    user = db.get_user(session["username"])
    if user is None:
        return None
    return (session["username"], user)


def _require_session(request: Request, db: DB):
    """Return ``(username, user_dict)`` or a 401 JSONResponse."""
    session = _session_from_cookie(request, db)
    if not session:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return session


def _require_admin(request: Request, db: DB):
    """Return ``(username, user_dict)`` or 401/403."""
    result = _require_session(request, db)
    if isinstance(result, Response):
        return result
    username, user = result
    if "admin" not in user.get("rights", []):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return (username, user)


def _is_admin(user: dict) -> bool:
    return "admin" in user.get("rights", [])


def _require_csrf(request: Request):
    """Return True or a 403 JSONResponse."""
    if request.headers.get("X-Coder") != "1":
        return JSONResponse({"error": "missing X-Coder header"}, status_code=403)
    return True


def _require_session_and_csrf(request: Request, db):
    """Return ``(username, user)`` or a 401/403 response."""
    session = _session_from_cookie(request, db)
    if not session:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return session


def _get_chat_or_404(db, chat_id: str, username: str):
    """Return the chat dict or ``None``."""
    if isinstance(chat_id, str):
        chat_id = int(chat_id)
    return db.get_chat(chat_id, username)


def _validate_user(username: str, user: dict):
    """Return ``True`` or a 403 JSONResponse."""
    if user.get("username") != username:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return True
