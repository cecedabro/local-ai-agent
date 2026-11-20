"""Files in and out (chat/SPEC.md §16): read uploads to text, render Markdown or CSV to files.

Pure functions: no database, no network. The routes in app.py and the make_file tool in answer.py use them.
"""
from __future__ import annotations

import csv
import html
import io
import os
import re
import urllib.parse
import zipfile
from xml.etree import ElementTree as ET

import openpyxl

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
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in ("pdf",):
        return "pdf"
    elif ext in ("docx",):
        return "docx"
    elif ext in ("xlsx",):
        return "xlsx"
    elif ext in ("html", "htm"):
        return "html"
    elif ext in ("csv",):
        return "csv"
    elif ext in ("doc", "xls"):
        raise FileError(415, "Save it as .docx or .xlsx first.")
    else:
        return "text"


def check_zip(data: bytes, what: str) -> zipfile.ZipFile:
    r"""TODO (task D01). Open a docx/xlsx as a zip, safely (SPEC §16.1). what = "Word" or "Excel".
      zipfile.ZipFile(io.BytesIO(data)); zipfile.BadZipFile (or any error opening it) -> FileError(415, f"That {what} file could not be read.")
      more than MAX_ZIP_ENTRIES entries, or the sum of their file_size > MAX_UNZIPPED -> FileError(413, "That file is too big inside.")
    Tests: test_step_files.py::test_check_zip
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise FileError(415, f"That {what} file could not be read.")
    except Exception:
        raise FileError(415, f"That {what} file could not be read.")
    if len(z.namelist()) > MAX_ZIP_ENTRIES:
        z.close()
        raise FileError(413, "That file is too big inside.")
    total = sum(i.file_size for i in z.infolist())
    if total > MAX_UNZIPPED:
        z.close()
        raise FileError(413, "That file is too big inside.")
    return z


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
    z = check_zip(data, "Word")
    try:
        root = ET.fromstring(z.read("word/document.xml"))
    except KeyError:
        z.close()
        raise FileError(415, "That Word file could not be read.")
    except ET.ParseError:
        z.close()
        raise FileError(415, "That Word file could not be read.")

    def para(p):
        parts = []
        for child in p.iter():
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "t" and child.text:
                parts.append(child.text)
            elif tag == "tab":
                parts.append("\t")
            elif tag == "br":
                parts.append("\n")
        text = "".join(parts).strip()
        if not text:
            return None
        prefix = ""
        pPr = p.find(f"{W}pPr")
        if pPr is not None:
            pStyle = pPr.find(f"{W}pStyle")
            if pStyle is not None:
                val = pStyle.get(f"{W}val", "")
                if val.startswith("Heading") and val[7:8].isdigit():
                    n = int(val[7])
                    if 1 <= n <= 6:
                        prefix = "#" * n + " "
                elif val.startswith("List") or val.startswith("List"):
                    prefix = "- "
            if pStyle is None and pPr.find(f"{W}numPr") is not None:
                prefix = "- "
        return prefix + text

    def cell_text(tc):
        para_texts = []
        for cp in tc.iter():
            if cp.tag == f"{W}p":
                t = para(cp)
                if t is not None:
                    para_texts.append(t)
        return " ".join(para_texts)

    lines = []
    body = root.find(f"{W}body")
    if body is None:
        z.close()
        return ""
    for child in body:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == "p":
            t = para(child)
            if t is not None:
                lines.append(t)
        elif tag == "tbl":
            for tr in child.iter(f"{W}tr"):
                cells = []
                for tc in tr.iter(f"{W}tc"):
                    cells.append(cell_text(tc))
                lines.append(" | ".join(cells))
    z.close()
    return "\n".join(lines)


def xlsx_text(data: bytes) -> str:
    r"""TODO (task D01). The text of an Excel file (SPEC §16.1).
      check_zip(data, "Excel"); wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        (any error -> FileError(415, "That Excel file could not be read."))
      per worksheet: a line "## <title>", then per row (ws.iter_rows(values_only=True)): cells as str ("" for None),
        trailing empty cells dropped, an empty row skipped, else " | ".join(cells); stop after MAX_XLSX_ROWS rows in all
      lines joined by "\n"; close the workbook
    Tests: test_step_files.py::test_xlsx_text
    """
    check_zip(data, "Excel")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise FileError(415, "That Excel file could not be read.")
    lines = []
    row_count = 0
    for ws in wb.worksheets:
        lines.append(f"## {ws.title}")
        for row in ws.iter_rows(values_only=True):
            if row_count >= MAX_XLSX_ROWS:
                break
            cells = ["" if c is None else str(c) for c in row]
            # drop trailing empty cells
            while cells and cells[-1] == "":
                cells.pop()
            if not cells:
                continue
            lines.append(" | ".join(cells))
            row_count += 1
        if row_count >= MAX_XLSX_ROWS:
            break
    wb.close()
    return "\n".join(lines)


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
    if len(data) > MAX_UPLOAD:
        raise FileError(413, "That file is too big (max 10 MB).")
    kind = kind_of(name)
    if kind == "pdf":
        import pypdf
        try:
            reader = pypdf.PdfReader(io.BytesIO(data))
        except Exception:
            raise FileError(415, "That PDF could not be read.")
        pages = []
        for i in range(min(len(reader.pages), MAX_PAGES)):
            pages.append(reader.pages[i].extract_text() or "")
        text = "\n\n".join(pages).strip()
        if not text:
            raise FileError(415, "That PDF has no text (is it a scan?).")
        return {"kind": "pdf", "text": text}
    elif kind == "docx":
        text = docx_text(data)
        if not text.strip():
            raise FileError(415, "That file has no text.")
        return {"kind": "docx", "text": text}
    elif kind == "xlsx":
        text = xlsx_text(data)
        if not text.strip():
            raise FileError(415, "That file has no text.")
        return {"kind": "xlsx", "text": text}
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise FileError(415, NOT_TEXT)
        if "\x00" in text:
            raise FileError(415, NOT_TEXT)
        if text.startswith("\ufeff"):
            text = text[1:]
        if kind == "html":
            from coder import tools
            text = tools.html_text(text)[1]
        if not text.strip():
            raise FileError(415, "That file has no text.")
        if len(text) > MAX_TEXT:
            raise FileError(413, "That file is too long (max 200 000 characters).")
        return {"kind": kind, "text": text}


def fence(text: str) -> str:
    r"""TODO (task D02). A code fence the text can't close (SPEC §16.1): "```", or one backtick more than the longest run of backticks
    in the text when that run is 3 or longer.
    Tests: test_step_files.py::test_fence
    """
    longest = 0
    run = 0
    for ch in text:
        if ch == "`":
            run += 1
        else:
            if run > longest:
                longest = run
            run = 0
    if run > longest:
        longest = run
    if longest >= 3:
        return "`" * (longest + 1)
    return "```"


def attach_block(name: str, text: str) -> str:
    r"""TODO (task D02). What an attached file adds to a message (SPEC §16.1): f"\n\nFile {name}:\n{f}\n{text}\n{f}" with f = fence(text).
    Tests: test_step_files.py::test_fence
    """
    f = fence(text)
    return f"\n\nFile {name}:\n{f}\n{text}\n{f}"


def disposition(name: str) -> str:
    r"""TODO (task D02). The Content-Disposition of a download (SPEC §16.1):
      ascii = name with every character outside printable ASCII, and '"' and '\\', replaced by "_"
      f'attachment; filename="{ascii}"; filename*=UTF-8\'\'{urllib.parse.quote(name, safe="")}'
    Tests: test_step_files.py::test_disposition
    """
    import urllib.parse
    ascii_parts = []
    for ch in name:
        if ch in ('"', '\\') or not (ch.isprintable() and 32 <= ord(ch) <= 126):
            ascii_parts.append("_")
        else:
            ascii_parts.append(ch)
    ascii_name = "".join(ascii_parts)
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{urllib.parse.quote(name, safe="")}'


# ------------------------------------------------------------------ Markdown (SPEC §16.4)
_RUN = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)|\*(?!\s)(.+?)\*|(?<!\w)_(?!\s)(.+?)_(?!\w)")


def runs(text: str) -> list[dict]:
    r"""TODO (task D03). Inline Markdown to runs {"text", "b", "i", "code", "href"} (SPEC §16.4), in order, no empty runs.
      **x** -> b; *x* or _x_ (not inside a word) -> i; `x` -> code; [x](url) -> href=url when url starts with http:// or
      https://, else plain text "x". Bold may hold italic: "**a *b***" is not needed; inside bold, text is plain bold.
      Everything else is plain text ({"b": False, "i": False, "code": False, "href": None}).
    Tests: test_step_files.py::test_runs
    """
    result = []
    pos = 0
    in_code = False
    code_lines = []
    for line in text.split("\n"):
        if not in_code and line.startswith("```"):
            in_code = True
            continue
        if in_code:
            if line.startswith("```"):
                in_code = False
                code_block = "\n".join(code_lines)
                result.append({"text": code_block, "b": False, "i": False, "code": True, "href": None})
                code_lines = []
            else:
                code_lines.append(line)
            continue
        for m in _RUN.finditer(line):
            if m.start() > pos:
                result.append({"text": line[pos:m.start()], "b": False, "i": False, "code": False, "href": None})
            if m.group(1) is not None:
                result.append({"text": m.group(1), "b": True, "i": False, "code": False, "href": None})
            elif m.group(2) is not None:
                result.append({"text": m.group(2), "b": False, "i": False, "code": True, "href": None})
            elif m.group(3) is not None and m.group(4):
                url = m.group(4)
                if url.startswith("http://") or url.startswith("https://"):
                    result.append({"text": m.group(3), "b": False, "i": False, "code": False, "href": url})
                else:
                    result.append({"text": m.group(3), "b": False, "i": False, "code": False, "href": None})
            elif m.group(5) is not None:
                result.append({"text": m.group(5), "b": False, "i": True, "code": False, "href": None})
            elif m.group(6) is not None:
                result.append({"text": m.group(6), "b": False, "i": True, "code": False, "href": None})
            pos = m.end()
        if pos < len(line):
            result.append({"text": line[pos:], "b": False, "i": False, "code": False, "href": None})
        pos = 0
    # Collect remaining code block if never closed
    if in_code and code_lines:
        result.append({"text": "\n".join(code_lines), "b": False, "i": False, "code": True, "href": None})
    return result


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
    lines = md.split("\n")
    result = []
    i = 0
    in_code = False
    code_lines = []
    buf = []

    while i < len(lines):
        line = lines[i]

        # Code blocks
        if not in_code and line.startswith("```"):
            in_code = True
            code_lines = []
            i += 1
            continue
        if in_code:
            if line.startswith("```"):
                in_code = False
                result.append({"t": "code", "text": "\n".join(code_lines)})
                code_lines = []
            else:
                code_lines.append(line)
            i += 1
            continue

        # Headings
        hm = re.match(r"^(#{1,6})\s(.*)", line)
        if hm:
            if buf:
                result.append({"t": "p", "runs": runs(" ".join(buf))})
                buf = []
            level = len(hm.group(1))
            result.append({"t": "h", "level": level, "runs": runs(hm.group(2).rstrip())})
            i += 1
            continue

        # Horizontal rules (3+ identical - * _)
        if re.fullmatch(r"([-*_]){3,}", line.strip()):
            if buf:
                result.append({"t": "p", "runs": runs(" ".join(buf))})
                buf = []
            result.append({"t": "hr"})
            i += 1
            continue

        # List items
        li = re.match(r"^(\s*)([-*+])\s+(.+)", line)
        if li:
            if buf:
                result.append({"t": "p", "runs": runs(" ".join(buf))})
                buf = []
            result.append({"t": "li", "ordered": False, "runs": runs(li.group(3))})
            i += 1
            continue
        oi = re.match(r"^(\s*)(\d+)[.)]\s+(.+)", line)
        if oi:
            if buf:
                result.append({"t": "p", "runs": runs(" ".join(buf))})
                buf = []
            result.append({"t": "li", "ordered": True, "runs": runs(oi.group(3))})
            i += 1
            continue

        # Tables
        if line.startswith("|"):
            table_lines = [line]
            i += 1
            while i < len(lines) and lines[i].startswith("|"):
                table_lines.append(lines[i])
                i += 1
            rows = []
            seen = set()
            for tl in table_lines:
                cells = _cells(tl)
                key = tuple(cells)
                if key not in seen and all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                    seen.add(key)
                    continue
                rows.append(cells)
            if rows:
                ncols = len(rows[0])
                filtered_rows = [r for r in rows if len(r) == ncols]
                if filtered_rows:
                    parsed_rows = [[runs(c) for c in row] for row in filtered_rows]
                    result.append({"t": "table", "rows": parsed_rows})
            continue

        # Empty line ends paragraph
        if line == "":
            if buf:
                result.append({"t": "p", "runs": runs(" ".join(buf))})
                buf = []
            i += 1
            continue

        # Paragraph content
        buf.append(line)
        i += 1

    if buf:
        result.append({"t": "p", "runs": runs(" ".join(buf))})
    if in_code and code_lines:
        result.append({"t": "code", "text": "\n".join(code_lines)})

    return result


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
    name = name.rsplit("/", 1)[-1]
    name = name.rsplit("\\", 1)[-1]
    lower_fmt = fmt.lower()
    if name.lower().endswith("." + lower_fmt):
        name = name[:-(len(lower_fmt) + 1)]
    safe = []
    for ch in name:
        if ch.isalpha() or ch.isdigit() or ch in " ._-()":
            safe.append(ch)
        else:
            safe.append("_")
    name = "".join(safe)
    name = re.sub(r" +", " ", name)
    name = name.strip(" .")
    if len(name) > 80:
        name = name[:80].strip(" .")
    if not name:
        name = "file"
    return name + "." + fmt


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
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    for row in csv_rows(content):
        w.writerow([_no_formula(c) for c in row])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def render_xlsx(content: str) -> bytes:
    r"""TODO (task D04). Excel out (SPEC §16.4): rows = csv_rows(content); more than MAX_ROWS rows or a row with more than MAX_COLS cells
    -> FileError(413, "Too many rows or columns."). openpyxl.Workbook(); per cell: _number(cell) when it is a number,
    else the text, set as text (cell.data_type = "s" after the value, so "=1+1" is never a formula); row 1 bold
    (openpyxl.styles.Font(bold=True)). Saved to bytes.
    Tests: test_step_files.py::test_render_csv_and_xlsx
    """
    import openpyxl
    from openpyxl.styles import Font
    rows = csv_rows(content)
    if len(rows) > MAX_ROWS or any(len(r) > MAX_COLS for r in rows):
        raise FileError(413, "Too many rows or columns.")
    wb = openpyxl.Workbook()
    ws = wb.active
    for ri, row in enumerate(rows, 1):
        for ci, cell_val in enumerate(row, 1):
            n = _number(cell_val)
            if n is not None:
                cell = ws.cell(row=ri, column=ci, value=n)
            else:
                cell = ws.cell(row=ri, column=ci, value=cell_val)
                cell.data_type = "s"
            if ri == 1:
                cell.font = Font(bold=True)
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


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
            href = r["href"]
            if href.lower().startswith("http://") or href.lower().startswith("https://"):
                t = f'<a href="{html.escape(href, quote=True)}" rel="noopener noreferrer">{t}</a>'
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
    from coder.files import blocks
    style = ("body{font-family:sans-serif;max-width:700px;margin:40px auto;padding:0 20px}"
             "pre{background:#f5f5f5;padding:10px;border-radius:4px}"
             "table{border-collapse:collapse;margin:10px 0}"
             "th,td{border:1px solid #ddd;padding:6px 10px;text-align:left}"
             "th{font-weight:bold}")
    escaped_title = html.escape(title)
    parts = [f"<!doctype html>\n<html><head><meta charset=\"utf-8\"><title>{escaped_title}</title>"
             f"<style>{style}</style></head><body>"]
    bs = blocks(md)
    # Collect lists: track current ordered/unordered list state
    cur_ol = None
    cur_ul = None
    for b in bs:
        t = b["t"]
        if t == "h":
            if cur_ul:
                parts.append("".join(cur_ul))
                parts.append("</ul>")
                cur_ul = None
            if cur_ol:
                parts.append("".join(cur_ol))
                parts.append("</ol>")
                cur_ol = None
            parts.append(f"<h{b['level']}>{_html_runs(b['runs'])}</h{b['level']}>")
        elif t == "p":
            if cur_ul:
                parts.append("".join(cur_ul))
                parts.append("</ul>")
                cur_ul = None
            if cur_ol:
                parts.append("".join(cur_ol))
                parts.append("</ol>")
                cur_ol = None
            parts.append(f"<p>{_html_runs(b['runs'])}</p>")
        elif t == "li":
            if b["ordered"]:
                if cur_ul:
                    parts.append("".join(cur_ul))
                    parts.append("</ul>")
                    cur_ul = None
                if cur_ol is None:
                    parts.append("<ol>")
                    cur_ol = []
                cur_ol.append(f"<li>{_html_runs(b['runs'])}</li>")
            else:
                if cur_ol:
                    parts.append("".join(cur_ol))
                    parts.append("</ol>")
                    cur_ol = None
                if cur_ul is None:
                    parts.append("<ul>")
                    cur_ul = []
                cur_ul.append(f"<li>{_html_runs(b['runs'])}</li>")
        elif t == "code":
            if cur_ul:
                parts.append("".join(cur_ul))
                parts.append("</ul>")
                cur_ul = None
            if cur_ol:
                parts.append("".join(cur_ol))
                parts.append("</ol>")
                cur_ol = None
            parts.append(f"<pre><code>{html.escape(b['text'])}</code></pre>")
        elif t == "table":
            if cur_ul:
                parts.append("".join(cur_ul))
                parts.append("</ul>")
                cur_ul = None
            if cur_ol:
                parts.append("".join(cur_ol))
                parts.append("</ol>")
                cur_ol = None
            rows_html = []
            for ri, row in enumerate(b["rows"]):
                tag = "th" if ri == 0 else "td"
                cells = "".join(f"<{tag}>{_html_runs(cell)}</{tag}>" for cell in row)
                rows_html.append(f"<tr>{cells}</tr>")
            parts.append(f"<table>{''.join(rows_html)}</table>")
        elif t == "hr":
            if cur_ul:
                parts.append("".join(cur_ul))
                parts.append("</ul>")
                cur_ul = None
            if cur_ol:
                parts.append("".join(cur_ol))
                parts.append("</ol>")
                cur_ol = None
            parts.append("<hr>")
    if cur_ul:
        parts.append("".join(cur_ul))
        parts.append("</ul>")
    if cur_ol:
        parts.append("".join(cur_ol))
        parts.append("</ol>")
    parts.append("</body></html>")
    return "".join(parts).encode("utf-8")


def _plain(runs):
    """Concatenate text from run dicts."""
    return "".join(r["text"] for r in runs)


def _docx_runs(par, runs, bold=False):
    """Add inline runs to a docx paragraph."""
    for r in runs:
        run = par.add_run(r["text"])
        run.bold = r["b"] or bold
        run.italic = r["i"]
        if r["code"]:
            run.font.name = "Consolas"


def render_docx(md: str) -> bytes:
    r"""TODO (task D06). Word out (SPEC §16.4), python-docx: d = docx.Document(); per block:
      h -> d.add_heading(level=n) with the runs added; p -> d.add_paragraph() with runs; li -> d.add_paragraph(style=
      "List Number" when ordered else "List Bullet") with runs; code -> a paragraph with one run in font "Consolas";
      table -> d.add_table(rows, cols = the longest row) style "Table Grid", each cell's text = plain(runs), first row's
      runs bold; hr -> d.add_paragraph(). A run: run = par.add_run(text); run.bold = b; run.italic = i; code -> font
      "Consolas". Saved to bytes.
    Tests: test_step_files.py::test_render_docx
    """
    import docx
    from coder.files import blocks

    d = docx.Document()
    for b in blocks(md):
        t = b["t"]
        if t == "h":
            par = d.add_heading(level=b["level"])
            par.clear()
            for r in b["runs"]:
                run = par.add_run(r["text"])
                run.bold = r["b"]
                run.italic = r["i"]
                if r["code"]:
                    run.font.name = "Consolas"
        elif t == "p":
            par = d.add_paragraph()
            par.clear()
            _docx_runs(par, b["runs"])
        elif t == "li":
            style_name = "List Number" if b["ordered"] else "List Bullet"
            par = d.add_paragraph(style=style_name)
            par.clear()
            _docx_runs(par, b["runs"])
        elif t == "code":
            par = d.add_paragraph()
            par.clear()
            run = par.add_run(b["text"])
            run.font.name = "Consolas"
        elif t == "table":
            longest = max(len(row) for row in b["rows"])
            tbl = d.add_table(rows=len(b["rows"]), cols=longest, style="Table Grid")
            for ri, row in enumerate(b["rows"]):
                for ci, cell_runs in enumerate(row):
                    cell = tbl.cell(ri, ci)
                    cell.text = ""
                    p = cell.paragraphs[0]
                    p.clear()
                    if ri == 0:
                        _docx_runs(p, cell_runs, bold=True)
                    else:
                        _docx_runs(p, cell_runs)
        elif t == "hr":
            d.add_paragraph()
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


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
    from fpdf import FPDF
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_margins(18, 18, 18)
    pdf.auto_page_break = True
    body_font, mono_font, unicode = _font(pdf)

    def safe_text(text: str) -> str:
        if unicode:
            return text
        return text.encode("latin-1", "replace").decode("latin-1")

    def write_runs(run_list, font=body_font, size=11):
        pdf.set_font(font, "", size)
        for r in run_list:
            style = ""
            if r["b"] and r["i"]:
                style = "BI"
            elif r["b"]:
                style = "B"
            elif r["i"]:
                style = "I"
            if r["code"]:
                pdf.set_font(mono_font, "", size)
            else:
                pdf.set_font(font, style, size)
            pdf.write(text=safe_text(r["text"]))
            if r["code"]:
                pdf.set_font(font, style if style in ("", "B", "I", "BI") else "", size)

    bs = blocks(md)
    pdf.add_page()
    h_sizes = [20, 16, 14, 12, 12, 12]
    li_count = 0

    for b in bs:
        t = b["t"]
        if t == "h":
            level = b["level"]
            pdf.set_font(body_font, "B", h_sizes[level - 1])
            pdf.write(text=safe_text(_runs_text(b["runs"])))
            pdf.ln(4)
        elif t == "p":
            pdf.set_font(body_font, "", 11)
            for r in b["runs"]:
                style = ""
                if r["b"]:
                    style = "B"
                elif r["i"]:
                    style = "I"
                pdf.set_font(body_font, style, 11)
                pdf.write(text=safe_text(r["text"]))
            pdf.ln(4)
        elif t == "li":
            n = b["ordered"]
            if n:
                li_count += 1
                prefix = f"{li_count}. "
            else:
                li_count = 0
                prefix = "• " if unicode else "- "
            pdf.set_font(body_font, "", 11)
            pdf.write(text=prefix)
            for r in b["runs"]:
                style = ""
                if r["b"]:
                    style = "B"
                elif r["i"]:
                    style = "I"
                pdf.set_font(body_font, style, 11)
                pdf.write(text=safe_text(r["text"]))
            pdf.ln(4)
        elif t == "code":
            pdf.set_font(mono_font, "", 9.5)
            pdf.multi_cell(w=0, h=5, text=safe_text(b["text"]))
            pdf.ln(2)
        elif t == "table":
            rows_data = []
            for row in b["rows"]:
                rows_data.append([safe_text(_runs_text(cell)) for cell in row])
            pdf.table(
                table_width=0,
                header_row=rows_data[0] if rows_data else [],
                rows=rows_data[1:] if len(rows_data) > 1 else [],
                default_col=dict(min_width=40),
                default_row=dict(padding=2),
                align_thead="L",
                align_th_body="L",
                align_tbody="L",
            )
            pdf.ln(2)
        elif t == "hr":
            y = pdf.get_y()
            pdf.line(18, y, 18 + pdf.w - 36, y)
            pdf.ln(4)
        elif t == "empty":
            pass

    return bytes(pdf.output())


def _runs_text(run_list):
    """Join run texts without formatting."""
    return "".join(r["text"] for r in run_list)





def render(name: str, fmt: str, content: str) -> tuple[bytes, str, str]:
    r"""TODO (task D06). (bytes, mime, filename) of a file (SPEC §16.4).
      fmt = str(fmt).lower().lstrip("."); not in FORMATS -> FileError(400, "Unknown format.")
      content = str(content); len > MAX_TEXT -> FileError(413, "That file is too long.")
      filename = safe_name(name, fmt); title = filename without its extension
      md/txt -> content.encode("utf-8"); csv -> render_csv; xlsx -> render_xlsx; html -> render_html(title, content);
      docx -> render_docx(content); pdf -> render_pdf(title, content); mime = MIME[fmt]
    Tests: test_step_files.py::test_render
    """
    fmt = str(fmt).lower().lstrip(".")
    if fmt not in FORMATS:
        raise FileError(400, "Unknown format.")
    content = str(content)
    if len(content) > MAX_TEXT:
        raise FileError(413, "That file is too long.")
    filename = safe_name(name, fmt)
    title = filename.rsplit(".", 1)[0] if "." in filename else filename
    fmt_map = {
        "md": (content.encode("utf-8"), ),
        "txt": (content.encode("utf-8"), ),
        "csv": (render_csv(content), ),
        "xlsx": (render_xlsx(content), ),
        "html": (render_html(title, content), ),
        "docx": (render_docx(content), ),
        "pdf": (render_pdf(title, content), ),
    }
    data = fmt_map[fmt][0]
    return data, MIME[fmt], f"{filename}.{fmt}" if not filename.endswith(f".{fmt}") else filename


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
