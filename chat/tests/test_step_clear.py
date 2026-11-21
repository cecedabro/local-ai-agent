"""Judge for Clear Source round 2: MeSH, study types, the order of sources and the default sources (chat/SPEC.md §14).
Protected: change the code, not this file.

Run one: make chat-test K="test_step_clear and study_type"
"""
from __future__ import annotations

import asyncio
import json

import httpx

from coder import profiles, research, tools


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- F01 tools.study_type
def test_study_type():
    assert tools.study_type(["Journal Article", "Practice Guideline"]) == "Guideline"
    assert tools.study_type(["Review", "Systematic Review", "Meta-Analysis"]) == "Meta-analysis", "the strongest wins"
    assert tools.study_type(["Journal Article", "Systematic Review"]) == "Systematic review"
    assert tools.study_type(["Randomized Controlled Trial", "Multicenter Study"]) == "RCT"
    assert tools.study_type(["Clinical Trial, Phase III"]) == "Clinical trial"
    assert tools.study_type(["Observational Study"]) == "Observational study"
    assert tools.study_type(["Review"]) == "Review" and tools.study_type(["Case Reports"]) == "Case report"
    assert tools.study_type(["Journal Article", "Comment"]) is None and tools.study_type([]) is None
    assert tools.study_type(None) is None


# ---------------------------------------------------------------- F02 pubmed_term + pubmed_search with MeSH
EFETCH = """<?xml version="1.0"?><PubmedArticleSet>
<PubmedArticle><MedlineCitation><PMID>7</PMID><Article><Journal><Title>Kidney Int</Title><JournalIssue><PubDate><Year>2022</Year></PubDate></JournalIssue></Journal>
<ArticleTitle>Apixaban in CKD</ArticleTitle><Abstract><AbstractText>Fewer bleeds.</AbstractText></Abstract>
<PublicationTypeList><PublicationType>Journal Article</PublicationType><PublicationType>Review</PublicationType>
<PublicationType>Research Support, Non-U.S. Gov't</PublicationType><PublicationType>Comment</PublicationType>
<PublicationType>Systematic Review</PublicationType></PublicationTypeList></Article></MedlineCitation></PubmedArticle>
</PubmedArticleSet>"""


def test_pubmed_term_mesh():
    assert tools.pubmed_term("apixaban ckd", None, ["Renal Insufficiency, Chronic", "Factor Xa Inhibitors"]) == \
        '(("Renal Insufficiency, Chronic"[MeSH Terms] AND "Factor Xa Inhibitors"[MeSH Terms]) OR (apixaban ckd))'
    assert tools.pubmed_term("x", {"humans": True}, ["Pregnancy"]) == '(("Pregnancy"[MeSH Terms]) OR (x)) AND humans[mh]'
    assert tools.pubmed_term("x", None, []) == "(x)" and tools.pubmed_term("x", None) == "(x)"


def test_pubmed_search_mesh_info_and_type():
    calls = []

    def handler(req):
        calls.append(dict(req.url.params))
        if req.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"idlist": ["7"], "count": "41",
                                                               "querytranslation": '"renal insufficiency, chronic"[MeSH Terms]'}})
        return httpx.Response(200, content=EFETCH.encode())

    async def go():
        async with client_for(handler) as c:
            info = {}
            res = await tools.pubmed_search(c, "apixaban", None, mesh=["Renal Insufficiency, Chronic"], info=info)
            assert calls[0]["term"] == '(("Renal Insufficiency, Chronic"[MeSH Terms]) OR (apixaban))'
            assert info == {"term": calls[0]["term"], "translation": '"renal insufficiency, chronic"[MeSH Terms]', "count": 41}
            assert res[0]["type"] == "Systematic review", "from all publication types, not only the first 4"
            assert len(res[0]["types"]) == 4
            res = await tools.pubmed_search(c, "apixaban")             # the old call still works
            assert calls[-2]["term"] == "(apixaban)" and res[0]["pmid"] == "7"
    run(go())


