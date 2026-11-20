"""Files in and out (chat/SPEC.md §16): read uploads to text, render Markdown or CSV to files.

Pure functions: no database, no network. The routes in app.py and the make_file tool in answer.py use them.
"""
from __future__ import annotations

import csv
import html
import io
import os
import re
import zipfile
from xml.etree import ElementTree as ET

MAX_UPLOAD = 10 * 1024 * 1024
MAX_TEXT = 200_000
MAX_PAGES = 300
MAX_ZIP_ENTRIES = 2000
MAX_UNZIPPED = 50 * 1024 * 1024
MAX_XLSX_ROWS = 5000
MAX_ROWS, MAX_COLS = 10_000, 100
FORMATS = ("md", "txt", "csv", "html", "docx", "xlsx", "pdf")
FONT_DIR = os.environ.get("PDF_FONT_DIR", "/usr/share/fonts/truetype/dejavu")
MIME = {
    "md": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "text": "text/plain; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
NOT_TEXT = "Only PDF, Word, Excel, HTML and text files."


class FileError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ reading uploads (SPEC §16.1)
def kind_of(name: str) -> str:
    r"""TODO (task D01). The kind of an upload from its name's extension, lower case (SPEC §16.1):
      .pdf -> "pdf", .docx -> "docx", .xlsx -> "xlsx", .html/.htm -> "html", .csv -> "csv",
      .doc/.xls -> FileError(415, "Save it as .docx or .xlsx first."), anything else -> "text"
    Tests: test_step_files.py::test_kind_of
    """
    raise NotImplementedError


def check_zip(data: bytes, what: str) -> zipfile.ZipFile:
    r"""TODO (task D01). Open a docx/xlsx as a zip, safely (SPEC §16.1). what = "Word" or "Excel".
      zipfile.ZipFile(io.BytesIO(data)); zipfile.BadZipFile (or any error opening it) -> FileError(415, f"That {what} file could not be read.")
      more than MAX_ZIP_ENTRIES entries, or the sum of their file_size > MAX_UNZIPPED -> FileError(413, "That file is too big inside.")
    Tests: test_step_files.py::test_check_zip
    """
    raise NotImplementedError


def docx_text(data: bytes) -> str:
    r"""TODO (task D01). The text of a Word file (SPEC §16.1), stdlib only.
      z = check_zip(data, "Word"); root = ET.fromstring(z.read("word/document.xml")) (KeyError or ET.ParseError ->
        FileError(415, "That Word file could not be read.")); body = root.find(f"{W}body")
      per child of body, in order: a w:p -> para(p); a w:tbl -> per w:tr (anywhere inside it): " | ".join(the text of
        each w:tc: its paragraphs' para() joined by " ")
      para(p): walk p.iter(): w:t -> its text, w:tab -> "\t", w:br -> "\n"; joined, stripped; empty -> skipped.
        style = p's w:pPr/w:pStyle w:val; "Heading1".."Heading6" -> "#" * n + " " in front;
        else a style starting with "List" or a w:pPr/w:numPr -> "- " in front
      lines joined by "\n"
    Tests: test_step_files.py::test_docx_text
    """
    raise NotImplementedError


def xlsx_text(data: bytes) -> str:
    r"""TODO (task D01). The text of an Excel file (SPEC §16.1).
      check_zip(data, "Excel"); wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        (any error -> FileError(415, "That Excel file could not be read."))
      per worksheet: a line "## <title>", then per row (ws.iter_rows(values_only=True)): cells as str ("" for None),
        trailing empty cells dropped, an empty row skipped, else " | ".join(cells); stop after MAX_XLSX_ROWS rows in all
      lines joined by "\n"; close the workbook
    Tests: test_step_files.py::test_xlsx_text
    """
    raise NotImplementedError


def read_upload(name: str, data: bytes) -> dict:
    r"""TODO (task D02). Read one upload to text: {"kind", "text"} (SPEC §16.1).
      len(data) > MAX_UPLOAD -> FileError(413, "That file is too big (max 10 MB).")
      kind = kind_of(name)
      pdf: pypdf.PdfReader(io.BytesIO(data)); the first MAX_PAGES pages' extract_text() (or ""), stripped, joined by "\n\n";
        any error -> FileError(415, "That PDF could not be read."); empty -> FileError(415, "That PDF has no text (is it a scan?).")
      docx -> docx_text(data); xlsx -> xlsx_text(data)
      html, csv, text: data.decode("utf-8") (UnicodeDecodeError -> FileError(415, NOT_TEXT)); a leading "﻿" dropped;
        a "\x00" in it -> FileError(415, NOT_TEXT); html -> text = tools.html_text(text)[1] (import inside)
      text.strip() empty -> FileError(415, "That file has no text."); len(text) > MAX_TEXT ->
        FileError(413, "That file is too long (max 200 000 characters).")
    Tests: test_step_files.py::test_read_upload_text_and_limits, ::test_read_upload_pdf_docx_xlsx
    """
    raise NotImplementedError


def fence(text: str) -> str:
    r"""TODO (task D02). A code fence the text can't close (SPEC §16.1): "```", or one backtick more than the longest run of backticks
    in the text when that run is 3 or longer.
    Tests: test_step_files.py::test_fence
    """
    raise NotImplementedError


def attach_block(name: str, text: str) -> str:
    r"""TODO (task D02). What an attached file adds to a message (SPEC §16.1): f"\n\nFile {name}:\n{f}\n{text}\n{f}" with f = fence(text).
    Tests: test_step_files.py::test_fence
    """
    raise NotImplementedError


def disposition(name: str) -> str:
    r"""TODO (task D02). The Content-Disposition of a download (SPEC §16.1):
      ascii = name with every character outside printable ASCII, and '"' and '\\', replaced by "_"
      f'attachment; filename="{ascii}"; filename*=UTF-8\'\'{urllib.parse.quote(name, safe="")}'
    Tests: test_step_files.py::test_disposition
    """
    raise NotImplementedError


# ------------------------------------------------------------------ Markdown (SPEC §16.4)
_RUN = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)|\*(?!\s)(.+?)\*|(?<!\w)_(?!\s)(.+?)_(?!\w)")


