"""Profiles: each user's settings per profile, the source cards, the system prompt and its budget
(chat/SPEC.md §12.1–12.2)."""
from __future__ import annotations

import math
from typing import Any
from urllib.parse import urlsplit

from . import context

PROFILES = ("general", "evidence")
NAMES = {"general": "General", "evidence": "EvidenceHunt"}
RIGHT = {"general": "chat", "evidence": "evidence"}
FILTER_TYPES = ("systematic_review", "meta_analysis", "rct", "guideline", "review")
FILTER_LANGS = ("english", "dutch")
# A new EvidenceHunt profile starts with these cards (the user can change or delete them).
SEED = {
    "evidence": [
        {"kind": "pubmed", "title": "PubMed",
         "note": "Prefer systematic reviews, meta-analyses, randomised trials and guidelines. Say how strong the evidence is.",
         "filters": {"types": ["systematic_review", "meta_analysis", "rct", "guideline"], "years": 10, "humans": True,
                     "languages": []}},
        {"kind": "website", "title": "rivm.nl", "domain": "rivm.nl",
         "note": "The Dutch public health institute: use it for Dutch advice and guidelines."},
    ],
}
CITE = ("When you use a search result or page, cite it with its number in square brackets, like [1], right after "
        "the sentence it supports; quote its exact words when they matter. Do not invent sources or quotes.")


def tokens(text: str) -> int:
    """The rough token count the app uses everywhere: ceil(len(text) / 4).
    """
    return math.ceil(len(text) / 4)


def domain_of(url: str) -> str | None:
    """'https://www.rivm.nl/griep?x=1' -> 'rivm.nl'; 'rivm.nl' -> 'rivm.nl'; not usable -> None.
    Add "https://" when there is no "://"; urlsplit; host = hostname lower-cased without a trailing dot;
    None unless the scheme is http/https, the host has a "." and only letters, digits, "." and "-"; drop a leading "www.".
    """
    if "://" not in url:
        url = "https://" + url
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or "." not in host:
        return None
    import re
    if not re.match(r'^[a-z0-9.\-]+$', host):
        return None
    if host.startswith("www."):
        host = host[4:]
    return host


PRESETS = {"strict": {"types": ["systematic_review", "meta_analysis", "guideline"], "years": 10, "humans": True},
           "balanced": {"types": ["systematic_review", "meta_analysis", "rct", "guideline"], "years": 15, "humans": True},
           "broad": {"types": [], "years": None, "humans": False}}


def clean_filters(f: Any) -> dict:
    """Keep only the known PubMed filter values (SPEC §12.1); `f` that is not a dict counts as {}:
    {"types": [t for t in FILTER_TYPES if t in f types], "years": years when an int 1–50 else None,
     "humans": bool(f humans), "languages": [x for x in FILTER_LANGS if x in f languages]}
    """
    if not isinstance(f, dict):
        f = {}
    preset = f.get("preset")
    if preset in PRESETS:
        base = {**f, **PRESETS[preset]}
        result = {
            "types": [t for t in FILTER_TYPES if t in base.get("types", [])],
            "years": base["years"] if isinstance(base.get("years"), int) and 1 <= base["years"] <= 50 else None,
            "humans": bool(base.get("humans")),
            "languages": [x for x in FILTER_LANGS if x in f.get("languages", [])],
            "preset": preset,
        }
    elif preset == "custom":
        result = {
            "types": [t for t in FILTER_TYPES if t in f.get("types", [])],
            "years": f["years"] if isinstance(f.get("years"), int) and 1 <= f["years"] <= 50 else None,
            "humans": bool(f.get("humans")),
            "languages": [x for x in FILTER_LANGS if x in f.get("languages", [])],
            "preset": "custom",
        }
    else:
        result = {
            "types": [t for t in FILTER_TYPES if t in f.get("types", [])],
            "years": f["years"] if isinstance(f.get("years"), int) and 1 <= f["years"] <= 50 else None,
            "humans": bool(f.get("humans")),
            "languages": [x for x in FILTER_LANGS if x in f.get("languages", [])],
        }
    return result


