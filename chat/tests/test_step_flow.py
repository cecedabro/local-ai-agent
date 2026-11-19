"""Judge for Clear Source v3, the research flow (chat/SPEC.md §15). Protected: change the code, not this file.

Run one: make chat-test K="test_step_flow and parse_quotes"
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from coder import gateway, profiles, research, tools, verify


def run(coro):
    return asyncio.run(coro)


def cfg(sites=("rivm.nl",), filters=None):
    src = [{"kind": "pubmed", "enabled": True, "domain": "", "filters": filters or {}}]
    src += [{"kind": "website", "enabled": True, "domain": d, "filters": {}} for d in sites]
    return {"profile": "evidence", "instructions": "", "web": True, "sources": src}


# ---------------------------------------------------------------- done (human-written): bot checks are never sources
def test_bot_check_pages():
    assert tools.looks_blocked("Checking your browser - reCAPTCHA", "Click here if you are not redirected.")
    assert tools.looks_blocked("Beveiligingscontrole wordt uitgevoerd", "")
    assert tools.looks_blocked("", "Just a moment... Enable JavaScript and cookies to continue")
    assert not tools.looks_blocked("Griepprik", "Wie krijgt de griepprik? " * 100)
    assert not tools.looks_blocked("Captcha use in web forms: a review", "x " * 2000), "a long real page about captchas is fine"


# ---------------------------------------------------------------- F12 the plan as an object
def test_plan_object_and_about():
    text = '```json\n{"about": "  Is apixaban safe\\n in severe CKD? ", "searches": [{"source": "pubmed", "query": "apixaban ckd"}, ' \
           '{"source": "site:rivm.nl", "query": "griep"}]}\n```'
    assert research.parse_plan(text, cfg()) == [{"source": "pubmed", "query": "apixaban ckd"}, {"source": "site:rivm.nl", "query": "griep"}]
    assert research.plan_about(text) == "Is apixaban safe in severe CKD?"
    assert research.parse_plan('Plan: {"about": "x", "searches": "nope"}', cfg(), "Q?") == [{"source": "pubmed", "query": "Q?"}]
    assert research.plan_about('[{"source": "pubmed", "query": "q"}]') == "", "the old list form has no about"
    assert research.plan_about("not json") == "" and research.plan_about('{"about": 5}') == ""
    assert len(research.plan_about(json.dumps({"about": "a " * 400}))) == 300


# ---------------------------------------------------------------- F13 PubMed Central full text
PMC_XML = """<?xml version="1.0"?><pmc-articleset><article><front><article-meta><title-group>
<article-title>Vitamin D and <italic>bone</italic> density</article-title></title-group>
<abstract><p>Vitamin D raised bone density by 2%.</p></abstract></article-meta></front>
<body><sec><title>Results</title><p>Fractures   fell in the
treated group.</p><p>No harm was seen.</p></sec></body></article></pmc-articleset>"""


def test_pmc():
    assert tools.pmc_id("https://pmc.ncbi.nlm.nih.gov/articles/PMC1234567/") == "PMC1234567"
    assert tools.pmc_id("https://www.ncbi.nlm.nih.gov/pmc/articles/pmc99/#sec2") == "PMC99"
    assert tools.pmc_id("https://pubmed.ncbi.nlm.nih.gov/123/") is None and tools.pmc_id("") is None
    calls = []

    def handler(req):
        calls.append(dict(req.url.params))
        if req.url.params.get("id") == "404":
            return httpx.Response(200, content=b"<pmc-articleset></pmc-articleset>")
        return httpx.Response(200, content=PMC_XML.encode())

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            page = await tools.pmc_text(c, "PMC1234567")
            assert calls[0] == {"db": "pmc", "id": "1234567", "retmode": "xml"}
            assert page == {"title": "Vitamin D and bone density", "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC1234567/",
                            "text": "Vitamin D raised bone density by 2%. Fractures fell in the treated group. No harm was seen."}
            with pytest.raises(tools.ToolError):
                await tools.pmc_text(c, "PMC404")
    run(go())


# ---------------------------------------------------------------- F14 gather: broader searches, PMC, opened pages
class FakeTools:
    def __init__(self, hits=2):
        self.calls, self.hits = [], hits

    async def mesh_check(self, client, terms):
        return list(terms)

    async def pubmed_search(self, client, q, filters=None, n=5, mesh=None, info=None):
        self.calls.append(("pubmed", q, filters, n, mesh))
        k = self.hits if filters or mesh else 6
        return [{"pmid": f"{q}{i}", "title": f"{q} {i}", "journal": "BMJ", "year": "2021", "type": None,
                 "abstract": f"Abstract {q} {i} says something useful here.", "url": f"https://pubmed.ncbi.nlm.nih.gov/{q}{i}/"}
                for i in range(k)]

    async def web_search(self, client, searxng_url, q, site=None):
        return [{"title": "Blocked", "url": "https://www.rivm.nl/blocked"},
                {"title": "Study", "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC5/"}]

    def pmc_id(self, url):
        return tools.pmc_id(url)

    async def pmc_text(self, client, pmcid):
        self.calls.append(("pmc", pmcid))
        return {"title": "PMC study", "url": f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/", "text": "Full text of the study."}

    async def fetch_page(self, client, url, resolve=None, allowed=None):
        raise tools.ToolError("blocked by a bot check")


def test_gather_broadens_and_reads_pmc():
    t = FakeTools(hits=1)
    sources, trail = run(research.gather([{"source": "pubmed", "query": "lpa", "mesh": ["Lipoprotein(a)"]}],
                                         cfg(filters={"types": ["rct"]}), None, "", t))
    assert t.calls[0] == ("pubmed", "lpa", {"types": ["rct"]}, research.PUBMED_N, ["Lipoprotein(a)"])
    assert t.calls[1] == ("pubmed", "lpa", None, research.PUBMED_N, []), "too few hits: again without filters and MeSH"
    assert [s.get("broader", False) for s in trail] == [False, True] and trail[1]["found"] == 6
    assert len(sources) == 6, "the one hit of the first search is among the six of the broader one"
    t = FakeTools(hits=5)
    _, trail = run(research.gather([{"source": "pubmed", "query": "lpa"}], cfg(filters={"types": ["rct"]}), None, "", t))
    assert len(trail) == 1, "enough hits: no broader search"
    _, trail = run(research.gather([{"source": "pubmed", "query": "lpa"}], cfg(), None, "", FakeTools(hits=0)))
    assert len(trail) == 1 and trail[0]["found"] == 6, "no filters and no MeSH: nothing to drop"
    t = FakeTools()
    sources, trail = run(research.gather([{"source": "site:rivm.nl", "query": "x"}], cfg(), None, "", t))
    assert trail[1] == {"kind": "open", "title": "Blocked", "url": "https://www.rivm.nl/blocked", "error": "blocked by a bot check"}
    assert trail[2] == {"kind": "open", "title": "PMC study", "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC5/"}
    assert ("pmc", "PMC5") in t.calls and [s["title"] for s in sources] == ["PMC study"]


# ---------------------------------------------------------------- F15 reading one source
SRC = {"n": 3, "title": "CVRM", "url": "https://richtlijnendatabase.nl/cvrm", "meta": "richtlijnendatabase.nl",
       "text": "Bij een eerstegraads familielid met premature hart- en vaatziekten wordt het risico geschat. Lp(a) wordt eenmalig bepaald."}


def test_extract_messages_and_parse_quotes():
    m = research.extract_messages("Wat betekent dit voor mij?", SRC)
    assert m[0] == {"role": "system", "content": research.EXTRACT_PROMPT}
    assert m[1]["content"] == ("Question: Wat betekent dit voor mij?\n\nSource: CVRM (richtlijnendatabase.nl)\n\n" + SRC["text"])
    assert research.parse_quotes('```json\n{"quotes": [" \\"Lp(a) wordt eenmalig bepaald.\\" ", "te kort", 5, "Lp(a) wordt eenmalig bepaald."]}\n```') == \
        ["Lp(a) wordt eenmalig bepaald."], "stripped, quote marks off, 4+ words, strings only, once"
    assert research.parse_quotes('Sure: ["one two three four", "five six seven eight"]') == ["one two three four", "five six seven eight"]
    many = json.dumps({"quotes": [f"passage number {i} is here" for i in range(9)]})
    assert len(research.parse_quotes(many)) == research.MAX_PASSAGES
    assert research.parse_quotes("no json") == [] and research.parse_quotes('{"quotes": "x"}') == []


def test_read_one():
    async def good(settings, messages):
        assert messages[0]["content"] == research.EXTRACT_PROMPT
        return json.dumps({"quotes": ["Lp(a) wordt eenmalig bepaald.", "Lp(a) wordt altijd jaarlijks bepaald."]})

    async def busy(settings, messages):
        raise gateway.Busy()

    async def nothing(settings, messages):
        return '{"quotes": []}'
    quotes, step = run(research.read_one("Q", SRC, None, good))
    assert quotes == ["Lp(a) wordt eenmalig bepaald."], "a passage that is not word for word in the source is dropped"
    assert step == {"kind": "read", "n": 3, "title": "CVRM", "url": SRC["url"], "quotes": 1, "status": "used"}
    assert run(research.read_one("Q", SRC, None, nothing))[1]["status"] == "not relevant"
    q, step = run(research.read_one("Q", SRC, None, busy))
    assert q == [] and step["status"] == "error" and step["quotes"] == 0


# ---------------------------------------------------------------- F16 the follow-up round's prompt, passages in the answer
def test_followup_and_passages():
    trail = [{"kind": "plan", "about": "x"}, {"kind": "search", "source": "PubMed", "query": "lpa", "found": 0, "url": None},
             {"kind": "search", "source": "rivm.nl", "query": "lp(a)", "found": 2, "url": None},
             {"kind": "read", "status": "used"}, {"kind": "read", "status": "not relevant"}]
    m = research.followup_messages("Wat betekent dit?", cfg(), trail)
    assert m[0]["content"].startswith("The searches below found too little") and "pubmed, site:rivm.nl, web" in m[0]["content"]
    assert m[1]["content"] == ("Question: Wat betekent dit?\n\nSearches so far:\n- PubMed: lpa (0 found)\n- rivm.nl: lp(a) (2 found)"
                               "\n\nUseful sources found: 1")
    src = [{**SRC, "n": 1, "passages": ["Lp(a) wordt eenmalig bepaald."]}]
    msgs = research.evidence_messages("Q", src, cfg())
    assert '[1] CVRM (richtlijnendatabase.nl)\n- "Lp(a) wordt eenmalig bepaald."' in msgs[1]["content"]
    assert "premature" not in msgs[1]["content"], "only the passages go to the answer, not the whole source"
    assert research.public_sources(src)[0]["quote"] == "Lp(a) wordt eenmalig bepaald."


# ---------------------------------------------------------------- F17 investigate: rounds, reading one by one
class S:
    SEARXNG_URL = ""


def collect(agen):
    async def go():
        return [x async for x in agen]
    return run(go())


def events(chunks):
    out = []
    for ch in chunks:
        ev, data = ch.strip("\n").split("\n")
        out.append((ev.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


def test_investigate_two_rounds():
    t = FakeTools()
    asked = []

    async def complete(settings, messages):
        system = messages[0]["content"]
        asked.append(system[:12])
        if system.startswith("You plan"):
            return json.dumps({"about": "LPA risk", "searches": [{"source": "pubmed", "query": "lpa", "mesh": ["Lipoprotein(a)"]}]})
        if system.startswith("The searches below"):
            return json.dumps({"searches": [{"source": "pubmed", "query": "lpa"}, {"source": "pubmed", "query": "lipoprotein a family"}]})
        text = messages[1]["content"]
        useful = "lpa 0" in text or "family 0" in text or "family 1" in text
        return json.dumps({"quotes": [text.split("\n\n", 2)[2]] if useful else []})
    out = {}
    evs = events(collect(research.investigate("Wat betekent Lp(a)?", cfg(sites=()), S(), complete, t, None, out)))
    phases = [d["label"] for e, d in evs if e == "phase"]
    assert phases[:2] == ["Understanding the question…", 'Searching PubMed for "lpa"…']
    assert "Reading 1 of 6: lpa 0…" in phases and "Looking further with other words…" in phases
    assert 'Searching PubMed for "lipoprotein a family"…' in phases and phases.count('Searching PubMed for "lpa"…') == 1, \
        "the second round skips a search already done"
    steps = [d["step"] for e, d in evs if e == "trail"]
    assert steps[0] == {"kind": "plan", "about": "LPA risk", "queries": ["lpa"], "mesh": ["Lipoprotein(a)"]}
    assert [s["kind"] for s in steps].count("plan") == 2 and steps[[s["kind"] for s in steps].index("plan", 1)]["round"] == 2
    reads = [s for s in steps if s["kind"] == "read"]
    assert len(reads) == 12 and sum(s["status"] == "used" for s in reads) == 3, "each new source read once, one by one"
    assert [s["n"] for s in out["used"]] == [1, 2, 3] and all(s["passages"] for s in out["used"])
    assert out["trail"] == steps


def test_investigate_enough_in_one_round_and_stop():
    async def complete(settings, messages):
        if messages[0]["content"].startswith("You plan"):
            return '{"about": "", "searches": [{"source": "pubmed", "query": "q"}]}'
        assert not messages[0]["content"].startswith("The searches below"), "enough sources: no second round"
        return json.dumps({"quotes": [messages[1]["content"].split("\n\n", 2)[2]]})
    out = {}
    collect(research.investigate("Q", cfg(sites=()), S(), complete, FakeTools(), None, out))
    assert len(out["used"]) == 6
    stop = asyncio.Event()
    stop.set()
    out = {}
    evs = events(collect(research.investigate("Q", cfg(sites=()), S(), complete, FakeTools(), stop, out)))
    assert out["used"] == [] and not [e for e, d in evs if e == "phase" and d["phase"] == "searching"]


# ---------------------------------------------------------------- F18 the checker: summaries need no source
def test_summary_sentences():
    a = ("## Kort antwoord\nJe risico is hoger dan gemiddeld bij een vader met een vroeg infarct.\n\n"
         "## Onderzoek\nDeze bewering heeft geen bron en is lang genoeg om te tellen.\n\n"
         "## Sterkte van het bewijs\nMatig bewijs uit een richtlijn en twee kleine studies samen.")
    assert verify.summary_sentences(a) == ["Je risico is hoger dan gemiddeld bij een vader met een vroeg infarct.",
                                           "Matig bewijs uit een richtlijn en twee kleine studies samen."]
    assert verify.check(a, [])["uncited"] == ["Deze bewering heeft geen bron en is lang genoeg om te tellen."]
    intro = "Een inleiding zonder kopje die lang genoeg is om te tellen hier.\n\n## Onderzoek\nNog een zin zonder bron die lang genoeg is."
    assert verify.summary_sentences(intro) == ["Een inleiding zonder kopje die lang genoeg is om te tellen hier."], \
        "the text before the first heading is the summary"
    assert verify.summary_sentences("No headings at all in this answer, so nothing here is a summary.") == []
    code = "## Kort antwoord\n```\n# not a heading\n```\nSamenvatting met genoeg woorden om mee te tellen."
    assert verify.summary_sentences(code) == ["Samenvatting met genoeg woorden om mee te tellen."]


# ---------------------------------------------------------------- F19 source groups for the sources page
def test_groups_and_labels():
    assert profiles.group_of({"kind": "pubmed"}) == "research" and profiles.group_of({"kind": "file"}) == "documents"
    assert profiles.group_of({"kind": "website", "domain": "richtlijnendatabase.nl"}) == "guidelines"
    assert profiles.group_of({"kind": "website", "domain": "lareb.nl"}) == "medicines"
    assert profiles.group_of({"kind": "website", "domain": "nvk.nl"}) == "websites"
    assert profiles.label_of({"kind": "website", "domain": "farmacotherapeutischkompas.nl", "title": "farmacotherapeutischkompas.nl"}) == \
        "Farmacotherapeutisch Kompas"
    assert profiles.label_of({"kind": "website", "domain": "nvk.nl", "title": "nvk.nl"}) == "nvk.nl"
    assert profiles.label_of({"kind": "pubmed", "title": "PubMed"}) == "PubMed"
    assert profiles.label_of({"kind": "file", "title": "protocol.pdf"}) == "protocol.pdf"
    assert all(c.get("group") in profiles.GROUPS and c.get("label") for c in profiles.SEED["evidence"][1:])
