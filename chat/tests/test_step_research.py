"""Judge for chat/coder/research.py, the Clear Source pipeline (chat/SPEC.md §13.2). Protected.

Run one: make chat-test K="test_step_research and parse_plan"
"""
from __future__ import annotations

import asyncio
import json

import pytest

from coder import research, tools
from coder.db import DB


def cfg(web=False, sites=("rivm.nl",), pubmed=True):
    src = []
    if pubmed:
        src.append({"kind": "pubmed", "enabled": True, "domain": "", "filters": {"types": ["rct"]}})
    for d in sites:
        src.append({"kind": "website", "enabled": True, "domain": d, "filters": {}})
    src.append({"kind": "website", "enabled": False, "domain": "off.example", "filters": {}})
    return {"profile": "evidence", "instructions": "Antwoord kort.", "web": web, "sources": src}


# ---------------------------------------------------------------- E01 helpers
def test_helpers():
    assert research.looks_dutch("Wat is de dosis van apixaban bij nierfalen?")
    assert not research.looks_dutch("What is the apixaban dose in renal failure?")
    assert research.enabled_sources(cfg()) == ["pubmed", "site:rivm.nl"]
    assert research.enabled_sources(cfg(web=True, pubmed=False)) == ["site:rivm.nl", "web"]
    m = research.plan_messages("Q?", cfg())
    assert m[0]["role"] == "system" and "pubmed, site:rivm.nl" in m[0]["content"] and m[1] == {"role": "user", "content": "Q?"}
    assert research.label({"source": "pubmed", "query": "apixaban renal"}) == 'Searching PubMed for "apixaban renal"…'
    assert research.label({"source": "site:rivm.nl", "query": "griep"}) == 'Searching rivm.nl for "griep"…'
    assert research.label({"source": "web", "query": "x"}) == 'Searching the web for "x"…'


# ---------------------------------------------------------------- E02 parse_plan
def test_parse_plan():
    text = 'Here:\n```json\n[{"source": "pubmed", "query": " apixaban AND renal insufficiency "},' \
           '{"source": "site:rivm.nl", "query": "griepprik"}, {"source": "web", "query": "x"},' \
           '{"source": "site:evil.example", "query": "y"}, {"source": "pubmed", "query": ""}, "junk"]\n```'
    assert research.parse_plan(text, cfg()) == [{"source": "pubmed", "query": "apixaban AND renal insufficiency"},
                                                {"source": "site:rivm.nl", "query": "griepprik"}]
    bare = '[' + ",".join('{"source": "pubmed", "query": "q%d"}' % i for i in range(9)) + ']'
    assert len(research.parse_plan("Sure! " + bare + " Done.", cfg())) == research.MAX_SEARCHES
    assert research.parse_plan("not json at all", cfg(), "Wat is de dosis?") == [{"source": "pubmed", "query": "Wat is de dosis?"}]
    assert research.parse_plan("nonsense", cfg(pubmed=False), "Q") == [], "no PubMed, no fallback"


# ---------------------------------------------------------------- E03 gather
class FakeTools:
    def __init__(self, fail=()):
        self.calls = []
        self.fail = fail

    async def pubmed_search(self, client, q, filters=None, n=5, mesh=None, info=None):
        self.calls.append(("pubmed", q, filters))
        if "pubmed" in self.fail:
            raise tools.ToolError("PubMed answered 500")
        return [{"pmid": str(i), "title": f"Trial {i}", "journal": "BMJ", "year": "2021", "abstract": f"Abstract {i} " + "x" * 5000,
                 "url": f"https://pubmed.ncbi.nlm.nih.gov/{i}/"} for i in (1, 2)]

    async def web_search(self, client, searxng_url, q, site=None):
        self.calls.append(("web", q, site))
        return [{"title": f"R{i}", "url": f"https://www.rivm.nl/p{i}", "snippet": "s"} for i in (1, 2, 3)]

    async def fetch_page(self, client, url, resolve=None, allowed=None):
        self.calls.append(("page", url, allowed("www.rivm.nl") if allowed else None))
        return {"title": "Page " + url[-2:], "url": url, "text": "Page text of " + url}


