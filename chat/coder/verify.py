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
    r"""TODO (task E02). True when the quote is in the text; an ellipsis splits it into parts that must appear in order (SPEC §13.1).
    Steps: hay = normalize(text)
           parts = [normalize(p).strip(" '\".,;:") for p in _ELLIPSIS.split(quote or "")]   (_ELLIPSIS: ... … [...])
           keep only parts of 3+ characters; none left -> False
           pos = 0; for each part: i = hay.find(part, pos); i < 0 -> False; pos = i + len(part)
           return True
    Tests: test_step_verify.py::test_find_quote_exact_and_tolerant and ::test_find_quote_catches_changes
    """
    raise NotImplementedError


def cites_in(text: str) -> list[int]:
    r"""TODO (task E03). Every [n] (also [1, 2]) in the text, in order, without duplicates: for each _CITE match split group(1) on ","
    and append int(x) when it is not in the list yet.
    """
    raise NotImplementedError


def _strip_marks(t: str) -> str:
    r"""TODO (task E03). A quote's text without its [n] and its quote marks: _CITE.sub("", t).strip(), then .strip(' "“”\'‘’').strip().
    """
    raise NotImplementedError


def quotes(answer: str) -> list[dict]:
    r"""TODO (task E03). Blockquote groups and inline quotes of 4+ words, in order, as {"text", "cites"} (SPEC §13.1).
    Line by line over answer.split("\n"):
      - a line starting with ``` toggles "in code"; lines in code are skipped
      - a line whose lstrip() starts with ">": collect it and the following ">" lines (the text after ">", stripped);
        raw = " ".join(group); append {"text": _strip_marks(raw), "cites": cites_in(raw)}
      - otherwise each _INLINE match (group 1 or 2 is the quote) with 4+ words: cites = cites_in of a [..] right after the
        closing quote (re.match(r"\s*(\[(\d+(?:\s*,\s*\d+)*)\])", rest of the line)), else []; append {"text": q.strip(), "cites"}
    Tests: test_step_verify.py::test_quotes
    """
    raise NotImplementedError


def sentences(answer: str) -> list[dict]:
    r"""TODO (task E03). Sentences outside quotes, headings and code, as {"text", "cites"} (SPEC §13.1).
    Line by line: skip code (``` toggles), empty lines and lines starting with ">" or "#"; drop a list marker
    (r"^([-*+]|\d+\.)\s+"); replace each inline quote (_INLINE) by '"q"'; split on r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[*])";
    each non-empty part -> {"text": part, "cites": cites_in(part)}
    Tests: test_step_verify.py::test_sentences
    """
    raise NotImplementedError


def check(answer: str, sources: list[dict]) -> dict:
    r"""TODO (task E04). The report for an answer and its sources ([{"n", "text"}]), SPEC §13.1.
      by_n = {int(s["n"]): s.get("text") or "" for s in sources}
      quotes: each q in quotes(answer): no cites -> {**q, "ok": False, "why": "no source"}; else ok = any(find_quote(q["text"],
        by_n[n]) for n in q["cites"] if n in by_n), why = "" if ok else "not in source " + ", ".join(the cites)
      uncited: over sents = sentences(answer): 8+ words (len(re.findall(r"\w+", text))) and no cites -> listed, EXCEPT the
        last sentence when it contains one of STRENGTH_WORDS (after normalize): the closing line on evidence strength
      bad_refs: every n cited in quotes or sentences that is not in by_n, sorted
      ok = all quotes ok and no bad_refs; return {"ok", "quotes", "uncited", "bad_refs"}
    Tests: test_step_verify.py::test_check_report, ::test_check_bad_refs_and_no_source, ::test_check_uncited_claims_are_strict
    """
    raise NotImplementedError
