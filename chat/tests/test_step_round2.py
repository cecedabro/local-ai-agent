"""Small judge for round 2 (chat/SPEC.md §11), one test per task. Protected.

Run one: make chat-test K="test_step_round2 and test_folders_db"
"""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from coder import answer, context
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
    d.create_user("u2", "x", ["chat"])
    yield d
    d.close()


def new_chat(db, first="hello there", user="u1"):
    c = db.create_chat(user, "general")
    db.add_message(c["id"], "user", first)
    return db.get_chat(c["id"], user)


def parse(chunks):
    out = []
    for ch in chunks:
        ev, data = ch.strip("\n").split("\n")
        out.append((ev.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


async def no_sleep(_):
    return None


def stream_of(events, on_yield=None, closed=None):
    async def stream(settings, messages, think):
        try:
            for i, e in enumerate(events):
                if on_yield:
                    on_yield(i)
                yield e
        finally:
            if closed is not None:
                closed.append(True)
    return stream


# ---------------------------------------------------------------- R01 (db.py)
def test_folders_db(db):
    f = db.create_folder("u1", "Work")
    assert f["name"] == "Work" and f["id"] and f["created"]
    db.create_folder("u1", "archive")
    db.create_folder("u2", "Theirs")
    assert [x["name"] for x in db.list_folders("u1")] == ["archive", "Work"], "sorted by name, case-insensitive"
    assert db.rename_folder(f["id"], "u2", "x") is False
    assert db.rename_folder(f["id"], "u1", "Jobs") is True
    assert db.get_folder(f["id"], "u1")["name"] == "Jobs" and db.get_folder(f["id"], "u2") is None
    c = db.create_chat("u1", "general")
    assert db.list_chats("u1")[0]["folder_id"] is None
    assert db.set_chat_folder(c["id"], "u1", f["id"]) is True
    assert db.list_chats("u1")[0]["folder_id"] == f["id"]
    assert db.get_chat(c["id"], "u1")["folder_id"] == f["id"]
    assert db.set_chat_folder(c["id"], "u2", None) is False, "not your chat"
    assert db.delete_folder(f["id"], "u2") is False
    assert db.delete_folder(f["id"], "u1") is True
    assert db.list_chats("u1")[0]["folder_id"] is None, "chats are kept, without a folder"
    db.delete_user("u2")
    assert db.list_folders("u2") == []


def test_stopped_column_and_migration(tmp_path):
    # an existing production database from before round 2
    con = sqlite3.connect(tmp_path / "coder.db")
    con.executescript("""
      CREATE TABLE chats (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, title TEXT NOT NULL DEFAULT 'New chat',
        profile TEXT NOT NULL DEFAULT 'general', created TEXT NOT NULL, updated TEXT NOT NULL,
        last_prompt_tokens INTEGER DEFAULT NULL, last_completion_tokens INTEGER DEFAULT NULL);
      CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, role TEXT NOT NULL,
        content TEXT NOT NULL, created TEXT NOT NULL, tokens INTEGER DEFAULT NULL, tps REAL DEFAULT NULL);
      INSERT INTO chats (username, title, created, updated) VALUES ('u1', 'Old', '2026-11-01T00:00:00+00:00', '2026-11-01T00:00:00+00:00');
      INSERT INTO messages (chat_id, role, content, created) VALUES (1, 'user', 'hi', '2026-11-01T00:00:00+00:00');
    """)
    con.commit()
    con.close()
    d = DB(str(tmp_path))
    d.init_schema()
    d.init_schema()                                        # twice is fine
    d.create_user("u1", "x", ["chat"])
    assert d.list_chats("u1")[0]["folder_id"] is None
    assert d.get_messages(1, "u1")[0]["stopped"] is False
    mid = d.add_message(1, "assistant", "part", tokens=2, tps=None, stopped=True)
    assert [m["stopped"] for m in d.get_messages(1, "u1") if m["id"] == mid] == [True]
    d.close()


# ---------------------------------------------------------------- R03 (answer.py)
def test_reading_phase_and_first_delta(db):
    chat = new_chat(db)
    evs = parse(asyncio.run(_collect(answer.run_answer(db, S(), chat, False, sleep=no_sleep,
                                                      stream=stream_of([("content", "Hi"), ("content", " you")])))))
    assert evs[0] == ("phase", {"phase": "reading", "label": "Reading your message…"})
    assert [d["text"] for e, d in evs if e == "delta"] == ["Hi", " you"], "the first chunk is sent too"
    assert [d["phase"] for e, d in evs if e == "phase"] == ["reading", "writing"]
    done = evs[-1][1]
    assert done["title"] == "hello there" and done["stopped"] is False
    assert db.get_messages(chat["id"], "u1")[-1]["content"] == "Hi you"
    # many reasoning chunks: still one thinking phase
    chat = new_chat(db)
    evs = parse(asyncio.run(_collect(answer.run_answer(db, S(), chat, True, sleep=no_sleep,
                                                      stream=stream_of([("reasoning", "a"), ("reasoning", "b"), ("content", "x")])))))
    assert [d["phase"] for e, d in evs if e == "phase"] == ["reading", "thinking", "writing"]


async def _collect(agen):
    return [x async for x in agen]


# ---------------------------------------------------------------- R04 (answer.py: stop)
def test_stop_event(db):
    chat = new_chat(db)
    stop = asyncio.Event()
    closed: list = []

    def on_yield(i):
        if i == 1:
            stop.set()                    # the user presses stop while chunk 2 is on its way
    stream = stream_of([("content", "a"), ("content", "b"), ("content", "c"), ("content", "d")], on_yield, closed)
    evs = parse(asyncio.run(_collect(answer.run_answer(db, S(), chat, False, sleep=no_sleep, stream=stream, stop=stop))))
    assert "".join(d["text"] for e, d in evs if e == "delta") == "ab"
    assert evs[-1][0] == "done" and evs[-1][1]["stopped"] is True
    last = db.get_messages(chat["id"], "u1")[-1]
    assert last["content"] == "ab" and last["stopped"] is True and last["id"] == evs[-1][1]["message_id"]
    assert closed == [True], "the gateway stream is closed"
    # stopped before any text: nothing stored, message_id null
    chat = new_chat(db)
    stop = asyncio.Event()
    stop.set()
    evs = parse(asyncio.run(_collect(answer.run_answer(db, S(), chat, False, sleep=no_sleep,
                                                      stream=stream_of([("reasoning", "hm"), ("content", "x")]), stop=stop))))
    assert evs[-1] == ("done", evs[-1][1]) and evs[-1][1]["stopped"] is True and evs[-1][1]["message_id"] is None
    assert [m["role"] for m in db.get_messages(chat["id"], "u1")] == ["user"]


# ---------------------------------------------------------------- R05 (answer.py: client gone)
def test_client_disconnect_stores_partial(db):
    chat = new_chat(db)
    closed: list = []

    async def go():
        agen = answer.run_answer(db, S(), chat, False, sleep=no_sleep,
                                 stream=stream_of([("content", "Hel"), ("content", "lo"), ("content", "!")], None, closed))
        seen = []
        async for ch in agen:
            seen.append(ch)
            if '"text": "lo"' in ch:
                break
        await agen.aclose()               # what Starlette does when the browser goes away
        return seen
    asyncio.run(go())
    last = db.get_messages(chat["id"], "u1")[-1]
    assert last["role"] == "assistant" and last["content"] == "Hello" and last["stopped"] is True
    assert closed == [True]


# ---------------------------------------------------------------- R02 (context.py)
def test_general_prompt_names_files():
    p = context.SYSTEM_PROMPTS["general"]
    assert "file name after the language" in p and "```html index.html" in p
    assert context.SYSTEM_PROMPTS["general"].startswith("You are Dewdrop")
