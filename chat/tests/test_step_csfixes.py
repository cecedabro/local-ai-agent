"""Clear Source fixes after the first real-model test (2026-11-20). Protected: change the code, not this file."""
from __future__ import annotations

import asyncio

import httpx

from coder import research, tools, verify


def cfg():
    src = [{"kind": "pubmed", "enabled": True, "domain": "", "filters": {}},
           {"kind": "website", "enabled": True, "domain": "thuisarts.nl", "filters": {}}]
    return {"profile": "evidence", "instructions": "", "web": True, "sources": src}


def test_searches_written_as_text():
    text = '{"about": "x", "searches": ["erythromelalgie site:thuisarts.nl", "erythromelalgia therapy site:pubmed.gov", ' \
           '"rode voeten site:evil.example", "aspirin dose children", "junk"]}'
    assert research.parse_plan(text, cfg()) == [
        {"source": "site:thuisarts.nl", "query": "erythromelalgie"},
        {"source": "pubmed", "query": "erythromelalgia therapy"},
        {"source": "web", "query": "rode voeten"},
        {"source": "pubmed", "query": "aspirin dose children"}]


def test_lead_ins_and_notes_about_the_sources_are_not_claims():
    a = ("## Richtlijnen\nDaarnaast worden specifieke medicijngroepen aanbevolen, met een belangrijke uitzondering:\n"
         '> "Thiazide diuretica en ACE-remmers worden aanbevolen voor het behandelen van hypertensie." [1]\n\n'
         "De bronnen geven geen specifieke informatie over doseringen bij kinderen of zwangeren.\n"
         "Apixaban verlaagt het risico op een beroerte met dertig procent bij oudere patiënten.")
    assert verify.check(a, [{"n": 1, "text": "Thiazide diuretica en ACE-remmers worden aanbevolen voor het behandelen "
                                              "van hypertensie."}])["uncited"] == [
        "Apixaban verlaagt het risico op een beroerte met dertig procent bij oudere patiënten."]


def test_ncbi_retries_a_429(monkeypatch):
    calls = []

    def handler(req):
        calls.append(dict(req.url.params))
        return httpx.Response(429 if len(calls) < 3 else 200, json={"ok": 1})

    monkeypatch.setattr(tools, "PUBMED", "http://127.0.0.1:9")        # local: no waiting between tries

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await tools.ncbi_get(c, "/esearch.fcgi", {"db": "pubmed", "term": "x"})
    r = asyncio.run(go())
    assert r.status_code == 200 and len(calls) == 3 and calls[0]["tool"] == "dewdrop"


def test_a_comment_after_a_quote_is_not_part_of_it():
    a = ('> "Behandel bij risicogroepen direct met een antibioticum om complicaties te voorkomen." [1]\n'
         "> De bron benadrukt dat zwangeren tot de risicogroep behoren [1].\n"
         '> "Bij zwangeren zeven dagen behandelen met nitrofurantoïne." [2]\n\n'
         "> A passage over two lines\n> without quote marks [1]")
    assert verify.quotes(a) == [
        {"text": "Behandel bij risicogroepen direct met een antibioticum om complicaties te voorkomen.", "cites": [1]},
        {"text": "Bij zwangeren zeven dagen behandelen met nitrofurantoïne.", "cites": [2]},
        {"text": "A passage over two lines without quote marks", "cites": [1]}]


def test_focus_finds_the_section_far_down_a_long_page():
    text = "Ibuprofen, de inleiding.\n\n" + "\n\n".join(f"Alinea {i}: ibuprofen dosering bij volwassenen. " * 8 for i in range(80)) \
        + "\n\nZwangerschap\n\nIbuprofen passeert de placenta.\n\nLactatie\n\nGaat over in de moedermelk."
    got = research.focus(text, "ibuprofen zwangerschap veilig")
    assert len(got) <= research.SOURCE_CHARS and got.startswith("Ibuprofen, de inleiding.")
    assert "Zwangerschap\n\nIbuprofen passeert de placenta." in got, "the rare word wins over the word on every paragraph"
    assert research.focus("kort", "x y z") == "kort"