def config(db: Any, username: str, profile: str) -> dict:
    """{"profile", "instructions", "web", "sources": [...]} for one user's profile; seeds the default cards once.

    TODO (task B08). st = db.get_profile_settings(username, profile); if not st["seeded"]: for each card in
    SEED.get(profile, []): db.add_source(username, profile, card kind, card title, domain=card.get("domain", ""),
    note=card note, filters=card.get("filters")); then db.set_profile_settings(username, profile, seeded=True).
    Return {"profile": profile, "instructions": st["instructions"], "web": st["web"], "sources": db.list_sources(...)}.
    """
    st = db.get_profile_settings(username, profile)
    if not st["seeded"]:
        for card in SEED.get(profile, []):
            db.add_source(username, profile, card["kind"], card["title"],
                          domain=card.get("domain", ""), note=card["note"],
                          filters=card.get("filters"))
        db.set_profile_settings(username, profile, seeded=True)
    return {"profile": profile, "instructions": st["instructions"], "web": st["web"],
            "sources": db.list_sources(username, profile)}


def system_prompt(cfg: dict) -> str:
    """The profile's system prompt (SPEC §12.2). Parts joined with "\n\n":

    TODO (task B08).
      1. context.SYSTEM_PROMPTS[cfg["profile"]]
      2. if instructions.strip(): "Extra instructions from the user:\n" + instructions.strip()
      3. lines, per enabled source that is not a file:
           pubmed -> "- PubMed (tool search_pubmed)", website -> f"- the website {domain} (tool search_site)",
           + f": {note.strip()}" when the note is not empty;
         and when cfg["web"]: "- the web (tools web_search and open_page), when a question needs current or outside facts".
         If there are lines: "Sources you can use:\n" + "\n".join(lines) + "\n" + CITE
      4. per enabled file: f"Document {title}" + (f" ({note.strip()})" if note) + f":\n```\n{content}\n```"
    """
    prompt = context.SYSTEM_PROMPTS[cfg["profile"]]
    instructions = cfg.get("instructions", "")
    if instructions.strip():
        prompt += "\n\nExtra instructions from the user:\n" + instructions.strip()

    lines = []
    for s in cfg.get("sources", []):
        if not s.get("enabled", True):
            continue
        if s["kind"] == "file":
            continue
        if s["kind"] == "pubmed":
            line = "- PubMed (tool search_pubmed)"
        elif s["kind"] == "website":
            line = f"- the website {s['domain']} (tool search_site)"
        else:
            line = f"- {s['title']}"
        note = s.get("note", "")
        if note and note.strip():
            line += f": {note.strip()}"
        lines.append(line)

    if cfg.get("web"):
        lines.append("- the web (tools web_search and open_page), when a question needs current or outside facts")

    if lines:
        prompt += "\n\nSources you can use:\n" + "\n".join(lines) + "\n" + CITE

    for s in cfg.get("sources", []):
        if not s.get("enabled", True):
            continue
        if s["kind"] == "file":
            title = s["title"]
            note = s.get("note", "")
            content = s.get("content", "")
            part = f"Document {title}"
            if note and note.strip():
                part += f" ({note.strip()})"
            part += f":\n```\n{content}\n```"
            prompt += "\n\n" + part

    return prompt


