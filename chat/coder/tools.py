"""The model's tools: web search (SearXNG), reading a web page, PubMed (chat/SPEC.md §12.3).

Every network call goes through an httpx.AsyncClient the caller passes in (tests use a MockTransport)
and every page fetch is checked against private addresses first (`resolve` is injectable for tests).
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import quote_plus, urljoin, urlsplit

import httpx

PUBMED = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
MAX_BYTES = 2_000_000          # a page larger than this is cut
PAGE_CHARS = 8000              # text of one page handed to the model
# Bot checks and consent walls instead of the page (SPEC §15.3): such a "page" is never a source.
BOT_CHECK = re.compile(r"checking your browser|just a moment\.\.\.|verify (that )?you are (a )?human|are you a robot|"
                       r"beveiligingscontrole|recaptcha|hcaptcha|attention required|access denied|"
                       r"enable javascript and cookies|ddos protection", re.I)


def looks_blocked(title: str, text: str) -> bool:
    """True when a fetched page is a bot check rather than the page: the title matches BOT_CHECK, or the text is short
    (under 1500 characters) and matches it."""
    return bool(BOT_CHECK.search(title or "")) or (len(text or "") < 1500 and bool(BOT_CHECK.search(text or "")))
ABSTRACT_CHARS = 1200
UA = "Dewdrop/1.0 (private research assistant)"
QUOTE_CHARS = 600              # the quote a source card shows (SPEC §12.4)

# PubMed filters (SPEC §12.1): the source card's checkboxes -> search terms
PUB_TYPES = {
    "systematic_review": "systematic[sb]",
    "meta_analysis": "meta-analysis[pt]",
    "rct": "randomized controlled trial[pt]",
    "guideline": "(guideline[pt] OR practice guideline[pt])",
    "review": "review[pt]",
}
LANGS = {"english": "english[la]", "dutch": "dutch[la]"}


class ToolError(Exception):
    """A tool could not do its job; the message goes back to the model as the tool result."""


# ------------------------------------------------------------------ pages
_SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside", "template", "iframe"}
_BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "table", "ul", "ol",
          "blockquote", "pre", "dd", "dt"}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.title = ""
        self.skip = 0
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIP:
            self.skip += 1
        elif tag == "title":
            self.in_title = True
        elif tag in _BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP and self.skip:
            self.skip -= 1
        elif tag == "title":
            self.in_title = False
        elif tag in _BLOCK:
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)


def html_text(html: str) -> tuple[str, str]:
    """(title, readable text) of an HTML page: no scripts, styles, menus or footers; whitespace tidied."""
    p = _Text()
    p.feed(html)
    p.close()
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.out))
    text = re.sub(r"\n\s*\n+", "\n\n", re.sub(r" *\n *", "\n", text)).strip()
    return re.sub(r"\s+", " ", p.title).strip(), text


def is_public(host: str, resolve: Callable = socket.getaddrinfo) -> bool:
    """True when every address `host` resolves to is a public one (no private, loopback, link-local,
    CGNAT/Tailscale or reserved ranges). Unresolvable -> False."""
    try:
        infos = resolve(host, None)
    except (OSError, UnicodeError):
        return False
    addrs = {info[4][0] for info in infos}
    if not addrs:
        return False
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if not ip.is_global or ip in ipaddress.ip_network("100.64.0.0/10"):
            return False
    return True


async def fetch_page(client: httpx.AsyncClient, url: str, resolve: Callable = socket.getaddrinfo,
                     allowed: Callable[[str], bool] | None = None) -> dict:
    """Fetch a public web page and return {"title", "url", "text"} (text cut to PAGE_CHARS).
    Only http(s) on the default ports, at most 3 redirects, each hop checked with is_public (and
    `allowed(host)` when given). Raises ToolError with a short reason."""
    for _ in range(4):
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.scheme not in ("http", "https") or not host or parts.port not in (None, 80, 443):
            raise ToolError("only public http(s) pages can be opened")
        if allowed is not None and not allowed(host):
            raise ToolError(f"{host} is not one of this profile's sources")
        if not is_public(host, resolve):
            raise ToolError(f"{host} is not a public address")
        try:
            async with client.stream("GET", url, headers={"User-Agent": UA, "Accept": "text/html,text/plain;q=0.9"},
                                     follow_redirects=False, timeout=15) as resp:
                if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                    url = urljoin(url, resp.headers["location"])
                    continue
                if resp.status_code != 200:
                    raise ToolError(f"the page answered {resp.status_code}")
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype not in ("text/html", "text/plain", "application/xhtml+xml", "text/markdown", ""):
                    raise ToolError(f"cannot read {ctype} pages")
                raw = b""
                async for chunk in resp.aiter_bytes():
                    raw += chunk
                    if len(raw) >= MAX_BYTES:
                        break
                body = raw[:MAX_BYTES].decode(resp.encoding or "utf-8", errors="replace")
        except httpx.HTTPError as e:
            raise ToolError(f"could not load the page ({type(e).__name__})") from e
        if ctype in ("text/plain", "text/markdown"):
            title, text = "", body.strip()
        else:
            title, text = html_text(body)
        if looks_blocked(title, text):
            raise ToolError("blocked by a bot check")
        return {"title": title or host, "url": url, "text": text[:PAGE_CHARS]}
    raise ToolError("too many redirects")


# ------------------------------------------------------------------ search
async def web_search(client: httpx.AsyncClient, searxng_url: str, query: str, site: str | None = None,
                     n: int = 5) -> list[dict]:
    """Top results from SearXNG: [{"title", "url", "snippet"}]. `site` limits to one domain.

    TODO (task B01). Tests: test_step_tools.py::test_web_search
      1. searxng_url empty -> raise ToolError("web search is not set up (SEARXNG_URL is empty)")
      2. q = f"site:{site} {query}" when site else query
      3. r = await client.get(searxng_url.rstrip("/") + "/search", params={"q": q, "format": "json"},
             headers={"User-Agent": UA}, timeout=20); an httpx.HTTPError -> ToolError("search failed (<type name>)");
         r.status_code != 200 -> ToolError(f"search answered {r.status_code}")
      4. for item in r.json().get("results", []): skip urls not starting with http:// or https://; with `site`,
         skip urls whose host is not within it (_within(host, site)); keep {"title": (title or url)[:200], "url": url,
         "snippet": (item "content" or "")[:300]}; stop at n results.
    """
    if not searxng_url:
        raise ToolError("web search is not set up (SEARXNG_URL is empty)")
    q = f"site:{site} {query}" if site else query
    try:
        r = await client.get(searxng_url.rstrip("/") + "/search",
                             params={"q": q, "format": "json"},
                             headers={"User-Agent": UA}, timeout=20)
    except httpx.HTTPError as e:
        raise ToolError(f"search failed ({type(e).__name__})") from e
    if r.status_code != 200:
        raise ToolError(f"search answered {r.status_code}")
    results = []
    for item in r.json().get("results", []):
        url = item.get("url", "")
        if not url.startswith(("http://", "https://")):
            continue
        if site and not _within(urlsplit(url).hostname or "", site):
            continue
        results.append({"title": (item.get("title", "") or url)[:200],
                        "url": url,
                        "snippet": (item.get("content", "") or "")[:300]})
        if len(results) >= n:
            break
    return results


def _within(host: str, domain: str) -> bool:
    host, domain = host.lower().rstrip("."), domain.lower().lstrip(".").rstrip(".")
    return host == domain or host.endswith("." + domain)


# PubMed publication types -> the label shown on a source (SPEC §14.2), strongest first.
STUDY_TYPES = (
    ("Guideline", ("Practice Guideline", "Guideline")),
    ("Meta-analysis", ("Meta-Analysis",)),
    ("Systematic review", ("Systematic Review",)),
    ("RCT", ("Randomized Controlled Trial",)),
    ("Clinical trial", ("Clinical Trial",)),          # also "Clinical Trial, Phase III" etc.: a prefix match
    ("Observational study", ("Observational Study",)),
    ("Review", ("Review",)),
    ("Case report", ("Case Reports",)),
)


def study_type(pub_types: list[str] | None) -> str | None:
    r"""The study type label of a PubMed article from its publication types (SPEC §14.2): the first label of
    STUDY_TYPES with a matching type ("Clinical trial" also matches any type starting with "Clinical Trial"); none -> None.
    """
    types = [str(p).strip() for p in pub_types or []]
    for label, names in STUDY_TYPES:
        for name in names:
            if any(p == name or (label == "Clinical trial" and p.startswith(name)) for p in types):
                return label
    return None


async def mesh_check(client: httpx.AsyncClient, terms: list[str] | None) -> list[str]:
    r"""TODO (task F03). The MeSH headings that exist for the model's terms, in the order asked, each once (SPEC §14.1).
      want = per term: str(term).strip(); skip empty ones, ones longer than 80 characters, ones with a '"', and ones
        already in want (case-insensitive); keep at most 8. want empty -> return [] without any request.
      r = GET PUBMED + "/esearch.fcgi" params {"db": "mesh", "term": " OR ".join(f'"{t}"[MeSH Terms]' for t in want),
          "retmode": "json", "retmax": 20} (headers User-Agent UA, timeout 20); status != 200 -> []
      ids = r.json()["esearchresult"]["idlist"]; none -> []
      r = GET PUBMED + "/esummary.fcgi" params {"db": "mesh", "id": ",".join(ids), "retmode": "json"} (same headers, timeout);
          status != 200 -> []; res = r.json()["result"]; records = [res[uid]["ds_meshterms"] (a list) for uid in res["uids"]]
      httpx.HTTPError, ValueError, KeyError, TypeError or AttributeError anywhere -> return [] (never raise)
      out: for each t in want: the first record that has t among its terms (case-insensitive) -> its heading record[0],
        added once (skip it when it is already in out); a term without a record is dropped.
    Tests: test_step_clear.py::test_mesh_check
    """
    raise NotImplementedError


def pubmed_term(query: str, filters: dict | None, mesh: list[str] | None = None) -> str:
    """The PubMed search term for `query` with a source card's filters (SPEC §12.1).

    TODO (task B02). parts = [f"({query.strip()})"], then, only when present (filters may be None):
      types     -> "(" + " OR ".join(PUB_TYPES[t] for each known t, in the card's order) + ")"
      years N>0 -> f'"last {N} years"[dp]'
      humans    -> "humans[mh]"
      languages -> "(" + " OR ".join(LANGS[x] for each known x) + ")"
    Return " AND ".join(parts). Unknown types/languages are skipped.
    """
    if mesh:
        parts = ['((' + ' AND '.join(f'"{m}"[MeSH Terms]' for m in mesh) + ') OR (' + query.strip() + '))']
    else:
        parts = [f"({query.strip()})"]
    if filters:
        types = filters.get("types")
        if types:
            known = [PUB_TYPES[t] for t in types if t in PUB_TYPES]
            if known:
                parts.append("(" + " OR ".join(known) + ")")
        years = filters.get("years")
        if years and years > 0:
            parts.append(f'"last {years} years"[dp]')
        if filters.get("humans"):
            parts.append("humans[mh]")
        languages = filters.get("languages")
        if languages:
            known_langs = [LANGS[x] for x in languages if x in LANGS]
            if known_langs:
                parts.append("(" + " OR ".join(known_langs) + ")")
    return " AND ".join(parts)


async def pubmed_search(client: httpx.AsyncClient, query: str, filters: dict | None = None, n: int = 5,
                        mesh: list[str] | None = None, info: dict | None = None) -> list[dict]:
    """PubMed via E-utilities: [{"pmid", "title", "journal", "year", "types", "abstract", "url"}], most relevant first.

    TODO (task B02). Tests: test_step_tools.py::test_pubmed_search
      1. r = GET PUBMED + "/esearch.fcgi" params {"db": "pubmed", "term": pubmed_term(query, filters), "retmode": "json",
         "retmax": n, "sort": "relevance"} (headers User-Agent UA, timeout 20); ids = r.json()["esearchresult"]["idlist"];
         status != 200 -> ToolError(f"PubMed answered {status}"); no ids -> return []
      2. r = GET PUBMED + "/efetch.fcgi" params {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"} (timeout 30);
         status != 200 -> ToolError; root = ET.fromstring(r.content)
         httpx.HTTPError, ValueError, KeyError or ET.ParseError anywhere -> ToolError(f"PubMed failed (<type name>)")
      3. per root.iter("PubmedArticle"): pmid = findtext(".//PMID"); title = all text of .//ArticleTitle (itertext, so
         <i> tags vanish), stripped; abstract = " ".join(all text of each AbstractText), stripped, cut to ABSTRACT_CHARS;
         year = .//PubDate/Year or the first 4 characters of .//PubDate/MedlineDate; journal = .//Journal/Title;
         types = the PublicationType texts (max 4); url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
      4. sort the results in the order of `ids` (PubMed's ranking), return them.
    """
    # TODO (task F02), SPEC §14.1-14.2. Tests: test_step_clear.py::test_pubmed_search_mesh_info_and_type
    #   1. term = pubmed_term(query, filters, mesh) and send that term.
    #   2. after reading esearchresult (call it esr): when `info` is a dict, set info["term"] = term,
    #      info["translation"] = str(esr.get("querytranslation") or ""), info["count"] = int(esr.get("count") or len(ids))
    #      (a count that is not a number -> len(ids)); do this before the "no ids -> []" return.
    #   3. per article: all_types = every PublicationType text; "types" stays all_types[:4]; add "type": study_type(all_types).
    term = pubmed_term(query, filters, mesh)
    try:
        r = await client.get(
            PUBMED + "/esearch.fcgi",
            params={"db": "pubmed", "term": term, "retmode": "json",
                    "retmax": n, "sort": "relevance"},
            headers={"User-Agent": UA}, timeout=20)
    except httpx.HTTPError as e:
        raise ToolError(f"PubMed failed ({type(e).__name__})") from e
    if r.status_code != 200:
        raise ToolError(f"PubMed answered {r.status_code}")
    try:
        esr = r.json()["esearchresult"]
        ids = esr["idlist"]
    except (ValueError, KeyError) as e:
        raise ToolError(f"PubMed failed ({type(e).__name__})") from e
    if info is not None:
        try:
            count = int(esr.get("count") or len(ids))
        except (ValueError, TypeError):
            count = len(ids)
        info["term"] = term
        info["translation"] = str(esr.get("querytranslation") or "")
        info["count"] = count
    if not ids:
        return []
    try:
        r2 = await client.get(
            PUBMED + "/efetch.fcgi",
            params={"db": "pubmed", "id": ",".join(ids), "retmode": "xml"},
            timeout=30)
    except httpx.HTTPError as e:
        raise ToolError(f"PubMed failed ({type(e).__name__})") from e
    if r2.status_code != 200:
        raise ToolError(f"PubMed answered {r2.status_code}")
    try:
        root = ET.fromstring(r2.content)
    except ET.ParseError as e:
        raise ToolError(f"PubMed failed ({type(e).__name__})") from e
    results: list[tuple[str, dict]] = []
    for article in root.iter("PubmedArticle"):
        try:
            pmid_el = article.findtext(".//PMID")
            if pmid_el is None:
                raise KeyError("PMID")
            article_title = article.find(".//ArticleTitle")
            raw = " ".join(title for title in article_title.itertext()) if article_title is not None else ""
            title = re.sub(r"\s+", " ", raw).strip()
            abstract_el = article.find(".//Abstract")
            abstract = ""
            if abstract_el is not None:
                abstract = " ".join(
                    t for t in (elem.text for elem in abstract_el.iter("AbstractText"))
                    if t
                )
            abstract = re.sub(r"\s+", " ", abstract).strip()
            abstract = abstract[:ABSTRACT_CHARS]
            pubdate = article.find(".//PubDate")
            year_el = pubdate.find("Year") if pubdate is not None else None
            medline_el = pubdate.find("MedlineDate") if pubdate is not None else None
            if year_el is not None and year_el.text:
                year = year_el.text
            elif medline_el is not None and medline_el.text:
                year = medline_el.text[:4]
            else:
                year = ""
            journal_el = article.find(".//Journal/Title")
            journal = journal_el.text if journal_el is not None and journal_el.text else ""
            all_types = []
            pt_el = article.find(".//PublicationTypeList")
            if pt_el is not None:
                for pt in pt_el.iter("PublicationType"):
                    if pt.text:
                        all_types.append(pt.text)
            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid_el}/"
            results.append((pmid_el, {
                "pmid": pmid_el,
                "title": title,
                "journal": journal,
                "year": year,
                "types": all_types[:4],
                "type": study_type(all_types),
                "abstract": abstract,
                "url": url,
            }))
        except (KeyError, ValueError, AttributeError) as e:
            raise ToolError(f"PubMed failed ({type(e).__name__})") from e
    order = {pid: i for i, pid in enumerate(ids)}
    results.sort(key=lambda x: order.get(x[0], 999999))
    return [r[1] for r in results]


_PMC = re.compile(r"^https?://(?:pmc\.ncbi\.nlm\.nih\.gov|(?:www\.)?ncbi\.nlm\.nih\.gov/pmc)/articles/(PMC\d+)", re.I)


def pmc_id(url: str) -> str | None:
    r"""TODO (task F11). The PubMed Central id in a link, upper case ("PMC1234567"), or None (SPEC §15.2):
      m = _PMC.match(url or ""); return m.group(1).upper() when it matched, else None.
    Tests: test_step_flow.py::test_pmc
    """
    raise NotImplementedError


async def pmc_text(client: httpx.AsyncClient, pmcid: str) -> dict:
    r"""TODO (task F11). The text of a PubMed Central article as a page {"title", "url", "text"} (SPEC §15.2).
      digits = pmcid upper-cased without its "PMC"; r = GET PUBMED + "/efetch.fcgi" params {"db": "pmc", "id": digits,
        "retmode": "xml"} (headers User-Agent UA, timeout 30); httpx.HTTPError -> ToolError(f"PubMed Central failed (<type name>)");
        status != 200 -> ToolError(f"PubMed Central answered {status}"); ET.ParseError -> ToolError("PubMed Central failed (ParseError)")
      art = root.find(".//article"); None -> ToolError("PubMed Central has no text for " + pmcid)
      title = the itertext of art's .//article-title joined, whitespace collapsed, stripped ("" when missing)
      text = every <p> inside art's <abstract> elements, then inside its <body> elements: each p's itertext joined, whitespace
        collapsed (re.sub(r"\s+", " ", …)), stripped; the non-empty ones joined with " "; empty -> the same ToolError as above
      return {"title": title or pmcid, "url": f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid.upper()}/", "text": text[:PAGE_CHARS]}
    Tests: test_step_flow.py::test_pmc
    """
    raise NotImplementedError


# ------------------------------------------------------------------ the tools offered to the model
def tool_specs(cfg: dict) -> list[dict]:
    """OpenAI-style tool definitions for a profile's settings (profiles.config).

    TODO (task B03). Tests: test_step_tools.py::test_tool_specs_and_describe. In this order, using _spec():
      sites = the domains of enabled "website" sources; pubmed = an enabled "pubmed" source exists
      cfg["web"] -> _spec("web_search", "Search the web. Use it for recent or factual questions you are unsure about.",
                          {"query": {"type": "string", "description": "what to search for"}}, ["query"])
      sites      -> _spec("search_site", "Search one of the trusted websites of this profile.",
                          {"site": {"type": "string", "enum": sites}, "query": {"type": "string"}}, ["site", "query"])
      pubmed     -> _spec("search_pubmed", "Search PubMed for medical studies (the profile's filters are added for you). "
                          "Use English medical terms.", {"query": {"type": "string"}}, ["query"])
      web or sites -> _spec("open_page", "Open a web page from the search results and read its text.",
                          {"url": {"type": "string"}}, ["url"])
    """
    result: list[dict] = []
    sites = [s["domain"] for s in cfg.get("sources", []) if s.get("enabled") and s["kind"] == "website"]
    pubmed = any(s.get("enabled") and s["kind"] == "pubmed" for s in cfg.get("sources", []))
    if cfg.get("web"):
        result.append(_spec("web_search",
                            "Search the web. Use it for recent or factual questions you are unsure about.",
                            {"query": {"type": "string", "description": "what to search for"}}, ["query"]))
    if sites:
        result.append(_spec("search_site",
                            "Search one of the trusted websites of this profile.",
                            {"site": {"type": "string", "enum": sites}, "query": {"type": "string"}},
                            ["site", "query"]))
    if pubmed:
        result.append(_spec("search_pubmed",
                            "Search PubMed for medical studies (the profile's filters are added for you). "
                            "Use English medical terms.",
                            {"query": {"type": "string"}}, ["query"]))
    if cfg.get("web") or sites:
        result.append(_spec("open_page",
                            "Open a web page from the search results and read its text.",
                            {"url": {"type": "string"}}, ["url"]))
    return result


def _spec(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required}}}


def describe(name: str, arguments: str) -> tuple[str, str]:
    """The phase and label the page shows while a tool runs.

    TODO (task B03). args = json.loads(arguments) (not JSON or not a dict -> {}); q = str(args.get("query") or "").strip()[:80]
      search_pubmed -> ("searching", f'Searching PubMed for "{q}"…')
      search_site   -> ("searching", f'Searching {args.get("site") or "the site"} for "{q}"…')
      web_search    -> ("searching", f'Searching the web for "{q}"…')
      open_page     -> ("reading", f"Reading {the url's hostname or 'a page'}…")
      anything else -> ("searching", "Looking things up…")
    """
    try:
        args = json.loads(arguments)
        if not isinstance(args, dict):
            args = {}
    except (json.JSONDecodeError, TypeError):
        args = {}
    q = str(args.get("query") or "").strip()[:80]
    if name == "search_pubmed":
        return ("searching", f'Searching PubMed for "{q}"…')
    elif name == "search_site":
        return ("searching", f'Searching {args.get("site") or "the site"} for "{q}"…')
    elif name == "web_search":
        return ("searching", f'Searching the web for "{q}"…')
    elif name == "open_page":
        url = str(args.get("url") or "")
        try:
            hostname = urlsplit(url).hostname or "a page"
        except Exception:
            hostname = "a page"
        return ("reading", f"Reading {hostname}…")
    return ("searching", "Looking things up…")


async def run_tool(name: str, arguments: str, cfg: dict, client: httpx.AsyncClient, searxng_url: str,
                   resolve: Callable = socket.getaddrinfo) -> tuple[str, list[dict]]:
    """Run one tool call. Returns (result text for the model, new sources [{"title", "url", "kind", "meta"?}]).

    TODO (task B04). Tests: test_step_tools.py::test_run_tool. Never raises:
      args = json.loads(arguments or "{}"), must be a dict, else return ("Error: the arguments were not valid JSON.", [])
      sites = enabled website domains; pub = the enabled pubmed source or None; q = str(args.get("query") or "").strip()[:300]
      inside try (except ToolError as e: return (f"Error: {e}", [])):
        web_search, only when cfg["web"] and q: res = await web_search(client, searxng_url, q)
            -> (_results(res), [{"title", "url", "kind": "web"} per result])
        search_site, only when args["site"] in sites and q: web_search(..., site=args["site"]) -> kind "site"
        search_pubmed, only when pub and q: res = await pubmed_search(client, q, pub.get("filters"));
            none -> ("No studies found with these filters. Try other English terms.", []);
            else (json.dumps([{k: a[k] for k in ("title", "journal", "year", "types", "abstract", "url")}], ensure_ascii=False),
                  [{"title", "url", "kind": "pubmed", "meta": " · ".join(the non-empty of journal, year)}])
        open_page, only when cfg["web"] or sites: allowed = None when web is on, else lambda h: any(_within(h, d) for d in sites);
            page = await fetch_page(client, args url, resolve, allowed)
            -> (f"{title}\n{url}\n\n{text}", [{"title": page title, "url": page url, "kind": "page"}])
      anything else -> (f"Error: the tool {name} is not available here.", [])
    """
    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            return ("Error: the arguments were not valid JSON.", [])
    except (json.JSONDecodeError, TypeError):
        return ("Error: the arguments were not valid JSON.", [])
    sites = [s["domain"] for s in cfg.get("sources", []) if s.get("enabled") and s["kind"] == "website"]
    pub = None
    for s in cfg.get("sources", []):
        if s.get("enabled") and s["kind"] == "pubmed":
            pub = s
            break
    q = str(args.get("query") or "").strip()[:300]
    try:
        if name == "web_search":
            if not cfg.get("web") or not q:
                return (f"Error: the tool {name} is not available here.", [])
            res = await web_search(client, searxng_url, q)
            return (_results(res), [{"title": r["title"], "url": r["url"], "kind": "web", "quote": r["snippet"]} for r in res])
        if name == "search_site":
            if not sites or not q or args.get("site") not in sites:
                return (f"Error: the tool {name} is not available here.", [])
            res = await web_search(client, searxng_url, q, site=args["site"])
            return (_results(res), [{"title": r["title"], "url": r["url"], "kind": "site", "quote": r["snippet"]} for r in res])
        if name == "search_pubmed":
            if not pub or not q:
                return (f"Error: the tool {name} is not available here.", [])
            res = await pubmed_search(client, q, pub.get("filters"))
            if not res:
                return ("No studies found with these filters. Try other English terms.", [])
            out = [{"title": a["title"], "journal": a["journal"], "year": a["year"],
                    "types": a["types"], "abstract": a["abstract"], "url": a["url"]} for a in res]
            meta = []
            for a in res:
                parts = [p for p in (a["journal"], a["year"]) if p]
                meta.append(" · ".join(parts))
            return (json.dumps(out, ensure_ascii=False),
                    [{"title": a["title"], "url": a["url"], "kind": "pubmed", "meta": m, "quote": _cut(a["abstract"])}
                     for a, m in zip(res, meta)])
        if name == "open_page":
            if not cfg.get("web") and not sites:
                return (f"Error: the tool {name} is not available here.", [])
            allowed = None if cfg.get("web") else (lambda h: any(_within(h, d) for d in sites))
            page = await fetch_page(client, args.get("url", ""), resolve, allowed)
            return (f"{page['title']}\n{page['url']}\n\n{page['text']}",
                    [{"title": page["title"], "url": page["url"], "kind": "page", "quote": _cut(page["text"])}])
    except ToolError as e:
        return (f"Error: {e}", [])
    return (f"Error: the tool {name} is not available here.", [])


def trail_step(name: str, arguments: str, found: list[dict]) -> dict:
    r"""One step of the search trail for a tool call (SPEC §13.3).
      args = json.loads(arguments or "{}") (not JSON or not a dict -> {}); q = str(args.get("query") or "").strip()
      open_page     -> {"kind": "read", "title": found[0]["title"] if found else the url, "url": found[0]["url"] if found else the url}
                       (the url = str(args.get("url") or ""))
      search_pubmed -> {"kind": "search", "source": "PubMed", "query": q, "found": len(found),
                        "url": "https://pubmed.ncbi.nlm.nih.gov/?term=" + urllib.parse.quote_plus(q)}
      search_site   -> {"kind": "search", "source": args["site"] (as str), "query": q, "found": len(found), "url": None}
      anything else -> the same with "source": "the web"
    Tests: test_step_research.py::test_trail_step
    """
    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            args = {}
    except (json.JSONDecodeError, TypeError):
        args = {}
    q = str(args.get("query") or "").strip()

    if name == "open_page":
        url = str(args.get("url") or "")
        if found:
            title = found[0].get("title") or url
            url_out = found[0].get("url") or url
        else:
            title = url
            url_out = url
        return {"kind": "read", "title": title, "url": url_out}

    if name == "search_pubmed":
        return {
            "kind": "search",
            "source": "PubMed",
            "query": q,
            "found": len(found),
            "url": "https://pubmed.ncbi.nlm.nih.gov/?term=" + quote_plus(q),
        }

    if name == "search_site":
        return {
            "kind": "search",
            "source": str(args.get("site", "")),
            "query": q,
            "found": len(found),
            "url": None,
        }

    return {
        "kind": "search",
        "source": "the web",
        "query": q,
        "found": len(found),
        "url": None,
    }


def _cut(text: str, n: int = QUOTE_CHARS) -> str:
    """The first `n` characters of a text for the source card's quote, cut at a word."""
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def _results(res: list[dict]) -> str:
    if not res:
        return "No results."
    return json.dumps(res, ensure_ascii=False)
