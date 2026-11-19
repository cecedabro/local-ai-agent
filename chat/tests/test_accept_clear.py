"""Judge for Clear Source round 2 over HTTP: the default sources and MeSH in a real answer (chat/SPEC.md §14).
Protected: change the code, not this file.

Run: make chat-test K=TestClearSource
"""
from __future__ import annotations

import pytest

from coder import profiles, tools
from test_accept_core import H, client, gw, login, new_chat, send  # noqa: F401


@pytest.fixture()
def pubmed(gw, monkeypatch):
    monkeypatch.setattr(tools, "PUBMED", gw.url)
    gw.pubmed = [{"pmid": "101", "title": "Apixaban in chronic kidney disease", "journal": "Kidney Int", "year": "2022",
                  "types": ["Journal Article", "Meta-Analysis"],
                  "abstract": "In patients with eGFR 15-29, apixaban 2.5 mg twice daily caused fewer major bleeds than warfarin."}]
    gw.mesh = {"Renal Insufficiency, Chronic": ["Renal Insufficiency, Chronic", "Chronic Kidney Disease"]}
    return gw


class TestClearSource:
    def test_new_profile_has_the_dutch_guidelines(self, client):
        login(client)
        src = client.get("/api/profiles/evidence").json()["sources"]
        assert [s["kind"] for s in src][0] == "pubmed"
        assert (src[0]["group"], src[0]["label"]) == ("research", "PubMed"), "every source has its group and name (§15.6)"
        assert {s["label"]: s["group"] for s in src}["Farmacotherapeutisch Kompas"] == "medicines"
        assert [s["domain"] for s in src if s["kind"] == "website"] == [c["domain"] for c in profiles.SEED["evidence"][1:]]

    def test_add_defaults(self, client):
        login(client)
        src = client.get("/api/profiles/evidence").json()["sources"]
        gone = [s for s in src if s["domain"] in ("lareb.nl", "ge-bu.nl")]
        for s in gone:
            assert client.delete(f"/api/profiles/evidence/sources/{s['id']}", headers=H).status_code in (200, 204)
        assert client.post("/api/profiles/evidence/defaults").status_code == 403, "the X-Coder header is needed"
        r = client.post("/api/profiles/evidence/defaults", headers=H)
        assert r.status_code == 200 and r.json()["added"] == 2
        assert sorted(s["domain"] for s in r.json()["sources"] if s["domain"] in ("lareb.nl", "ge-bu.nl")) == ["ge-bu.nl", "lareb.nl"]
        again = client.post("/api/profiles/evidence/defaults", headers=H).json()
        assert again["added"] == 0 and len(again["sources"]) == len(r.json()["sources"])
        assert client.post("/api/profiles/nope/defaults", headers=H).status_code == 404

    def test_mesh_in_a_real_answer(self, client, pubmed):
        login(client)
        pubmed.plan = '[{"source": "pubmed", "query": "apixaban", "mesh": ["chronic kidney disease", "Made Up"]}]'
        pubmed.script = {"content": ["## Kort antwoord\n\nMinder bloedingen [1].\n\n",
                                     '> "apixaban 2.5 mg twice daily caused fewer major bleeds than warfarin" [1]']}
        cid = new_chat(client, "evidence")
        evs = send(client, cid, "Is apixaban veilig bij nierfalen?")
        step = [d["step"] for e, d in evs if e == "trail" and d["step"]["kind"] == "search"][0]
        assert step["mesh"] == ["Renal Insufficiency, Chronic"], "checked against MeSH; the made-up term is gone"
        assert "%22Renal+Insufficiency%2C+Chronic%22%5BMeSH+Terms%5D" in step["url"], "the link opens the exact search"
        searched = [r["path"] for r in pubmed.requests if r["path"].startswith("/esearch.fcgi?") and "db=pubmed" in r["path"]]
        assert searched and "Renal+Insufficiency" in searched[0]
        done = evs[-1][1]
        assert done["sources"][0]["type"] == "Meta-analysis" and done["sources"][0]["meta"].startswith("Meta-analysis · ")
        assert done["verify"]["ok"] is True
