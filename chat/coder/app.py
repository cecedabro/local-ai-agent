"""FastAPI application for the coder chat app.

Exposes ``create_app(settings: dict | None = None)`` – the entry-point used by
both uvicorn and the test client.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import hashlib

from . import answer, context, files, gateway, passkeys, profiles
from .config import Settings
from .db import DB

ICONS = ("aero", "blossom")

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


log = logging.getLogger("dewdrop")
LIVE_PING = 15        # seconds of silence before a keep-alive comment
LIVE_KEEP = 60  # seconds a finished answer's events stay available to a page that reconnects


class Live:
    """The events of one answer while it is written: kept in order so any number of pages can follow them."""

    def __init__(self, username: str) -> None:
        self.username = username
        self.chunks: list[str] = []
        self.done = False
        self.task: asyncio.Task | None = None
        self._changed = asyncio.Event()

    def add(self, chunk: str) -> None:
        self.chunks.append(chunk)
        self._changed.set()

    def finish(self) -> None:
        self.done = True
        self._changed.set()

    async def follow(self):
        i = 0
        while True:
            while i < len(self.chunks):
                yield self.chunks[i]
                i += 1
            if self.done:
                return
            self._changed.clear()
            if i < len(self.chunks) or self.done:
                continue
            try:
                await asyncio.wait_for(self._changed.wait(), LIVE_PING)
            except asyncio.TimeoutError:
                yield ": ping\n\n"          # keeps proxies (Cloudflare closes after ~100 s of silence) from hanging up


def client_ip(request: Request) -> str:
    """The visitor's address for rate limits: Cloudflare's Cf-Connecting-IP (Cloudflare sets it and overwrites what a
    visitor sends), else the connection's address. X-Forwarded-For is not used: a visitor can write it themselves."""
    cf = request.headers.get("cf-connecting-ip", "").strip()
    if cf:
        return cf[:64]
    return request.client.host if request.client else "unknown"


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
    app.state.live: dict[int, Live] = {}

    # ------------------------------------------------------------------ #
    #  Security middleware (CSRF + headers)                               #
    # ------------------------------------------------------------------ #
    CSP = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        response = await call_next(request)
        if "content-security-policy" not in response.headers:      # downloads set a stricter one (SPEC §16.1)
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
        ipkey = f"ip:{client_ip(request)}"      # one visitor trying many names is stopped too
        if app.state.db.has_recent_failures(f"user:{username}") or app.state.db.has_recent_failures(ipkey, limit=20):
            return JSONResponse({"error": "too many failures – wait a moment"}, status_code=429)

        from .auth import verify_password
        user = app.state.db.get_user(username)
        if user is None or not verify_password(password, user["pw_hash"]):
            app.state.db.record_login_failure(f"user:{username}")
            app.state.db.record_login_failure(ipkey)
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
        username, _ = session
        return JSONResponse(_me(username, app.state.db))

    @app.patch("/api/me")
    async def api_me_patch(request: Request) -> JSONResponse:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, _ = session
        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse({"error": "Nothing to change."}, status_code=400)
        if not body:
            return JSONResponse({"error": "Nothing to change."}, status_code=400)
        if "theme" in body:
            theme = body["theme"]
            if theme not in ("auto", "light", "dark", "blossom"):
                return JSONResponse({"error": "invalid theme"}, status_code=400)
            app.state.db.update_user_theme(username, theme)
        if "icon" in body:
            icon = body["icon"]
            if icon not in ICONS:
                return JSONResponse({"error": "invalid icon"}, status_code=400)
            app.state.db.update_user_icon(username, icon)
        return JSONResponse(_me(username, app.state.db))

    @app.get("/manifest.json")
    async def manifest_json(request: Request) -> Response:
        session = _session_from_cookie(request, app.state.db)
        if session:
            username, _ = session
            icon = _me(username, app.state.db)["icon"]
        else:
            icon = "aero"
        icons = [{"src": f"/static/icons/{icon}-{n}.png", "sizes": f"{n}x{n}", "type": "image/png", "purpose": "any maskable"} for n in (192, 512)]
        return JSONResponse(
            {"name": "Dewdrop", "short_name": "Dewdrop", "description": "Your private chat, running on a local model.",
             "start_url": "/", "scope": "/", "display": "standalone", "background_color": "#0b2233", "theme_color": "#0b2233",
             "icons": icons},
            media_type="application/manifest+json",
        )

    @app.get("/apple-touch-icon.png")
    async def apple_touch_icon(request: Request) -> Response:
        session = _session_from_cookie(request, app.state.db)
        if session:
            username, _ = session
            icon = _me(username, app.state.db)["icon"]
        else:
            icon = "aero"
        icon_path = Path(__file__).parent.parent / "static" / "icons" / f"{icon}-180.png"
        data = icon_path.read_bytes()
        return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-cache"})

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

    # ------------------------------------------------------------------ #
    #  Profile routes (task B12, B13, B14)                               #
    # ------------------------------------------------------------------ #

    def _profile_access(request: Request, profile: str):
        r = _require_session(request, app.state.db)
        if isinstance(r, Response):
            return r
        if profile not in profiles.PROFILES:
            return JSONResponse({"error": "not found"}, status_code=404)
        rights = r[1]["rights"]
        if "admin" not in rights and profiles.RIGHT.get(profile) not in rights:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return r

    def _profile_json(username: str, profile: str) -> JSONResponse:
        cfg = profiles.config(app.state.db, username, profile)
        return JSONResponse(profiles.public(cfg, app.state.settings))

    async def _json_body(request: Request):
        try:
            body = await request.json()
            return body if isinstance(body, dict) else None
        except Exception:
            return None

    @app.get("/api/profiles/{profile}")
    async def api_get_profile(profile: str, request: Request) -> JSONResponse:
        r = _profile_access(request, profile)
        if isinstance(r, Response):
            return r
        username, _ = r
        return _profile_json(username, profile)

    @app.patch("/api/profiles/{profile}")
    async def api_update_profile(profile: str, request: Request) -> JSONResponse:
        r = _profile_access(request, profile)
        if isinstance(r, Response):
            return r
        username, _ = r
        body = await _json_body(request)
        if body is None or ("instructions" not in body and "web" not in body):
            return JSONResponse({"error": "Nothing to change."}, status_code=400)
        if "instructions" in body:
            if not isinstance(body["instructions"], str) or len(body["instructions"]) > 4000:
                return JSONResponse({"error": "instructions must be a string ≤ 4000 characters"}, status_code=400)
        if "web" in body:
            if not isinstance(body["web"], bool):
                return JSONResponse({"error": "web must be a boolean"}, status_code=400)
        profiles.config(app.state.db, username, profile)
        app.state.db.set_profile_settings(username, profile, **body)
        return _profile_json(username, profile)

    @app.get("/profiles", response_class=HTMLResponse)
    def profiles_page(request: Request) -> HTMLResponse:
        s = _session_from_cookie(request, app.state.db)
        if not s:
            return RedirectResponse("/login", status_code=303)
        username, user = s
        rights = user.get("rights", [])
        profiles_list = [
            {"id": p, "name": profiles.NAMES[p]}
            for p in profiles.PROFILES
            if "admin" in rights or profiles.RIGHT.get(p) in rights
        ]
        return templates.TemplateResponse(request, "profiles.html",
                                          {"me": {"username": username, "rights": sorted(rights)},
                                           "profiles": profiles_list})

    def _source_json(s: dict) -> dict:
        out = {k: s[k] for k in ("id", "kind", "title", "domain", "note", "filters", "enabled", "created")}
        out["tokens"] = profiles.tokens(s.get("content", "") or "") if s.get("kind") == "file" else 0
        out["group"], out["label"] = profiles.group_of(s), profiles.label_of(s)   # SPEC §15.6
        return out

    @app.post("/api/profiles/{profile}/sources", status_code=201)
    async def api_add_source(profile: str, request: Request) -> Response:
        r = _profile_access(request, profile)
        if isinstance(r, Response):
            return r
        username, _ = r
        body = await request.json()
        note = body.get("note") if body.get("note") is not None else ""
        if not isinstance(note, str) or len(note) > 1000:
            return JSONResponse({"error": "Invalid note"}, status_code=400)
        cfg = profiles.config(app.state.db, username, profile)
        kind = body.get("kind")
        if kind == "pubmed":
            if any(s.get("kind") == "pubmed" for s in cfg["sources"]):
                return JSONResponse({"error": "This profile already has a PubMed card."}, status_code=409)
            src = app.state.db.add_source(username, profile, "pubmed", "PubMed",
                                          note=note, filters=profiles.clean_filters(body.get("filters")))
            return JSONResponse(_source_json(src), status_code=201)
        elif kind == "website":
            domain = profiles.domain_of(str(body.get("url") or ""))
            if domain is None:
                return JSONResponse({"error": "Invalid URL"}, status_code=400)
            src = app.state.db.add_source(username, profile, "website", domain,
                                          domain=domain, note=note)
            return JSONResponse(_source_json(src), status_code=201)
        else:
            return JSONResponse({"error": "Invalid kind"}, status_code=400)

    @app.post("/api/profiles/{profile}/defaults")
    async def api_add_defaults(profile: str, request: Request) -> Response:
        r = _profile_access(request, profile)
        if isinstance(r, Response):
            return r
        username, _ = r
        cfg = profiles.config(app.state.db, username, profile)
        cards = profiles.missing_defaults(profile, cfg["sources"])
        added = []
        for card in cards:
            s = app.state.db.add_source(username, profile, card["kind"], card["title"],
                                        domain=card.get("domain", ""), note=card["note"],
                                        filters=card.get("filters"))
            added.append(s)
        if profile in profiles.WEB_OFF:
            app.state.db.set_profile_settings(username, profile, web=False)   # the defaults are the trusted sites only
        return JSONResponse({"added": len(cards), "sources": [_source_json(s) for s in app.state.db.list_sources(username, profile)]})

    @app.patch("/api/profiles/{profile}/sources/{source_id}")
    async def api_update_source(profile: str, source_id: int, request: Request) -> Response:
        r = _profile_access(request, profile)
        if isinstance(r, Response):
            return r
        username, _ = r
        body = await request.json()
        if not body:
            return JSONResponse({"error": "Nothing to change"}, status_code=400)
        updatable = {"note", "enabled", "filters"} & set(body.keys())
        if not updatable:
            return JSONResponse({"error": "Nothing to change"}, status_code=400)
        src = app.state.db.get_source(source_id, username)
        if not src or src.get("profile") != profile:
            return Response(status_code=404)
        note = body.get("note")
        if note is not None:
            if not isinstance(note, str) or len(note) > 1000:
                return JSONResponse({"error": "Invalid note"}, status_code=400)
        enabled = body.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            return JSONResponse({"error": "Invalid enabled"}, status_code=400)
        if "filters" in body:
            if src.get("kind") != "pubmed":
                return JSONResponse({"error": "Filters only for pubmed"}, status_code=400)
            clean = profiles.clean_filters(body["filters"])
            body = {**body, "filters": clean}
        if app.state.db.update_source(source_id, username,
                                        **{k: v for k, v in body.items() if k in updatable}):
            return JSONResponse(_source_json(app.state.db.get_source(source_id, username)))
        return Response(status_code=404)

    @app.delete("/api/profiles/{profile}/sources/{source_id}", status_code=204)
    async def api_delete_source(profile: str, source_id: int, request: Request) -> Response:
        r = _profile_access(request, profile)
        if isinstance(r, Response):
            return r
        username, _ = r
        src = app.state.db.get_source(source_id, username)
        if not src or src.get("profile") != profile:
            return Response(status_code=404)
        app.state.db.delete_source(source_id, username)
        return Response(status_code=204)

    @app.post("/api/profiles/{profile}/documents", status_code=201)
    async def api_add_document(profile: str, request: Request) -> Response:
        r = _profile_access(request, profile)
        if isinstance(r, Response):
            return r
        username, _ = r
        form = await request.form()
        file = form.get("file")
        if file is None:
            return JSONResponse({"error": "No file"}, status_code=400)
        filename = os.path.basename(getattr(file, "filename", "upload") or "upload")
        content = await file.read()
        if isinstance(content, str):
            content = content.encode("utf-8")
        try:
            text = profiles.document_text(filename, content)
        except profiles.DocumentError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)
        src = app.state.db.add_source(username, profile, "file", filename,
                                        content=text)
        return JSONResponse(_source_json(src), status_code=201)

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
        r"""Upload a file to attach (SPEC §7, §16.1). Tests: test_accept_files.py (upload), test_accept_core.py::TestFiles
          session = _session_from_cookie(request, app.state.db); none -> 401 {"error": "unauthorized"}; username, _ = session
          form = await request.form(); file_obj = form.get("file"); None or a str -> 400 {"error": "no file"}
          content = await file_obj.read(files.MAX_UPLOAD + 1)   (read at most one byte more than allowed)
          name = re.split(r"[/\\]", file_obj.filename or "")[-1][:120] or "upload"
          got = files.read_upload(name, content); files.FileError e -> JSONResponse({"error": str(e)}, status_code=e.status)
          file_id = app.state.db.add_file(username, name, len(content), content, kind=got["kind"], text=got["text"])
          201 {"id": file_id, "name", "size": len(content), "kind": got["kind"], "tokens": profiles.tokens(got["text"])}
        """
        if request.headers.get("X-Coder") != "1":
            return JSONResponse({"error": "missing X-Coder header"}, status_code=403)
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, _ = session
        form = await request.form()
        file_obj = form.get("file")
        if not file_obj or isinstance(file_obj, str):
            return JSONResponse({"error": "no file"}, status_code=400)
        content = await file_obj.read(files.MAX_UPLOAD + 1)
        name = re.split(r"[/\\]", file_obj.filename or "")[-1][:120] or "upload"
        try:
            got = files.read_upload(name, content)
        except files.FileError as e:
            return JSONResponse({"error": str(e)}, status_code=e.status)
        file_id = app.state.db.add_file(username, name, len(content), content, kind=got["kind"], text=got["text"])
        return JSONResponse({
            "id": file_id,
            "name": name,
            "size": len(content),
            "kind": got["kind"],
            "tokens": profiles.tokens(got["text"] or ""),
        }, status_code=201)

    def _download(data: bytes, name: str, kind: str) -> Response:
        resp = Response(content=data, media_type=files.MIME.get(kind, "application/octet-stream"))
        resp.headers["Content-Disposition"] = files.disposition(name)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return resp

    @app.get("/api/files/{file_id}")
    async def api_get_file(request: Request, file_id: int) -> Response:
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, _ = session
        f = app.state.db.get_file(file_id, username)
        if f is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return _download(bytes(f["content"]), f["name"], f.get("kind") or "text")

    @app.get("/api/messages/{message_id}/export")
    async def api_export_message(request: Request, message_id: int, format: str = "md") -> Response:
        r"""Download an answer as a file (SPEC §16.5). Tests: test_accept_files.py (export)
          session as in api_get_file (401); m = app.state.db.get_message(message_id, username); None -> 404 {"error": "not found"}
          format not in ("md", "docx", "pdf", "html") -> 400 {"error": "Unknown format."}
          chat = app.state.db.get_chat(m["chat_id"], username); text = m["content"]
          when m["sources"]: text += "\n\n## Sources\n\n" + "\n".join of, per source s (i from 0):
            f"- [{s.get('n', i + 1)}] {s.get('title') or s.get('url')}" + (f" — {s['url']}" when s has a url, else "")
          data, _, name = files.render(chat["title"] if chat else "answer", format, text)  (FileError e -> {"error"}, e.status)
          return _download(data, name, format)
        """
        session = _session_from_cookie(request, app.state.db)
        if not session:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        username, _ = session
        m = app.state.db.get_message(message_id, username)
        if m is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if format not in ("md", "docx", "pdf", "html"):
            return JSONResponse({"error": "Unknown format."}, status_code=400)
        chat = app.state.db.get_chat(m["chat_id"], username)
        text = m["content"]
        if m.get("sources"):
            text += "\n\n## Sources\n\n" + "\n".join(
                f"- [{s.get('n', i + 1)}] {s.get('title') or s.get('url')}"
                + (f" — {s['url']}" if s.get("url") else "")
                for i, s in enumerate(m["sources"])
            )
        try:
            data, _, name = files.render(chat["title"] if chat else "answer", format, text)
        except files.FileError as e:
            return JSONResponse({"error": str(e)}, status_code=e.status)
        return _download(data, name, format)

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
        for fid in file_ids:
            f = db.get_file(fid, username)
            content += files.attach_block(f["name"], db.get_file_text(fid) or "")
        msg_id = db.add_message(chat_id, "user", content)
        if file_ids:
            db.add_message_files(msg_id, file_ids)
        # The answer runs on its own (SPEC §5.1): a phone that locks or switches apps drops the connection,
        # and a Clear Source answer takes minutes. The response only follows the answer's events; when the
        # page comes back it follows them again via GET /api/chats/<id>/live.
        stop = asyncio.Event()
        app.state.running[chat_id] = stop
        live = Live(username)
        app.state.live[chat_id] = live

        async def pump():
            try:
                async for chunk in answer.run_answer(db, settings, chat, body.get("mode") == "think", stop=stop):
                    live.add(chunk)
            except Exception as e:  # noqa: BLE001 - the page must hear that it ended
                log.exception("answer failed: %s", e)
                live.add(answer.sse("error", {"error": "Something went wrong.", "status": 500}))
            finally:
                live.finish()
                if app.state.running.get(chat_id) is stop:
                    del app.state.running[chat_id]
                await asyncio.sleep(LIVE_KEEP)       # a page that reconnects right after still sees the end
                if app.state.live.get(chat_id) is live:
                    del app.state.live[chat_id]

        live.task = asyncio.create_task(pump())
        return StreamingResponse(live.follow(), media_type="text/event-stream")

    @app.get("/api/chats/{chat_id}/live")
    async def api_live(chat_id: int, request: Request) -> Response:
        """Follow the answer that is being written in this chat, from its first event (SPEC §5.1); 204 when none."""
        r = _require_session(request, app.state.db)
        if isinstance(r, Response):
            return r
        username, _ = r
        live = app.state.live.get(chat_id)
        if live is None or live.username != username:
            return Response(status_code=204)
        return StreamingResponse(live.follow(), media_type="text/event-stream")

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
        ipkey = f"ip:{client_ip(request)}"
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

def _me(username: str, db: DB) -> dict[str, Any]:
    """Return the user dict for /api/me responses."""
    user = db.get_user(username)
    icon = (user.get("icon") or "blossom") if user.get("theme") == "blossom" else (user.get("icon") or "aero")
    return {"username": username, "rights": sorted(user.get("rights", [])), "theme": user.get("theme", "auto"), "icon": icon}


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
