"""Small judge for chat/coder/context.py (tasks S05–S08) and the db helpers it needs (task S01). Protected."""
from __future__ import annotations

import asyncio

import pytest

from coder import context
from coder.db import DB
from fake_gateway import FakeGateway


class S:
    def __init__(self, status_url: str = "", default: int = 32768) -> None:
        self.STATUS_URL = status_url
        self.CTX_DEFAULT = default


@pytest.fixture()
def db(tmp_path):
    d = DB(str(tmp_path))
    d.init_schema()
    d.create_user("u1", "x", ["chat"])
    yield d
    d.close()


def chat_of(db, profile="general"):
    c = db.create_chat("u1", profile)
    return db.get_chat(c["id"], "u1")


# ---------------------------------------------------------------- S05
def test_ctx_max_and_cache():
    context._cache.clear()
    assert context.ctx_max(S("", 16384)) == 16384
    g = FakeGateway().start()
    try:
        g.n_ctx = 65536
        t = [1000.0]
        assert context.ctx_max(S(g.url), now=lambda: t[0]) == 65536
        g.n_ctx = 8192
        t[0] += 100
        assert context.ctx_max(S(g.url), now=lambda: t[0]) == 65536, "cached for 300 s"
        t[0] += 300
        assert context.ctx_max(S(g.url), now=lambda: t[0]) == 8192
    finally:
        g.stop()
    context._cache.clear()
    assert context.ctx_max(S("http://127.0.0.1:9", 4096)) == 4096
    assert context.window(S("", 32768)) == {"max": 32768, "compress_at": 24576}


# ---------------------------------------------------------------- S06
def test_context_and_build_messages(db):
    c = chat_of(db, "evidence")
    db.add_message(c["id"], "user", "q1")
    db.add_message(c["id"], "assistant", "a1")
    assert context.context_messages(db, c) == [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]
    db.add_message(c["id"], "summary", "S1")
    db.add_message(c["id"], "user", "q2")
    assert context.context_messages(db, c) == [
        {"role": "system", "content": context.SUMMARY_PREFIX + "S1"}, {"role": "user", "content": "q2"}]
    msgs = context.build_messages(db, c)
    assert msgs[0] == {"role": "system", "content": context.SYSTEM_PROMPTS["evidence"]} and len(msgs) == 3


# ---------------------------------------------------------------- S07
def test_used_and_info(db):
    c = chat_of(db)
    db.add_message(c["id"], "user", "x" * 400)
    est = context.used(db, c)
    assert est == -(-(len(context.SYSTEM_PROMPTS["general"]) + 400) // 4)
    db.update_chat_usage(c["id"], 1000, 50)
    c = db.get_chat(c["id"], "u1")
    assert context.used(db, c) == 1050
    assert context.info(db, c, S("", 32768)) == {"used": 1050, "max": 32768, "compress_at": 24576}


# ---------------------------------------------------------------- S01
def test_db_helpers(db):
    c = chat_of(db)
    db.update_chat_usage(c["id"], 10, 2)
    db.reset_chat_usage(c["id"])
    c = db.get_chat(c["id"], "u1")
    assert c["last_prompt_tokens"] is None and c["last_completion_tokens"] is None
    db.add_message(c["id"], "user", "later")
    db.add_message_at(c["id"], "summary", "earlier", "2000-01-01T00:00:00+00:00")
    assert [m["content"] for m in db.get_messages(c["id"], "u1")] == ["earlier", "later"]


# ---------------------------------------------------------------- S08
def test_just_before():
    assert context.just_before("2026-11-18T10:00:00.500000+00:00") == "2026-11-18T10:00:00.499999+00:00"


def test_compress(db):
    c = chat_of(db)
    for i in range(3):
        db.add_message(c["id"], "user", f"q{i}")
        db.add_message(c["id"], "assistant", f"a{i}")
    db.update_chat_usage(c["id"], 999, 1)
    seen = []

    async def fake_complete(settings, messages):
        seen.append(messages)
        return "SUM"

    assert asyncio.run(context.compress(db, c, S(), fake_complete)) is True
    assert seen[0][0] == {"role": "system", "content": context.SUMMARISE_PROMPT}
    assert "q0" in seen[0][1]["content"] and "a0" in seen[0][1]["content"] and "q1" not in seen[0][1]["content"]
    roles = [m["role"] for m in db.get_messages(c["id"], "u1")]
    assert roles == ["user", "assistant", "summary", "user", "assistant", "user", "assistant"]
    assert db.get_chat(c["id"], "u1")["last_prompt_tokens"] is None
    assert asyncio.run(context.compress(db, c, S(), fake_complete)) is False, "only 4 left after the summary"
