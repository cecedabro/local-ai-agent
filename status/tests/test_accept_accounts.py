"""Acceptance tests for accounts and login (SPEC §14). Human-owned: the agent may not edit this file.
Run: python3 -m unittest -q status/tests/test_accept_accounts.py (or make status-test)
"""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_contract import TOKEN, Base, app  # noqa: E402
from test_unit_gateway import FakeLlama  # noqa: E402

ADMIN_PW = "correct horse battery"
ALL = ["admin", "chat", "evidence", "queue"]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


OPENER = urllib.request.build_opener(NoRedirect)


class AccountsBase(Base):
    admin: tuple[str, str] | None = ("cedric", ADMIN_PW)
    with_llama = False

    def setUp(self) -> None:
        super().setUp()
        self.stop()
        self.fake = None
        if self.with_llama:
            self.fake = FakeLlama()
            self.addCleanup(self.fake.close)
        self.boot()

    def boot(self, admin: tuple[str, str] | None | bool = True) -> None:
        srv = app.make_server("127.0.0.1", 0, db_path=self.db, token=TOKEN, clock=self.clock,
                              llama_url=self.fake.url if self.fake else None, llama_key="llk",
                              job_owners=["cecedabro"], admin=self.admin if admin is True else admin)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servers.append(srv)
        self.base = f"http://127.0.0.1:{srv.server_address[1]}"

    # -- http with cookies
    def call(self, method: str, path: str, body: object = None, cookie: str | None = None,
             token: str | None = None) -> tuple[int, dict, bytes]:
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = f"agent_session={cookie}"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with OPENER.open(r, timeout=10) as resp:
                return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()

    def code(self, *args: object, **kw: object) -> int:
        return self.call(*args, **kw)[0]  # type: ignore[arg-type]

    def login(self, name: str = "cedric", password: str = ADMIN_PW) -> str:
        code, headers, raw = self.call("POST", "/login", {"name": name, "password": password})
        self.assertEqual(code, 200, raw)
        cookie = headers.get("set-cookie", "")
        self.assertTrue(cookie.startswith("agent_session="), cookie)
        return cookie.split(";", 1)[0].split("=", 1)[1]

    def invite(self, cookie: str, name: str, rights: list[str]) -> str:
        code, _, raw = self.call("POST", "/api/invites", {"name": name, "rights": rights}, cookie=cookie)
        self.assertEqual(code, 201, raw)
        url = json.loads(raw)["url"]
        self.assertTrue(url.startswith("/invite#"), url)
        return url.split("#", 1)[1]

    def make_user(self, name: str, rights: list[str], password: str = "a long password") -> str:
        token = self.invite(self.login(), name, rights)
        code, headers, raw = self.call("POST", "/api/invites/accept", {"token": token, "password": password})
        self.assertEqual(code, 200, raw)
        return headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]

    def db_text(self) -> str:
        con = sqlite3.connect(self.db)
        try:
            out = []
            for (table,) in con.execute("select name from sqlite_master where type='table'"):
                out += [repr(row) for row in con.execute(f"select * from {table}")]
            return "\n".join(out)
        finally:
            con.close()


class NoAccountsTest(AccountsBase):
    admin = None

    def test_open_as_before(self) -> None:
        self.assertEqual(self.code("GET", "/"), 200)
        self.assertEqual(self.code("GET", "/api/status"), 200)
        self.assertEqual(self.code("GET", "/api/me"), 401)
        self.assertEqual(self.code("GET", "/login"), 200)
        self.assertEqual(self.code("POST", "/api/jobs", {"repo": "cecedabro/x"}, token=TOKEN), 201)


