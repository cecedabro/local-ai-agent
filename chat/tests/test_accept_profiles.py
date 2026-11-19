"""Judge for profiles, sources and tools over HTTP (chat/SPEC.md §12). Protected: change the code, not this file.

Run: make chat-test K=TestProfileApi   (one group)
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from test_accept_core import H, client, gw, invite_user, login, make_client, new_chat, send  # noqa: F401
from test_step_profiles import pdf_bytes


class TestProfileApi:
    def test_get_seeds_evidence(self, client):
        login(client)
        p = client.get("/api/profiles/evidence").json()
        assert p["profile"] == "evidence" and p["name"] == "EvidenceHunt" and p["web"] is True and p["instructions"] == ""
        assert [(s["kind"], s["title"]) for s in p["sources"]] == [("pubmed", "PubMed"), ("website", "rivm.nl")]
        assert set(p["sources"][0]) == {"id", "kind", "title", "domain", "note", "filters", "enabled", "created", "tokens"}
        assert p["budget"]["max"] == 32768 and 0 < p["budget"]["used"] < 2000
        assert client.get("/api/profiles/general").json()["sources"] == []
        assert client.get("/api/profiles/nope").status_code == 404

    def test_rights_and_session(self, client):
        assert client.get("/api/profiles/general").status_code == 401
        login(client)
        friend = invite_user(client, "friend", ["chat"])
        assert friend.get("/api/profiles/general").status_code == 200
        assert friend.get("/api/profiles/evidence").status_code == 403
        assert friend.post("/api/profiles/evidence/sources", json={"kind": "pubmed"}, headers=H).status_code == 403
        assert client.patch("/api/profiles/general", json={"web": False}).status_code == 403, "CSRF header needed"

    def test_settings(self, client):
        login(client)
        r = client.patch("/api/profiles/evidence", json={"instructions": "Antwoord in het Nederlands.", "web": False}, headers=H)
        assert r.status_code == 200 and r.json()["instructions"] == "Antwoord in het Nederlands." and r.json()["web"] is False
        assert len(r.json()["sources"]) == 2, "the seed cards stay"
        for bad in ({}, {"web": "yes"}, {"instructions": "x" * 4001}, {"instructions": 5}):
            assert client.patch("/api/profiles/evidence", json=bad, headers=H).status_code == 400, bad
        assert client.get("/api/profiles/evidence").json()["web"] is False

    def test_sources(self, client):
        login(client)
        r = client.post("/api/profiles/general/sources", json={"kind": "website", "url": "https://www.thuisarts.nl/x",
                                                                "note": "Patient advice"}, headers=H)
        assert r.status_code == 201 and r.json()["title"] == "thuisarts.nl" and r.json()["domain"] == "thuisarts.nl"
        sid = r.json()["id"]
        assert client.post("/api/profiles/general/sources", json={"kind": "website", "url": "localhost"}, headers=H).status_code == 400
        assert client.post("/api/profiles/general/sources", json={"kind": "file"}, headers=H).status_code == 400
        assert client.post("/api/profiles/general/sources", json={"kind": "website", "url": "a.nl", "note": "x" * 1001},
                           headers=H).status_code == 400
        r = client.post("/api/profiles/general/sources", json={"kind": "pubmed", "filters": {"types": ["rct", "zz"], "years": 5}}, headers=H)
        assert r.status_code == 201 and r.json()["filters"] == {"types": ["rct"], "years": 5, "humans": False, "languages": []}
        assert client.post("/api/profiles/general/sources", json={"kind": "pubmed"}, headers=H).status_code == 409
        pid = r.json()["id"]
        r = client.patch(f"/api/profiles/general/sources/{pid}", json={"filters": {"humans": True}, "note": "Only RCTs"}, headers=H)
        assert r.status_code == 200 and r.json()["filters"]["humans"] is True and r.json()["note"] == "Only RCTs"
        r = client.patch(f"/api/profiles/general/sources/{sid}", json={"enabled": False}, headers=H)
        assert r.json()["enabled"] is False
        assert client.patch(f"/api/profiles/general/sources/{sid}", json={}, headers=H).status_code == 400
        assert client.patch(f"/api/profiles/evidence/sources/{sid}", json={"enabled": True}, headers=H).status_code == 404, "wrong profile"
        friend = invite_user(client, "friend", ["chat"])
        assert friend.patch(f"/api/profiles/general/sources/{sid}", json={"enabled": True}, headers=H).status_code == 404
        assert friend.delete(f"/api/profiles/general/sources/{sid}", headers=H).status_code == 404
        assert client.delete(f"/api/profiles/general/sources/{sid}", headers=H).status_code == 204
        assert [s["kind"] for s in client.get("/api/profiles/general").json()["sources"]] == ["pubmed"]

    def test_documents_and_budget(self, client):
        login(client)
        before = client.get("/api/profiles/general").json()["budget"]["used"]
        r = client.post("/api/profiles/general/documents", files={"file": ("../richtlijn.pdf", pdf_bytes("Dosering 400 IE"), "application/pdf")}, headers=H)
        assert r.status_code == 201, r.text
        assert r.json()["kind"] == "file" and r.json()["title"] == "richtlijn.pdf" and r.json()["tokens"] > 0
        r = client.post("/api/profiles/general/documents", files={"file": ("notes.md", ("word " * 4000).encode(), "text/markdown")}, headers=H)
        assert r.status_code == 201 and r.json()["tokens"] == 5000
        p = client.get("/api/profiles/general").json()
        assert p["budget"]["used"] >= before + 5000
        assert [x["label"] for x in p["budget"]["parts"]] == ["Instructions", "richtlijn.pdf", "notes.md"]
        assert client.post("/api/profiles/general/documents", files={"file": ("x.bin", b"\xff\x00", "application/octet-stream")},
                           headers=H).status_code == 415
        assert client.post("/api/profiles/general/documents", files={"file": ("big.txt", b"a" * 200_001, "text/plain")},
                           headers=H).status_code == 413

    def test_profiles_page(self, client):
        assert client.get("/profiles", follow_redirects=False).status_code == 303
        login(client)
        page = client.get("/profiles").text
        assert 'data-profile="general"' in page and 'data-profile="evidence"' in page and "/static/js/profiles.js" in page
        friend = invite_user(client, "friend", ["chat"])
        assert 'data-profile="evidence"' not in friend.get("/profiles").text


class TestProfileAnswers:
    def test_prompt_and_tools_sent(self, client, gw):
        login(client)
        client.patch("/api/profiles/general", json={"instructions": "Be brief."}, headers=H)
        client.post("/api/profiles/general/documents", files={"file": ("plan.md", b"SECRET PLAN", "text/markdown")}, headers=H)
        cid = new_chat(client)
        send(client, cid, "hi")
        body = gw.chat_requests()[-1]["body"]
        system = body["messages"][0]["content"]
        assert "Be brief." in system and "SECRET PLAN" in system
        assert [t["function"]["name"] for t in body["tools"]] == ["web_search", "open_page"]
        client.patch("/api/profiles/general", json={"web": False}, headers=H)
        send(client, cid, "again")
        assert "tools" not in gw.chat_requests()[-1]["body"], "no tools when nothing is enabled"

    def test_search_round(self, tmp_path, gw):
        gw.search_results = [{"title": "Griepprik", "url": "https://www.rivm.nl/griepprik", "content": "Wie krijgt hem"}]
        gw.script = {"tool_calls": [{"name": "web_search", "arguments": '{"query": "griepprik 60+"}'}],
                     "content": ["Ja", " [1]."]}
        with make_client(tmp_path, gw, SEARXNG_URL=gw.url) as c:
            login(c)
            cid = new_chat(c)
            evs = send(c, cid, "Griepprik voor 60-plussers?")
            labels = [d["label"] for e, d in evs if e == "phase"]
            assert 'Searching the web for "griepprik 60+"…' in labels
            done = evs[-1][1]
            assert done["sources"] == [{"n": 1, "title": "Griepprik", "url": "https://www.rivm.nl/griepprik", "kind": "web",
                                        "quote": "Wie krijgt hem"}]
            assert any(r["path"].startswith("/search?") for r in gw.requests), "SearXNG was asked"
            second = gw.chat_requests()[-1]["body"]["messages"]
            assert second[-1]["role"] == "tool" and "griepprik" in second[-1]["content"]
            msg = c.get(f"/api/chats/{cid}").json()["messages"][-1]
            assert msg["content"] == "Ja [1]." and msg["sources"][0]["n"] == 1