# ---------------------------------------------------------------- F03 tools.mesh_check
CKD = ["Renal Insufficiency, Chronic", "Chronic Kidney Disease", "Kidney Disease, Chronic"]
PREG = ["Pregnancy", "Pregnancies", "Gestation"]


def test_mesh_check():
    calls = []

    def handler(req):
        p = dict(req.url.params)
        calls.append((req.url.path.rsplit("/", 1)[-1], p))
        if req.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"idlist": ["68051436", "68011247"]}})
        return httpx.Response(200, json={"result": {"uids": ["68051436", "68011247"],
                                                    "68051436": {"ds_meshterms": CKD}, "68011247": {"ds_meshterms": PREG}}})

    async def go():
        async with client_for(handler) as c:
            got = await tools.mesh_check(c, ["pregnancy", " chronic kidney disease ", "Made Up Thing", "Pregnancy", ""])
            assert got == ["Pregnancy", "Renal Insufficiency, Chronic"], "official headings, in the order asked, once"
            name, p = calls[0]
            assert name == "esearch.fcgi" and p["db"] == "mesh" and p["retmode"] == "json"
            assert p["term"] == '"pregnancy"[MeSH Terms] OR "chronic kidney disease"[MeSH Terms] OR "Made Up Thing"[MeSH Terms]'
            assert calls[1][0] == "esummary.fcgi" and calls[1][1]["db"] == "mesh" and calls[1][1]["id"] == "68051436,68011247"
            n = len(calls)
            assert await tools.mesh_check(c, []) == [] and await tools.mesh_check(c, None) == [] and len(calls) == n, "no terms, no calls"

    def broken(req):
        return httpx.Response(500, text="down")

    async def go_broken():
        async with client_for(broken) as c:
            assert await tools.mesh_check(c, ["Pregnancy"]) == [], "a failure means: search without MeSH"

    def nothing(req):
        return httpx.Response(200, json={"esearchresult": {"idlist": []}})

    async def go_nothing():
        async with client_for(nothing) as c:
            assert await tools.mesh_check(c, ["Made Up Thing"]) == []
    run(go())
    run(go_broken())
    run(go_nothing())


# ---------------------------------------------------------------- F04 parse_plan keeps mesh
def cfg(sites=("rivm.nl",)):
    src = [{"kind": "pubmed", "enabled": True, "domain": "", "filters": {"types": ["rct"]}}]
    src += [{"kind": "website", "enabled": True, "domain": d, "filters": {}} for d in sites]
    return {"profile": "evidence", "instructions": "", "web": False, "sources": src}


def test_parse_plan_mesh():
    text = json.dumps([
        {"source": "pubmed", "query": "apixaban ckd", "mesh": [" Renal Insufficiency, Chronic ", "", "A", "B", "C", "D"]},
        {"source": "pubmed", "query": "warfarin", "mesh": []},
        {"source": "pubmed", "query": "x", "mesh": "not a list"},
        {"source": "site:rivm.nl", "query": "griep", "mesh": ["Influenza, Human"]}])
    assert research.parse_plan(text, cfg()) == [
        {"source": "pubmed", "query": "apixaban ckd", "mesh": ["Renal Insufficiency, Chronic", "A", "B", "C"]},
        {"source": "pubmed", "query": "warfarin"}, {"source": "pubmed", "query": "x"},
        {"source": "site:rivm.nl", "query": "griep"}]
    long = json.dumps([{"source": "pubmed", "query": "q", "mesh": ["x" * 200]}])
    assert research.parse_plan(long, cfg())[0]["mesh"] == ["x" * 80]


