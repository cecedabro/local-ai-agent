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

MAX_SEARCHES = 6
MAX_SOURCES = 10
SOURCE_CHARS = 4000
PAGES_PER_SEARCH = 2
PLAN_PROMPT = (
    "You plan literature searches for a medical question. Answer with JSON only: a list of at most 6 objects "
    '{"source": "pubmed" | "site:<domain>" | "web", "query": "<search terms>"}. Use English medical terms '
    "(MeSH words) for PubMed and the site's own language for websites. Use only these sources: {sources}.")
NO_EVIDENCE = {
    "nl": "Ik heb in de bronnen die ik doorzocht geen betrouwbaar bewijs gevonden voor deze vraag. Ik geef daarom "
          "geen antwoord uit eigen kennis. Probeer de vraag specifieker te stellen of voeg een bron toe.",
    "en": "I found no reliable evidence for this question in the sources I searched, so I won't answer from my own "
          "knowledge. Try asking more specifically, or add a source.",
}
_DUTCH = {"de", "het", "een", "is", "bij", "van", "en", "niet", "wat", "hoe", "welke", "voor", "met", "zijn",
          "wordt", "mag", "kan", "helpt", "tegen", "tijdens", "patiënten", "dosis"}
RULES = (
    "You are Clear Source. Answer ONLY from the numbered sources below; never use your own knowledge. "
    "Answer in the language of the question. Structure: first a short answer of one or two sentences; then the "
    "reasoning, built from verbatim quotes of the sources as Markdown blockquotes followed by their number, like "
    '> "exact words from the source" [2]; every other claim also ends with its [n]; finally one line on how strong '
    "the evidence is. Quote exactly, never change numbers or words. If the sources do not answer the question, "
    "say so plainly instead of answering.")


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
        first = t.find("[")
        last = t.rfind("]")
        if first != -1 and last != -1:
            t = t[first:last + 1]
        else:
            t = ""
    try:
        items = json.loads(t)
    except ValueError:
        items = []
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source", "")).strip().lower()
        query = str(item.get("query", "")).strip()[:200]
        if source not in allowed or not query:
            continue
        result.append({"source": source, "query": query})
        if len(result) >= MAX_SEARCHES:
            break
    if not result and "pubmed" in allowed and question.strip():
        result.append({"source": "pubmed", "query": question.strip()[:200]})
    return result


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
    r"""Run the plan: (sources, trail), SPEC §13.2 step 2. `t` has pubmed_search, web_search and fetch_page (tools or fakes).
      pub = the enabled pubmed card (or None). add(title, url, kind, meta, text): skip when MAX_SOURCES reached or the url is
        already a source; else append {"n": len(sources)+1, "title": title or url, "url", "kind", "meta", "text": text[:SOURCE_CHARS]}
      step(s): append s to trail; call on_step(s) when given (await it when it returns a coroutine)
      for each {"source", "query"} in plan:
        pubmed: res = await t.pubmed_search(client, q, pub["filters"] or None); tools.ToolError e -> step {"kind": "search",
          "source": "PubMed", "query": q, "found": 0, "url": None, "error": str(e)} and continue; else step {"kind": "search",
          "source": "PubMed", "query": q, "found": len(res), "url": "https://pubmed.ncbi.nlm.nih.gov/?term=" + quote_plus(q)}
          and add(a["title"], a["url"], "pubmed", " · ".join of the non-empty journal and year, a["abstract"]) per result
        site:<d> or web: res = await t.web_search(client, searxng_url, q, site=d or None); ToolError -> the error step with
          "source": d or "the web"; else step {"kind": "search", "source": d or "the web", "query": q, "found": len(res), "url": None};
          then for the first PAGES_PER_SEARCH results: page = await t.fetch_page(client, r["url"], allowed=a function
          lambda h: tools._within(h, d) for a site, None for web) (ToolError -> skip); step {"kind": "read", "title": page["title"],
          "url": page["url"]}; add(page["title"], page["url"], "page", urlsplit(page["url"]).hostname or "", page["text"])
    Tests: test_step_research.py::test_gather, ::test_gather_caps_sources
    """
    sources: list[dict] = []
    trail: list[dict] = []
    pub = None
    for s in cfg.get("sources", []):
        if s.get("enabled", True) and s["kind"] == "pubmed":
            pub = s
            break

    def add(title: str, url: str, kind: str, meta: str, text: str) -> None:
        if len(sources) >= MAX_SOURCES:
            return
        if any(s["url"] == url for s in sources):
            return
        sources.append({"n": len(sources) + 1, "title": title or url, "url": url, "kind": kind,
                         "meta": meta, "text": text[:SOURCE_CHARS]})

    for item in plan:
        source = item["source"]
        query = item["query"]

        if source == "pubmed":
            if len(sources) >= MAX_SOURCES:
                continue
            try:
                res = await t.pubmed_search(client, query, pub["filters"] if pub else None)
            except tools.ToolError as e:
                step = {"kind": "search", "source": "PubMed", "query": query, "found": 0, "url": None, "error": str(e)}
                trail.append(step)
                if on_step:
                    ret = on_step(step)
                    if asyncio.iscoroutine(ret):
                        await ret
                continue
            step = {"kind": "search", "source": "PubMed", "query": query, "found": len(res),
                    "url": "https://pubmed.ncbi.nlm.nih.gov/?term=" + quote_plus(query)}
            trail.append(step)
            if on_step:
                ret = on_step(step)
                if asyncio.iscoroutine(ret):
                    await ret
            for a in res:
                if len(sources) >= MAX_SOURCES:
                    break
                meta_parts = []
                if a.get("journal"):
                    meta_parts.append(a["journal"])
                if a.get("year"):
                    meta_parts.append(a["year"])
                add(a["title"], a["url"], "pubmed", " · ".join(meta_parts), a["abstract"])

        elif source.startswith("site:") or source == "web":
            if len(sources) >= MAX_SOURCES:
                continue
            domain = source.removeprefix("site:") if source.startswith("site:") else None
            try:
                res = await t.web_search(client, searxng_url, query, site=domain)
            except tools.ToolError as e:
                step = {"kind": "search", "source": domain if domain else "the web", "query": query,
                        "found": 0, "url": None, "error": str(e)}
                trail.append(step)
                if on_step:
                    ret = on_step(step)
                    if asyncio.iscoroutine(ret):
                        await ret
                continue
            step = {"kind": "search", "source": domain if domain else "the web", "query": query,
                    "found": len(res), "url": None}
            trail.append(step)
            if on_step:
                ret = on_step(step)
                if asyncio.iscoroutine(ret):
                    await ret
            for r in res[:PAGES_PER_SEARCH]:
                if len(sources) >= MAX_SOURCES:
                    break
                allowed = (lambda h, d=domain: tools._within(h, d)) if source.startswith("site:") else None
                try:
                    page = await t.fetch_page(client, r["url"], allowed=allowed)
                except tools.ToolError:
                    continue
                page_step = {"kind": "read", "title": page["title"], "url": page["url"]}
                trail.append(page_step)
                if on_step:
                    ret = on_step(page_step)
                    if asyncio.iscoroutine(ret):
                        await ret
                add(page["title"], page["url"], "page",
                    urlsplit(page["url"]).hostname or "", page["text"])

    return sources, trail


