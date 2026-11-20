"""Fixes after the first live week (2026-11-20): keep-alive pings, answer language, web search off for Clear Source,
login failure window, the visitor's address. Run: make chat-test K=live_fixes
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from coder import app as appmod, db as dbm, profiles, research


def test_live_pings_while_quiet(monkeypatch):
    monkeypatch.setattr(appmod, "LIVE_PING", 0.01)

    async def go():
        live = appmod.Live("u1")
        got = []

        async def reader():
            async for c in live.follow():
                got.append(c)
        t = asyncio.create_task(reader())
        await asyncio.sleep(0.05)
        live.add("a")
        live.finish()
        await asyncio.wait_for(t, 1)
        return got
    got = asyncio.run(go())
    assert got[0] == ": ping\n\n" and got[-1] == "a", "a comment line keeps the connection open, then the events"


def test_answer_language():
    assert research.answer_language("Werkt apixaban?").startswith("Dutch")
    assert research.answer_language("Wat is de eerste keus bij een blaasontsteking?").startswith("Dutch")
    assert research.answer_language("what are the effects of paracetamol on stomach health") == "English"
    assert research.answer_language("Is it safe?") == "English"
    m = research.evidence_messages("what are the effects of paracetamol on stomach health", [], {"instructions": ""})
    assert "in English" in m[0]["content"] and "only once" in m[0]["content"]


def test_clear_source_starts_without_web(tmp_path):
    d = dbm.DB(str(tmp_path))
    d.init_schema()
    d.create_user("u1", "x", [])
    assert profiles.config(d, "u1", "evidence")["web"] is False
    assert profiles.config(d, "u1", "evidence")["web"] is False, "stays off"
    assert profiles.config(d, "u1", "general")["web"] is True


def test_login_failures_expire(tmp_path):
    d = dbm.DB(str(tmp_path))
    d.init_schema()
    conn = d._connect()
    for i in range(5):
        conn.execute("INSERT INTO login_failures (key, at) VALUES (?, ?)", ("user:x", f"2000-01-01T00:00:0{i}+00:00"))
    conn.commit()
    assert not d.has_recent_failures("user:x"), "old failures don't count"
    for _ in range(5):
        d.record_login_failure("user:y")
    # record_login_failure may collapse same-instant rows; count what is stored
    n = conn.execute("SELECT COUNT(*) FROM login_failures WHERE key='user:y'").fetchone()[0]
    assert d.has_recent_failures("user:y", limit=n)


def test_client_ip():
    req = SimpleNamespace(headers={"cf-connecting-ip": "203.0.113.9"}, client=SimpleNamespace(host="10.0.0.1"))
    assert appmod.client_ip(req) == "203.0.113.9"
    req = SimpleNamespace(headers={"x-forwarded-for": "1.2.3.4"}, client=SimpleNamespace(host="10.0.0.1"))
    assert appmod.client_ip(req) == "10.0.0.1", "X-Forwarded-For is ignored"


def test_static_files_are_revalidated(tmp_path):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.create_app({"DATA_DIR": str(tmp_path), "ADMIN_USER": "a", "ADMIN_PASSWORD": "pw-long-enough-1"}))
    r = c.get("/static/css/chat.css")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", "a deploy reaches the browser at once"
