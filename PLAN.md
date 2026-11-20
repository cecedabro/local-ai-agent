# PLAN: Files in and out

Dewdrop gets files both ways (chat/SPEC.md §16): you attach PDF, Word, Excel, HTML or text files to a message (their
text goes to the model; in Clear Source they are read as sources first), the model can make a file for you with the
`make_file` tool (Markdown, text, CSV, HTML, Word, Excel, PDF) that shows as a download card, and every answer can be
downloaded as Markdown, Word, PDF or HTML. The work is in a new module `chat/coder/files.py` (pure functions) plus two
routes, one tool function, two research helpers and the page. Small steps: every task names one file and a few
functions; the steps are in their docstrings or TODO comments (`grep -n "task Dxx" <file>`). Each task has its own test.

Rules for every task:
- Non-interactive: nobody reads questions. Make the edit, run the accept command, fix what fails, stop.
- Only change what the task names. Don't refactor, rename or move other code. Remove the TODO text you fill in.
- Never read a whole large file (`chat/coder/app.py` ~1100 lines, `chat/static/js/app.js` ~830, `chat/coder/research.py`
  ~800): `grep -n`, then `sed -n 'a,bp'`.
- Python: only the packages in `chat/requirements.txt` (python-docx is imported as `docx`, fpdf2 as `fpdf`, openpyxl, pypdf).
- Do not touch tests, `chat/SPEC.md`, `chat/static/css/`, `chat/static/js/stream.js`, `chat/static/vendor/`,
  `chat/static/icons/`, `chat/coder/templates/`, `Makefile`, `docs/`, `chat/requirements.txt`, `chat/Dockerfile`, and in
  `chat/coder/files.py` the constants, `MIME`, `TOOL`, `FileError`, `_html_runs`, `_font`, `_number`, `_no_formula`,
  `csv_rows`, `_cells`, `plain`; in `chat/coder/app.py` `_download`, `api_get_file` and the security middleware.

- [x] D01: In chat/coder/files.py fill in `kind_of`, `check_zip`, `docx_text` and `xlsx_text` as their docstrings say
  accept: make chat-test K="test_step_files and (kind_of or check_zip or docx_text or xlsx_text)"
- [x] D02: In chat/coder/files.py fill in `read_upload`, `fence`, `attach_block` and `disposition`
  accept: make chat-test K="test_step_files and (read_upload_text or fence or disposition)"
- [x] D03: In chat/coder/files.py fill in `runs` and `blocks` (Markdown to runs and blocks)
  accept: make chat-test K="test_step_files and (runs or blocks)"
- [x] D04: In chat/coder/files.py fill in `safe_name`, `render_csv` and `render_xlsx`
  accept: make chat-test K="test_step_files and (safe_name or csv_and_xlsx)"
- [ ] D05: In chat/coder/files.py fill in `render_html` (every text escaped, only http(s) links; use `_html_runs`)
  accept: make chat-test K="test_step_files and render_html"
- [ ] D06: In chat/coder/files.py fill in `render_docx`, `render_pdf` and `render`
  accept: make chat-test K="test_step_files and not (make_file or split_files or file_sources)"
- [ ] D07: In chat/coder/app.py fill in the routes `api_upload_file` and `api_export_message` (`TODO (task D07)`)
  accept: make chat-test K="upload_download_and_attach or limits_and_isolation or upload_kinds or download_headers or attached_file_text or export_an_answer or export_lists"
- [ ] D08: In chat/coder/answer.py fill in `make_file` as its docstring says
  accept: make chat-test K="make_file or the_model_makes_a_file or test_step_answer or test_tool_rounds"
- [ ] D09: In chat/coder/research.py fill in `split_files` and `file_sources` as their docstrings say
  accept: make chat-test K="split_files or file_sources or a_file_is_a_source or test_step_research or test_step_clear or TestEvidenceV2 or TestClearSource or file_block_format"
- [ ] D10: In chat/static/js/app.js fill in `uploadFile` (`TODO (task D10)`)
  accept: node --check chat/static/js/app.js && make chat-test
- [ ] D11: In chat/static/js/app.js fill in `fmtSize`, `filesOutHtml`, `addFileCard` and `dlMenu` (`TODO (task D11)`)
  accept: node --check chat/static/js/app.js && make chat-test
- [ ] D12: Make the browser tests pass: run `make chat-browser-test`, read the FAIL line, fix only chat/coder/ or chat/static/js/app.js (see "D12 details")
  accept: make chat-test && make chat-browser-test && node --test chat/tests/js/stream.test.mjs
- [ ] D13: Add a "## Files in and out" section to chat/README.md (see "D13 details")
  accept: grep -q "## Files in and out" chat/README.md && grep -q "make_file" chat/README.md && make chat-test

## D12 details
`make chat-browser-test` drives a real headless Chromium against the app (chat/tests/browser/run.mjs; serve.py fakes the
model: a message with "word file" in it makes the model call make_file). Round 6 attaches notes.md and old.doc through
#file-input, sends "make a word file <b>please</b>", and checks the chips, the download card, the Download menu and that
both survive a reload. Read the FAIL line; it names the element it waited for. Fix the code, never the test.

## D13 details
In chat/README.md, after the last section, add "## Files in and out" with: what you can attach (PDF, Word .docx, Excel
.xlsx, HTML, text; max 10 MB, 200 000 characters of text), that the text (not the file) goes to the model, that Clear
Source reads attached files first as sources, what `make_file` makes (md, txt, csv, html, docx, xlsx, pdf) and the
"Download as" menu (`/api/messages/<id>/export`). Mention `chat/coder/files.py` and the safety rules: zip size checks,
no formulas in CSV/Excel, escaped HTML, downloads as attachments with a strict Content-Security-Policy.
