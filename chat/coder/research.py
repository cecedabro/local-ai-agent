"""Clear Source v2: the app does the research, the model only plans the searches and writes from the excerpts,
and the checker reads every answer (chat/SPEC.md §13.2). Tests: chat/tests/test_step_research.py
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, AsyncIterator, Callable
from urllib.parse import quote_plus, urlsplit

import httpx

from . import answer, context, gateway, profiles, tools, verify

MAX_SEARCHES = 7
MAX_SOURCES = 20          # candidates gathered over all rounds
MAX_USED = 10             # sources the answer is written from
SOURCE_CHARS = 8000
PAGES_PER_SEARCH = 2
PUBMED_N = 8              # articles per PubMed search
MIN_FOUND = 3             # a PubMed search with fewer hits is tried again without filters and MeSH
MIN_USED = 3              # fewer useful sources than this after a round: one more round with new searches
MAX_ROUNDS = 2
MAX_PASSAGES = 4
PUBMED_WEB = "https://pubmed.ncbi.nlm.nih.gov/?term="
PLAN_PROMPT = (
    "You plan literature searches for a medical question. Answer with JSON only: an object "
    '{"about": "<the question in one plain sentence: who, what, which outcome>", "searches": [<at most 7 objects '
    '{"source": "pubmed" | "site:<domain>" | "web", "query": "<search terms>", "mesh": ["<MeSH heading>", ...]}>]}. '
    "PubMed: 2 to 4 searches, from specific to broad, with English search terms and synonyms, and in \"mesh\" up to "
    '4 official MeSH headings for the main concepts (for example "Renal Insufficiency, Chronic" or "Pregnancy"); leave '
    "out drugs too new for MeSH. Websites: choose the 1 to 3 sites that fit the question best (drugs: "
    "farmacotherapeutischkompas.nl; children's doses: kinderformularium.nl; infections and vaccination: rivm.nl; "
    "specialist care: richtlijnendatabase.nl; side effects: lareb.nl; patient advice: thuisarts.nl) and search them in "
    "Dutch with 2 to 4 key words, no mesh. Use only these sources: {sources}.")
FOLLOWUP_PROMPT = (
    "The searches below found too little for this medical question. Plan new searches with other words: synonyms, "
    "broader or related terms, fewer words per search, other sites. Do not repeat a search. Answer with JSON only, the "
    'same format: {"about": "...", "searches": [...]}. Use only these sources: {sources}.')
EXTRACT_PROMPT = (
    "You read one source for a medical question. Copy, word for word, up to 4 passages of the source that help "
    "answer the question: whole sentences, exactly as written, in the source's own language; change nothing. Answer "
    'with JSON only: {"quotes": ["...", "..."]}, or {"quotes": []} when nothing in the source helps.')
NO_EVIDENCE = {
    "nl": "Ik heb in de bronnen die ik doorzocht geen betrouwbaar bewijs gevonden voor deze vraag. Ik geef daarom "
          "geen antwoord uit eigen kennis. Probeer de vraag specifieker te stellen of voeg een bron toe.",
    "en": "I found no reliable evidence for this question in the sources I searched, so I won't answer from my own "
          "knowledge. Try asking more specifically, or add a source.",
}
_DUTCH = {"de", "het", "een", "is", "bij", "van", "en", "niet", "wat", "hoe", "welke", "voor", "met", "zijn",
          "wordt", "mag", "kan", "helpt", "tegen", "tijdens", "patiënten", "dosis"}
RULES = (
    "You are Clear Source. Answer ONLY from the passages of the numbered sources below; never use your own knowledge. "
    "Answer in the language of the question, in Markdown. Write a long, detailed answer under these ## headings "
    "(in Dutch: Kort antwoord, Nederlandse richtlijnen, Onderzoek, Dosering en bijzondere groepen, Sterkte van het "
    "bewijs), and leave out a part the sources do not cover:\n"
    "## Short answer: two or three sentences.\n"
    "## Dutch guidelines: what the guideline sources say.\n"
    "## Studies: what the studies show, with their numbers (effects, doses, groups, follow-up).\n"
    "## Dosing and special groups: children, pregnancy, kidney or liver function, the elderly, interactions.\n"
    "## Strength of the evidence: the study types, their size and limitations.\n"
    "Build every part from verbatim quotes of the sources as Markdown blockquotes followed by their number, like "
    '> "exact words from the source" [2], and explain each quote in your own words; every claim ends with its [n]. '
    "Quote exactly, never change numbers or words, never add facts that are not in the sources. If the sources do "
    "not answer the question, say so plainly instead of answering.")


def looks_dutch(text: str) -> bool:
    r"""True when at least two of the question's words (re.findall(r"[a-zà-ÿ]+", lower case)) are in _DUTCH.
    """
    words = re.findall(r"[a-zà-ÿ]+", text.lower())
    return sum(1 for w in words if w in _DUTCH) >= 2


def enabled_sources(cfg: dict) -> list[str]:
    r"""The plan sources this profile allows, in this order: "pubmed" (once, when an enabled pubmed card exists), then
    "site:<domain>" for each enabled website card, then "web" when cfg["web"].
    """
    result = []
    for s in cfg.get("sources", []):
        if not s.get("enabled", True):
            continue
        if s["kind"] == "pubmed":
            result.append("pubmed")
        elif s["kind"] == "website":
            result.append(f"site:{s['domain']}")
    if cfg.get("web"):
        result.append("web")
    return result


def plan_messages(question: str, cfg: dict) -> list[dict]:
    r"""The planning call's messages: [{"role": "system", "content": PLAN_PROMPT with "{sources}" replaced by
    ", ".join(enabled_sources(cfg)) (or "none")}, {"role": "user", "content": question}].
    """
    src = ", ".join(enabled_sources(cfg)) if enabled_sources(cfg) else "none"
    return [
        {"role": "system", "content": PLAN_PROMPT.replace("{sources}", src)},
        {"role": "user", "content": question},
    ]


def parse_plan(text: str, cfg: dict, question: str = "") -> list[dict]:
    r"""The searches in the model's plan (SPEC §13.2 step 1).
      allowed = enabled_sources(cfg); t = text stripped
      if there is a ``` fence (re.search(r"```(?:json)?\s*(.*?)```", t, re.S)): t = its inside; else t = from the first "["
        to the last "]" when both exist
      json.loads(t) (ValueError -> []); for each dict item: source = str(item["source"]).strip().lower(),
        query = str(item["query"]).strip()[:200]; keep it when source in allowed and query; stop at MAX_SEARCHES
      nothing kept and "pubmed" in allowed and question.strip() -> [{"source": "pubmed", "query": question.strip()[:200]}]
    Tests: test_step_research.py::test_parse_plan
    """
    allowed = enabled_sources(cfg)
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1)
    else:
        first_bracket = t.find("[")
        last_bracket = t.rfind("]")
        first_brace = t.find("{")
        last_brace = t.rfind("}")
        if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
            if first_brace != -1 and last_brace != -1:
                t = t[first_brace:last_brace + 1]
            else:
                t = ""
        elif first_bracket != -1 and last_bracket != -1:
            t = t[first_bracket:last_bracket + 1]
        else:
            t = ""
    try:
        items = json.loads(t)
    except ValueError:
        items = []
    # TODO (task F04), SPEC §15.1: the plan may be an object {"about", "searches": [...]}: a dict -> items = its
    #   "searches" (or []); anything that not a list then -> []. (Also the slicing above: when a "{" comes before any
    #   "[", take t from the first "{" to the last "}" instead.) Tests: test_step_flow.py::test_plan_object_and_about
    if isinstance(items, dict):
        searches = items.get("searches")
        items = searches if isinstance(searches, list) else []
    elif not isinstance(items, list):
        items = []
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source", "")).strip().lower()
        query = str(item.get("query", "")).strip()[:200]
        if source not in allowed or not query:
            continue
        # TODO (task F04): MeSH (SPEC §14.1). Only for a "pubmed" item whose "mesh" is a list: heads = [str(m).strip()[:80]
        #   for each m whose str(m).strip() is not empty][:4]; when heads is not empty the dict also gets "mesh": heads.
        #   Other sources never get "mesh". Tests: test_step_clear.py::test_parse_plan_mesh
        entry = {"source": source, "query": query}
        if source == "pubmed" and isinstance(item.get("mesh"), list):
            heads = [str(m).strip()[:80] for m in item["mesh"] if str(m).strip()]
            if heads:
                entry["mesh"] = heads[:4]
        result.append(entry)
        if len(result) >= MAX_SEARCHES:
            break
    if not result and "pubmed" in allowed and question.strip():
        result.append({"source": "pubmed", "query": question.strip()[:200]})
    return result


def plan_about(text: str) -> str:
    r"""The plan's "about" sentence (SPEC §15.1), "" when there is none.
      t = text stripped (None counts as ""); a ``` fence (re.search(r"```(?:json)?\s*(.*?)```", t, re.S)) -> its inside
      from the first "{" to the last "}" (none -> ""); json.loads (ValueError -> ""); not a dict, or "about" not a str -> ""
      return " ".join(about.split())[:300]
    Tests: test_step_flow.py::test_plan_object_and_about
    """
    t = text.strip() if text else ""
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1)
    first = t.find("{")
    last = t.rfind("}")
    if first != -1 and last != -1:
        t = t[first:last + 1]
    else:
        return ""
    try:
        obj = json.loads(t)
    except ValueError:
        return ""
    if not isinstance(obj, dict):
        return ""
    about = obj.get("about")
    if not isinstance(about, str):
        return ""
    return " ".join(about.split())[:300]


def label(step: dict) -> str:
    r"""The phase label for one planned search: 'Searching PubMed for "<query[:80]>"…' for pubmed, 'Searching the web for …'
    for web, 'Searching <domain> for …' for "site:<domain>".
    Tests (E07): test_step_research.py::test_helpers
    """
    q = step["query"][:80]
    if step["source"] == "pubmed":
        return f'Searching PubMed for "{q}"…'
    elif step["source"] == "web":
        return f'Searching the web for "{q}"…'
    else:
        domain = step["source"].removeprefix("site:")
        return f'Searching {domain} for "{q}"…'


async def gather(plan: list[dict], cfg: dict, client: Any, searxng_url: str, t: Any = tools,
                 on_step: Callable[[dict], Any] | None = None) -> tuple[list[dict], list[dict]]:
    r"""TODO (task F05). Run the plan: (sources, trail), SPEC §13.2 step 2, §14.1-14.2, §15.2. `t` has mesh_check,
    pubmed_search, web_search, pmc_id, pmc_text and fetch_page (tools or fakes).
      pub = the first enabled pubmed card of cfg["sources"] (or None); filters = pub["filters"] or None (None without a card)
      add(title, url, kind, meta, text, type_=None, site=None): skip when MAX_SOURCES sources exist or the url is already
        a source; else append {"n": len(sources)+1, "title": title or url, "url", "kind", "meta", "text": text[:SOURCE_CHARS],
        "type": type_, "site": site}   (every source has the keys "type" and "site")
      step(s): append s to trail; call on_step(s) when given (await it when it returns a coroutine)
      articles(res): per article a: meta = " · ".join of the non-empty a.get("type"), a.get("journal"), a.get("year");
        add(a["title"], a["url"], "pubmed", meta, a.get("abstract", ""), type_=a.get("type"))
      for each item {"source", "query" (q), "mesh"?} of the plan:
        pubmed: mesh = await t.mesh_check(client, item["mesh"]) when the item has a non-empty "mesh", else [] (no call)
          info = {}; res = await t.pubmed_search(client, q, filters, n=PUBMED_N, mesh=mesh, info=info)
          tools.ToolError e -> step {"kind": "search", "source": "PubMed", "query": q, "found": 0, "url": None, "error": str(e)}, next item
          else step {"kind": "search", "source": "PubMed", "query": q, "found": len(res),
                     "url": PUBMED_WEB + quote_plus(info.get("term") or q), "mesh": mesh, "translation": info.get("translation", "")}
          and articles(res)
          broader (§15.2): when len(res) < MIN_FOUND and (filters or mesh): info = {}; res = await t.pubmed_search(client, q, None,
            n=PUBMED_N, mesh=[], info=info); the same two kinds of step as above, each with "broader": True (the found step with
            "mesh": []); then articles(res)
        "site:<d>" or "web" (d = None for web): res = await t.web_search(client, searxng_url, q, site=d)
          ToolError -> the error step with "source": d or "the web"; else step {"kind": "search", "source": d or "the web",
          "query": q, "found": len(res), "url": None}
          for the first PAGES_PER_SEARCH results r (stop when MAX_SOURCES sources exist):
            pmc = t.pmc_id(r["url"]); page = await t.pmc_text(client, pmc) when pmc, else await t.fetch_page(client, r["url"],
              allowed=(lambda h: tools._within(h, d)) for a site, None for web)
            ToolError e -> step {"kind": "open", "title": r.get("title") or r["url"], "url": r["url"], "error": str(e)}, next result
            else step {"kind": "open", "title": page["title"], "url": page["url"]};
              add(page["title"], page["url"], "page", urlsplit(page["url"]).hostname or "", page["text"], site=d)
    Tests: test_step_research.py::test_gather, ::test_gather_caps_sources, test_step_clear.py::test_gather_mesh_types_and_sites,
      test_step_flow.py::test_gather_broadens_and_reads_pmc
    """
    raise NotImplementedError


def extract_messages(question: str, src: dict) -> list[dict]:
    r"""TODO (task F12). The messages that ask the model to read one source (SPEC §15.4):
      [{"role": "system", "content": EXTRACT_PROMPT},
       {"role": "user", "content": f"Question: {question}\n\nSource: {src['title']} ({src.get('meta') or src['url']})\n\n{src['text']}"}]
    Tests: test_step_flow.py::test_extract_messages_and_parse_quotes
    """
    raise NotImplementedError


def parse_quotes(text: str) -> list[str]:
    r"""TODO (task F12). The passages in the model's reply (SPEC §15.4), [] when it has none.
      t = text stripped; a ``` fence -> its inside, stripped
      then t = from the first "{" to the last "}" when a "{" comes before any "[", else from the first "[" to the last "]"
      json.loads (ValueError -> []); items = obj["quotes"] (obj.get) for a dict, else obj; not a list -> []
      per item: skip non-strings; q = item.strip(), then .strip('"“”').strip(); keep it when it has 4+ words
        (len(re.findall(r"\w+", q)) >= 4) and is not kept already; stop at MAX_PASSAGES
    Tests: test_step_flow.py::test_extract_messages_and_parse_quotes
    """
    raise NotImplementedError


async def read_one(question: str, src: dict, settings: Any, complete: Callable) -> tuple[list[str], dict]:
    r"""TODO (task F12). Read one source: (passages, trail step), SPEC §15.4.
      step = {"kind": "read", "n": src["n"], "title": src["title"], "url": src["url"]}
      reply = await complete(settings, extract_messages(question, src)); gateway.Busy or gateway.GatewayError ->
        return [], {**step, "quotes": 0, "status": "error"}
      found = [q for q in parse_quotes(reply) if verify.find_quote(q, src["text"])]   (only word-for-word passages count)
      return found, {**step, "quotes": len(found), "status": "used" if found else "not relevant"}
    Tests: test_step_flow.py::test_read_one
    """
    raise NotImplementedError


def followup_messages(question: str, cfg: dict, trail: list[dict]) -> list[dict]:
    r"""TODO (task F13). The messages for one more round of searches (SPEC §15.5):
      src = ", ".join(enabled_sources(cfg)) or "none"
      done = one line f"- {s['source']}: {s['query']} ({s['found']} found)" per trail step of kind "search"
      used = the number of trail steps of kind "read" with status "used"
      [{"role": "system", "content": FOLLOWUP_PROMPT with "{sources}" replaced by src},
       {"role": "user", "content": f"Question: {question}\n\nSearches so far:\n" + "\n".join(done) + f"\n\nUseful sources found: {used}"}]
    Tests: test_step_flow.py::test_followup_and_passages
    """
    raise NotImplementedError


RANKS = {"Guideline": 1, "Meta-analysis": 2, "Systematic review": 2, "RCT": 3}


def rank(src: dict) -> int:
    r"""TODO (task F06). A source's group for `order` (SPEC §14.2), lower comes first:
      kind "page" -> 0 when src.get("site") (a page of a guideline website card), else 5 (a web page)
      anything else (PubMed) -> RANKS.get(src.get("type") or "", 4)
    Tests: test_step_clear.py::test_order
    """
    raise NotImplementedError


def order(sources: list[dict]) -> list[dict]:
    r"""TODO (task F06). The sources sorted by rank (a stable sort: the same rank keeps its order), as new dicts
    ({**s, "n": i + 1}) numbered from 1; the input list and its dicts stay unchanged.
    Tests: test_step_clear.py::test_order
    """
    raise NotImplementedError


def evidence_messages(question: str, sources: list[dict], cfg: dict) -> list[dict]:
    r"""The answer call's messages (SPEC §13.2 step 4).
      pack = "\n\n".join(f"[{n}] {title} ({meta or url})\n{text}" per source)
      system = RULES, plus "\n\nExtra instructions from the user:\n" + cfg["instructions"].strip() when not empty
      return [{"role": "system", "content": system}, {"role": "user", "content": f"Sources:\n\n{pack}\n\nQuestion: {question}"}]
    """
    # TODO (task F13), SPEC §15.5: a source with "passages" shows them instead of its text, one line each: '- "<passage>"'
    #   (joined with "\n"). Tests: test_step_flow.py::test_followup_and_passages
    pack = "\n\n".join(f"[{s['n']}] {s['title']} ({s.get('meta') or s['url']})\n{s['text']}" for s in sources)
    extra = cfg.get("instructions", "")
    if extra and extra.strip():
        system = RULES + "\n\nExtra instructions from the user:\n" + extra.strip()
    else:
        system = RULES
    return [{"role": "system", "content": system}, {"role": "user", "content": f"Sources:\n\n{pack}\n\nQuestion: {question}"}]


def public_sources(sources: list[dict]) -> list[dict]:
    r"""Sources as the page gets them: per source {n, title, url, kind, meta} plus "quote": tools._cut(text); never "text".
    Tests (E10): test_step_research.py::test_evidence_messages_and_public_sources
    """
    result = []
    for s in sources:
        # TODO (task F13): also "type" and "site" (s.get: None when missing), SPEC §14.2/§15.6, and the quote is
        #   tools._cut(the first passage) when the source has "passages". Tests: test_step_clear.py::test_public_sources_type,
        #   test_step_flow.py::test_followup_and_passages
        out = {k: s[k] for k in ("n", "title", "url", "kind", "meta")}
        out["quote"] = tools._cut(s.get("text", ""))
        result.append(out)
    return result


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _phase(name: str, text: str) -> str:
    return _sse("phase", {"phase": name, "label": text})


def _site_first(plan: list[dict]) -> list[dict]:
    return sorted(plan, key=lambda i: 0 if i["source"].startswith("site:") else 1 if i["source"] == "pubmed" else 2)


async def investigate(question: str, cfg: dict, settings: Any, complete: Callable, t: Any = tools,
                      stop: asyncio.Event | None = None, out: dict | None = None) -> AsyncIterator[str]:
    r"""TODO (task F14). The research stages as SSE text (SPEC §15.1-15.5); the results go into `out`.
      out = out or {}; trail, candidates, used = [], [], []; done_q = set() of (source, query.lower()); out.update(used=used, trail=trail)
      note(step): append it to trail and return _sse("trail", {"step": step})   (yield what it returns)
      yield _phase("thinking", "Understanding the question…")
      plan_text = await complete(settings, plan_messages(question, cfg)) (gateway.Busy/GatewayError -> ""); plan = parse_plan(plan_text, cfg, question)
      yield note({"kind": "plan", "about": plan_about(plan_text), "queries": [i["query"] for i in plan],
                  "mesh": [m for i in plan for m in i.get("mesh", [])]})
      async with httpx.AsyncClient() as client: for rnd in 1..MAX_ROUNDS:
        new = []
        for item in _site_first(plan): stop set -> break; skip it when its (source, query.lower()) is in done_q, else add it;
          yield _phase("searching", label(item)); got, steps = await gather([item], cfg, client, settings.SEARXNG_URL, t);
          yield note(s) per step; per src of got: when fewer than MAX_SOURCES candidates and its url is not a candidate yet:
          src = {**src, "n": len(candidates) + 1}; append it to candidates and to new
        for i, src in enumerate(new, 1): stop set -> break; yield _phase("reading", f"Reading {i} of {len(new)}: {src['title'][:60]}…");
          quotes, st = await read_one(question, src, settings, complete); yield note(st); quotes -> used.append({**src, "passages": quotes})
        len(used) >= MIN_USED, rnd == MAX_ROUNDS or stop set -> break
        yield _phase("thinking", "Looking further with other words…")
        more = await complete(settings, followup_messages(question, cfg, trail)) (Busy/GatewayError -> break)
        plan = [i for i in parse_plan(more, cfg) if (i["source"], i["query"].lower()) not in done_q]; empty -> break
        yield note({"kind": "plan", "about": plan_about(more), "queries": [...], "mesh": [...], "round": rnd + 1})   (as above)
      at the end, after the rounds: used[:] = [{**s, "n": i + 1} for i, s in enumerate(order(used)[:MAX_USED])]   (change `used` in place: out holds it)
    Tests: test_step_flow.py::test_investigate_two_rounds, ::test_investigate_enough_in_one_round_and_stop
    """
    raise NotImplementedError


async def run_evidence(db: Any, settings: Any, chat: dict, think: bool, *,
                       stream: Callable = gateway.stream_chat, complete: Callable = gateway.complete,
                       t: Any = tools, clock: Callable[[], float] = time.monotonic,
                       stop: asyncio.Event | None = None) -> AsyncIterator[str]:
    r"""One Clear Source answer as SSE text (SPEC §13.2, the event order there). The user's message is already stored.
      question = the last user message of the chat; cfg = profiles.config(db, username, profile)
      yield _phase("reading", "Reading your question…"); yield _phase("thinking", "Choosing search terms…")
      plan_text = await complete(settings, plan_messages(question, cfg)) (gateway.Busy/GatewayError -> ""); plan = parse_plan(...)
      async with httpx.AsyncClient() as client: per item: yield _phase("searching", label(item)); got, steps = await gather([item],
        cfg, client, settings.SEARXNG_URL, t); yield _sse("trail", {"step": s}) per step (and keep the trail); add got to
        sources renumbered from 1 (skip a url already there, max MAX_SOURCES); stop when `stop` is set
      lang = "nl" if looks_dutch(question) else "en"; stopped, tokens, tps = False, 0, None
      no sources: text = NO_EVIDENCE[lang]; yield _phase("writing", "Writing the answer…"); yield the text as one delta;
        report = None; db.add_message(..., text, tokens=0, sources=[], trail=trail, verify=None)
      else: yield _phase("reading", f"Reading {n} sources…") and _phase("summarising", f"Building the answer from {n} sources…");
        stream(settings, evidence_messages(...), think) (no tools): reasoning -> _sse("thinking", {"text"}), collect it, note
        when it started; content -> first time _phase("writing", "Writing the answer…"), then _sse("delta", {"text"}); usage ->
        keep; stop set -> stopped = True, break. Busy/GatewayError -> _sse("error", {"error": "The model could not answer.",
        "status": ...}) and return.
        report = verify.check(text, sources); yield _sse("verify", {"report": report}); tokens/tps as in answer.py;
        db.add_message(..., sources=public_sources(sources), trail=trail, verify=report, thinking=..., thought_s=seconds from
        the first reasoning chunk to the first content chunk or None); update_chat_usage when usage came
      title "New chat" -> db.update_chat_title(..., answer.make_title(question)) (import make_title inside the function)
      chat = db.get_chat(...); yield _sse("done", {"message_id", "tokens", "tps", "context": context.info(db, chat, settings),
        "title", "stopped", "sources": public_sources(sources), "trail", "verify": report})
    Tests: test_step_research.py::test_run_evidence_events_and_storage, ::test_run_evidence_nothing_found, ::test_run_evidence_flags_a_changed_quote
    """
    msgs = db.get_messages(chat["id"], chat["username"])
    question = msgs[-1]["content"]
    cfg = profiles.config(db, chat["username"], chat["profile"])
    # TODO (task F15), SPEC §15.5: run the research: out = {}; yield every chunk of
    #   investigate(question, cfg, settings, complete, t, stop, out); then sources, trail = out["used"], out["trail"].
    #   Tests: test_step_research.py (run_evidence), test_step_clear.py::test_run_evidence_sites_first_and_ordered
    sources: list[dict] = []
    trail: list[dict] = []

    lang = "nl" if looks_dutch(question) else "en"

    if not sources:
        text = NO_EVIDENCE[lang]
        yield _phase("writing", "Writing the answer…")
        yield _sse("delta", {"text": text})
        msg_id = db.add_message(chat["id"], "assistant", text, tokens=0,
                                sources=[], trail=trail, verify=None)
        if chat["title"] == "New chat":
            db.update_chat_title(chat["id"], chat["username"], answer.make_title(question))
        chat = db.get_chat(chat["id"], chat["username"])
        yield _sse("done", {"message_id": msg_id, "tokens": 0, "tps": None,
                            "context": context.info(db, chat, settings),
                            "title": chat["title"], "stopped": False,
                            "sources": [], "trail": trail, "verify": None})
        return

    yield _phase("writing", f"Writing the answer from {len(sources)} sources…")

    stopped = False
    text = ""
    reasoning_text = ""
    th0 = None
    t0 = None
    usage = None
    tokens = 0
    agen = None

    try:
        agen = stream(settings, evidence_messages(question, sources, cfg), think)
        async for tag, val in agen:
            if stop and stop.is_set():
                stopped = True
                break
            if tag == "reasoning":
                if val:
                    if th0 is None:
                        th0 = clock()
                    reasoning_text += val
                    yield _sse("thinking", {"text": val})
            elif tag == "content":
                if t0 is None:
                    t0 = clock()
                text += val
                yield _sse("delta", {"text": val})
            elif tag == "usage":
                usage = val
    except (gateway.Busy, gateway.GatewayError) as e:
        yield _sse("error", {"error": "The model could not answer.", "status": getattr(e, "status", 502)})
        return
    finally:
        if agen is not None:
            try:
                await agen.aclose()
            except Exception:
                pass

    report = verify.check(text, sources)
    yield _sse("verify", {"report": report})

    tokens = usage["completion_tokens"] if usage else tokens
    tps = round(tokens / (clock() - t0), 1) if t0 else None

    msg_id = db.add_message(chat["id"], "assistant", text, tokens=tokens, tps=tps, stopped=stopped,
                            sources=public_sources(sources), trail=trail, verify=report,
                            thinking=reasoning_text,
                            thought_s=round(t0 - th0, 1) if th0 and t0 else None)
    if usage:
        db.update_chat_usage(chat["id"], usage["prompt_tokens"], usage["completion_tokens"])

    if chat["title"] == "New chat":
        db.update_chat_title(chat["id"], chat["username"], answer.make_title(question))

    chat = db.get_chat(chat["id"], chat["username"])
    yield _sse("done", {"message_id": msg_id, "tokens": tokens, "tps": tps,
                        "context": context.info(db, chat, settings),
                        "title": chat["title"], "stopped": stopped,
                        "sources": public_sources(sources), "trail": trail, "verify": report})
