"""Judge for answers that keep going when the page goes away (chat/SPEC.md §5.1). Protected.

Run: make chat-test K=live
"""
from __future__ import annotations

import asyncio
import time

from coder.app import Live
from test_accept_core import H, client, gw, invite_user, login, new_chat, sse  # noqa: F401


def test_live_replays_and_follows():
    async def go():
        live = Live("u1")
        live.add("a")
        got = []

        async def reader():
            async for c in live.follow():
                got.append(c)
        t = asyncio.create_task(reader())
        await asyncio.sleep(0)
        live.add("b")
        await asyncio.sleep(0)
        live.add("c")
        live.finish()
        await asyncio.wait_for(t, 1)
        late = [c async for c in live.follow()]
        return got, late
    got, late = asyncio.run(go())
    assert got == ["a", "b", "c"] and late == ["a", "b", "c"]


class TestLive:
    def test_answer_survives_a_dropped_connection(self, client):
        login(client)
        cid = new_chat(client)
        with client.stream("POST", f"/api/chats/{cid}/messages", json={"content": "hello"}, headers=H) as r:
            assert r.status_code == 200
            next(r.iter_text())              # read a little, then the "phone" goes away
        msgs = []
        for _ in range(100):
            msgs = client.get(f"/api/chats/{cid}").json()["messages"]
            if msgs and msgs[-1]["role"] == "assistant":
                break
            time.sleep(0.05)
        assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[-1]["content"]

    def test_live_route(self, client):
        login(client)
        cid = new_chat(client)
        assert client.get(f"/api/chats/{cid}/live").status_code == 204, "nothing running"
        client.post(f"/api/chats/{cid}/messages", json={"content": "hello"}, headers=H)
        r = client.get(f"/api/chats/{cid}/live")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        evs = sse(r.text)
        assert evs[0][0] == "phase" and evs[-1][0] == "done", "replays the whole answer, up to done"
        other = invite_user(client, "friend", ["chat"])
        assert other.get(f"/api/chats/{cid}/live").status_code == 204, "someone else's answer stays private"
        assert client.app.__class__ and TestClientNoSession(client).status_code == 401


def TestClientNoSession(client):
    from fastapi.testclient import TestClient
    return TestClient(client.app).get("/api/chats/1/live")