def evidence_messages(question: str, sources: list[dict], cfg: dict) -> list[dict]:
    r"""The answer call's messages (SPEC §13.2 step 4).
      pack = "\n\n".join(f"[{n}] {title} ({meta or url})\n{text}" per source)
      system = RULES, plus "\n\nExtra instructions from the user:\n" + cfg["instructions"].strip() when not empty
      return [{"role": "system", "content": system}, {"role": "user", "content": f"Sources:\n\n{pack}\n\nQuestion: {question}"}]
    """
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
        out = {k: s[k] for k in ("n", "title", "url", "kind", "meta")}
        out["quote"] = tools._cut(s.get("text", ""))
        result.append(out)
    return result


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _phase(name: str, text: str) -> str:
    return _sse("phase", {"phase": name, "label": text})


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

    yield _phase("reading", "Reading your question…")
    yield _phase("thinking", "Choosing search terms…")

    try:
        plan_text = await complete(settings, plan_messages(question, cfg))
    except (gateway.Busy, gateway.GatewayError):
        plan_text = ""                                  # the question itself becomes the PubMed search
    plan = parse_plan(plan_text, cfg, question)

    sources: list[dict] = []
    trail: list[dict] = []

    async with httpx.AsyncClient() as client:
        for item in plan:
            if stop and stop.is_set():
                break
            yield _phase("searching", label(item))
            got, steps = await gather([item], cfg, client, settings.SEARXNG_URL, t)
            for s in steps:
                yield _sse("trail", {"step": s})
            trail.extend(steps)
            for src in got:
                existing = next((s for s in sources if s["url"] == src["url"]), None)
                if existing:
                    src["n"] = existing["n"]
                else:
                    if len(sources) >= MAX_SOURCES:
                        continue
                    src["n"] = len(sources) + 1
                    sources.append(src)

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

    yield _phase("reading", f"Reading {len(sources)} sources…")
    yield _phase("summarising", f"Building the answer from {len(sources)} sources…")

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
        writing_sent = False
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
                if not writing_sent:
                    yield _phase("writing", "Writing the answer…")
                    writing_sent = True
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
