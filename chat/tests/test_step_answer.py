"""Small judge for chat/coder/answer.py (tasks S09–S11). Protected."""
from __future__ import annotations

import asyncio
import json

import pytest

from coder import answer, context, gateway
from coder.db import DB


class S:
    STATUS_URL = ""
    CTX_DEFAULT = 32768
    WAIT_LIMIT = 6


@pytest.fixture()
def db(tmp_path):
    d = DB(str(tmp_path))
    d.init_schema()
    d.create_user("u1", "x", ["chat"])
    yield d
    d.close()


def new_chat(db, first="hello there"):
    c = db.create_chat("u1", "general")
    db.add_message(c["id"], "user", first)
    return db.get_chat(c["id"], "u1")


def parse(chunks: list[str]) -> list[tuple[str, dict]]:
    out = []
    for ch in chunks:
        assert ch.endswith("\n\n"), ch
        ev, data = ch.strip("\n").split("\n")
        out.append((ev.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


def fake_stream(events, busy_times=0, error=None, error_after=None):
    calls = {"n": 0}

    async def stream(settings, messages, think):
        calls["n"] += 1
        calls["messages"] = messages
        if calls["n"] <= busy_times:
            raise gateway.Busy()
        if error:
            raise gateway.GatewayError(error)
        for i, e in enumerate(events):
            if error_after is not None and i == error_after:
                raise gateway.GatewayError(500)
            yield e
    return stream, calls


async def collect(agen):
    return [x async for x in agen]


async def no_sleep(_):
    return None


def run(db, chat, stream, **kw):
    return parse(asyncio.run(collect(answer.run_answer(db, S(), chat, True, stream=stream, sleep=no_sleep, **kw))))


# ---------------------------------------------------------------- S09
def test_helpers():
    assert answer.sse("delta", {"text": "é"}) == 'event: delta\ndata: {"text": "é"}\n\n'
    assert answer.phase("thinking") == 'event: phase\ndata: {"phase": "thinking", "label": "Thinking…"}\n\n'
    assert answer.make_title("  short   one ") == "short one"
    long = "Say hello to the whole wide world please, in a friendly and very long way"
    assert answer.make_title(long) == "Say hello to the whole wide world please, in a friendly and…"


# ---------------------------------------------------------------- S10
def test_stream_events_and_store(db):
    chat = new_chat(db)
    stream, calls = fake_stream([("reasoning", "hm"), ("content", "Hel"), ("content", "lo"),
                                 ("usage", {"prompt_tokens": 40, "completion_tokens": 3})])
    evs = run(db, chat, stream)
    assert [e for e, _ in evs] == ["phase", "phase", "phase", "delta", "delta", "done"]
    assert [d["phase"] for e, d in evs if e == "phase"] == ["reading", "thinking", "writing"]
    assert "".join(d["text"] for e, d in evs if e == "delta") == "Hello"
    done = evs[-1][1]
    assert done["tokens"] == 3 and set(done) == {"message_id", "tokens", "tps", "context", "title", "stopped"}
    assert done["stopped"] is False and done["title"] == "hello there"
    msgs = db.get_messages(chat["id"], "u1")
    assert msgs[-1]["role"] == "assistant" and msgs[-1]["content"] == "Hello" and msgs[-1]["id"] == done["message_id"]
    assert calls["messages"][0]["role"] == "system" and calls["messages"][-1] == {"role": "user", "content": "hello there"}
    assert db.get_chat(chat["id"], "u1")["last_prompt_tokens"] == 40


def test_count_without_usage(db):
    chat = new_chat(db)
    stream, _ = fake_stream([("content", "a"), ("content", "b")])
    assert run(db, chat, stream)[-1][1]["tokens"] == 2


def test_busy_then_ok(db):
    chat = new_chat(db)
    stream, calls = fake_stream([("content", "ok")], busy_times=2)
    evs = run(db, chat, stream)
    assert [d["phase"] for e, d in evs if e == "phase"] == ["reading", "waiting", "writing"], "one waiting phase"
    assert calls["n"] == 3 and evs[-1][0] == "done"


def test_busy_too_long(db):
    chat = new_chat(db)
    stream, _ = fake_stream([], busy_times=99)
    t = [0.0]

    async def tick(_):
        t[0] += 2

    evs = parse(asyncio.run(collect(answer.run_answer(db, S(), chat, False, stream=stream, sleep=tick, clock=lambda: t[0]))))
    assert evs[-1][0] == "error" and evs[-1][1]["status"] == 503
    assert [m["role"] for m in db.get_messages(chat["id"], "u1")] == ["user"], "no assistant message on error"


def test_gateway_errors(db):
    chat = new_chat(db)
    stream, _ = fake_stream([], error=500)
    evs = run(db, chat, stream)
    assert evs == [("phase", {"phase": "reading", "label": "Reading your message…"}),
                   ("error", {"error": "The model could not answer.", "status": 500})]
    stream, _ = fake_stream([("content", "a"), ("content", "b")], error_after=1)
    evs = run(db, chat, stream)
    assert evs[-1][0] == "error"


# ---------------------------------------------------------------- S11
def test_title_and_context(db):
    chat = new_chat(db, "  Say   hello to the whole wide world please, in a friendly and very long way ")
    stream, _ = fake_stream([("content", "hi"), ("usage", {"prompt_tokens": 100, "completion_tokens": 1})])
    done = run(db, chat, stream)[-1][1]
    assert done["context"] == {"used": 101, "max": 32768, "compress_at": 24576}
    assert db.get_chat(chat["id"], "u1")["title"] == "Say hello to the whole wide world please, in a friendly and…"
    db.update_chat_title(chat["id"], "u1", "Mine")
    run(db, db.get_chat(chat["id"], "u1"), fake_stream([("content", "x")])[0])
    assert db.get_chat(chat["id"], "u1")["title"] == "Mine", "only a 'New chat' title is replaced"


def test_auto_compress(db):
    chat = new_chat(db)
    for i in range(3):
        db.add_message(chat["id"], "assistant", f"a{i}")
        db.add_message(chat["id"], "user", f"q{i}")
    db.update_chat_usage(chat["id"], 30000, 1)
    chat = db.get_chat(chat["id"], "u1")

    async def fake_complete(settings, messages):
        return "SUM"

    stream, calls = fake_stream([("content", "ok")])
    evs = run(db, chat, stream, complete=fake_complete)
    assert evs[0] == ("phase", {"phase": "compressing", "label": "Compressing older messages…"})
    assert calls["messages"][1] == {"role": "system", "content": context.SUMMARY_PREFIX + "SUM"}