def test_gather():
    t = FakeTools()
    seen = []
    plan = [{"source": "pubmed", "query": "apixaban"}, {"source": "pubmed", "query": "apixaban again"},
            {"source": "site:rivm.nl", "query": "griep"}]
    sources, trail = asyncio.run(research.gather(plan, cfg(), None, "http://searx", t, on_step=seen.append))
    assert [s["n"] for s in sources] == [1, 2, 3, 4], "numbered in order, duplicates by URL once"
    assert sources[0]["kind"] == "pubmed" and sources[0]["meta"] == "BMJ · 2021" and len(sources[0]["text"]) == research.SOURCE_CHARS
    assert sources[2] == {"n": 3, "title": "Page p1", "url": "https://www.rivm.nl/p1", "kind": "page", "meta": "www.rivm.nl",
                          "text": "Page text of https://www.rivm.nl/p1", "type": None, "site": "rivm.nl"}
    assert ("pubmed", "apixaban", {"types": ["rct"]}) in t.calls, "the card's filters are used"
    assert ("web", "griep", "rivm.nl") in t.calls and ("page", "https://www.rivm.nl/p3", True) not in t.calls, "top 2 pages only"
    assert trail[0] == {"kind": "search", "source": "PubMed", "query": "apixaban", "found": 2,
                        "url": "https://pubmed.ncbi.nlm.nih.gov/?term=apixaban", "mesh": [], "translation": ""}
    assert trail[2] == {"kind": "search", "source": "rivm.nl", "query": "griep", "found": 3, "url": None}
    assert trail[3] == {"kind": "read", "title": "Page p1", "url": "https://www.rivm.nl/p1"}
    assert seen == trail
    bad = FakeTools(fail=("pubmed",))
    sources, trail = asyncio.run(research.gather([{"source": "pubmed", "query": "q"}], cfg(), None, "", bad))
    assert sources == [] and trail == [{"kind": "search", "source": "PubMed", "query": "q", "found": 0, "url": None,
                                        "error": "PubMed answered 500"}], "a failing search is a trail step, not an exception"


def test_gather_caps_sources():
    t = FakeTools()
    plan = [{"source": "site:rivm.nl", "query": f"q{i}"} for i in range(8)]

    async def search(client, url, q, site=None):
        return [{"title": q + str(k), "url": f"https://www.rivm.nl/{q}/{k}", "snippet": ""} for k in range(2)]
    t.web_search = search
    sources, _ = asyncio.run(research.gather(plan, cfg(), None, "", t))
    assert len(sources) == research.MAX_SOURCES


# ---------------------------------------------------------------- E04 prompts
def test_evidence_messages_and_public_sources():
    src = [{"n": 1, "title": "Trial", "url": "https://pubmed.ncbi.nlm.nih.gov/1/", "kind": "pubmed", "meta": "BMJ · 2021",
            "text": "Apixaban was superior. " + "y" * 900}]
    m = research.evidence_messages("Werkt apixaban?", src, cfg())
    assert m[0]["role"] == "system" and "ONLY from the numbered sources" in m[0]["content"] and "Antwoord kort." in m[0]["content"]
    assert m[1]["content"].startswith("Sources:\n\n[1] Trial (BMJ · 2021)\nApixaban was superior.")
    assert m[1]["content"].endswith("Question: Werkt apixaban?")
    pub = research.public_sources(src)[0]
    assert "text" not in pub and pub["quote"].startswith("Apixaban was superior.") and len(pub["quote"]) <= 601


# ---------------------------------------------------------------- E05 run_evidence
class S:
    STATUS_URL = ""
    CTX_DEFAULT = 32768
    WAIT_LIMIT = 6
    SEARXNG_URL = "http://searx"


@pytest.fixture()
def db(tmp_path):
    d = DB(str(tmp_path))
    d.init_schema()
    d.create_user("lisa", "x", ["chat", "evidence"])
    yield d
    d.close()


def evidence_chat(db, question):
    c = db.create_chat("lisa", "evidence")
    db.add_message(c["id"], "user", question)
    return db.get_chat(c["id"], "lisa")


