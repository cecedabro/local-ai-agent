"""Judge for the chat app foundation (chat/SPEC.md §1–§8). Protected: change the code, not this file.

Run: make chat-test            (all)
     make chat-test K=Auth     (one group: pytest -k)
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from coder.app import create_app
from fake_gateway import FakeGateway

H = {"X-Coder": "1"}
ADMIN = ("cedric", "admin-password-123")


@pytest.fixture()
def gw():
    g = FakeGateway().start()
    yield g
    g.stop()


def make_client(tmp_path, gw, **over) -> TestClient:
    settings = {"DATA_DIR": str(tmp_path), "GATEWAY_URL": gw.url + "/v1", "GATEWAY_KEY": "sk-chat",
                "STATUS_URL": gw.url, "ADMIN_USER": ADMIN[0], "ADMIN_PASSWORD": ADMIN[1],
                "SECURE_COOKIES": "0", "WAIT_LIMIT": "6"}
    settings.update(over)
    return TestClient(create_app(settings))


@pytest.fixture()
def client(tmp_path, gw):
    with make_client(tmp_path, gw) as c:
        yield c


def login(c: TestClient, user=ADMIN[0], pw=ADMIN[1]):
    return c.post("/api/login", json={"username": user, "password": pw}, headers=H)


def invite_user(c: TestClient, name: str, rights: list[str], pw="user-password-123") -> TestClient:
    """Admin c creates an invite; a fresh client accepts it. Returns the new user's client."""
    r = c.post("/api/invites", json={"rights": rights}, headers=H)
    assert r.status_code == 201, r.text
    other = TestClient(c.app)
    r = other.post("/api/invites/accept", json={"token": r.json()["token"], "username": name, "password": pw}, headers=H)
    assert r.status_code == 201, r.text
    return other


def sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        ev, data = "message", None
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if data is not None:
            out.append((ev, data))
    return out


def send(c: TestClient, chat_id: int, content: str, **extra) -> list[tuple[str, dict]]:
    r = c.post(f"/api/chats/{chat_id}/messages", json={"content": content, **extra}, headers=H)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    return sse(r.text)


def new_chat(c: TestClient, profile: str = "general") -> int:
    r = c.post("/api/chats", json={"profile": profile}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ----------------------------------------------------------------------------- §1, §8 basics
class TestBasics:
    def test_healthz_and_first_admin(self, client):
        assert client.get("/healthz").json() == {"ok": True}
        assert login(client).status_code == 200
        me = client.get("/api/me").json()
        assert me["username"] == "cedric" and "admin" in me["rights"] and me["theme"] == "auto"

    def test_admin_seeded_only_once(self, tmp_path, gw):
        with make_client(tmp_path, gw) as c:
            assert login(c).status_code == 200
        with make_client(tmp_path, gw, ADMIN_PASSWORD="another-password-1") as c:
            assert login(c, pw="another-password-1").status_code == 401
            assert login(c).status_code == 200

    def test_root_redirects_without_session(self, client):
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].endswith("/login")
        for page in ("/login", "/invite"):
            assert client.get(page).status_code == 200

    def test_security_headers(self, client):
        r = client.get("/login")
        csp = r.headers.get("content-security-policy", "")
        assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp
        assert r.headers.get("x-content-type-options") == "nosniff"
        assert "<script>" not in r.text.replace(" ", "").lower(), "no inline scripts (CSP)"

    def test_chat_page_footer_and_hooks(self, client):
        login(client)
        page = client.get("/").text
        assert "Qwen3.6 35B-A3B" in page and "RTX 3080 Ti" in page
        assert 'href="https://github.com/cecedabro/local-ai-agent"' in page
        for hook in ('class="side"', 'class="main"', 'class="composer"', 'class="ctx"', 'class="orb"',
                     "/static/css/tokens.css", "/static/js/theme.js", "/static/vendor/markdown-it.min.js"):
            assert hook in page, hook
        assert client.get("/static/vendor/markdown-it.min.js").status_code == 200


