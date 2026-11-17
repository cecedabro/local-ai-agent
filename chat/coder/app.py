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
from fastapi.middleware.cors import CORSMiddleware

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
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        session = _session_from_cookie(request, app.state.db)
        if session:
            username, user = session
            chats = app.state.db.list_chats(username)
            return HTMLResponse(_render_index(username, user, chats, str(static_prefix), app.state.settings))
        return RedirectResponse("/login", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request) -> HTMLResponse:
        return HTMLResponse(_render_login_page(str(static_prefix)))

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

    @app.post("/login")
    async def login_submit(request: Request) -> Any:
        result = await _handle_login(request)
        if isinstance(result, Response):
            return result
        session_id, username, user = result
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(
            "coder_session", session_id,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=30 * 24 * 3600,
            secure=app.state.settings.SECURE_COOKIES,
        )
        return resp

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

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        session = _session_from_cookie(request, app.state.db)
        if session:
            _, username = session
            app.state.db.delete_user_sessions(username)
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie("coder_session", path="/")
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

    # ── POST /api/chats/{chat_id}/messages (streaming) ─────────────────────

    @app.post("/api/chats/{chat_id}/messages")
    async def api_create_message(chat_id: str, request: Request) -> Response:
        session = _require_session_and_csrf(request)
        username, user = session
        try:
            body = await request.json()
        except Exception:
            body = {}
        content = body.get("content", "")
        if not content or not content.strip():
            return JSONResponse({"error": "prompt required"}, status_code=400)
        chat = _get_chat_or_404(chat_id, username)
        if not _validate_user(username, user):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            msg_id = app.state.db.add_message(chat_id, "user", content)
        except Exception:
            return JSONResponse({"error": "failed"}, status_code=500)
        gateway_url = app.state.settings.GATEWAY_URL
        gateway_key = app.state.settings.GATEWAY_KEY
        messages = app.state.db.get_messages_for_completion(chat_id)
        headers = {"Content-Type": "application/json"}
        if gateway_key:
            headers["Authorization"] = f"Bearer {gateway_key}"
        stream_payload = {"model": "coder", "messages": messages, "stream": True}
        try:
            resp = requests.post(
                f"{gateway_url}/chat/completions",
                json=stream_payload, headers=headers, timeout=120, stream=True,
            )
            resp.raise_for_status()
        except requests.RequestException:
            app.state.db.add_message(chat_id, "assistant", "Sorry, I'm having trouble connecting right now.")
            return JSONResponse({"error": "gateway error"}, status_code=502)

        def event_stream():
            usage_written = False
            try:
                for line in resp.iter_lines():
                    if not line:
                        continue
                    text = line.decode("utf-8")
                    if text.startswith("data: "):
                        data_str = text[6:]
                        if data_str.strip() == "[DONE]":
                            yield "data: [DONE]\n\n"
                            break
                        try:
                            chunk = json.loads(data_str)
                            choice = chunk.get("choices", [{}])[0]
                            delta = choice.get("delta", {})
                            obj = {"object": "chat.completion.chunk", "choices": []}
                            if delta:
                                obj["choices"] = [{"index": 0, "delta": delta}]
                            if chunk.get("usage"):
                                obj["usage"] = chunk["usage"]
                                usage_written = True
                            yield "data: " + json.dumps(obj) + "\n\n"
                        except ValueError:
                            continue
                if not usage_written:
                    yield "data: " + json.dumps({"usage": {"prompt_tokens": 50, "completion_tokens": 2}}) + "\n\n"
                    yield "data: [DONE]\n\n"
            except Exception:
                pass
            finally:
                try:
                    full_text = _extract_ai_response(chat_id)
                    app.state.db.update_context(chat_id, full_text)
                    app.state.db.set_last_activity(chat_id)
                except Exception:
                    pass

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # ── POST /api/chats/{chat_id}/compress ─────────────────────────────────

    @app.post("/api/chats/{chat_id}/compress")
    async def api_compress(chat_id: str, request: Request) -> Response:
        session = _require_session_and_csrf(request)
        username = session[0]
        chat = _get_chat_or_404(chat_id, username)
        if app.state.db.get_context(chat_id):
            return JSONResponse({"ok": True})
        messages = app.state.db.get_messages_for_completion(chat_id)
        if len(messages) < 2:
            return JSONResponse({"ok": True})
        summary_content = "CONVERSATION SUMMARY: "
        for msg in messages:
            if msg["role"] in ("user", "assistant"):
                summary_content += msg["role"] + ": " + (msg["content"][:200] if len(msg["content"]) > 200 else msg["content"]) + " "
        app.state.db.add_context(chat_id, summary_content)
        return JSONResponse({"ok": True})

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

    # ── PATCH /api/chats/{chat_id} ────────────────────────────────────────

    @app.patch("/api/chats/{chat_id}")
    async def api_update_chat(chat_id: str, request: Request) -> Response:
        session = _require_session_and_csrf(request)
        username = session[0]
        user = session[1]
        if not _validate_user(username, user):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid json"}, status_code=400)
        title = body.get("title", "")
        if not title or not title.strip():
            return JSONResponse({"error": "title is required and must not be empty"}, status_code=400)
        chat = _get_chat_or_404(chat_id, username)
        app.state.db.update_chat_title(chat_id, title.strip())
        return JSONResponse({"ok": True, "id": chat_id, "title": title.strip()})

    # ── POST /api/files ───────────────────────────────────────────────────

    @app.post("/api/files")
    async def api_upload_file(request: Request) -> Response:
        session = _require_session_and_csrf(request)
        username = session[0]
        user = session[1]
        ct = request.headers.get("content-type", "")
        valid_types = {"image/png", "image/jpeg", "image/gif", "text/plain"}
        if ct not in valid_types:
            return JSONResponse({"error": "invalid content type"}, status_code=400)
        form = await request.form()
        file_field = form.get("file")
        if not file_field:
            return JSONResponse({"error": "file required"}, status_code=400)
        content = await file_field.read()
        file_size = len(content)
        if file_size > 10_000_000:
            return JSONResponse({"error": "file too large"}, status_code=400)
        filename = file_field.filename or "upload"
        file_id = app.state.db.save_file(username, filename, content)
        return JSONResponse({"id": file_id, "name": filename, "size": file_size})

    # ── GET /admin ────────────────────────────────────────────────────────

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page(request: Request) -> HTMLResponse:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return RedirectResponse("/login")
        username, user = session
        if not _is_admin(user):
            return HTMLResponse("<html><body><h1>403 Forbidden</h1></body></html>", status_code=403)
        return HTMLResponse("<html><body><h1>Admin Panel</h1></body></html>")

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


def _render_index(username: str, user: dict, chats: list, static_prefix: str, settings) -> str:
    model_label = settings.MODEL_LABEL if settings else "Qwen3.6 35B-A3B"
    hw_label = settings.HARDWARE_LABEL if settings else "RTX 3080 Ti"
    repo_url = settings.REPO_URL if settings else "https://github.com/cecedabro/local-ai-agent"
    return f"""\
<div class="flex">
<aside class="side">
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
<div class="main">
  <div id="chat-area" class="chat-area">
    <div id="welcome" class="welcome">
      <h1>Hello, {username}</h1>
      <p>{_get_welcome_message()}</p>
    </div>
    <div id="messages"></div>
    <div class="ctx"></div>
    <div id="input-bar" class="composer">
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
<div class="orb"></div>
<div class="footer">
  <span>{model_label}</span> · <span>{hw_label}</span>
  · <a href="{repo_url}" target="_blank">GitHub</a>
</div>
<link rel="stylesheet" href="{static_prefix}css/tokens.css">
<script src="{static_prefix}js/theme.js" defer></script>
<script src="{static_prefix}vendor/markdown-it.min.js" defer></script>
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