def runs(text: str) -> list[dict]:
    r"""TODO (task D03). Inline Markdown to runs {"text", "b", "i", "code", "href"} (SPEC §16.4), in order, no empty runs.
      **x** -> b; *x* or _x_ (not inside a word) -> i; `x` -> code; [x](url) -> href=url when url starts with http:// or
      https://, else plain text "x". Bold may hold italic: "**a *b***" is not needed; inside bold, text is plain bold.
      Everything else is plain text ({"b": False, "i": False, "code": False, "href": None}).
    Tests: test_step_files.py::test_runs
    """
    raise NotImplementedError


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def blocks(md: str) -> list[dict]:
    r"""TODO (task D03). Markdown to blocks (SPEC §16.4), line by line:
      "```" (optionally followed by a language) starts a code block up to the next line starting with "```" (or the end):
        {"t": "code", "text": the lines between, joined by "\n"}
      "#".."######" + space -> {"t": "h", "level": n, "runs": runs(rest stripped)}
      a line of only 3+ "-", "*" or "_" (spaces allowed) -> {"t": "hr"}
      "- ", "* " or "+ " -> {"t": "li", "ordered": False, "runs"}; "<digits>. " or "<digits>) " -> ordered True
        (leading spaces allowed)
      a line starting with "|": the table goes on while lines start with "|"; rows = _cells per line, a row whose cells
        all match ^:?-{3,}:?$ is dropped; {"t": "table", "rows": [[runs(c) per cell] per row]}
      an empty line ends a paragraph; other lines join the current paragraph with a space:
        {"t": "p", "runs": runs(joined)}
    Tests: test_step_files.py::test_blocks
    """
    raise NotImplementedError


def plain(rs: list[dict]) -> str:
    """The text of runs, without marks."""
    return "".join(r["text"] for r in rs)


# ------------------------------------------------------------------ rendering (SPEC §16.4)
def safe_name(name: str, fmt: str) -> str:
    r"""TODO (task D04). A safe file name (SPEC §16.4): last path part (after "/" or "\\"); a trailing "." + fmt dropped (any case);
    characters other than letters, digits, space, ".", "_", "-", "(", ")" -> "_"; runs of spaces -> one space;
    stripped of spaces and dots; at most 80 characters (then stripped again); empty -> "file"; + "." + fmt
    Tests: test_step_files.py::test_safe_name
    """
    raise NotImplementedError


def _no_formula(cell: str) -> str:
    if cell[:1] in ("=", "+", "-", "@") and not _number(cell):
        return "'" + cell
    return cell


def _number(cell: str) -> int | float | None:
    s = cell.strip()
    if re.fullmatch(r"[-+]?\d+", s):
        return int(s)
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+)([eE][-+]?\d+)?|[-+]?\d+[eE][-+]?\d+", s):
        return float(s)
    return None


def csv_rows(content: str) -> list[list[str]]:
    """The CSV content's rows (csv module), empty rows dropped."""
    return [r for r in csv.reader(io.StringIO(content)) if any(c.strip() for c in r)]


def render_csv(content: str) -> bytes:
    r"""TODO (task D04). CSV out (SPEC §16.4): rows = csv_rows(content); written with csv.writer (lineterminator "\r\n"), each cell through
    _no_formula; "﻿" + that, encoded UTF-8.
    Tests: test_step_files.py::test_render_csv_and_xlsx
    """
    raise NotImplementedError


def render_xlsx(content: str) -> bytes:
    r"""TODO (task D04). Excel out (SPEC §16.4): rows = csv_rows(content); more than MAX_ROWS rows or a row with more than MAX_COLS cells
    -> FileError(413, "Too many rows or columns."). openpyxl.Workbook(); per cell: _number(cell) when it is a number,
    else the text, set as text (cell.data_type = "s" after the value, so "=1+1" is never a formula); row 1 bold
    (openpyxl.styles.Font(bold=True)). Saved to bytes.
    Tests: test_step_files.py::test_render_csv_and_xlsx
    """
    raise NotImplementedError