# ---------------------------------------------------------------- F05 gather with MeSH, types and sites
class FakeTools:
    def __init__(self):
        self.calls = []

    async def mesh_check(self, client, terms):
        self.calls.append(("mesh", list(terms)))
        return [t for t in terms if t != "Made Up"]

    async def pubmed_search(self, client, q, filters=None, n=5, mesh=None, info=None):
        self.calls.append(("pubmed", q, mesh))
        if info is not None:
            info.update({"term": f"(({mesh}) OR ({q}))", "translation": "TRANSLATED", "count": 9})
        return [{"pmid": "1", "title": "A review", "journal": "BMJ", "year": "2021", "type": "Systematic review",
                 "abstract": "Abs 1 reports fewer bleeds overall with apixaban.", "url": "https://pubmed.ncbi.nlm.nih.gov/1/"},
                {"pmid": "2", "title": "A trial", "journal": "", "year": "2020", "type": None,
                 "abstract": "Abs 2 reports no difference at all for apixaban.", "url": "https://pubmed.ncbi.nlm.nih.gov/2/"}]

    async def web_search(self, client, searxng_url, q, site=None):
        self.calls.append(("web", q, site))
        host = site or "example.org"
        return [{"title": "R", "url": f"https://www.{host}/{q}", "snippet": ""}]

    async def fetch_page(self, client, url, resolve=None, allowed=None):
        return {"title": "Page " + url.rsplit("/", 1)[-1], "url": url, "text": "Text of " + url}

    def pmc_id(self, url):
        return None


def test_gather_mesh_types_and_sites():
    t = FakeTools()
    plan = [{"source": "pubmed", "query": "apixaban", "mesh": ["Renal Insufficiency, Chronic", "Made Up"]},
            {"source": "pubmed", "query": "warfarin"}, {"source": "site:rivm.nl", "query": "griep"}]
    sources, trail = run(research.gather(plan, cfg(), None, "http://searx", t))
    assert ("mesh", ["Renal Insufficiency, Chronic", "Made Up"]) in t.calls and t.calls.count(("mesh", [])) == 0, \
        "MeSH is checked only when the plan gives headings"
    assert ("pubmed", "apixaban", ["Renal Insufficiency, Chronic"]) in t.calls and ("pubmed", "warfarin", []) in t.calls
    assert trail[0] == {"kind": "search", "source": "PubMed", "query": "apixaban", "found": 2,
                        "url": "https://pubmed.ncbi.nlm.nih.gov/?term=%28%28%5B%27Renal+Insufficiency%2C+Chronic%27%5D%29+OR+%28apixaban%29%29",
                        "mesh": ["Renal Insufficiency, Chronic"], "translation": "TRANSLATED"}
    assert sources[0]["type"] == "Systematic review" and sources[0]["meta"] == "Systematic review · BMJ · 2021"
    assert sources[1]["type"] is None and sources[1]["meta"] == "2020" and sources[0]["site"] is None
    page = [s for s in sources if s["kind"] == "page"][0]
    assert page["site"] == "rivm.nl" and page["type"] is None and page["meta"] == "www.rivm.nl"


# ---------------------------------------------------------------- F06 rank and order
def test_order():
    def s(n, kind, type_=None, site=None):
        return {"n": n, "title": str(n), "url": f"u{n}", "kind": kind, "meta": "", "text": "", "type": type_, "site": site}
    src = [s(1, "pubmed", "RCT"), s(2, "page"), s(3, "pubmed", None), s(4, "pubmed", "Meta-analysis"),
           s(5, "page", site="richtlijnendatabase.nl"), s(6, "pubmed", "Guideline"), s(7, "pubmed", "Systematic review"),
           s(8, "pubmed", "Review")]
    out = research.order(src)
    assert [x["url"] for x in out] == ["u5", "u6", "u4", "u7", "u1", "u3", "u8", "u2"], \
        "guideline sites, PubMed guidelines, reviews of trials, RCTs, other PubMed, web pages"
    assert [x["n"] for x in out] == list(range(1, 9)) and src[0]["n"] == 1, "renumbered copies, the input is not changed"
    assert research.rank(s(1, "page", site="lareb.nl")) < research.rank(s(2, "pubmed", "Guideline")) < research.rank(s(3, "page"))


