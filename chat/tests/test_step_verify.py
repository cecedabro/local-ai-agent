"""Judge for the checker, chat/coder/verify.py (chat/SPEC.md §13.1). Protected. The hard cases are the ones a
medical answer must survive: changed numbers, dropped "not", half quotes, other quote marks, wrong source.

Run one: make chat-test K="test_step_verify and normalize"
"""
from __future__ import annotations

from coder import verify

ABSTRACT_1 = ("Background: Atrial fibrillation (AF) increases stroke risk. Methods: In this randomised trial of 18 201 "
              "patients, apixaban 5 mg twice daily was compared with warfarin. Results: Apixaban reduced stroke or "
              "systemic embolism by 21% (HR 0.79; 95% CI 0.66–0.95) and caused less major bleeding. Conclusions: "
              "Apixaban was superior to warfarin in preventing stroke or systemic embolism.")
ABSTRACT_2 = ("Metformin is not recommended when eGFR is below 30 mL/min/1.73 m²; the dose should be reduced when eGFR "
              "is 30–45. Lactic acidosis is rare.")
SOURCES = [{"n": 1, "text": ABSTRACT_1}, {"n": 2, "text": ABSTRACT_2}]


# ---------------------------------------------------------------- V01 normalize
def test_normalize():
    assert verify.normalize("  “Apixaban”  Was\n\tSUPERIOR — to  ‘warfarin’ ") == '"apixaban" was superior - to \'warfarin\''
    assert verify.normalize("ef­fect 0.66–0.95") == "effect 0.66-0.95"
    assert verify.normalize("") == "" and verify.normalize(None) == ""


# ---------------------------------------------------------------- V02 find_quote
def test_find_quote_exact_and_tolerant():
    assert verify.find_quote("Apixaban was superior to warfarin in preventing stroke", ABSTRACT_1)
    assert verify.find_quote("apixaban WAS superior to   warfarin", ABSTRACT_1), "case and spaces do not matter"
    assert verify.find_quote("HR 0.79; 95% CI 0.66-0.95", ABSTRACT_1), "– and - are the same"
    assert verify.find_quote("Apixaban reduced stroke … by 21%", ABSTRACT_1), "an ellipsis skips text, in order"
    assert verify.find_quote("In this randomised trial [...] apixaban 5 mg twice daily", ABSTRACT_1)


def test_find_quote_catches_changes():
    assert not verify.find_quote("Apixaban reduced stroke or systemic embolism by 31%", ABSTRACT_1), "a changed number"
    assert not verify.find_quote("Metformin is recommended when eGFR is below 30", ABSTRACT_2), "a dropped 'not'"
    assert not verify.find_quote("apixaban 10 mg twice daily", ABSTRACT_1), "a changed dose"
    assert not verify.find_quote("by 21% … Apixaban reduced stroke", ABSTRACT_1), "parts out of order"
    assert not verify.find_quote("…", ABSTRACT_1) and not verify.find_quote("", ABSTRACT_1), "nothing left to check"


# ---------------------------------------------------------------- V03 quotes, sentences
ANSWER = """**Kort:** apixaban werkt beter dan warfarine [1].

> "Apixaban was superior to warfarin in preventing stroke or systemic embolism." [1]

Bij nierfunctiestoornissen geldt voor metformine: "is not recommended when eGFR is below 30" [2], en volgens
de bron wordt de dosis verlaagd bij een eGFR tussen 30 en 45.

> “Apixaban reduced stroke or systemic embolism by 31%” [1]

Het bewijs is sterk: één grote gerandomiseerde studie."""


def test_quotes():
    q = verify.quotes(ANSWER)
    assert [x["text"] for x in q] == [
        "Apixaban was superior to warfarin in preventing stroke or systemic embolism.",
        "is not recommended when eGFR is below 30",
        "Apixaban reduced stroke or systemic embolism by 31%"]
    assert [x["cites"] for x in q] == [[1], [2], [1]]
    assert verify.quotes('He said "short one" [1].') == [], "inline quotes under 4 words are not checked"
    assert verify.quotes("```\n> not a quote\n```") == [], "code is not checked"
    assert verify.quotes('> "Two numbers [1, 2]"')[0]["cites"] == [1, 2]


def test_sentences():
    s = verify.sentences(ANSWER)
    texts = [x["text"] for x in s]
    assert texts[0].startswith("**Kort:** apixaban werkt beter dan warfarine [1].") and s[0]["cites"] == [1]
    assert not any(t.startswith(">") for t in texts), "quotes are not sentences"
    assert verify.sentences("# Heading\nPlain sentence one. Second one [2].") == [
        {"text": "Plain sentence one.", "cites": []}, {"text": "Second one [2].", "cites": [2]}]


# ---------------------------------------------------------------- V04 check
def test_check_report():
    r = verify.check(ANSWER, SOURCES)
    assert [q["ok"] for q in r["quotes"]] == [True, True, False]
    assert r["quotes"][2]["why"] == "not in source 1"
    assert r["ok"] is False and r["bad_refs"] == []
    assert any("de dosis verlaagd" in u for u in r["uncited"]), "a claim of 8+ words without [n] is listed"
    assert not any("Het bewijs is sterk" in u for u in r["uncited"]), "the closing line on evidence strength may go without [n]"


def test_check_bad_refs_and_no_source():
    r = verify.check('Ze is effectief [3].\n\n> "Lactic acidosis is rare."', SOURCES)
    assert r["bad_refs"] == [3] and r["ok"] is False
    assert r["quotes"][0]["ok"] is False and r["quotes"][0]["why"] == "no source"
    good = verify.check('> "Lactic acidosis is rare." [2]\n\nHet bewijs is beperkt tot één bron.', SOURCES)
    assert good == {"ok": True, "quotes": [{"text": "Lactic acidosis is rare.", "cites": [2], "ok": True, "why": ""}],
                    "uncited": [], "bad_refs": []}


def test_check_uncited_claims_are_strict():
    r = verify.check("Studies show that vitamin D clearly helps against winter depression in adults. "
                     "Het bewijs is zwak.", SOURCES)
    assert r["uncited"] == ["Studies show that vitamin D clearly helps against winter depression in adults."], \
        "only the closing strength line is exempt, not any sentence that mentions studies"
