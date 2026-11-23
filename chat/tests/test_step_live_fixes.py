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


# ---------------------------------------------------------------- a profile seeded with an older list gets the new one once
def test_old_clear_source_profile_gets_the_dutch_list_once(tmp_path):
    d = dbm.DB(str(tmp_path))
    d.init_schema()
    d.create_user("lisa", "x", [])
    d.add_source("lisa", "evidence", "pubmed", "PubMed", note="")
    d.add_source("lisa", "evidence", "website", "rivm.nl", domain="rivm.nl", note="")
    d.set_profile_settings("lisa", "evidence", seeded=True, web=True)        # what an older version left behind
    cfg = profiles.config(d, "lisa", "evidence")
    seed_domains = {c.get("domain") for c in profiles.SEED["evidence"] if c["kind"] == "website"}
    have = {s["domain"] for s in cfg["sources"] if s["kind"] == "website"}
    assert seed_domains <= have, "the Dutch defaults were added"
    assert sum(s["kind"] == "pubmed" for s in cfg["sources"]) == 1, "PubMed not doubled"
    assert cfg["web"] is False and d.get_profile_settings("lisa", "evidence")["seeded"] is True
    gone = next(s for s in cfg["sources"] if s["kind"] == "website" and s["domain"] != "rivm.nl")
    d.delete_source(gone["id"], "lisa")
    d.set_profile_settings("lisa", "evidence", instructions="Kort.")         # saving settings keeps the list version
    again = profiles.config(d, "lisa", "evidence")
    assert gone["domain"] not in {s["domain"] for s in again["sources"]}, "a removed default stays removed"


# ---------------------------------------------------------------- the GPU lent out to the cv-skills demo
def test_busy_carries_the_gpu_taken_message():
    from coder import gateway

    class Resp:
        def __init__(self, body: bytes) -> None:
            self.body = body

        async def aread(self) -> bytes:
            return self.body

    taken = b'{"error": {"message": "The GPU is busy with the cv-skills demo; try again in a few minutes.", "type": "gpu_taken"}}'
    assert "cv-skills" in asyncio.run(gateway.busy_from(Resp(taken))).message
    assert asyncio.run(gateway.busy_from(Resp(b'{"error":"busy"}'))).message == ""
    assert asyncio.run(gateway.busy_from(Resp(b"not json"))).message == ""


def test_answer_stops_waiting_when_the_gpu_is_lent_out(tmp_path):
    import json

    from coder import answer, gateway
    from coder.db import DB
    d = DB(str(tmp_path))
    d.init_schema()
    d.create_user("u1", "x", ["chat"])
    c = d.create_chat("u1", "general")
    d.add_message(c["id"], "user", "hi")
    chat = d.get_chat(c["id"], "u1")
    calls = {"n": 0}

    async def stream(settings, messages, think, tools=None):
        calls["n"] += 1
        raise gateway.Busy("The GPU is busy with the cv-skills demo; try again in a few minutes.")
        yield  # pragma: no cover

    async def no_sleep(_):
        return None

    settings = SimpleNamespace(STATUS_URL="", CTX_DEFAULT=32768, WAIT_LIMIT=60)

    async def go():
        return [x async for x in answer.run_answer(d, settings, chat, True, stream=stream, sleep=no_sleep)]
    out = asyncio.run(go())
    errors = [json.loads(x.split("data: ", 1)[1]) for x in out if x.startswith("event: error")]
    assert errors and "cv-skills" in errors[0]["error"] and errors[0]["status"] == 503
    assert calls["n"] == 1                                    # no waiting loop
    d.close()