# ---------------------------------------------------------------- F07 public sources and run_evidence
def test_public_sources_type():
    pub = research.public_sources([{"n": 1, "title": "T", "url": "u", "kind": "pubmed", "meta": "RCT · BMJ", "text": "abc",
                                    "type": "RCT", "site": None}])[0]
    assert pub == {"n": 1, "title": "T", "url": "u", "kind": "pubmed", "meta": "RCT · BMJ", "type": "RCT", "site": None, "quote": "abc"}


class S:
    STATUS_URL = ""
    CTX_DEFAULT = 32768
    WAIT_LIMIT = 6
    SEARXNG_URL = "http://searx"


def test_run_evidence_sites_first_and_ordered(tmp_path):
    from coder.db import DB
    db = DB(str(tmp_path))
    db.init_schema()
    db.create_user("lisa", "x", ["chat", "evidence"])
    c = db.create_chat("lisa", "evidence")
    db.add_message(c["id"], "user", "Is apixaban veilig bij nierfalen?")
    chat = db.get_chat(c["id"], "lisa")
    profiles.config(db, "lisa", "evidence")                     # seeds the default cards
    t = FakeTools()
    told = {}

    async def complete(settings, messages):
        if messages[0]["content"].startswith("You read one source"):           # read: quote its first five words
            return json.dumps({"quotes": [" ".join(messages[1]["content"].split("\n\n", 2)[2].split()[:5])]})
        return json.dumps([{"source": "pubmed", "query": "apixaban", "mesh": ["Renal Insufficiency, Chronic"]},
                           {"source": "site:farmacotherapeutischkompas.nl", "query": "apixaban nierfunctie"}])

    async def stream(settings, messages, think, tools=None):
        told["user"] = messages[1]["content"]
        yield ("content", "## Kort antwoord\n\nJa [1].")

    async def go():
        return [x async for x in research.run_evidence(db, S(), chat, False, stream=stream, complete=complete, t=t)]
    out = run(go())
    searched = [c for c in t.calls if c[0] in ("pubmed", "web")]
    assert searched[0][0] == "web", "the guideline sites are searched first, so PubMed cannot crowd them out"
    done = json.loads(out[-1].split("data: ", 1)[1])
    assert [s["kind"] for s in done["sources"]] == ["page", "pubmed", "pubmed"] and done["sources"][0]["n"] == 1
    assert done["sources"][1]["type"] == "Systematic review"
    assert told["user"].startswith("Sources:\n\n[1] Page apixaban nierfunctie"), "the answer is written from the ordered list"
    db.close()


def test_rules_and_plan_prompt():
    assert "## " in research.RULES and "Kort antwoord" in research.RULES and "ONLY from the passages of the numbered sources" in research.RULES
    assert research.PLAN_PROMPT.startswith("You plan literature searches") and '"mesh"' in research.PLAN_PROMPT


# ---------------------------------------------------------------- F08 default sources
def test_seed_and_missing_defaults():
    seed = profiles.SEED["evidence"]
    domains = [c["domain"] for c in seed if c["kind"] == "website"]
    assert seed[0]["kind"] == "pubmed" and "richtlijnendatabase.nl" in domains and "farmacotherapeutischkompas.nl" in domains
    assert all(profiles.domain_of(d) == d for d in domains)
    have = [{"kind": "pubmed", "domain": ""}, {"kind": "website", "domain": "rivm.nl"}, {"kind": "file", "domain": ""}]
    missing = profiles.missing_defaults("evidence", have)
    assert [c.get("domain") for c in missing] == [d for d in domains if d != "rivm.nl"]
    assert profiles.missing_defaults("evidence", [{"kind": "website", "domain": d} for d in domains])[0]["kind"] == "pubmed"
    assert profiles.missing_defaults("general", []) == []
