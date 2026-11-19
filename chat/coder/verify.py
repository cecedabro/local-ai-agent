"""The checker: every quote in an answer must really be in a source it cites (chat/SPEC.md §13.1).

Pure functions, no I/O. Tests: chat/tests/test_step_verify.py
"""
from __future__ import annotations

import re

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "„": '"', "–": "-", "—": "-", "−": "-", "­": None})
_ELLIPSIS = re.compile(r"\.\.\.|…|\[\s*(?:\.\.\.|…)\s*\]")
_CITE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
_INLINE = re.compile(r'"([^"\n]+)"|“([^”\n]+)”')
STRENGTH_WORDS = ("evidence", "bewijs", "bewijskracht", "studies", "onderzoek")


def normalize(text: str) -> str:
    r"""Lower case, straight quotes, plain dashes, no soft hyphens, single spaces, stripped (SPEC §13.1).
    Steps: t = (text or "").translate(_QUOTES).lower()   (_QUOTES maps '' ' '""„ to ' " and –—− to -, drops the soft hyphen)
           return re.sub(r"\s+", " ", t).strip()
    Tests: test_step_verify.py::test_normalize
    """
    t = (text or "").translate(_QUOTES).lower()
    return re.sub(r"\s+", " ", t).strip()


def find_quote(quote: str, text: str) -> bool:
    r"""True when the quote is in the text; an ellipsis splits it into parts that must appear in order (SPEC §13.1).
    Steps: hay = normalize(text)
           parts = [normalize(p).strip(" '\".,;:") for p in _ELLIPSIS.split(quote or "")]   (_ELLIPSIS: ... … [...])
           keep only parts of 3+ characters; none left -> False
           pos = 0; for each part: i = hay.find(part, pos); i < 0 -> False; pos = i + len(part)
            return True
    Tests: test_step_verify.py::test_find_quote_exact_and_tolerant and ::test_find_quote_catches_changes
    """
    hay = normalize(text)
    parts = [normalize(p).strip(" '\".,;:") for p in _ELLIPSIS.split(quote or "")]
    parts = [p for p in parts if len(p) >= 3]
    if not parts:
        return False
    pos = 0
    for part in parts:
        i = hay.find(part, pos)
        if i < 0:
            return False
        pos = i + len(part)
    return True


def cites_in(text: str) -> list[int]:
    r"""Every [n] (also [1, 2]) in the text, in order, without duplicates: for each _CITE match split group(1) on ","
    and append int(x) when it is not in the list yet.
    """
    result: list[int] = []
    for m in _CITE.finditer(text):
        for x in m.group(1).split(","):
            n = int(x)
            if n not in result:
                result.append(n)
    return result


def _strip_marks(t: str) -> str:
    r"""A quote's text without its [n] and its quote marks: _CITE.sub("", t).strip(), then .strip(' "“”\'‘’').strip().
    """
    return _CITE.sub("", t).strip().strip(' "“”\'‘’').strip()


def quotes(answer: str) -> list[dict]:
    r"""Blockquote groups and inline quotes of 4+ words, in order, as {"text", "cites"} (SPEC §13.1).
    Line by line over answer.split("\n"):
      - a line starting with ``` toggles "in code"; lines in code are skipped
      - a line whose lstrip() starts with ">": collect it and the following ">" lines (the text after ">", stripped);
        raw = " ".join(group); append {"text": _strip_marks(raw), "cites": cites_in(raw)}
      - otherwise each _INLINE match (group 1 or 2 is the quote) with 4+ words: cites = cites_in of a [..] right after the
        closing quote (re.match(r"\s*(\[(\d+(?:\s*,\s*\d+)*)\])", rest of the line)), else []; append {"text": q.strip(), "cites"}
    """
    result: list[dict] = []
    in_code = False
    i = 0
    lines = answer.split("\n")
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            in_code = not in_code
            i += 1
            continue
        if not in_code and line.lstrip().startswith(">"):
            group_lines: list[str] = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                group_lines.append(lines[i].lstrip()[1:].strip())
                i += 1
            raw = " ".join(group_lines)
            result.append({"text": _strip_marks(raw), "cites": cites_in(raw)})
            continue
        if not in_code:
            for m in _INLINE.finditer(line):
                q = m.group(1) or m.group(2)
                if len(q.split()) >= 4:
                    rest = line[m.end():]
                    cite_m = re.match(r"\s*(\[(\d+(?:\s*,\s*\d+)*)\])", rest)
                    cites = cites_in(cite_m.group(1)) if cite_m else []
                    result.append({"text": q.strip(), "cites": cites})
        i += 1
    return result