class LoginRequiredTest(AccountsBase):
    def test_gates(self) -> None:
        for path in ("/", "/chat"):
            code, headers, _ = self.call("GET", path)
            self.assertEqual(code, 303, path)
            self.assertEqual(headers.get("location"), "/login", path)
        for path in ("/api/status", "/partial", "/api/status/head"):
            self.assertEqual(self.code("GET", path), 401, path)
        for path in ("/healthz", "/static/style.css", "/login", "/invite"):
            self.assertEqual(self.code("GET", path), 200, path)
        self.assertEqual(self.code("GET", "/api/status", token=TOKEN), 200)          # machines
        page = self.call("GET", "/login")[2].decode()
        self.assertTrue('<form id="login"' in page and 'name="password"' in page, "login form")

    def test_login_logout_and_cookie(self) -> None:
        self.assertEqual(self.code("POST", "/login", {"name": "cedric", "password": "nope nope nope"}), 401)
        self.assertEqual(self.code("POST", "/login", {"name": "nobody", "password": ADMIN_PW}), 401)
        code, headers, raw = self.call("POST", "/login", {"name": "cedric", "password": ADMIN_PW})
        self.assertEqual(code, 200, raw)
        self.assertEqual(json.loads(raw), {"name": "cedric", "rights": ALL})
        set_cookie = headers["set-cookie"]
        for part in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/"):
            self.assertIn(part, set_cookie)
        cookie = set_cookie.split(";", 1)[0].split("=", 1)[1]
        self.assertEqual(self.code("GET", "/", cookie=cookie), 200)
        self.assertEqual(json.loads(self.call("GET", "/api/me", cookie=cookie)[2]), {"name": "cedric", "rights": ALL})
        self.assertNotIn(cookie, self.db_text(), "the session id must be stored hashed")
        code, headers, _ = self.call("POST", "/logout", cookie=cookie)
        self.assertEqual(code, 200)
        self.assertIn("Max-Age=0", headers.get("set-cookie", ""))
        self.assertEqual(self.code("GET", "/api/me", cookie=cookie), 401)

    def test_password_stored_with_scrypt(self) -> None:
        text = self.db_text()
        self.assertIn("scrypt$", text)
        self.assertNotIn(ADMIN_PW, text)

    def test_rate_limit(self) -> None:
        for _ in range(5):
            self.assertEqual(self.code("POST", "/login", {"name": "cedric", "password": "wrong wrong"}), 401)
        self.assertEqual(self.code("POST", "/login", {"name": "cedric", "password": ADMIN_PW}), 429)
        self.clock.t += 15 * 60 + 1
        self.login()

    def test_session_expires_after_30_idle_days(self) -> None:
        cookie = self.login()
        self.clock.t += 29 * 86400
        self.assertEqual(self.code("GET", "/api/me", cookie=cookie), 200)            # use refreshes it
        self.clock.t += 29 * 86400
        self.assertEqual(self.code("GET", "/api/me", cookie=cookie), 200)
        self.clock.t += 31 * 86400
        self.assertEqual(self.code("GET", "/api/me", cookie=cookie), 401)

    def test_seed_only_when_empty(self) -> None:
        self.stop()
        self.boot(admin=("cedric", "another password"))
        self.assertEqual(self.code("POST", "/login", {"name": "cedric", "password": "another password"}), 401)
        self.login()

    def test_change_password(self) -> None:
        cookie = self.login()
        self.assertEqual(self.code("POST", "/api/me/password", {"old": "wrong", "new": "a new password"}, cookie=cookie), 403)
        self.assertEqual(self.code("POST", "/api/me/password", {"old": ADMIN_PW, "new": "short"}, cookie=cookie), 400)
        self.assertEqual(self.code("POST", "/api/me/password", {"old": ADMIN_PW, "new": "a new password"}, cookie=cookie), 200)
        self.assertEqual(self.code("POST", "/login", {"name": "cedric", "password": ADMIN_PW}), 401)
        self.login(password="a new password")


class InviteTest(AccountsBase):
    def test_invite_and_accept(self) -> None:
        admin = self.login()
        self.assertEqual(self.code("POST", "/api/invites", {"name": "Anna!", "rights": ["chat"]}, cookie=admin), 400)
        self.assertEqual(self.code("POST", "/api/invites", {"name": "anna", "rights": ["chat", "fly"]}, cookie=admin), 400)
        token = self.invite(admin, "anna", ["chat", "evidence"])
        self.assertNotIn(token, self.db_text(), "the invite token must be stored hashed")
        self.assertEqual(self.code("POST", "/api/invites", {"name": "anna", "rights": ["chat"]}, cookie=admin), 409)
        self.assertEqual(self.code("POST", "/api/invites", {"name": "cedric", "rights": ["chat"]}, cookie=admin), 409)
        self.assertEqual(self.code("POST", "/api/invites/accept", {"token": token, "password": "short"}), 400)
        self.assertEqual(self.code("POST", "/api/invites/accept", {"token": "nope", "password": "a long password"}), 404)
        code, headers, raw = self.call("POST", "/api/invites/accept", {"token": token, "password": "a long password"})
        self.assertEqual(code, 200, raw)
        self.assertEqual(json.loads(raw), {"name": "anna", "rights": ["chat", "evidence"]})
        self.assertIn("agent_session=", headers.get("set-cookie", ""))
        self.assertEqual(self.code("POST", "/api/invites/accept", {"token": token, "password": "a long password"}), 410)
        self.login("anna", "a long password")

    def test_invite_expires(self) -> None:
        token = self.invite(self.login(), "bob", ["chat"])
        self.clock.t += 7 * 86400 + 1
        self.assertEqual(self.code("POST", "/api/invites/accept", {"token": token, "password": "a long password"}), 410)

    def test_only_admin_invites(self) -> None:
        anna = self.make_user("anna", ["chat", "queue"])
        self.assertEqual(self.code("POST", "/api/invites", {"name": "eve", "rights": ["admin"]}, cookie=anna), 403)
        self.assertEqual(self.code("POST", "/api/invites", {"name": "eve", "rights": ["chat"]}), 401)