# ----------------------------------------------------------------------------- §2 auth
class TestAuth:
    def test_csrf_header_required(self, client):
        r = client.post("/api/login", json={"username": ADMIN[0], "password": ADMIN[1]})
        assert r.status_code == 403
        login(client)
        assert client.post("/api/chats", json={}).status_code == 403
        assert client.patch("/api/me", json={"theme": "dark"}).status_code == 403

    def test_login_logout_cookie(self, client):
        assert login(client, pw="wrong-password-1").status_code == 401
        assert login(client, user="nobody").status_code == 401
        r = login(client)
        assert r.status_code == 200 and r.json()["username"] == "cedric"
        cookie = r.headers["set-cookie"].lower()
        assert "coder_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
        assert client.get("/api/me").status_code == 200
        assert client.post("/api/logout", headers=H).status_code == 204
        assert client.get("/api/me").status_code == 401

    def test_secure_cookie_by_default(self, tmp_path, gw):
        with make_client(tmp_path, gw, SECURE_COOKIES="1") as c:
            r = login(c)
            assert "secure" in r.headers["set-cookie"].lower()

    def test_session_stored_hashed(self, client, tmp_path):
        r = login(client)
        sid = r.cookies.get("coder_session") or client.cookies.get("coder_session")
        assert sid and len(sid) >= 40
        raw = b"".join(f.read_bytes() for f in tmp_path.glob("coder.db*"))   # WAL files too
        assert sid.encode() not in raw, "the session id must be stored as a hash"
        assert ADMIN[1].encode() not in raw, "the password must be stored as a hash"

    def test_needs_session(self, client):
        for method, path in (("GET", "/api/me"), ("GET", "/api/chats"), ("POST", "/api/chats"), ("GET", "/api/invites")):
            assert client.request(method, path, headers=H, json={}).status_code == 401, path

    def test_theme(self, client):
        login(client)
        assert client.patch("/api/me", json={"theme": "blossom"}, headers=H).json()["theme"] == "blossom"
        assert client.get("/api/me").json()["theme"] == "blossom"
        assert client.patch("/api/me", json={"theme": "purple"}, headers=H).status_code == 400


class TestRateLimit:
    def test_five_failures_then_429(self, client):
        for _ in range(5):
            assert login(client, pw="wrong-password-1").status_code == 401
        assert login(client).status_code == 429, "even the right password waits out the window"


# ----------------------------------------------------------------------------- §3 invites
class TestInvites:
    def test_invite_flow(self, client):
        login(client)
        r = client.post("/api/invites", json={"rights": ["chat", "evidence"], "note": "for L"}, headers=H)
        assert r.status_code == 201
        inv = r.json()
        assert inv["url"] == "/invite#" + inv["token"] and inv["rights"] == ["chat", "evidence"]
        listed = client.get("/api/invites").json()
        assert listed[0]["id"] == inv["id"] and "token" not in listed[0]
        other = TestClient(client.app)
        bad = other.post("/api/invites/accept", json={"token": inv["token"], "username": "Bad Name", "password": "long-enough-1"}, headers=H)
        assert bad.status_code == 400
        short = other.post("/api/invites/accept", json={"token": inv["token"], "username": "lisa", "password": "short"}, headers=H)
        assert short.status_code == 400
        taken = other.post("/api/invites/accept", json={"token": inv["token"], "username": "cedric", "password": "long-enough-1"}, headers=H)
        assert taken.status_code == 409
        ok = other.post("/api/invites/accept", json={"token": inv["token"], "username": "lisa", "password": "long-enough-1"}, headers=H)
        assert ok.status_code == 201 and sorted(ok.json()["rights"]) == ["chat", "evidence"]
        assert other.get("/api/me").json()["username"] == "lisa"
        again = TestClient(client.app).post("/api/invites/accept", json={"token": inv["token"], "username": "lisa2", "password": "long-enough-1"}, headers=H)
        assert again.status_code == 400, "single use"
        assert client.get("/api/invites").json()[0]["used_by"] == "lisa"

    def test_only_admin(self, client):
        login(client)
        user = invite_user(client, "friend", ["chat"])
        assert user.post("/api/invites", json={"rights": ["chat"]}, headers=H).status_code == 403
        assert user.get("/api/users").status_code == 403
        assert user.get("/admin", follow_redirects=False).status_code == 403
        assert client.post("/api/invites", json={"rights": []}, headers=H).status_code == 400
        assert client.post("/api/invites", json={"rights": ["root"]}, headers=H).status_code == 400

    def test_users_and_delete(self, client):
        login(client)
        invite_user(client, "friend", ["chat"])
        names = [u["username"] for u in client.get("/api/users").json()]
        assert sorted(names) == ["cedric", "friend"]
        assert client.delete("/api/users/cedric", headers=H).status_code == 400
        assert client.delete("/api/users/friend", headers=H).status_code == 204
        assert [u["username"] for u in client.get("/api/users").json()] == ["cedric"]

    def test_invite_delete(self, client):
        login(client)
        inv = client.post("/api/invites", json={"rights": ["chat"]}, headers=H).json()
        assert client.delete(f"/api/invites/{inv['id']}", headers=H).status_code == 204
        r = TestClient(client.app).post("/api/invites/accept", json={"token": inv["token"], "username": "x1", "password": "long-enough-1"}, headers=H)
        assert r.status_code == 400


