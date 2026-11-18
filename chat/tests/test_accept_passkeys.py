"""Judge for passkeys (chat/SPEC.md §10). Protected: change the code, not this file.
Run: make chat-test K=Passkey
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from soft_authenticator import SoftAuthenticator, b64u
from test_accept_core import H, client, gw, invite_user, login, make_client  # noqa: F401 (fixtures)

ORIGIN = "http://localhost:8090"          # the default ORIGIN setting


def register(c: TestClient, auth: SoftAuthenticator, name: str | None = None):
    r = c.post("/api/passkeys/register/options", json={}, headers=H)
    assert r.status_code == 200, r.text
    assert "coder_wa=" in r.headers.get("set-cookie", "")
    body = {"credential": auth.create(r.text)}
    if name:
        body["name"] = name
    return c.post("/api/passkeys/register/verify", json=body, headers=H)


def passkey_login(c: TestClient, auth: SoftAuthenticator, **kw):
    r = c.post("/api/passkeys/login/options", json={}, headers=H)
    assert r.status_code == 200, r.text
    return c.post("/api/passkeys/login/verify", json={"credential": auth.get(r.text, **kw)}, headers=H)


class TestPasskeyRegister:
    def test_register_list_delete(self, client):
        login(client)
        auth = SoftAuthenticator(ORIGIN)
        r = register(client, auth, name="MacBook")
        assert r.status_code == 201, r.text
        pk = r.json()
        assert pk["name"] == "MacBook" and pk["id"]
        listed = client.get("/api/passkeys").json()
        assert [p["name"] for p in listed] == ["MacBook"] and listed[0]["last_used"] is None
        r2 = register(client, SoftAuthenticator(ORIGIN))
        assert r2.json()["name"] == "Passkey"
        opts = client.post("/api/passkeys/register/options", json={}, headers=H).json()
        assert len(opts["excludeCredentials"]) == 2, "your existing passkeys are excluded"
        assert client.delete(f"/api/passkeys/{pk['id']}", headers=H).status_code == 204
        assert len(client.get("/api/passkeys").json()) == 1

    def test_register_rejects_bad_answers(self, client):
        login(client)
        auth = SoftAuthenticator(ORIGIN)
        r = client.post("/api/passkeys/register/options", json={}, headers=H)
        cred = auth.create(r.text)
        assert client.post("/api/passkeys/register/verify", json={"credential": cred}, headers=H).status_code == 201
        again = client.post("/api/passkeys/register/verify", json={"credential": cred}, headers=H)
        assert again.status_code == 400, "a challenge is single use"
        r = client.post("/api/passkeys/register/options", json={}, headers=H)
        evil = auth.create(r.text, origin="https://evil.example")
        assert client.post("/api/passkeys/register/verify", json={"credential": evil}, headers=H).status_code == 400
        assert client.post("/api/passkeys/register/verify", json={"credential": {}}, headers=H).status_code == 400

    def test_needs_session_and_header(self, client):
        assert client.post("/api/passkeys/register/options", json={}, headers=H).status_code == 401
        assert client.get("/api/passkeys").status_code == 401
        assert client.delete("/api/passkeys/1", headers=H).status_code == 401
        assert client.post("/api/passkeys/login/options", json={}).status_code == 403, "CSRF header"

    def test_cannot_delete_someone_elses(self, client):
        login(client)
        r = register(client, SoftAuthenticator(ORIGIN))
        other = invite_user(client, "friend", ["chat"])
        assert other.delete(f"/api/passkeys/{r.json()['id']}", headers=H).status_code == 404
        assert other.get("/api/passkeys").json() == []


class TestPasskeyLogin:
    def test_login_with_passkey(self, client):
        login(client)
        auth = SoftAuthenticator(ORIGIN)
        assert register(client, auth).status_code == 201
        fresh = TestClient(client.app)
        r = passkey_login(fresh, auth)
        assert r.status_code == 200, r.text
        assert r.json()["username"] == "cedric"
        assert "coder_session=" in r.headers["set-cookie"]
        assert fresh.get("/api/me").json()["username"] == "cedric"
        assert client.get("/api/passkeys").json()[0]["last_used"] is not None
        assert passkey_login(TestClient(client.app), auth).status_code == 200, "the counter goes up each time"

    def test_bad_logins(self, client):
        login(client)
        auth = SoftAuthenticator(ORIGIN)
        register(client, auth)
        fresh = TestClient(client.app)
        assert passkey_login(fresh, auth, tamper=True).status_code == 401
        assert passkey_login(fresh, auth, origin="https://evil.example").status_code == 401
        stranger = SoftAuthenticator(ORIGIN)
        r = fresh.post("/api/passkeys/login/options", json={}, headers=H)
        stranger.create('{"rp":{"id":"localhost"},"user":{"id":"%s"},"challenge":"x"}' % b64u(b"cedric"))
        assert fresh.post("/api/passkeys/login/verify", json={"credential": stranger.get(r.text)}, headers=H).status_code == 401
        r = fresh.post("/api/passkeys/login/options", json={}, headers=H)
        ok = auth.get(r.text)
        assert fresh.post("/api/passkeys/login/verify", json={"credential": ok}, headers=H).status_code == 200
        replay = TestClient(client.app)
        assert replay.post("/api/passkeys/login/verify", json={"credential": ok}, headers=H).status_code == 401
        assert fresh.get("/api/me").status_code == 200

    def test_deleted_passkey_no_longer_works(self, client):
        login(client)
        auth = SoftAuthenticator(ORIGIN)
        pk = register(client, auth).json()
        client.delete(f"/api/passkeys/{pk['id']}", headers=H)
        assert passkey_login(TestClient(client.app), auth).status_code == 401

    def test_rate_limit(self, client):
        login(client)
        auth = SoftAuthenticator(ORIGIN)
        register(client, auth)
        fresh = TestClient(client.app)
        for _ in range(5):
            assert passkey_login(fresh, auth, tamper=True).status_code == 401
        assert passkey_login(fresh, auth).status_code == 429

    def test_invited_user_adds_a_passkey(self, client):
        login(client)
        lisa = invite_user(client, "lisa", ["chat", "evidence"])
        auth = SoftAuthenticator(ORIGIN)
        assert register(lisa, auth).status_code == 201
        r = passkey_login(TestClient(client.app), auth)
        assert r.status_code == 200 and r.json()["username"] == "lisa"

    def test_account_page(self, client):
        assert client.get("/account", follow_redirects=False).status_code == 303
        login(client)
        page = client.get("/account").text
        assert "/static/js/webauthn.js" in page and 'id="passkey-list"' in page
        assert "/static/js/webauthn.js" in client.get("/login").text
