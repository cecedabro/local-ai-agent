"""Judge for Clear Source v2, the trail, thinking, strictness and the app icon over HTTP (chat/SPEC.md §13).
Protected: change the code, not this file.

Run: make chat-test K=TestEvidenceV2   (one group)
"""
from __future__ import annotations

import pytest

from coder import profiles, tools
from test_accept_core import H, client, gw, invite_user, login, new_chat, send  # noqa: F401

ARTICLES = [
    {"pmid": "101", "title": "Apixaban in chronic kidney disease", "journal": "Kidney Int", "year": "2022",
     "abstract": "In patients with eGFR 15-29, apixaban 2.5 mg twice daily caused fewer major bleeds than warfarin."},
    {"pmid": "102", "title": "DOACs in dialysis", "journal": "BMJ", "year": "2021",
     "abstract": "Evidence in patients on dialysis is limited to observational studies."},
]


@pytest.fixture()
def pubmed(gw, monkeypatch):
    monkeypatch.setattr(tools, "PUBMED", gw.url)        # PubMed's E-utilities, faked by the gateway
    gw.pubmed = list(ARTICLES)
    return gw


class TestEvidenceV2:
    def test_research_answer_checked_and_stored(self, client, pubmed):
        login(client)
        pubmed.script = {"reasoning": ["eerst zoeken"], "content": [
            "**Kort:** apixaban lijkt veiliger [1].\n\n",
            '> "apixaban 2.5 mg twice daily caused fewer major bleeds than warfarin" [1]\n\n',
            '> "apixaban 5 mg caused no bleeds at all" [2]\n\n',
            "Het bewijs is beperkt."]}
        cid = new_chat(client, "evidence")
        evs = send(client, cid, "Is apixaban veilig bij een eGFR onder 30?", mode="think")
        names = [e for e, _ in evs]
        assert [d["phase"] for e, d in evs if e == "phase"][:3] == ["reading", "thinking", "searching"]
        assert "trail" in names and "verify" in names and names[-1] == "done"
        plan_call = [r for r in pubmed.chat_requests() if not r["body"].get("stream")][0]
        assert plan_call["body"]["messages"][0]["content"].startswith("You plan literature searches")
        answer_call = [r for r in pubmed.chat_requests() if r["body"].get("stream")][-1]["body"]
        assert "tools" not in answer_call and "[1] Apixaban in chronic kidney disease" in answer_call["messages"][1]["content"]
        assert any(r["path"].startswith("/esearch.fcgi") for r in pubmed.requests)
        done = evs[-1][1]
        assert [q["ok"] for q in done["verify"]["quotes"]] == [True, False], "the made-up quote is caught"
        assert done["trail"][0] == {"kind": "search", "source": "PubMed", "query": "apixaban renal", "found": 2,
                                    "url": "https://pubmed.ncbi.nlm.nih.gov/?term=apixaban+renal"}
        msg = client.get(f"/api/chats/{cid}").json()["messages"][-1]
        assert msg["verify"]["ok"] is False and msg["trail"] == done["trail"] and msg["thinking"] == "eerst zoeken"
        assert [s["n"] for s in msg["sources"]] == [1, 2] and "text" not in msg["sources"][0]

    def test_nothing_found_means_no_answer(self, client, pubmed):
        login(client)
        pubmed.pubmed = []
        cid = new_chat(client, "evidence")
        before = len(pubmed.chat_requests())
        evs = send(client, cid, "Wat is de dosis van een niet-bestaand middel bij kinderen?")
        assert "".join(d["text"] for e, d in evs if e == "delta").startswith("Ik heb in de bronnen")
        assert [r for r in pubmed.chat_requests()[before:] if r["body"].get("stream")] == [], "the model never wrote an answer"

    def test_general_keeps_tools_with_trail(self, tmp_path, gw):
        from test_accept_core import make_client
        gw.search_results = [{"title": "Griepprik", "url": "https://www.rivm.nl/griepprik", "content": "Wie krijgt hem"}]
        gw.script = {"tool_calls": [{"name": "web_search", "arguments": '{"query": "griepprik"}'}], "content": ["Ja [1]."]}
        with make_client(tmp_path, gw, SEARXNG_URL=gw.url) as c:
            login(c)
            cid = new_chat(c)
            evs = send(c, cid, "Griepprik?")
            steps = [d["step"] for e, d in evs if e == "trail"]
            assert steps == [{"kind": "search", "source": "the web", "query": "griepprik", "found": 1, "url": None}]
            assert c.get(f"/api/chats/{cid}").json()["messages"][-1]["trail"] == steps


class TestStrictnessAndIcon:
    def test_presets(self, client):
        login(client)
        pid = [s for s in client.get("/api/profiles/evidence").json()["sources"] if s["kind"] == "pubmed"][0]["id"]
        r = client.patch(f"/api/profiles/evidence/sources/{pid}", json={"filters": {"preset": "strict", "languages": ["dutch"]}}, headers=H)
        assert r.json()["filters"] == {"types": ["systematic_review", "meta_analysis", "guideline"], "years": 10, "humans": True,
                                       "languages": ["dutch"], "preset": "strict"}
        r = client.patch(f"/api/profiles/evidence/sources/{pid}", json={"filters": {"preset": "broad"}}, headers=H)
        assert r.json()["filters"] == {"types": [], "years": None, "humans": False, "languages": [], "preset": "broad"}
        custom = {"preset": "custom", "types": ["rct"], "years": 5, "humans": False}
        r = client.patch(f"/api/profiles/evidence/sources/{pid}", json={"filters": custom}, headers=H)
        assert r.json()["filters"] == {"types": ["rct"], "years": 5, "humans": False, "languages": [], "preset": "custom"}
        assert profiles.clean_filters({"preset": "nonsense", "types": ["rct"]}) == {"types": ["rct"], "years": None, "humans": False, "languages": []}

    def test_icon(self, client):
        assert client.get("/manifest.json").json()["icons"][0]["src"] == "/static/icons/aero-192.png", "no session: aero"
        login(client)
        assert client.get("/api/me").json()["icon"] == "aero"
        client.patch("/api/me", json={"theme": "blossom"}, headers=H)
        assert client.get("/api/me").json()["icon"] == "blossom", "Blossom users get the pink drop by default"
        r = client.patch("/api/me", json={"icon": "aero"}, headers=H)
        assert r.status_code == 200 and r.json()["icon"] == "aero" and r.json()["theme"] == "blossom", "the theme stays"
        assert client.patch("/api/me", json={"icon": "rainbow"}, headers=H).status_code == 400
        assert client.patch("/api/me", json={}, headers=H).status_code == 400
        client.patch("/api/me", json={"icon": "blossom"}, headers=H)
        m = client.get("/manifest.json")
        assert m.headers["content-type"].startswith("application/manifest+json")
        assert [i["src"] for i in m.json()["icons"]] == ["/static/icons/blossom-192.png", "/static/icons/blossom-512.png"]
        png = client.get("/apple-touch-icon.png")
        assert png.status_code == 200 and png.headers["content-type"] == "image/png" and png.content[:4] == b"\x89PNG"
        assert png.content == client.get("/static/icons/blossom-180.png").content
        page = client.get("/").text
        assert '<link rel="manifest" href="/manifest.json">' in page and '<link rel="apple-touch-icon" href="/apple-touch-icon.png">' in page