# ----------------------------------------------------------------------------- §4 chats
class TestChats:
    def test_crud_and_isolation(self, client):
        login(client)
        a = new_chat(client)
        b = new_chat(client)
        listed = client.get("/api/chats").json()
        assert [c["id"] for c in listed] == [b, a] and listed[0]["title"] == "New chat"
        assert client.patch(f"/api/chats/{a}", json={"title": "Renamed"}, headers=H).json()["title"] == "Renamed"
        assert client.patch(f"/api/chats/{a}", json={"title": ""}, headers=H).status_code == 400
        other = invite_user(client, "friend", ["chat"])
        assert other.get(f"/api/chats/{a}").status_code == 404
        assert other.get("/api/chats").json() == []
        assert client.delete(f"/api/chats/{b}", headers=H).status_code == 204
        assert client.get(f"/api/chats/{b}").status_code == 404

    def test_profiles_need_rights(self, client):
        login(client)
        assert client.post("/api/chats", json={"profile": "nope"}, headers=H).status_code == 400
        friend = invite_user(client, "friend", ["chat"])
        assert friend.post("/api/chats", json={"profile": "evidence"}, headers=H).status_code == 403
        assert friend.post("/api/chats", json={"profile": "general"}, headers=H).status_code == 201
        eh = invite_user(client, "eh", ["evidence"])
        assert eh.post("/api/chats", json={"profile": "general"}, headers=H).status_code == 403
        assert eh.post("/api/chats", json={"profile": "evidence"}, headers=H).json()["profile"] == "evidence"


# ----------------------------------------------------------------------------- §5 streaming
class TestStream:
    def test_stream_events_and_storage(self, client, gw):
        login(client)
        cid = new_chat(client)
        gw.script = {"reasoning": ["let me think"], "content": ["Hel", "lo ", "world"],
                     "usage": {"prompt_tokens": 40, "completion_tokens": 3}}
        evs = send(client, cid, "  Say   hello to the whole wide world please, in a friendly and very long way  ", mode="think")
        names = [e for e, _ in evs]
        phases = [d["phase"] for e, d in evs if e == "phase"]
        assert phases == ["reading", "thinking", "writing"]
        assert "".join(d["text"] for e, d in evs if e == "delta") == "Hello world"
        # SPEC §13.3: reasoning comes as thinking events, never as part of the answer
        assert [d["text"] for e, d in evs if e == "thinking"] == ["let me think"]
        assert "let me think" not in "".join(d["text"] for e, d in evs if e == "delta")
        assert names[-1] == "done"
        done = evs[-1][1]
        assert done["tokens"] == 3 and done["context"]["used"] == 43 and done["context"]["max"] == 32768
        body = gw.chat_requests()[-1]["body"]
        assert gw.chat_requests()[-1]["headers"].get("Authorization") == "Bearer sk-chat"
        assert body["model"] == "coder" and body["stream"] is True
        assert body["chat_template_kwargs"] == {"enable_thinking": True}
        assert body["messages"][0]["role"] == "system" and body["messages"][-1]["role"] == "user"
        chat = client.get(f"/api/chats/{cid}").json()
        assert [m["role"] for m in chat["messages"]] == ["user", "assistant"]
        assert chat["messages"][1]["content"] == "Hello world" and chat["messages"][1]["id"] == done["message_id"]
        assert chat["title"] == 'Say hello to the whole wide world please, in a friendly and…'

    def test_quick_mode_and_history(self, client, gw):
        login(client)
        cid = new_chat(client)
        send(client, cid, "first")
        send(client, cid, "second", mode="quick")
        body = gw.chat_requests()[-1]["body"]
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert [m["content"] for m in body["messages"][1:]] == ["first", "Hello there", "second"]

    def test_waiting_when_busy(self, client, gw):
        login(client)
        cid = new_chat(client)
        gw.busy_left = 1
        evs = send(client, cid, "hi")
        assert [d["phase"] for e, d in evs if e == "phase"][:3] == ["reading", "waiting", "writing"]
        assert evs[-1][0] == "done"

    def test_gives_up_after_wait_limit(self, tmp_path, gw):
        with make_client(tmp_path, gw, WAIT_LIMIT="3") as c:
            login(c)
            cid = new_chat(c)
            gw.busy_left = 100
            t = time.time()
            evs = send(c, cid, "hi")
            assert evs[-1][0] == "error" and time.time() - t < 15
            assert [m["role"] for m in c.get(f"/api/chats/{cid}").json()["messages"]] == ["user"]

    def test_gateway_error(self, client, gw):
        login(client)
        cid = new_chat(client)
        gw.script = {"status": 500}
        evs = send(client, cid, "hi")
        assert evs[-1][0] == "error" and evs[-1][1]["status"] == 500

    def test_input_limits(self, client):
        login(client)
        cid = new_chat(client)
        assert client.post(f"/api/chats/{cid}/messages", json={"content": "  "}, headers=H).status_code == 400
        assert client.post(f"/api/chats/{cid}/messages", json={"content": "x" * 20001}, headers=H).status_code == 413
        assert client.post("/api/chats/99999/messages", json={"content": "hi"}, headers=H).status_code == 404


