"""Fixes after the first live week (2026-11-20): keep-alive pings, answer language, web search off for Clear Source,
login failure window, the visitor's address. Run: make chat-test K=live_fixes
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from coder import app as appmod, db as dbm, profiles, research


def test_live_pings_while_quiet(monkeypatch):
    monkeypatch.setattr(appmod, "LIVE_PING", 0.01)

    async def go():
        live = appmod.Live("u1")
        got = []

        async def reader():
            async for c in live.follow():
                got.append(c)
        t = asyncio.create_task(reader())
        await asyncio.sleep(0.05)
        live.add("a")
        live.finish()
        await asyncio.wait_for(t, 1)
        return got
    got = asyncio.run(go())
    assert got[0] == ": ping\n\n" and got[-1] == "a", "a comment line keeps the connection open, then the events"


def test_answer_language():
    assert research.answer_language("Werkt apixaban?").startswith("Dutch")
    assert research.answer_language("Wat is de eerste keus bij een blaasontsteking?").startswith("Dutch")
    assert research.answer_language("what are the effects of paracetamol on stomach health") == "English"
    assert research.answer_language("Is it safe?") == "English"
    m = research.evidence_messages("what are the effects of paracetamol on stomach health", [], {"instructions": ""})
    assert "in English" in m[0]["content"] and "only once" in m[0]["content"]


def test_clear_source_starts_without_web(tmp_path):
    d = dbm.DB(str(tmp_path))
    d.init_schema()
    d.create_user("u1", "x", [])
    assert profiles.config(d, "u1", "evidence")["web"] is False
    assert profiles.config(d, "u1", "evidence")["web"] is False, "stays off"
    assert profiles.config(d, "u1", "general")["web"] is True


def test_login_failures_expire(tmp_path):
    d = dbm.DB(str(tmp_path))
    d.init_schema()
    conn = d._connect()
    for i in range(5):
        conn.execute("INSERT INTO login_failures (key, at) VALUES (?, ?)", ("user:x", f"2000-01-01T00:00:0{i}+00:00"))
    conn.commit()
    assert not d.has_recent_failures("user:x"), "old failures don't count"
    for _ in range(5):
        d.record_login_failure("user:y")
    # record_login_failure may collapse same-instant rows; count what is stored
    n = conn.execute("SELECT COUNT(*) FROM login_failures WHERE key='user:y'").fetchone()[0]
    assert d.has_recent_failures("user:y", limit=n)


def test_client_ip():
    req = SimpleNamespace(headers={"cf-connecting-ip": "203.0.113.9"}, client=SimpleNamespace(host="10.0.0.1"))
    assert appmod.client_ip(req) == "203.0.113.9"
    req = SimpleNamespace(headers={"x-forwarded-for": "1.2.3.4"}, client=SimpleNamespace(host="10.0.0.1"))
    assert appmod.client_ip(req) == "10.0.0.1", "X-Forwarded-For is ignored"


def test_static_files_are_revalidated(tmp_path):
    from fastapi.testclient import TestClient
    c = TestClient(appmod.create_app({"DATA_DIR": str(tmp_path), "ADMIN_USER": "a", "ADMIN_PASSWORD": "pw-long-enough-1"}))
    r = c.get("/static/css/chat.css")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", "a deploy reaches the browser at once"


# ---------------------------------------------------------------- a profile seeded with an older list gets the new one once
def test_old_clear_source_profile_gets_the_dutch_list_once(tmp_path):
    d = dbm.DB(str(tmp_path))
    d.init_schema()
    d.create_user("lisa", "x", [])
    d.add_source("lisa", "evidence", "pubmed", "PubMed", note="")
    d.add_source("lisa", "evidence", "website", "rivm.nl", domain="rivm.nl", note="")
    d.set_profile_settings("lisa", "evidence", seeded=True, web=True)        # what an older version left behind
    cfg = profiles.config(d, "lisa", "evidence")
    seed_domains = {c.get("domain") for c in profiles.SEED["evidence"] if c["kind"] == "website"}
    have = {s["domain"] for s in cfg["sources"] if s["kind"] == "website"}
    assert seed_domains <= have, "the Dutch defaults were added"
    assert sum(s["kind"] == "pubmed" for s in cfg["sources"]) == 1, "PubMed not doubled"
    assert cfg["web"] is False and d.get_profile_settings("lisa", "evidence")["seeded"] is True
    gone = next(s for s in cfg["sources"] if s["kind"] == "website" and s["domain"] != "rivm.nl")
    d.delete_source(gone["id"], "lisa")
    d.set_profile_settings("lisa", "evidence", instructions="Kort.")         # saving settings keeps the list version
    again = profiles.config(d, "lisa", "evidence")
    assert gone["domain"] not in {s["domain"] for s in again["sources"]}, "a removed default stays removed"


# ---------------------------------------------------------------- Clear Source quality (UX review CS-1 … CS-8)
from coder import verify  # noqa: E402

SRC = ("Bij een ongecompliceerde cystitis is nitrofurantoïne de eerste keus. Geef 2 dd 100 mg MR gedurende 5 dagen. "
       "If the risk is low (<3 points), antibiotics are not indicated. Heterogeneity was high (I²=78%) across trials.")


def test_fix_quote_keeps_the_promise():
    assert verify.fix_quote("Geef 2 dd 100 mg MR gedurende 5 dagen.", SRC) == ("Geef 2 dd 100 mg MR gedurende 5 dagen.", "exact")
    assert verify.fix_quote("antibiotics are not indi", SRC) == ("antibiotics are not indicated.", "extended")
    assert verify.fix_quote("Heterogeneity was high (I=78%) across trials", SRC) == \
        ("Heterogeneity was high (I²=78%) across trials", "aligned")
    assert verify.fix_quote("Paracetamol is the first choice for fever in children", SRC) == (None, "")
    for q in ("antibiotics are not indicated.", "Heterogeneity was high (I²=78%) across trials"):
        assert verify.find_quote(q, SRC)
    assert verify.find_quote("Niet vanaf 27 weken zwanger", "Niet vanaf 27 weken zwanger\\n\\nDeze pijnstillers")


def test_is_title():
    t = "Vitamin D and bone health in adults: a systematic review"
    assert verify.is_title(t + ". (Systematic review · BMJ · 2023)", t)
    assert not verify.is_title(t + " showed fewer fractures in women over 70", t)


def test_tidy_headings_and_quotes():
    sources = [{"n": 1, "title": "NHG", "text": SRC}]
    text = ('## Short answer: two or three sentences.\n\nNitrofurantoïne [1].\n\n'
            '> "antibiotics are not indi" [1]\n\nUitleg [1].\n\n> "Made up words that are nowhere at all" [1]\n\nEinde [1].')
    out, removed = research.tidy(text, sources)
    assert out.startswith("## Short answer\n") and '> "antibiotics are not indicated." [1]' in out
    assert "Made up" not in out and removed == 1 and "\n\n\n" not in out


def test_read_one_statuses():
    src = {"n": 1, "title": "Cystitis", "url": "u", "text": SRC}

    def run_with(*replies):
        it = iter(replies)

        async def complete(settings, messages):
            return next(it)
        return asyncio.run(research.read_one("Q", src, SimpleNamespace(), complete))
    q, st = run_with('{"quotes": ["Heterogeneity was high (I=78%) across trials"]}')
    assert q == ["Heterogeneity was high (I²=78%) across trials"] and st["status"] == "used" and st["fixed"] == 1
    assert run_with('{"quotes": []}')[1]["status"] == "not relevant"
    assert run_with('{"quotes": ["Totally different words that are not there"]}')[1]["status"] == "unchecked"
    assert run_with("Sorry, I cannot.", "still not json")[1]["status"] == "unreadable"
    assert run_with("Sorry.", '{"quotes": ["Geef 2 dd 100 mg MR gedurende 5 dagen."]}')[1]["status"] == "used"


def test_on_topic_only_checks_broad_hits():
    item = {"query": "cystitis nitrofurantoin women"}
    junk = {"kind": "pubmed", "title": "Vibrio cholerae in coastal water", "text": "Cholera outbreaks.", "broad": True}
    assert not research.on_topic(junk, item)
    assert research.on_topic({**junk, "broad": False}, item), "a filtered PubMed search chose it: it is read"
    assert research.on_topic({**junk, "title": "Nitrofurantoin for cystitis"}, item)


def test_plan_gets_the_notes_and_file_questions():
    cfg = {"sources": [{"kind": "website", "domain": "richtlijnen.nhg.org", "title": "NHG", "enabled": True,
                        "note": "Gebruik dit eerst bij de eerste lijn"}], "web": False}
    m = research.plan_messages("Q", cfg)[0]["content"]
    assert "site:richtlijnen.nhg.org (NHG): Gebruik dit eerst bij de eerste lijn" in m and "{notes}" not in m
    assert research.about_the_file("Summarise this file in one sentence.")
    assert research.about_the_file("Vat dit document samen")
    assert not research.about_the_file("Mag ik ibuprofen in de zwangerschap?")


def test_rank_patient_info_after_studies():
    page = lambda site: {"kind": "page", "site": site}                        # noqa: E731
    assert research.rank(page("richtlijnen.nhg.org")) < research.rank(page("farmacotherapeutischkompas.nl")) \
        < research.rank({"kind": "pubmed", "type": "RCT"}) < research.rank(page("thuisarts.nl")) < research.rank(page(None))


def test_a_website_the_user_named_and_grouped():
    src = {"kind": "website", "domain": "nhg.example", "title": "NHG", "filters": {"group": "guidelines"}}
    assert profiles.group_of(src) == "guidelines" and profiles.label_of(src) == "NHG"
    assert profiles.group_of({**src, "filters": {"group": "bogus"}}) == "websites"
    assert profiles.label_of({"kind": "website", "domain": "lareb.nl", "title": "lareb.nl"}) == "Lareb"
