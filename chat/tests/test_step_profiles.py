"""Small judge for profiles (db.py, profiles.py) and tool rounds in answer.py (chat/SPEC.md §12). Protected.

Run one: make chat-test K="test_step_profiles and seed"
"""
from __future__ import annotations

import asyncio
import json

import pytest

from coder import answer, context, profiles
from coder.db import DB


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
    d.create_user("u2", "x", ["chat"])
    yield d
    d.close()


def pdf_bytes(text: str) -> bytes:
    """A minimal one-page PDF with `text` in Helvetica (enough for pypdf to extract)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()
    return out


# ---------------------------------------------------------------- B06 db: settings, sources, message sources
def test_profile_db(db):
    assert db.get_profile_settings("lisa", "evidence") == {"instructions": "", "web": True, "seeded": False}
    db.set_profile_settings("lisa", "evidence", instructions="Dutch please", web=False)
    db.set_profile_settings("lisa", "evidence", seeded=True)
    assert db.get_profile_settings("lisa", "evidence") == {"instructions": "Dutch please", "web": False, "seeded": True}
    s = db.add_source("lisa", "evidence", "pubmed", "PubMed", note="RCTs", filters={"types": ["rct"]})
    assert s["kind"] == "pubmed" and s["filters"] == {"types": ["rct"]} and s["enabled"] is True and s["id"]
    w = db.add_source("lisa", "evidence", "website", "rivm.nl", domain="rivm.nl")
    db.add_source("lisa", "general", "file", "notes.md", content="hello")
    assert [x["title"] for x in db.list_sources("lisa", "evidence")] == ["PubMed", "rivm.nl"]
    assert db.get_source(w["id"], "u2") is None
    assert db.update_source(w["id"], "lisa", note="Dutch advice", enabled=False) is True
    w2 = db.get_source(w["id"], "lisa")
    assert w2["note"] == "Dutch advice" and w2["enabled"] is False
    assert db.update_source(w["id"], "u2", note="x") is False
    assert db.delete_source(w["id"], "u2") is False and db.delete_source(w["id"], "lisa") is True
    c = db.create_chat("lisa", "evidence")
    mid = db.add_message(c["id"], "assistant", "a [1]", sources=[{"n": 1, "title": "T", "url": "https://x.org", "kind": "web"}])
    assert db.get_messages(c["id"], "lisa")[-1]["sources"][0]["url"] == "https://x.org"
    db.add_message(c["id"], "user", "q")
    assert db.get_messages(c["id"], "lisa")[-1]["sources"] == [] and mid
    db.delete_user("lisa")
    assert db.list_sources("lisa", "general") == [] and db.get_profile_settings("lisa", "evidence")["seeded"] is False


# ---------------------------------------------------------------- B07 profiles: helpers
def test_helpers():
    assert profiles.tokens("") == 0 and profiles.tokens("abcde") == 2
    assert profiles.domain_of("https://www.RIVM.nl/griep?x=1") == "rivm.nl"
    assert profiles.domain_of("thuisarts.nl") == "thuisarts.nl"
    for bad in ("", "localhost", "javascript:alert(1)", "ftp://x.org", "http://a b.org"):
        assert profiles.domain_of(bad) is None, bad
    assert profiles.clean_filters({"types": ["rct", "bogus", "guideline"], "years": 10, "humans": 1,
                                   "languages": ["dutch", "klingon"], "extra": 1}) == {
        "types": ["rct", "guideline"], "years": 10, "humans": True, "languages": ["dutch"]}
    assert profiles.clean_filters(None) == {"types": [], "years": None, "humans": False, "languages": []}
    assert profiles.clean_filters({"years": 500})["years"] is None


# ---------------------------------------------------------------- B08 profiles: config (seeding), system prompt, budget
def test_seed_prompt_budget(db):
    cfg = profiles.config(db, "lisa", "evidence")
    assert [(s["kind"], s["title"]) for s in cfg["sources"]] == [("pubmed", "PubMed"), ("website", "rivm.nl")]
    assert cfg["sources"][0]["filters"]["types"] == ["systematic_review", "meta_analysis", "rct", "guideline"]
    db.delete_source(cfg["sources"][1]["id"], "lisa")
    assert len(profiles.config(db, "lisa", "evidence")["sources"]) == 1, "seeded only once"
    assert profiles.config(db, "lisa", "general")["sources"] == [], "General starts empty"
    db.set_profile_settings("lisa", "general", instructions="Answer briefly.")
    db.add_source("lisa", "general", "website", "thuisarts.nl", domain="thuisarts.nl", note="Patient advice")
    db.add_source("lisa", "general", "file", "plan.md", note="our plan", content="STEP ONE")
    off = db.add_source("lisa", "general", "file", "old.md", content="OLD TEXT")
    db.update_source(off["id"], "lisa", enabled=False)
    cfg = profiles.config(db, "lisa", "general")
    p = profiles.system_prompt(cfg)
    assert p.startswith(context.SYSTEM_PROMPTS["general"])
    assert "Answer briefly." in p and "thuisarts.nl" in p and "Patient advice" in p and "web_search" in p
    assert "Document plan.md (our plan):\n```\nSTEP ONE\n```" in p and "OLD TEXT" not in p
    assert "[1]" in p, "the model is told how to cite"
    b = profiles.budget(cfg, S())
    assert b["used"] == profiles.tokens(p) and b["max"] == 32768
    assert [x["label"] for x in b["parts"]] == ["Instructions", "plan.md"] and sum(x["tokens"] for x in b["parts"]) == b["used"]
    pub = profiles.public(cfg, S())
    assert pub["name"] == "General" and pub["instructions"] == "Answer briefly." and pub["web"] is True
    assert all("content" not in s for s in pub["sources"])
    assert [s["tokens"] for s in pub["sources"] if s["kind"] == "file"] == [profiles.tokens("STEP ONE"), profiles.tokens("OLD TEXT")]


# ---------------------------------------------------------------- B09 profiles: documents
def test_document_text():
    assert profiles.document_text("a.md", "# Hoi\nwereld".encode()) == "# Hoi\nwereld"
    assert profiles.document_text("a.html", b"<title>x</title><script>y</script><p>Tekst</p>") == "Tekst"
    assert "Hello PDF" in profiles.document_text("guide.pdf", pdf_bytes("Hello PDF"))
    for name, data, status in (("a.bin", b"\xff\xfe\x00", 415), ("a.txt", b"a\x00b", 415), ("x.pdf", b"%PDF-1.4 broken", 415),
                               ("big.txt", b"a" * 5_000_001, 413), ("long.txt", b"a" * 200_001, 413)):
        with pytest.raises(profiles.DocumentError) as e:
            profiles.document_text(name, data)
        assert e.value.status == status, name


# ---------------------------------------------------------------- B10 answer: tool rounds
def test_tool_rounds(db):
    # SPEC §13: EvidenceHunt has its own pipeline now; tool rounds are General's, here with the same sources
    db.add_source("lisa", "general", "pubmed", "PubMed", filters={"types": ["rct"]})
    db.add_source("lisa", "general", "website", "rivm.nl", domain="rivm.nl")
    c = db.create_chat("lisa", "general")
    db.add_message(c["id"], "user", "Helpt vitamine D?")
    chat = db.get_chat(c["id"], "lisa")
    seen = []

    async def stream(settings, messages, think, tools=None):
        seen.append({"tools": [t["function"]["name"] for t in tools or []], "messages": messages})
        if len(seen) == 1:
            yield ("tool_calls", [{"id": "a", "name": "search_pubmed", "arguments": '{"query": "vitamin D depression"}'},
                                  {"id": "", "name": "open_page", "arguments": '{"url": "https://www.rivm.nl/vitamine-d"}'}])
        else:
            yield ("content", "Weinig bewijs [1].")
            yield ("usage", {"prompt_tokens": 900, "completion_tokens": 5})

    async def run_tool(name, arguments, cfg, client, searxng_url):
        assert searxng_url == "http://searx" and cfg["profile"] == "general"
        if name == "search_pubmed":
            return "RESULTS", [{"title": "Trial", "url": "https://pubmed.ncbi.nlm.nih.gov/1/", "kind": "pubmed", "meta": "BMJ · 2021"},
                               {"title": "Review", "url": "https://pubmed.ncbi.nlm.nih.gov/2/", "kind": "pubmed"}]
        return "PAGE", [{"title": "Vitamine D", "url": "https://www.rivm.nl/vitamine-d", "kind": "page"},
                        {"title": "Trial again", "url": "https://pubmed.ncbi.nlm.nih.gov/1/", "kind": "page"}]

    async def collect():
        return [x async for x in answer.run_answer(db, S(), chat, False, stream=stream, run_tool=run_tool,
                                                   sleep=lambda _: asyncio.sleep(0))]
    evs = []
    for ch in asyncio.run(collect()):
        ev, data = ch.strip("\n").split("\n")
        evs.append((ev.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    phases = [(d["phase"], d["label"]) for e, d in evs if e == "phase"]
    assert phases[:3] == [("reading", "Reading your message…"), ("searching", 'Searching PubMed for "vitamin D depression"…'),
                          ("reading", "Reading www.rivm.nl…")]
    assert seen[0]["tools"] == ["web_search", "search_site", "search_pubmed", "open_page"], "the profile's tools are offered"
    second = seen[1]["messages"]
    assert second[-3]["role"] == "assistant" and [t["function"]["name"] for t in second[-3]["tool_calls"]] == ["search_pubmed", "open_page"]
    assert second[-2]["role"] == "tool" and second[-2]["tool_call_id"] == "a" and second[-2]["content"].startswith("RESULTS")
    assert "[1] https://pubmed.ncbi.nlm.nih.gov/1/" in second[-2]["content"] and "[2]" in second[-2]["content"]
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"], "every tool call gets an id"
    assert "[3] https://www.rivm.nl/vitamine-d" in second[-1]["content"] and "[1] https://pubmed" in second[-1]["content"]
    done = evs[-1][1]
    assert evs[-1][0] == "done" and [s["n"] for s in done["sources"]] == [1, 2, 3]
    assert done["sources"][0] == {"n": 1, "title": "Trial", "url": "https://pubmed.ncbi.nlm.nih.gov/1/", "kind": "pubmed", "meta": "BMJ · 2021"}
    last = db.get_messages(chat["id"], "lisa")[-1]
    assert last["content"] == "Weinig bewijs [1]." and [s["n"] for s in last["sources"]] == [1, 2, 3]


def test_tool_rounds_stop_at_max(db):
    c = db.create_chat("lisa", "general")
    db.add_message(c["id"], "user", "loop forever")
    chat = db.get_chat(c["id"], "lisa")
    offered = []

    async def stream(settings, messages, think, tools=None):
        offered.append(bool(tools))
        if tools:
            yield ("tool_calls", [{"id": "x", "name": "web_search", "arguments": '{"query": "again"}'}])
        else:
            yield ("content", "Done.")

    async def run_tool(*a):
        return "nothing", []

    async def collect():
        return [x async for x in answer.run_answer(db, S(), chat, False, stream=stream, run_tool=run_tool)]
    out = asyncio.run(collect())
    assert offered == [True] * answer.MAX_ROUNDS + [False], "after MAX_ROUNDS rounds the model must answer"
    assert '"Done."' in "".join(out)
