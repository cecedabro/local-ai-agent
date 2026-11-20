"""Judge for round 2 over HTTP (chat/SPEC.md §11). Protected: change the code, not this file.

Run: make chat-test K=TestFolders   (one group)
"""
from __future__ import annotations

from test_accept_core import H, client, gw, invite_user, login, new_chat, send, sse  # noqa: F401


class TestFolders:
    def test_crud(self, client):
        login(client)
        assert client.get("/api/folders").json() == []
        r = client.post("/api/folders", json={"name": "  Work  "}, headers=H)
        assert r.status_code == 201 and r.json()["name"] == "Work"
        work = r.json()["id"]
        client.post("/api/folders", json={"name": "archive"}, headers=H)
        assert [f["name"] for f in client.get("/api/folders").json()] == ["archive", "Work"]
        for bad in ({"name": ""}, {"name": "   "}, {"name": "x" * 61}, {}):
            assert client.post("/api/folders", json=bad, headers=H).status_code == 400, bad
        r = client.patch(f"/api/folders/{work}", json={"name": "Jobs"}, headers=H)
        assert r.status_code == 200 and r.json() == {"id": work, "name": "Jobs", "created": r.json()["created"]}
        assert client.patch(f"/api/folders/{work}", json={"name": ""}, headers=H).status_code == 400
        assert client.post("/api/folders", json={"name": "x"}).status_code == 403, "CSRF header needed"
        assert client.delete(f"/api/folders/{work}", headers=H).status_code == 204
        assert client.delete(f"/api/folders/{work}", headers=H).status_code == 404

    def test_move_chats_and_isolation(self, client):
        login(client)
        fid = client.post("/api/folders", json={"name": "Work"}, headers=H).json()["id"]
        cid = new_chat(client)
        assert client.get("/api/chats").json()[0]["folder_id"] is None
        r = client.patch(f"/api/chats/{cid}", json={"folder_id": fid}, headers=H)
        assert r.status_code == 200
        assert {k: r.json()[k] for k in ("id", "folder_id", "title")} == {"id": cid, "folder_id": fid, "title": "New chat"}
        assert set(r.json()) == {"id", "title", "profile", "updated", "folder_id"}
        assert client.get("/api/chats").json()[0]["folder_id"] == fid
        assert client.patch(f"/api/chats/{cid}", json={}, headers=H).status_code == 400
        both = client.patch(f"/api/chats/{cid}", json={"title": "T", "folder_id": None}, headers=H).json()
        assert both["title"] == "T" and both["folder_id"] is None
        other = invite_user(client, "friend", ["chat"])
        theirs = other.post("/api/folders", json={"name": "Mine"}, headers=H).json()["id"]
        assert client.patch(f"/api/chats/{cid}", json={"folder_id": theirs}, headers=H).status_code == 400
        assert other.patch(f"/api/folders/{fid}", json={"name": "x"}, headers=H).status_code == 404
        assert other.delete(f"/api/folders/{fid}", headers=H).status_code == 404
        assert [f["name"] for f in other.get("/api/folders").json()] == ["Mine"]
        client.patch(f"/api/chats/{cid}", json={"folder_id": fid}, headers=H)
        client.delete(f"/api/folders/{fid}", headers=H)
        assert client.get(f"/api/chats/{cid}").status_code == 200, "deleting a folder keeps its chats"
        assert client.get("/api/chats").json()[0]["folder_id"] is None

    def test_needs_session(self, client):
        assert client.get("/api/folders").status_code == 401
        assert client.post("/api/folders", json={"name": "x"}, headers=H).status_code == 401


class TestRound2Answer:
    def test_done_has_title_and_stopped(self, client):
        login(client)
        cid = new_chat(client)
        evs = send(client, cid, "Plan my week please")
        assert evs[0][1]["phase"] == "thinking"
        done = evs[-1][1]
        assert done["title"] == "Plan my week please" and done["stopped"] is False
        msgs = client.get(f"/api/chats/{cid}").json()["messages"]
        assert [m["stopped"] for m in msgs] == [False, False]

    def test_stop_endpoint(self, client):
        login(client)
        cid = new_chat(client)
        assert client.post(f"/api/chats/{cid}/stop", headers=H).status_code == 204, "nothing running: fine"
        evs = send(client, cid, "hi")
        assert evs[-1][1]["stopped"] is False, "an earlier stop leaves no flag behind"
        other = invite_user(client, "friend", ["chat"])
        assert other.post(f"/api/chats/{cid}/stop", headers=H).status_code == 404
        assert client.post("/api/chats/999999/stop", headers=H).status_code == 404

    def test_file_block_format(self, client, gw):
        login(client)
        f = client.post("/api/files", files={"file": ("notes.md", b"line one\n", "text/markdown")}, headers=H).json()
        cid = new_chat(client)
        send(client, cid, "what is in it?", files=[f["id"]])
        last = gw.chat_requests()[-1]["body"]["messages"][-1]["content"]
        assert last == "what is in it?\n\nFile notes.md:\n```\nline one\n\n```"

    def test_prompt_mentions_file_names(self, client, gw):
        login(client)
        cid = new_chat(client)
        send(client, cid, "hi")
        system = gw.chat_requests()[-1]["body"]["messages"][0]["content"]
        assert "```html index.html" in system


class TestRound2Pages:
    def test_chat_page_hooks(self, client):
        login(client)
        page = client.get("/").text
        for hook in ('id="jump"', 'id="cmenu"', 'id="new-folder"', "/static/vendor/highlight.min.js"):
            assert hook in page, hook
        assert client.get("/static/vendor/highlight.min.js").status_code == 200