def sentences(answer: str) -> list[dict]:
    r"""Sentences outside quotes, headings and code, as {"text", "cites"} (SPEC §13.1).
    Line by line: skip code (``` toggles), empty lines and lines starting with ">" or "#"; drop a list marker
    (r"^([-*+]|\d+\.)\s+"); replace each inline quote (_INLINE) by '"q"'; split on r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[*])";
    each non-empty part -> {"text": part, "cites": cites_in(part)}
    """
    result: list[dict] = []
    in_code = False
    _SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[*])")
    for line in answer.split("\n"):
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line.strip() or line.lstrip().startswith(">") or line.lstrip().startswith("#"):
            continue
        text = re.sub(r"^([-*+]|\d+\.)\s+", "", line.strip())
        text = _INLINE.sub('"q"', text)
        for part in _SENTENCE_SPLIT.split(text):
            part = part.strip()
            if part:
                result.append({"text": part, "cites": cites_in(part)})
    return result


# Sections that summarise the quotes below them (SPEC §15.5): their sentences need no [n].
SUMMARY_HEADINGS = re.compile(r"short answer|kort antwoord|summary|samenvatting|conclusi|strength|sterkte|bewijskracht", re.I)


def summary_sentences(answer: str) -> list[str]:
    r"""TODO (task F16). The sentences that summarise the quotes and need no [n] (SPEC §15.5), in order.
      no line of the answer starts with "#" (re.search(r"^\s*#", answer, re.M) is None) -> []
      Split the answer into sections at heading lines (a line whose lstrip() starts with "#", outside ``` code blocks; a line
      starting with ``` toggles code). The text before the first heading is a section with the name "". A section's name is
      its heading line with the "#"s and spaces stripped from the left, then stripped.
      For each section whose name is "" or matches SUMMARY_HEADINGS: add [x["text"] for x in sentences(the section's lines joined by "\n")].
    Tests: test_step_flow.py::test_summary_sentences
    """
    raise NotImplementedError


def check(answer: str, sources: list[dict]) -> dict:
    r"""The report for an answer and its sources ([{"n", "text"}]), SPEC §13.1.
      by_n = {int(s["n"]): s.get("text") or "" for s in sources}
      quotes: each q in quotes(answer): no cites -> {**q, "ok": False, "why": "no source"}; else ok = any(find_quote(q["text"],
        by_n[n]) for n in q["cites"] if n in by_n), why = "" if ok else "not in source " + ", ".join(the cites)
      uncited: over sents = sentences(answer): 8+ words (len(re.findall(r"\w+", text))) and no cites -> listed, EXCEPT the
        last sentence when it contains one of STRENGTH_WORDS (after normalize): the closing line on evidence strength
      bad_refs: every n cited in quotes or sentences that is not in by_n, sorted
      ok = all quotes ok and no bad_refs; return {"ok", "quotes", "uncited", "bad_refs"}
    Tests: test_step_verify.py::test_check_report, ::test_check_bad_refs_and_no_source, ::test_check_uncited_claims_are_strict
    """
    by_n = {int(s["n"]): s.get("text") or "" for s in sources}
    quoted: list[dict] = []
    cited_nums: set[int] = set()
    for q in quotes(answer):
        cited_nums.update(q["cites"])
        if not q["cites"]:
            quoted.append({**q, "ok": False, "why": "no source"})
        else:
            ok = any(find_quote(q["text"], by_n[n]) for n in q["cites"] if n in by_n)
            cited = [n for n in q["cites"] if n in by_n]
            why = "" if ok else "not in source " + ", ".join(str(c) for c in cited)
            quoted.append({**q, "ok": ok, "why": why})
    sents = sentences(answer)
    # TODO (task F16), SPEC §15.5: summary = set(summary_sentences(answer)); a sentence in it is never listed as uncited.
    #   Tests: test_step_flow.py::test_summary_sentences
    uncited: list[str] = []
    for idx, s in enumerate(sents):
        cited_nums.update(s["cites"])
        if not s["cites"] and len(re.findall(r"\w+", s["text"])) >= 8:
            if idx == len(sents) - 1 and any(w in normalize(s["text"]) for w in STRENGTH_WORDS):
                continue
            uncited.append(s["text"])
    bad_refs = sorted(n for n in cited_nums if n not in by_n)
    ok = all(q["ok"] for q in quoted) and not bad_refs
    return {"ok": ok, "quotes": quoted, "uncited": uncited, "bad_refs": bad_refs}