def _html_runs(rs: list[dict]) -> str:
    out = []
    for r in rs:
        t = html.escape(r["text"])
        if r["code"]:
            t = f"<code>{t}</code>"
        if r["i"]:
            t = f"<em>{t}</em>"
        if r["b"]:
            t = f"<strong>{t}</strong>"
        if r["href"]:
            t = f'<a href="{html.escape(r["href"], quote=True)}" rel="noopener noreferrer">{t}</a>'
        out.append(t)
    return "".join(out)


def render_html(title: str, md: str) -> bytes:
    r"""TODO (task D05). HTML out (SPEC §16.4): a whole page, every text escaped (html.escape), only http(s) links.
      '<!doctype html>\n<html><head><meta charset="utf-8"><title>{escaped title}</title><style>{a few rules}</style></head><body>'
      + per block: h -> <h{n}>, p -> <p>, li -> list items inside <ul>/<ol> (one list per run of items of the same kind),
        code -> <pre><code>, table -> <table> with <th> in the first row and <td> after, hr -> <hr>
      + "</body></html>", encoded UTF-8. Runs: code -> <code>, i -> <em>, b -> <strong>, href -> <a href="..."
        rel="noopener noreferrer">.
    Tests: test_step_files.py::test_render_html_is_safe
    """
    raise NotImplementedError


def render_docx(md: str) -> bytes:
    r"""TODO (task D06). Word out (SPEC §16.4), python-docx: d = docx.Document(); per block:
      h -> d.add_heading(level=n) with the runs added; p -> d.add_paragraph() with runs; li -> d.add_paragraph(style=
      "List Number" when ordered else "List Bullet") with runs; code -> a paragraph with one run in font "Consolas";
      table -> d.add_table(rows, cols = the longest row) style "Table Grid", each cell's text = plain(runs), first row's
      runs bold; hr -> d.add_paragraph(). A run: run = par.add_run(text); run.bold = b; run.italic = i; code -> font
      "Consolas". Saved to bytes.
    Tests: test_step_files.py::test_render_docx
    """
    raise NotImplementedError


def _font(pdf) -> tuple[str, str, bool]:
    """(body font, mono font, unicode) for fpdf2: DejaVu from FONT_DIR when there, else Helvetica / Courier."""
    path = os.path.join(FONT_DIR, "DejaVuSans.ttf")
    if not os.path.exists(path):
        return "helvetica", "courier", False
    pdf.add_font("dejavu", "", path)
    for style, file in (("B", "DejaVuSans-Bold.ttf"), ("I", "DejaVuSans-Oblique.ttf"), ("BI", "DejaVuSans-BoldOblique.ttf")):
        p = os.path.join(FONT_DIR, file)
        pdf.add_font("dejavu", style, p if os.path.exists(p) else path)
    mono = os.path.join(FONT_DIR, "DejaVuSansMono.ttf")
    if os.path.exists(mono):
        pdf.add_font("dejavumono", "", mono)
        return "dejavu", "dejavumono", True
    return "dejavu", "dejavu", True


def render_pdf(title: str, md: str) -> bytes:
    r"""TODO (task D06). PDF out (SPEC §16.4), fpdf2: FPDF(format="A4"), margins 18 mm, auto page break, one page added; fonts from _font.
      Without unicode, every text goes through .encode("latin-1", "replace").decode("latin-1").
      h -> bold, size 20/16/14/12/12/12 by level; p -> size 11, the runs written with pdf.write (bold/italic per run);
      li -> "• " (unicode) or "- " (else), or "<n>. " for ordered items (n counts within a run of items); code -> the
      mono font, size 9.5, multi_cell; table -> with pdf.table(): cells = plain(runs); hr -> a line across.
      Returns bytes(pdf.output()).
    Tests: test_step_files.py::test_render_pdf
    """
    raise NotImplementedError


def render(name: str, fmt: str, content: str) -> tuple[bytes, str, str]:
    r"""TODO (task D06). (bytes, mime, filename) of a file (SPEC §16.4).
      fmt = str(fmt).lower().lstrip("."); not in FORMATS -> FileError(400, "Unknown format.")
      content = str(content); len > MAX_TEXT -> FileError(413, "That file is too long.")
      filename = safe_name(name, fmt); title = filename without its extension
      md/txt -> content.encode("utf-8"); csv -> render_csv; xlsx -> render_xlsx; html -> render_html(title, content);
      docx -> render_docx; pdf -> render_pdf(title, content); mime = MIME[fmt]
    Tests: test_step_files.py::test_render
    """
    raise NotImplementedError


# ------------------------------------------------------------------ the make_file tool (SPEC §16.3)
TOOL = {"type": "function", "function": {
    "name": "make_file",
    "description": "Make a file the user can download. content is Markdown for md, txt, html, docx and pdf; "
                   "CSV (first row = the headers) for csv and xlsx. Use it when the user asks for a file or a document.",
    "parameters": {"type": "object", "properties": {
        "name": {"type": "string", "description": "the file name, without the extension"},
        "format": {"type": "string", "enum": list(FORMATS)},
        "content": {"type": "string"}},
        "required": ["name", "format", "content"]}}}
