"""Changing your own password (chat/SPEC.md §17)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from test_accept_core import ADMIN, H, client, gw, login  # noqa: F401  (fixtures)

URL = "/api/me/password"
NEW = "a-brand-new-password"


def change(c: TestClient, current: str, new: str):
    return c.post(URL, json={"current": current, "new": new}, headers=H)


def test_needs_a_session_and_the_header(client):
    assert change(client, ADMIN[1], NEW).status_code == 401
    assert login(client).status_code == 200
    assert client.post(URL, json={"current": ADMIN[1], "new": NEW}).status_code == 403   # no X-Coder


def test_change_then_sign_in_with_the_new_one_only(client):
    login(client)
    r = change(client, ADMIN[1], NEW)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert client.get("/api/me").status_code == 200                  # this session stays
    fresh = TestClient(client.app)
    assert login(fresh, pw=ADMIN[1]).status_code == 401
    assert login(fresh, pw=NEW).status_code == 200


def test_wrong_current_password_is_refused_and_counted(client):
    login(client)
    for _ in range(5):
        r = change(client, "not-my-password", NEW)
        assert r.status_code == 403
    assert change(client, ADMIN[1], NEW).status_code == 429           # rate limited like sign-in
    assert login(TestClient(client.app), pw=ADMIN[1]).status_code == 429


def test_rules_for_the_new_password(client):
    login(client)
    assert change(client, ADMIN[1], "short").status_code == 400
    assert change(client, ADMIN[1], "x" * 201).status_code == 400
    assert change(client, ADMIN[1], ADMIN[1]).status_code == 400
    assert client.post(URL, json={"current": ADMIN[1]}, headers=H).status_code == 400
    assert client.post(URL, json=["nope"], headers=H).status_code == 400
    assert client.post(URL, json={"current": 1, "new": 2}, headers=H).status_code == 400


def test_spaces_around_are_ignored_like_at_sign_in(client):
    login(client)
    assert change(client, f"  {ADMIN[1]} ", f" {NEW}  ").status_code == 200
    assert login(TestClient(client.app), pw=NEW).status_code == 200


def test_other_sessions_end_this_one_stays(client):
    login(client)
    other = TestClient(client.app)
    login(other)
    assert other.get("/api/me").status_code == 200
    r = change(client, ADMIN[1], NEW)
    assert r.json()["other_sessions_ended"] == 1
    assert other.get("/api/me").status_code == 401
    assert client.get("/api/me").status_code == 200


def test_pages_have_the_form_and_the_eye(client):
    login(client)
    page = client.get("/account").text
    for bit in ('id="pw-form"', 'autocomplete="current-password"', 'autocomplete="new-password"',
                'autocomplete="username"', 'class="pw-eye"', "/static/js/password.js"):
        assert bit in page, bit
    assert 'class="pw-eye"' in TestClient(client.app).get("/login").text