# ----------------------------------------------------------------------------- §6 context
class TestContext:
    def test_max_from_status_and_estimate(self, client, gw):
        gw.n_ctx = 65536
        login(client)
        cid = new_chat(client)
        ctx = client.get(f"/api/chats/{cid}").json()["context"]
        assert ctx["max"] == 65536 and ctx["compress_at"] == 49152

    def test_default_without_status(self, tmp_path, gw):
        with make_client(tmp_path, gw, STATUS_URL="", CTX_DEFAULT="16384") as c:
            login(c)
            cid = new_chat(c)
            assert c.get(f"/api/chats/{cid}").json()["context"]["max"] == 16384

    def test_compress_now(self, client, gw):
        login(client)
        cid = new_chat(client)
        for i in range(3):
            send(client, cid, f"question {i}")
        r = client.post(f"/api/chats/{cid}/compress", headers=H)
        assert r.status_code == 200
        msgs = client.get(f"/api/chats/{cid}").json()["messages"]
        roles = [m["role"] for m in msgs]
        assert roles.count("summary") == 1
        s = roles.index("summary")
        assert roles[s + 1:] == ["user", "assistant", "user", "assistant"], "the last 4 stay after the summary"
        assert msgs[s]["content"] == gw.summary
        send(client, cid, "next")
        sent = gw.chat_requests()[-1]["body"]["messages"]
        assert gw.summary in sent[1]["content"] and sent[1]["role"] == "system"
        assert "question 0" not in json.dumps(sent), "summarised messages are not sent again"

    def test_auto_compress(self, client, gw):
        login(client)
        cid = new_chat(client)
        for i in range(3):
            send(client, cid, f"q{i}")
        gw.script = {"content": ["ok"], "usage": {"prompt_tokens": 30000, "completion_tokens": 1}}
        send(client, cid, "big")
        gw.script = {}
        evs = send(client, cid, "after")
        assert [d["phase"] for e, d in evs if e == "phase"][0] == "compressing"


# ----------------------------------------------------------------------------- §7 files
class TestFiles:
    def test_upload_download_and_attach(self, client, gw):
        login(client)
        r = client.post("/api/files", files={"file": ("../../notes.md", b"# Notes\nline two\n", "text/markdown")}, headers=H)
        assert r.status_code == 201
        f = r.json()
        assert f["name"] == "notes.md" and f["size"] == 17
        d = client.get(f"/api/files/{f['id']}")
        assert d.status_code == 200 and d.content == b"# Notes\nline two\n"
        assert "attachment" in d.headers["content-disposition"] and d.headers["x-content-type-options"] == "nosniff"
        cid = new_chat(client)
        send(client, cid, "what is in it?", files=[f["id"]])
        last = gw.chat_requests()[-1]["body"]["messages"][-1]["content"]
        assert "what is in it?" in last and "File notes.md:" in last and "line two" in last
        msg = client.get(f"/api/chats/{cid}").json()["messages"][0]
        assert msg["files"] == [{"id": f["id"], "name": "notes.md", "size": 17}]

    def test_limits_and_isolation(self, client):
        login(client)
        assert client.post("/api/files", files={"file": ("a.bin", b"\x00\x01\xff", "application/octet-stream")}, headers=H).status_code == 415
        assert client.post("/api/files", files={"file": ("big.txt", b"a" * 200001, "text/plain")}, headers=H).status_code == 413
        f = client.post("/api/files", files={"file": ("a.txt", b"hello", "text/plain")}, headers=H).json()
        other = invite_user(client, "friend", ["chat"])
        assert other.get(f"/api/files/{f['id']}").status_code == 404
        cid = new_chat(other)
        r = other.post(f"/api/chats/{cid}/messages", json={"content": "hi", "files": [f["id"]]}, headers=H)
        assert r.status_code == 400