def budget(cfg: dict, settings: Any) -> dict:
    """How much of the context the profile itself takes: {"used", "max", "parts": [{"label", "tokens"}]}.

    TODO (task B08). docs = enabled file sources; total = tokens(system_prompt(cfg));
    per doc: {"label": title, "tokens": tokens(system_prompt({**cfg, "sources": [doc], "web": False, "instructions": ""}))
              - tokens(context.SYSTEM_PROMPTS[cfg["profile"]]) - 1};
    parts = [{"label": "Instructions", "tokens": max(0, total - sum of the doc tokens)}] + the doc parts;
    return {"used": total, "max": context.ctx_max(settings), "parts": parts}
    """
    docs = [s for s in cfg.get("sources", []) if s.get("enabled", True) and s["kind"] == "file"]
    total = tokens(system_prompt(cfg))
    base_tokens = tokens(context.SYSTEM_PROMPTS[cfg["profile"]])
    doc_parts = []
    for doc in docs:
        prompt_with_doc = system_prompt({**cfg, "sources": [doc], "web": False, "instructions": ""})
        doc_tokens = tokens(prompt_with_doc) - base_tokens - 1
        doc_parts.append({"label": doc["title"], "tokens": max(0, doc_tokens)})
    instruction_tokens = max(0, total - sum(dp["tokens"] for dp in doc_parts))
    parts = [{"label": "Instructions", "tokens": instruction_tokens}] + doc_parts
    return {"used": total, "max": context.ctx_max(settings), "parts": parts}


def public(cfg: dict, settings: Any) -> dict:
    """The profile as the API returns it (SPEC §12.1).

    TODO (task B08). sources: per source {k: s[k] for k in ("id", "kind", "title", "domain", "note", "filters", "enabled",
    "created")} + "tokens": tokens(content) for files, else 0 (never the content itself). Return {"profile", "name":
    NAMES[profile], "instructions", "web", "sources", "budget": budget(cfg, settings)}.
    """
    profile = cfg["profile"]
    sources_out = []
    for s in cfg.get("sources", []):
        out = {k: s[k] for k in ("id", "kind", "title", "domain", "note", "filters", "enabled", "created")}
        if s["kind"] == "file":
            out["tokens"] = tokens(s.get("content", ""))
        else:
            out["tokens"] = 0
        sources_out.append(out)
    return {"profile": profile, "name": NAMES[profile], "instructions": cfg["instructions"],
            "web": cfg["web"], "sources": sources_out, "budget": budget(cfg, settings)}


MAX_DOC_BYTES = 5_000_000
MAX_DOC_CHARS = 200_000


class DocumentError(Exception):
    """The upload is not a document we can read; `status` is the HTTP status to answer (413 or 415)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def document_text(name: str, data: bytes) -> str:
    """The text of an uploaded document (SPEC §12.1).

    TODO (task B09). Tests: test_step_profiles.py::test_document_text
      1. len(data) > MAX_DOC_BYTES -> DocumentError(413, "That document is too big (max 5 MB).")
      2. a name ending in .pdf, or data starting with b"%PDF-": import io, pypdf; reader = pypdf.PdfReader(io.BytesIO(data));
         text = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip();
         any exception -> DocumentError(415, "That PDF could not be read."); empty text -> DocumentError(415,
         "That PDF has no text (is it a scan?).")
      3. else: data.decode("utf-8") (UnicodeDecodeError) or a "\x00" in it -> DocumentError(415, "Only PDF, text, Markdown
         and HTML documents."); a name ending in .html/.htm -> text = tools.html_text(text)[1] (import tools inside)
      4. len(text) > MAX_DOC_CHARS -> DocumentError(413, "That document is too long (max 200 000 characters).")
    """
    if len(data) > MAX_DOC_BYTES:
        raise DocumentError(413, "That document is too big (max 5 MB).")

    if name.endswith(".pdf") or data.startswith(b"%PDF-"):
        import io
        import pypdf
        try:
            reader = pypdf.PdfReader(io.BytesIO(data))
            text = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
        except Exception:
            raise DocumentError(415, "That PDF could not be read.")
        if not text:
            raise DocumentError(415, "That PDF has no text (is it a scan?).")
        return text

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise DocumentError(415, "Only PDF, text, Markdown and HTML documents.")
    if "\x00" in text:
        raise DocumentError(415, "Only PDF, text, Markdown and HTML documents.")

    if name.endswith((".html", ".htm")):
        from . import tools
        _, text = tools.html_text(text)

    if len(text) > MAX_DOC_CHARS:
        raise DocumentError(413, "That document is too long (max 200 000 characters).")

    return text
