"""Judge for files in and out (chat/SPEC.md §16). Protected: change the code, not this file.

Run one: make chat-test K="test_step_files and blocks"
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from coder import answer, files, research


def err(fn, *a):
    with pytest.raises(files.FileError) as e:
        fn(*a)
    return e.value.status, str(e.value)


# ---------------------------------------------------------------- D01 reading uploads
def test_kind_of():
    assert [files.kind_of(n) for n in ("a.PDF", "b.docx", "c.xlsx", "d.htm", "e.html", "f.csv", "g.md", "h", "i.py")] == \
        ["pdf", "docx", "xlsx", "html", "html", "csv", "text", "text", "text"]
    assert err(files.kind_of, "old.doc") == (415, "Save it as .docx or .xlsx first.")
    assert err(files.kind_of, "old.XLS")[0] == 415


def test_check_zip():
    assert err(files.check_zip, b"not a zip", "Word") == (415, "That Word file could not be read.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("big.xml", b"0" * (files.MAX_UNZIPPED + 1))          # a zip bomb: tiny packed, huge unpacked
    assert len(buf.getvalue()) < 200_000
    assert err(files.check_zip, buf.getvalue(), "Excel") == (413, "That file is too big inside.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in range(files.MAX_ZIP_ENTRIES + 1):
            z.writestr(f"f{i}", b"")
    assert err(files.check_zip, buf.getvalue(), "Word")[0] == 413


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def docx(body: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {W}><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


def test_docx_text():
    body = ('<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Dosering</w:t></w:r></w:p>'
            '<w:p><w:r><w:t xml:space="preserve">Twee keer </w:t></w:r><w:r><w:t>per dag</w:t><w:tab/><w:t>oraal</w:t></w:r></w:p>'
            '<w:p></w:p>'
            '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/></w:numPr></w:pPr><w:r><w:t>met eten</w:t></w:r></w:p>'
            '<w:p><w:pPr><w:pStyle w:val="ListBullet"/></w:pPr><w:r><w:t>niet bij kinderen</w:t></w:r></w:p>'
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>eGFR</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Dosis</w:t></w:r></w:p></w:tc></w:tr>'
            '<w:tr><w:tc><w:p><w:r><w:t>&lt;30</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>2,5 mg</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
            '<w:p><w:r><w:t>Einde</w:t><w:br/><w:t>regel</w:t></w:r></w:p>')
    assert files.docx_text(docx(body)) == ("## Dosering\nTwee keer per dag\toraal\n- met eten\n- niet bij kinderen\n"
                                           "eGFR | Dosis\n<30 | 2,5 mg\nEinde\nregel")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("other.xml", "<x/>")
    assert err(files.docx_text, buf.getvalue()) == (415, "That Word file could not be read.")


def xlsx(rows) -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Lab"
    for r in rows:
        ws.append(r)
    wb.create_sheet("Empty")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_text():
    data = xlsx([["Test", "Waarde", None], [None, None], ["eGFR", 22, None], ["Hb", 7.9]])
    assert files.xlsx_text(data) == "## Lab\nTest | Waarde\neGFR | 22\nHb | 7.9\n## Empty"
    assert err(files.xlsx_text, b"PK\x03\x04 broken")[0] == 415


def test_read_upload_text_and_limits():
    assert files.read_upload("n.md", "\ufeff# Notes\nline".encode()) == {"kind": "text", "text": "# Notes\nline"}
    assert files.read_upload("p.html", b"<html><title>T</title><body><p>Hallo daar</p><script>x()</script></body></html>") \
        == {"kind": "html", "text": "Hallo daar"}
    assert files.read_upload("d.csv", b"a,b\n1,2") == {"kind": "csv", "text": "a,b\n1,2"}
    assert err(files.read_upload, "a.bin", b"\xff\xfe\x00") == (415, files.NOT_TEXT)
    assert err(files.read_upload, "a.txt", b"a\x00b") == (415, files.NOT_TEXT)
    assert err(files.read_upload, "a.txt", b"   \n ") == (415, "That file has no text.")
    assert err(files.read_upload, "a.txt", b"a" * (files.MAX_TEXT + 1)) == (413, "That file is too long (max 200 000 characters).")
    assert err(files.read_upload, "a.txt", b"a" * (files.MAX_UPLOAD + 1)) == (413, "That file is too big (max 10 MB).")
    assert err(files.read_upload, "a.doc", b"x")[0] == 415


def test_read_upload_pdf_docx_xlsx():
    pdf, _, _ = files.render("x", "pdf", "# Apixaban\n\nTwee keer per dag 5 mg.")
    got = files.read_upload("x.pdf", pdf)
    assert got["kind"] == "pdf" and "Apixaban" in got["text"] and "5 mg" in got["text"]
    assert err(files.read_upload, "x.pdf", b"%PDF-1.4 broken") == (415, "That PDF could not be read.")
    assert files.read_upload("d.docx", docx('<w:p><w:r><w:t>Hallo</w:t></w:r></w:p>')) == {"kind": "docx", "text": "Hallo"}
    assert files.read_upload("t.xlsx", xlsx([["a", 1]])) == {"kind": "xlsx", "text": "## Lab\na | 1\n## Empty"}
    assert err(files.read_upload, "d.docx", docx(""))[1] == "That file has no text."


def test_fence():
    assert files.fence("plain") == "```" and files.fence("a ``` b") == "````" and files.fence("x ````` y") == "``````"
    assert files.attach_block("n.md", "hi") == "\n\nFile n.md:\n```\nhi\n```"
    assert files.attach_block("c.md", "```py\nx\n```") == "\n\nFile c.md:\n````\n```py\nx\n```\n````"


def test_disposition():
    assert files.disposition("notes.md") == "attachment; filename=\"notes.md\"; filename*=UTF-8''notes.md"
    assert files.disposition('ü "q".pdf') == "attachment; filename=\"_ _q_.pdf\"; filename*=UTF-8''%C3%BC%20%22q%22.pdf"


# ---------------------------------------------------------------- D02 Markdown
def R(text, b=False, i=False, code=False, href=None):
    return {"text": text, "b": b, "i": i, "code": code, "href": href}


def test_runs():
    assert files.runs("a **b** *c* _d_ `e` [f](https://x.nl/a) [g](javascript:alert) snake_case_name") == [
        R("a "), R("b", b=True), R(" "), R("c", i=True), R(" "), R("d", i=True), R(" "), R("e", code=True), R(" "),
        R("f", href="https://x.nl/a"), R(" "), R("g"), R(" snake_case_name")]
    assert files.runs("") == [] and files.runs("plain") == [R("plain")]


def test_blocks():
    md = ("# Title\n\nOne line\nsame paragraph.\n\n- a\n* b\n1. c\n2) d\n\n```py\nx = 1\n\ny = 2\n```\n---\n"
          "| A | B |\n|:---|---:|\n| 1 | **2** |\n\n###### Small\nlast")
    assert files.blocks(md) == [
        {"t": "h", "level": 1, "runs": [R("Title")]},
        {"t": "p", "runs": [R("One line same paragraph.")]},
        {"t": "li", "ordered": False, "runs": [R("a")]}, {"t": "li", "ordered": False, "runs": [R("b")]},
        {"t": "li", "ordered": True, "runs": [R("c")]}, {"t": "li", "ordered": True, "runs": [R("d")]},
        {"t": "code", "text": "x = 1\n\ny = 2"},
        {"t": "hr"},
        {"t": "table", "rows": [[[R("A")], [R("B")]], [[R("1")], [R("2", b=True)]]]},
        {"t": "h", "level": 6, "runs": [R("Small")]},
        {"t": "p", "runs": [R("last")]}]
    assert files.blocks("```\nnever closed") == [{"t": "code", "text": "never closed"}]


# ---------------------------------------------------------------- D03 rendering
def test_safe_name():
    assert files.safe_name("../../etc/passwd", "md") == "passwd.md"
    assert files.safe_name("C:\\x\\Rapport 2026.PDF", "pdf") == "Rapport 2026.pdf"
    assert files.safe_name('a<b>:"c"|?*', "txt") == "a_b___c____.txt"
    assert files.safe_name("  ..  ", "csv") == "file.csv" and files.safe_name("", "html") == "file.html"
    assert files.safe_name("Überweisung   (kopie)", "docx") == "Überweisung (kopie).docx"
    assert len(files.safe_name("x" * 200, "md")) == 83


def test_render_csv_and_xlsx():
    out = files.render_csv('naam,waarde\n"a, b",=HYPERLINK("x")\n\n-3,+cmd\n@SUM(1),-2.5')
    assert out == ("\ufeffnaam,waarde\r\n\"a, b\",\"'=HYPERLINK(\"\"x\"\")\"\r\n-3,'+cmd\r\n'@SUM(1),-2.5\r\n").encode("utf-8")
    import openpyxl
    data = files.render_xlsx("naam,aantal,prijs\nthee,3,2.50\nsom,=1+1,-4")
    ws = openpyxl.load_workbook(io.BytesIO(data)).active
    assert [[c.value for c in r] for r in ws.iter_rows()] == [["naam", "aantal", "prijs"], ["thee", 3, 2.5], ["som", "=1+1", -4]]
    assert ws["B3"].data_type == "s", "text that starts with = is never a formula"
    assert ws["A1"].font.bold and not ws["A2"].font.bold
    assert err(files.render_xlsx, "a\n" * (files.MAX_ROWS + 1)) == (413, "Too many rows or columns.")
    assert err(files.render_xlsx, ",".join(["x"] * (files.MAX_COLS + 1)))[0] == 413


def test_render_html_is_safe():
    page = files.render_html("<Rapport>", "# Kop <script>alert(1)</script>\n\nTekst met [link](https://a.nl/?q=\"x\") en "
                             "[slecht](javascript:alert(1)) en <img src=x onerror=alert(1)>\n\n- een\n- twee\n\n1. drie\n\n"
                             "```\n<b>code</b>\n```\n\n| A | B |\n|---|---|\n| 1 | 2 |").decode()
    assert page.startswith("<!doctype html>") and '<meta charset="utf-8">' in page and "<title>&lt;Rapport&gt;</title>" in page
    assert "<script>" not in page and "<img" not in page and 'href="javascript' not in page and "&lt;script&gt;" in page and "&lt;img" in page
    assert '<a href="https://a.nl/?q=&quot;x&quot;" rel="noopener noreferrer">link</a>' in page
    assert "<ul><li>een</li><li>twee</li></ul><ol><li>drie</li></ol>" in page
    assert "<pre><code>&lt;b&gt;code&lt;/b&gt;</code></pre>" in page
    assert "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>" in page


def test_render_docx():
    import docx as pydocx
    data = files.render_docx("# Titel\n\nTekst **vet** en *schuin* en `code`.\n\n- een\n1. twee\n\n| A | B |\n|---|---|\n| 1 | 2 |")
    d = pydocx.Document(io.BytesIO(data))
    ps = [(p.style.name, p.text) for p in d.paragraphs if p.text]
    assert ps[0] == ("Heading 1", "Titel") and ("List Bullet", "een") in ps and ("List Number", "twee") in ps
    runs = {r.text: r for r in d.paragraphs[1].runs}
    assert runs["vet"].bold and runs["schuin"].italic and runs["code"].font.name == "Consolas"
    t = d.tables[0]
    assert t.style.name == "Table Grid" and t.cell(0, 0).text == "A" and t.cell(1, 1).text == "2"
    assert t.cell(0, 0).paragraphs[0].runs[0].bold
    assert files.read_upload("x.docx", data)["text"].startswith("# Titel\nTekst vet en schuin en code.\n- een\n- twee")


def test_render_pdf():
    import pypdf
    data = files.render_pdf("Rapport", "# Apixaban ≥ 30\n\nTwee keer **per dag**.\n\n- met eten\n2. later\n\n```\ncode\n```\n---\n"
                            "| A | B |\n|---|---|\n| 1 | 2 |")
    assert data.startswith(b"%PDF-")
    text = pypdf.PdfReader(io.BytesIO(data)).pages[0].extract_text()
    for want in ("Apixaban", "Twee keer", "per dag", "met eten", "later", "code"):
        assert want in text, want


def test_render():
    data, mime, name = files.render("../Notities", "MD", "# Hoi")
    assert (data, mime, name) == (b"# Hoi", "text/markdown; charset=utf-8", "Notities.md")
    assert files.render("x", "txt", "a")[1:] == ("text/plain; charset=utf-8", "x.txt")
    assert files.render("x", ".pdf", "a")[2] == "x.pdf"
    assert files.render("x", "xlsx", "a,b")[1] == files.MIME["xlsx"] and files.render("x", "docx", "a")[1] == files.MIME["docx"]
    assert files.render("t", "html", "a")[0].startswith(b"<!doctype html>")
    assert err(files.render, "x", "exe", "a") == (400, "Unknown format.")
    assert err(files.render, "x", "md", "a" * (files.MAX_TEXT + 1)) == (413, "That file is too long.")


# ---------------------------------------------------------------- D06 the make_file tool
class FakeDB:
    def __init__(self):
        self.files = []

    def add_file(self, username, name, size, content, kind="text", text=None, made=False):
        self.files.append({"username": username, "name": name, "size": size, "content": content, "kind": kind,
                           "text": text, "made": made})
        return len(self.files)


def test_make_file():
    db = FakeDB()
    result, f = answer.make_file(db, "cedric", json.dumps({"name": "Boodschappen", "format": "CSV", "content": "item,aantal\nthee,2"}))
    assert f == {"id": 1, "name": "Boodschappen.csv", "size": len(db.files[0]["content"]), "kind": "csv"}
    assert result == f"Made Boodschappen.csv ({f['size']} bytes). The user sees a download card for it; do not repeat its content."
    assert db.files[0]["made"] is True and db.files[0]["text"] == "item,aantal\nthee,2" and db.files[0]["username"] == "cedric"
    assert answer.make_file(db, "cedric", '{"name": "x", "format": "exe", "content": "a"}') == ("Error: Unknown format.", None)
    assert answer.make_file(db, "cedric", "not json") == ("Error: the arguments were not valid JSON.", None)
    assert answer.make_file(db, "cedric", "[1]") == ("Error: the arguments were not valid JSON.", None)
    assert len(db.files) == 1


# ---------------------------------------------------------------- D07 attached files in Clear Source
def test_split_files():
    content = "Wat staat hierin?" + files.attach_block("brief.pdf", "Uitslag: eGFR 22.\n\nVolgende controle.") \
        + files.attach_block("code.md", "```\nx\n```")
    assert research.split_files(content) == ("Wat staat hierin?", [
        {"name": "brief.pdf", "text": "Uitslag: eGFR 22.\n\nVolgende controle."}, {"name": "code.md", "text": "```\nx\n```"}])
    assert research.split_files("  just a question \n") == ("just a question", [])
    assert research.split_files("Talk about File stuff:\nnothing") == ("Talk about File stuff:\nnothing", [])


def test_file_sources():
    short = research.file_sources([{"name": "a.txt", "text": "  kort  "}])
    assert short == [{"title": "a.txt", "url": "", "kind": "file", "meta": "Your file", "text": "kort", "type": None, "site": None}]
    para = "x" * 7500 + "\n\n" + "y" * 7500 + "\n\n" + "z" * 7500 + "\n\n" + "w" * 100
    parts = research.file_sources([{"name": "lang.pdf", "text": para}])
    assert [p["title"] for p in parts] == ["lang.pdf (part 1 of 3)", "lang.pdf (part 2 of 3)", "lang.pdf (part 3 of 3)"]
    assert parts[0]["text"] == "x" * 7500 and parts[1]["text"] == "y" * 7500, "cut at the blank line"
    assert all(len(p["text"]) <= research.SOURCE_CHARS for p in parts)
    hard = research.file_sources([{"name": "b", "text": "q" * 20000}])
    assert [len(p["text"]) for p in hard] == [research.SOURCE_CHARS, research.SOURCE_CHARS, 20000 - 2 * research.SOURCE_CHARS]
    assert research.file_sources([{"name": "leeg", "text": " "}]) == []
