"""Small judge for chat/coder/tools.py and the tool parts of gateway.py (chat/SPEC.md §12.3). Protected.

Run one: make chat-test K="test_step_tools and html_text"
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from coder import gateway, tools

PUBLIC = lambda host, port: [(2, 1, 6, "", ("93.184.215.14", 0))]          # noqa: E731
PRIVATE = {"intranet.example": "10.0.0.5", "tailnet.example": "100.101.1.2", "local.example": "127.0.0.1",
           "meta.example": "169.254.169.254"}


def resolve(host, port):
    if host in PRIVATE:
        return [(2, 1, 6, "", (PRIVATE[host], 0))]
    if host == "nowhere.example":
        raise OSError("no such host")
    return PUBLIC(host, port)


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- done (human-written): html_text
def test_html_text():
    title, text = tools.html_text("<html><head><title> Griep  | RIVM </title><style>p{}</style></head><body>"
                                  "<nav>Menu Home</nav><h1>Griep</h1><p>Een prik &amp; meer.</p><script>x()</script>"
                                  "<ul><li>een</li><li>twee</li></ul><footer>(c) 2026</footer></body></html>")
    assert title == "Griep | RIVM"
    assert "Menu" not in text and "x()" not in text and "(c)" not in text and "p{}" not in text
    assert "Griep" in text and "Een prik & meer." in text and "een" in text and "twee" in text
    assert "\n\n\n" not in text and not text.startswith((" ", "\n"))


# ---------------------------------------------------------------- done (human-written): is_public + fetch_page
def test_is_public():
    assert tools.is_public("example.org", resolve) is True
    for host in PRIVATE:
        assert tools.is_public(host, resolve) is False, host
    assert tools.is_public("nowhere.example", resolve) is False


def test_fetch_page():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if req.url.host == "redirect.example":
            return httpx.Response(302, headers={"location": "http://intranet.example/secret"})
        if req.url.host == "hop.example":
            return httpx.Response(301, headers={"location": "https://example.org/page"})
        if req.url.path == "/pdf":
            return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")
        if req.url.path == "/big":
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>" + b"a" * 3_000_000)
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                              content="<title>Page</title><p>Hallo wereld</p>".encode())

    async def go():
        async with client_for(handler) as c:
            page = await tools.fetch_page(c, "https://hop.example/x", resolve)
            assert page == {"title": "Page", "url": "https://example.org/page", "text": "Hallo wereld"}
            for bad in ("http://intranet.example/", "https://redirect.example/", "ftp://example.org/x",
                        "http://example.org:8080/", "https://example.org/pdf", "file:///etc/passwd"):
                with pytest.raises(tools.ToolError):
                    await tools.fetch_page(c, bad, resolve)
            assert not any("intranet" in u for u in seen), "never connect to a private address"
            big = await tools.fetch_page(c, "https://example.org/big", resolve)
            assert len(big["text"]) == tools.PAGE_CHARS
            with pytest.raises(tools.ToolError):
                await tools.fetch_page(c, "https://other.example/", resolve, allowed=lambda h: h.endswith("rivm.nl"))
    run(go())


# ---------------------------------------------------------------- B01 web_search
def test_web_search():
    got = {}

    def handler(req: httpx.Request) -> httpx.Response:
        got["q"] = req.url.params["q"]
        got["format"] = req.url.params["format"]
        return httpx.Response(200, json={"results": [
            {"title": "Griep", "url": "https://www.rivm.nl/griep", "content": "Over griep"},
            {"title": "Ander", "url": "https://example.org/x", "content": "x" * 900},
            {"title": "Raar", "url": "javascript:alert(1)", "content": ""}]})

    async def go():
        async with client_for(handler) as c:
            res = await tools.web_search(c, "http://searx:8080/", "griepprik")
            assert got == {"q": "griepprik", "format": "json"}
            assert [r["url"] for r in res] == ["https://www.rivm.nl/griep", "https://example.org/x"]
            assert res[0] == {"title": "Griep", "url": "https://www.rivm.nl/griep", "snippet": "Over griep"}
            assert len(res[1]["snippet"]) == 300
            res = await tools.web_search(c, "http://searx:8080", "griepprik", site="rivm.nl")
            assert got["q"] == "site:rivm.nl griepprik" and [r["url"] for r in res] == ["https://www.rivm.nl/griep"]
            with pytest.raises(tools.ToolError):
                await tools.web_search(c, "", "x")
    run(go())


# ---------------------------------------------------------------- B02 PubMed
EFETCH = """<?xml version="1.0"?><PubmedArticleSet>
<PubmedArticle><MedlineCitation><PMID>222</PMID><Article><Journal><Title>BMJ</Title><JournalIssue><PubDate><Year>2021</Year></PubDate></JournalIssue></Journal>
<ArticleTitle>Vitamin D and <i>mood</i></ArticleTitle><Abstract><AbstractText Label="AIM">No effect.</AbstractText><AbstractText>Really.</AbstractText></Abstract>
<PublicationTypeList><PublicationType>Meta-Analysis</PublicationType></PublicationTypeList></Article></MedlineCitation></PubmedArticle>
<PubmedArticle><MedlineCitation><PMID>111</PMID><Article><Journal><Title>Lancet</Title><JournalIssue><PubDate><MedlineDate>2019 Jan-Feb</MedlineDate></PubDate></JournalIssue></Journal>
<ArticleTitle>Trial</ArticleTitle><PublicationTypeList><PublicationType>Randomized Controlled Trial</PublicationType></PublicationTypeList></Article></MedlineCitation></PubmedArticle>
</PubmedArticleSet>"""


def test_pubmed_term():
    assert tools.pubmed_term("vitamin D depression", None) == "(vitamin D depression)"
    t = tools.pubmed_term(" vitamin D ", {"types": ["meta_analysis", "rct", "bogus"], "years": 10, "humans": True,
                                         "languages": ["english", "dutch"]})
    assert t == ('(vitamin D) AND (meta-analysis[pt] OR randomized controlled trial[pt]) AND "last 10 years"[dp] '
                 'AND humans[mh] AND (english[la] OR dutch[la])')


def test_pubmed_search():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.url.path, dict(req.url.params)))
        if req.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"idlist": ["111", "222"]}})
        return httpx.Response(200, content=EFETCH.encode())

    async def go():
        async with client_for(handler) as c:
            res = await tools.pubmed_search(c, "vitamin D", {"types": ["rct"]})
            assert calls[0][1]["db"] == "pubmed" and calls[0][1]["term"] == "(vitamin D) AND (randomized controlled trial[pt])"
            assert calls[1][1]["id"] == "111,222"
            assert [a["pmid"] for a in res] == ["111", "222"], "in the order PubMed ranked them"
            a = res[1]
            assert a["title"] == "Vitamin D and mood" and a["journal"] == "BMJ" and a["year"] == "2021"
            assert a["abstract"] == "No effect. Really." and a["types"] == ["Meta-Analysis"]
            assert a["url"] == "https://pubmed.ncbi.nlm.nih.gov/222/" and res[0]["year"] == "2019"
    run(go())


# ---------------------------------------------------------------- B03/B04 tool_specs, describe (B03), run_tool (B04)
def cfg(web=True, pubmed=True, sites=("rivm.nl",)):
    srcs = []
    if pubmed:
        srcs.append({"kind": "pubmed", "enabled": True, "domain": "", "filters": {"types": ["rct"]}})
    for d in sites:
        srcs.append({"kind": "website", "enabled": True, "domain": d, "filters": {}})
    srcs.append({"kind": "website", "enabled": False, "domain": "off.example", "filters": {}})
    return {"profile": "evidence", "instructions": "", "web": web, "sources": srcs}


def names(specs):
    return [s["function"]["name"] for s in specs]


def test_tool_specs_and_describe():
    assert names(tools.tool_specs(cfg())) == ["web_search", "search_site", "search_pubmed", "open_page"]
    assert names(tools.tool_specs(cfg(web=False))) == ["search_site", "search_pubmed", "open_page"]
    assert names(tools.tool_specs(cfg(web=False, sites=()))) == ["search_pubmed"]
    assert tools.tool_specs(cfg(web=False, pubmed=False, sites=())) == []
    site = tools.tool_specs(cfg(sites=("rivm.nl", "thuisarts.nl")))[1]["function"]
    assert site["parameters"]["properties"]["site"]["enum"] == ["rivm.nl", "thuisarts.nl"]
    assert tools.describe("search_pubmed", '{"query": "vitamin D"}') == ("searching", 'Searching PubMed for "vitamin D"…')
    assert tools.describe("open_page", '{"url": "https://www.rivm.nl/griep"}') == ("reading", "Reading www.rivm.nl…")
    assert tools.describe("web_search", "not json")[0] == "searching"


def test_run_tool():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/search":
            return httpx.Response(200, json={"results": [{"title": "T", "url": "https://www.rivm.nl/a", "content": "s"}]})
        if req.url.path.endswith("esearch.fcgi"):
            assert "randomized controlled trial[pt]" in req.url.params["term"], "the card's filters are used"
            return httpx.Response(200, json={"esearchresult": {"idlist": ["222"]}})
        if req.url.path.endswith("efetch.fcgi"):
            return httpx.Response(200, content=EFETCH.encode())
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<title>A</title><p>Body</p>")

    async def go():
        async with client_for(handler) as c:
            text, found = await tools.run_tool("web_search", '{"query": "griep"}', cfg(), c, "http://searx")
            assert json.loads(text)[0]["url"] == "https://www.rivm.nl/a"
            assert found == [{"title": "T", "url": "https://www.rivm.nl/a", "kind": "web", "quote": "s"}]
            text, found = await tools.run_tool("search_pubmed", '{"query": "vitamin D"}', cfg(), c, "http://searx")
            assert json.loads(text)[0]["title"] == "Vitamin D and mood"
            assert found[0]["kind"] == "pubmed" and found[0]["meta"] == "BMJ · 2021" and found[0]["quote"] == "No effect. Really."
            text, found = await tools.run_tool("open_page", '{"url": "https://www.rivm.nl/a"}', cfg(web=False), c, "", resolve)
            assert text.startswith("A\nhttps://www.rivm.nl/a\n\nBody") and found[0]["kind"] == "page" and found[0]["quote"] == "Body"
            # web off: only the profile's websites can be opened
            text, found = await tools.run_tool("open_page", '{"url": "https://example.org/"}', cfg(web=False), c, "", resolve)
            assert text.startswith("Error:") and found == []
            text, _ = await tools.run_tool("web_search", '{"query": "x"}', cfg(web=False), c, "http://searx")
            assert text.startswith("Error:"), "web_search is not available when web is off"
            text, _ = await tools.run_tool("search_site", '{"site": "evil.example", "query": "x"}', cfg(), c, "http://searx")
            assert text.startswith("Error:")
            text, _ = await tools.run_tool("web_search", "{oops", cfg(), c, "http://searx")
            assert text.startswith("Error:")
    run(go())


# ---------------------------------------------------------------- B05 gateway: tool calls
def test_gateway_tool_calls():
    assert "tools" not in gateway.request_body([], False)
    assert gateway.request_body([], False, tools=[{"x": 1}])["tools"] == [{"x": 1}]
    line = 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"web_search","arguments":"{\\"q"}}]}}]}'
    assert gateway.parse_line(line) == [("tool_call", [{"index": 0, "id": "c1", "function": {"name": "web_search", "arguments": '{"q'}}])]
    calls: dict = {}
    gateway.merge_tool_calls(calls, [{"index": 0, "id": "c1", "function": {"name": "web_search", "arguments": '{"q'}}])
    gateway.merge_tool_calls(calls, [{"index": 0, "function": {"arguments": 'uery": "x"}'}}, {"index": 1, "id": "c2",
                                     "function": {"name": "open_page", "arguments": "{}"}}])
    assert calls == {0: {"id": "c1", "name": "web_search", "arguments": '{"query": "x"}'},
                     1: {"id": "c2", "name": "open_page", "arguments": "{}"}}