class RightsTest(AccountsBase):
    with_llama = True

    def chat(self, cookie: str) -> int:
        return self.code("POST", "/v1/chat/completions",
                         {"model": "coder", "messages": [{"role": "user", "content": "hi"}]}, cookie=cookie)

    def test_rights_enforced(self) -> None:
        anna = self.make_user("anna", ["chat", "evidence"])
        quinn = self.make_user("quinn", ["queue"])
        self.assertEqual(self.code("GET", "/api/status", cookie=anna), 200)          # any login may read
        self.assertEqual(self.code("POST", "/api/jobs", {"repo": "cecedabro/x"}, cookie=anna), 403)
        self.assertEqual(self.code("POST", "/api/jobs", {"repo": "cecedabro/x"}, cookie=quinn), 201)
        self.assertEqual(self.chat(anna), 200)
        self.assertEqual(self.chat(quinn), 403)
        clients = {c["client"] for c in json.loads(self.call("GET", "/api/status", cookie=anna)[2])["gateway"]["recent"]}
        self.assertIn("chat:anna", clients)
        admin = self.login()
        # machine-only endpoints ignore sessions, even an admin's
        body = {"run_id": "r1", "repo": "x", "kind": "run_started", "ts": "2026-11-17T10:00:00+00:00"}
        self.assertEqual(self.code("POST", "/api/events", body, cookie=admin), 401)
        self.assertEqual(self.code("POST", "/api/events", body, token=TOKEN), 201)
        self.assertEqual(self.code("POST", "/api/runs/r1/pause", {}, cookie=anna), 403)
        self.assertEqual(self.code("POST", "/api/runs/r1/pause", {}, cookie=quinn), 200)


class AdminTest(AccountsBase):
    def test_list_update_delete(self) -> None:
        admin = self.login()
        anna = self.make_user("anna", ["chat"])
        self.assertEqual(self.code("GET", "/api/users", cookie=anna), 403)
        code, _, raw = self.call("GET", "/api/users", cookie=admin)
        self.assertEqual(code, 200, raw)
        users = json.loads(raw)
        self.assertEqual([u["name"] for u in users], ["anna", "cedric"])
        self.assertEqual(set(users[0]), {"name", "rights", "created", "last_seen", "disabled"})
        self.assertEqual(self.code("POST", "/api/users/anna", {"rights": ["chat", "fly"]}, cookie=admin), 400)
        self.assertEqual(self.code("POST", "/api/users/nobody", {"rights": ["chat"]}, cookie=admin), 404)
        code, _, raw = self.call("POST", "/api/users/anna", {"rights": ["chat", "queue"]}, cookie=admin)
        self.assertEqual((code, json.loads(raw)["rights"]), (200, ["chat", "queue"]))
        self.assertEqual(self.code("POST", "/api/users/anna", {"disabled": True}, cookie=admin), 200)
        self.assertEqual(self.code("GET", "/api/me", cookie=anna), 401)               # sessions ended
        self.assertEqual(self.code("POST", "/login", {"name": "anna", "password": "a long password"}), 401)
        self.assertEqual(self.code("POST", "/api/users/cedric", {"rights": ["chat"]}, cookie=admin), 409)
        self.assertEqual(self.code("POST", "/api/users/cedric", {"disabled": True}, cookie=admin), 409)
        self.assertEqual(self.code("POST", "/api/users/cedric/delete", {}, cookie=admin), 409)
        self.assertEqual(self.code("POST", "/api/users/anna/delete", {}, cookie=admin), 200)
        self.assertEqual([u["name"] for u in json.loads(self.call("GET", "/api/users", cookie=admin)[2])], ["cedric"])


class PagesTest(AccountsBase):
    with_llama = True                                     # the chat card needs the gateway

    def page(self, path: str, cookie: str) -> str:
        code, _, raw = self.call("GET", path, cookie=cookie)
        self.assertEqual(code, 200, path)
        return raw.decode()

    def test_page_follows_rights(self) -> None:
        admin = self.login()
        anna = self.make_user("anna", ["chat", "evidence"])
        p = self.page("/", anna)
        self.assertTrue('id="chat"' in p, "chat right: chat card")
        self.assertFalse('<form id="add"' in p, "no queue right: no add-job form")
        self.assertFalse('<form id="unlock"' in p, "logged in: no token unlock form")
        self.assertTrue('id="logout"' in p and "anna" in p, "header shows the name and Log out")
        self.assertFalse('href="/people"' in p, "only admins see /people")
        p = self.page("/", admin)
        self.assertTrue('<form id="add"' in p and 'href="/people"' in p, "admin: add-job form and /people link")
        self.assertEqual(self.code("GET", "/people", cookie=anna), 403)
        code, headers, _ = self.call("GET", "/people")
        self.assertEqual((code, headers.get("location")), (303, "/login"))
        p = self.page("/people", admin)
        self.assertTrue('<form id="invite"' in p and "anna" in p, "/people: invite form and the accounts")
        chat = self.page("/chat", anna)
        self.assertFalse('<form id="unlock"' in chat, "/chat logged in: no token form")