def parse(chunks):
    out = []
    for ch in chunks:
        ev, data = ch.strip("\n").split("\n")
        out.append((ev.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


def run(db, chat, stream, t, plan='[{"source": "pubmed", "query": "apixaban renal"}]', think=False):
    async def complete(settings, messages):
        assert messages[0]["content"].startswith("You plan literature searches")
        return plan

    async def go():
        return [x async for x in research.run_evidence(db, S(), chat, think, stream=stream, complete=complete, t=t)]
    return parse(asyncio.run(go()))


def test_run_evidence_events_and_storage(db):
    chat = evidence_chat(db, "Werkt apixaban bij nierfalen?")
    told = {}

    async def stream(settings, messages, think, tools=None):
        told["messages"], told["tools"] = messages, tools
        yield ("reasoning", "even denken")
        for piece in ["**Kort:** ja [1].\n\n", '> "Abstract 1" [1]\n\n', "Het bewijs is matig."]:
            yield ("content", piece)
        yield ("usage", {"prompt_tokens": 900, "completion_tokens": 30})
    evs = run(db, chat, stream, FakeTools(), think=True)
    phases = [(d["phase"], d["label"]) for e, d in evs if e == "phase"]
    assert phases == [("reading", "Reading your question…"), ("thinking", "Choosing search terms…"),
                      ("searching", 'Searching PubMed for "apixaban renal"…'), ("reading", "Reading 2 sources…"),
                      ("summarising", "Building the answer from 2 sources…"), ("writing", "Writing the answer…")]
    names = [e for e, _ in evs]
    assert names.index("trail") < names.index("delta") and names[-2:] == ["verify", "done"]
    assert [d["text"] for e, d in evs if e == "thinking"] == ["even denken"]
    assert told["tools"] is None and "[1] Trial 1" in told["messages"][1]["content"], "no tools: the model only writes"
    done = evs[-1][1]
    assert done["verify"]["ok"] is True and [s["n"] for s in done["sources"]] == [1, 2] and done["trail"][0]["found"] == 2
    assert done["title"] == "Werkt apixaban bij nierfalen?"
    last = db.get_messages(chat["id"], "lisa")[-1]
    assert last["role"] == "assistant" and last["verify"]["ok"] is True and last["trail"][0]["source"] == "PubMed"
    assert last["thinking"] == "even denken" and "text" not in last["sources"][0]


def test_run_evidence_nothing_found(db):
    chat = evidence_chat(db, "Wat is de dosis van een niet-bestaand middel bij kinderen?")
    t = FakeTools()

    async def nothing(client, q, filters=None, **kw):
        return []
    t.pubmed_search = nothing

    async def stream(*a, **k):
        raise AssertionError("the model is not asked when nothing was found")
        yield
    evs = run(db, chat, stream, t)
    text = "".join(d["text"] for e, d in evs if e == "delta")
    assert text == research.NO_EVIDENCE["nl"]
    assert evs[-1][0] == "done" and evs[-1][1]["sources"] == [] and evs[-1][1]["verify"] is None
    assert db.get_messages(chat["id"], "lisa")[-1]["content"] == research.NO_EVIDENCE["nl"]


def test_run_evidence_flags_a_changed_quote(db):
    chat = evidence_chat(db, "Does apixaban work in renal failure?")

    async def stream(settings, messages, think, tools=None):
        yield ("content", '> "Abstract 3 shows a benefit" [1]\n\nThe evidence is weak.')
    evs = run(db, chat, stream, FakeTools())
    report = [d for e, d in evs if e == "verify"][0]["report"]
    assert report["ok"] is False and report["quotes"][0]["why"] == "not in source 1"


# ---------------------------------------------------------------- E06 tools.trail_step
def test_trail_step():
    found = [{"title": "Griepprik", "url": "https://www.rivm.nl/griepprik", "kind": "page"}]
    assert tools.trail_step("open_page", '{"url": "https://www.rivm.nl/griepprik"}', found) == \
        {"kind": "read", "title": "Griepprik", "url": "https://www.rivm.nl/griepprik"}
    assert tools.trail_step("open_page", '{"url": "https://x.org/a"}', []) == {"kind": "read", "title": "https://x.org/a", "url": "https://x.org/a"}
    assert tools.trail_step("search_pubmed", '{"query": "vitamin D depression"}', [{}, {}]) == \
        {"kind": "search", "source": "PubMed", "query": "vitamin D depression", "found": 2,
         "url": "https://pubmed.ncbi.nlm.nih.gov/?term=vitamin+D+depression"}
    assert tools.trail_step("search_site", '{"site": "rivm.nl", "query": "griep"}', found) == \
        {"kind": "search", "source": "rivm.nl", "query": "griep", "found": 1, "url": None}
    assert tools.trail_step("web_search", "not json", []) == {"kind": "search", "source": "the web", "query": "", "found": 0, "url": None}


# ---------------------------------------------------------------- E05 db: the new message fields and the user icon
def test_db_fields(db):
    c = db.create_chat("lisa", "evidence")
    mid = db.add_message(c["id"], "assistant", "a", trail=[{"kind": "search", "source": "PubMed", "query": "q", "found": 1, "url": None}],
                         verify={"ok": True, "quotes": [], "uncited": [], "bad_refs": []}, thinking="hmm", thought_s=2.5)
    m = db.get_messages(c["id"], "lisa")[-1]
    assert m["id"] == mid and m["trail"][0]["source"] == "PubMed" and m["verify"]["ok"] is True
    assert m["thinking"] == "hmm" and m["thought_s"] == 2.5
    db.add_message(c["id"], "user", "q")
    m = db.get_messages(c["id"], "lisa")[-1]
    assert m["trail"] == [] and m["verify"] is None and m["thinking"] == "" and m["thought_s"] is None
    db.update_user_icon("lisa", "blossom")
    assert db.get_user("lisa")["icon"] == "blossom"
