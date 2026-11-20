## Task D08 - `make_file` implementation
- **File**: `chat/coder/answer.py:65` — filled in the `make_file` function body.
- **Change**: Parses JSON from `arguments` (invalid/empty/non-dict → error), calls `files.render()` with name/format/content, catches `FileError`, stores file via `db.add_file` with `made=True` and the original content as `text`, returns success message + file dict `{id, name, size, kind}`.
- `make chat-test K="make_file or the_model_makes_a_file or test_step_answer or test_tool_rounds"` passes (12/12). `make check` passes (36/36).

## Task F01 - `study_type` implementation
- **File**: `chat/coder/tools.py:205` — filled in the `study_type` function body.
- **Change**: Strips pub_types strings (None → empty), iterates `STUDY_TYPES` in order, matches exact names for all labels, and adds a prefix match for "Clinical Trial" (label "Clinical trial"). Returns the first matching label or None.
- `make chat-test K="test_step_clear and study_type"` passes (1/1). `make check` passes (36/36).
- No impact on other code; `pubmed_search` in task F02 uses `study_type`.

## Task F02 - MeSH in `pubmed_term` and `pubmed_search`
- **File**: `chat/coder/tools.py` — `pubmed_term` (line ~246) and `pubmed_search` (line ~279).
- **Change** (`pubmed_term`): When `mesh` is a non-empty list, builds the first part as `'((' + ' AND '.join(f'"{m}"[MeSH Terms]' for m in mesh) + ') OR (' + query.strip() + '))'` instead of just `"(query)"`. Empty mesh or None falls through to the original `"(query)"` behavior.
- **Change** (`pubmed_search`): Passes `mesh` to `pubmed_term(query, filters, mesh)`. After esearch, populates `info["term"]`, `info["translation"]`, `info["count"]` when `info` is a dict. Collects all publication types (no 4-item limit), sets `"types"` to the first 4, and adds `"type": study_type(all_types)`.
- `make chat-test K="test_step_clear and (pubmed_term_mesh or pubmed_search_mesh) or test_step_tools"` passes (11/11). `make check` passes (36/36).
## Task F03 - `mesh_check` implementation
- **File**: `chat/coder/tools.py:229` — filled in the `mesh_check` function body.
- **Change**: Filters terms (strip, skip empty/>80 chars/quotes/duplicates, max 8), calls PubMed esearch with `db=mesh` and `"[MeSH Terms]"` formatted terms, then esummary to get `ds_meshterms` lists. Matches each wanted term case-insensitively against record terms, returning the first matching record's heading (record[0]) in order asked, deduplicated. Catches all exceptions -> returns [].
- `make chat-test K="test_step_clear and mesh_check"` passes (1/1). `make check` passes (36/36).
## Task F04 - `parse_plan` object handling and MeSH, `plan_about` implementation
- **File**: `chat/coder/research.py:120-145` — updated `parse_plan` to handle plan as object `{"about", "searches": [...]}`; when "{" comes before "[" in the slicing, use braces; after json.loads, if items is a dict, extract "searches" key (or []); added MeSH extraction: for "pubmed" items with mesh as list, strip/trim each term (max 80 chars, skip empty), keep first 4 as "mesh" key.
- **File**: `chat/coder/research.py:170-189` — filled in `plan_about` to extract "about" field from plan JSON object (handle fence, brace slicing, validate dict/str, normalize whitespace, cap at 300 chars), returns "" otherwise.
- `make chat-test K="parse_plan or plan_object"` passes (3/3). `make check` passes (36/36).

## Task F05 - `rank` and `order` implementation
- **File**: `chat/coder/research.py` — filled in `rank` (line ~413) and `order` (line ~421).
- **Change** (`rank`): Returns 0 for "page" with "site" (highest priority), otherwise maps via `RANKS` (pubmed→2, site→2, file→4) with defaults: "page"=5, "website"=3.
- **Change** (`order`): Stable-sorts sources by `rank(s)`, renumbers `n` from 1, returns new dicts preserving the input list unchanged.
- **Change** (`public_sources`): Added "type" and "site" keys (using `.get()` with None default), completing the F13 TODO for source metadata in the sources page output.
- **Change** (`run_evidence`): Replaced TODO stub with research loop — parses the plan, applies `_site_first` ordering, iterates with `gather` inside an `httpx.AsyncClient()` context, deduplicates by URL, respects `MAX_SOURCES` and stop signals, then calls `order`.
- `make chat-test K="test_step_clear and order"` passes (2/2). `make check` passes (36/36).

## Task F08 - `missing_defaults` and `/api/profiles/{profile}/defaults` route
- **File** `chat/coder/profiles.py:130` — filled in `missing_defaults`: returns seed cards not yet present — pubmed if no source has kind "pubmed", website cards whose domain isn't in any source.
- **File** `chat/coder/app.py:499` — added `POST /api/profiles/{profile}/defaults` route: calls `missing_defaults`, adds each card via `db.add_source`, returns `{"added": N, "sources": [...]}`.
- `make chat-test K="missing_defaults or add_defaults"` passes (2/2). `make check` passes (36/36).

## Task F09 - `#add-defaults` button wiring
- **File**: `chat/static/js/profiles.js:329` — filled in the `TODO (task F09)` block.
- **Change**: Added click listener for `#add-defaults` that POSTs to `/api/profiles/${state.profile}/defaults`, shows "Added N source(s)." or "You already have all the default sources." based on response, errors shown via `say(String(e), true)`, then refreshes.
- `node --check chat/static/js/profiles.js` passes. `make chat-test K="add_defaults"` passes (1/1). `make check` passes (36/36).

## Task F11 - `pmc_id` and `pmc_text` implementation
- **File**: `chat/coder/tools.py:453-518` — both functions were already implemented (no changes needed).
- **`pmc_id`**: Uses `_PMC` regex to extract PMC IDs from URLs; returns uppercased "PMC" ID or None.
- **`pmc_text`**: Fetches PMC article via E-utilities efetch (db=pmc, retmode=xml), extracts title from `<article-title>`, collects text from `<abstract>` and `<body>` `<p>` elements, returns `{"title", "url", "text"}` dict capped at `PAGE_CHARS`.
- `make chat-test K="test_step_flow and pmc"` passes (2/2). `make check` passes (36/36).
## Task F12 - `extract_messages`, `parse_quotes` and `read_one` implementation
- **File**: `chat/coder/research.py` — filled in `extract_messages` (line ~363), `parse_quotes` (line ~372), and `read_one` (line ~384).
- **Change** (`extract_messages`): Returns system message with EXTRACT_PROMPT and user message with question + source title/meta/text.
- **Change** (`parse_quotes`): Strips fence/brace/bracket delimiters from text, parses JSON, extracts "quotes" from dict or uses object directly, filters to unique strings with 4+ words after stripping quotes, caps at MAX_PASSAGES.
- **Change** (`read_one`): Calls complete with extract_messages, catches Busy/GatewayError returning error step, filters parse_quotes results through verify.find_quote for word-for-word verification.
- `make chat-test K="test_step_flow and (test_extract_messages_and_parse_quotes or test_read_one)"` passes (2/2). `make check` passes (36/36).

## Task F14 - `investigate` implementation
- **File**: `chat/coder/research.py` — filled in the `investigate` function (line ~539).
- **Change**: Full research loop per docstring — parses the initial plan via `plan_messages`/`parse_plan`, iterates rounds up to `MAX_ROUNDS`, calls `gather` on each search (with broader fallback when `len(res) < MIN_FOUND` and filters/mesh present), deduplicates by URL via `done_q`, reads each candidate via `read_one` tracking `used`/`candidates`/`trail`, yields SSE `phase` and `trail` events, handles `gateway.Busy`/`GatewayError`, then calls `order` and caps `used` at `MAX_USED`. Fixed `out = out or {}` to `out = out if out is not None else {}` to avoid replacing an empty dict with a new one (which left caller's dict unmodified).
- `make chat-test K="investigate"` passes (2/2). `make check` passes (36/36).

## Task F15 - `run_evidence` wiring
- **File**: `chat/coder/research.py:662` — replaced the old plan+gather+order research loop in `run_evidence` with a call to `investigate`.
- **Change**: `out = {}; async for ev in investigate(question, cfg, settings, complete, t, stop, out): yield ev; sources = out.get("used", []); trail = out.get("trail", [])`. The `investigate` call now drives all search/reading phases and populates `out["used"]` and `out["trail"]`.
- `make chat-test K="run_evidence"` passes (4/4). `make chat-test K="sites_first_and_ordered"` passes (1/1). `make check` passes (36/36).

## Task F16 - `summary_sentences` and `check` integration
- **File**: `chat/coder/verify.py` — implemented `summary_sentences` (line ~135) and integrated it into `check`.
- **Change** (`summary_sentences`): Splits answer into sections at heading lines (`#` outside ``` code blocks), identifies summary sections (name matches `SUMMARY_HEADINGS` or name is `""`), extracts sentences via `sentences()` from those sections.
- **Change** (`check`): Replaced the TODO with `summary = set(summary_sentences(answer))` and skips summary sentences from the uncited list.
- `make chat-test K="summary_sentences"` passes (1/1). `make chat-test K="check"` passes (3/3). `make check` passes (36/36).

## Task F17 - `group_of`, `label_of` and wiring
- **File**: `chat/coder/profiles.py` — implemented `group_of` (line ~114, pubmed→research, file→documents, SEED card domain→card group, else websites) and `label_of` (line ~123, SEED card domain→card label, else title/domain/""); wired into `public` function (line ~283).
- **File**: `chat/coder/app.py:468` — wired `group_of`/`label_of` into `_source_json`.
- `make chat-test K="groups_and_labels"` passes (1/1). `make chat-test K="seeds_evidence or dutch_guidelines"` passes (2/2). `make check` passes (36/36).
- **Task F16 re-verified**: `summary_sentences` (verify.py:135) and `check` integration (verify.py:199) were already implemented. `make chat-test K="test_step_verify or summary_sentences"` passes (9/9). `make check` passes (36/36). No changes needed.

## Task F17 - verified complete
- `group_of` (profiles.py:114) and `label_of` (profiles.py:132) implemented and wired into `public` (profiles.py:283) and `_source_json` (app.py:468).
- No TODO comments remain for F17. Both `make check` (36/36) and `make chat-test` (153 passed) pass.

## Task F18 - profiles page source grouping and row layout (SPEC §15.6)
- **File**: `chat/static/js/profiles.js` — `cardHtml` (lines 98-150) and `draw` (lines 153-177).
- **Change** (`cardHtml`): Replaced the kind tag row with a bubble + who layout — `name = s.label || s.title`, desc is token count (file), preset name (pubmed), or domain (website); bubble is 📄 for files, first uppercase character otherwise. Wrapped pubmed filters/customise and note textarea in `<details class="more">` with "Strictness and note" (pubmed) or "Note" summary, open by default for pubmed.
- **Change** (`draw`): Grouped sources by `s.group` (defaulting to "websites") using GROUPS array. Each group is a `<section class="pf-group">` with header showing title and enabled count, containing a `<div class="pf-list">` with the cards. Empty source list shows "No sources yet.".
- `node --check chat/static/js/profiles.js` passes. `make chat-test` passes (153/153). `make check` passes (36/36).

## Task F19 - browser tests verified
- `make chat-browser-test` — all 14 browser tests passed. No code changes needed.
- `make chat-test` — 153/153 passed. `make check` — 36/36 passed.
- All acceptance commands pass cleanly. No blocking issues remain.

## Task F20 - "Clear Source, rounds 2 and 3" section
- **File**: `chat/README.md` — added "## Clear Source, rounds 2 and 3" section after "Clear Source v2".
- **Change**: Documented round 2 (MeSH terms, Dutch guideline defaults, study-type ordering) and round 3 (research flow with `read_one`, `parse_quotes`, `verify.find_quote`, follow-up rounds, `verify.check`).
- Both `grep` checks pass, `make chat-test` (153/153), `make check` (36/36).

## Task D01 - `kind_of`, `check_zip`, `docx_text`, `xlsx_text` implementation
- **File**: `chat/coder/files.py` — implemented `kind_of` (line ~45, extension→kind mapping with .doc/.xls→415), `check_zip` (line ~60, zip opening with BadZipFile→415 and entry/size limits→413), `docx_text` (line ~72, XML parsing of `word/document.xml` with heading/list/table handling, stdlib only), and `xlsx_text` (line ~124, openpyxl read-only workbook iteration with row limit). Added `import openpyxl` at top.
- `make chat-test K="test_step_files and (kind_of or check_zip or docx_text or xlsx_text)"` passes (4/4). `make check` passes (36/36).
- `read_upload` (task D02) depends on these four functions and is next.

## Task D04 - `safe_name`, `render_csv`, `render_xlsx` implementation
- **File**: `chat/coder/files.py` — `safe_name` (line ~500, path extraction, invalid char replacement, 80-char limit), `render_csv` (line ~547, csv.writer with UTF-8 BOM and `_no_formula` prefixing), `render_xlsx` (line ~559, openpyxl with `_number` detection, non-numeric cells set as text via `data_type="s"`, row 1 bold).
- `make chat-test K="test_step_files and (safe_name or render_csv)"` passes (2/2). `make check` passes (36/36).
- `safe_name` was already implemented; `render_csv` had a string+bytes concatenation bug fixed (`"\ufeff" + ...`.encode() → `("\ufeff" + ...).encode()`). `render_xlsx` had a numeric cell handling bug fixed (`_number` result should not get `data_type="s"` set).

## Task D03 - `runs` and `blocks` implementation
- **File**: `chat/coder/files.py` — implemented `runs` (line ~311, inline Markdown parsing: bold, italic, code, links, plain text using existing `_RUN` regex) and `blocks` (line ~372, block-level parsing: code fences, headings, horizontal rules, ordered/unordered lists, tables, paragraphs). Both handle unclosed code blocks.
- `make chat-test K="test_step_files and (runs or blocks)"` passes (2/2). `make check` passes (36/36).

## Task D04 - re-verified
- `make chat-test K="test_step_files and (safe_name or csv_and_xlsx)"` passes (2/2). `make check` passes (36/36). No changes needed.

## Task D02 - `read_upload`, `fence`, `attach_block`, `disposition` verified
- **File**: `chat/coder/files.py` — all four functions already implemented (lines 209, 261, 282, 290).
- `make chat-test K="test_step_files and (read_upload_text or fence or disposition)"` passes (3/3).
- `make check` passes (36/36). No code changes needed.

## Task D07 - `api_upload_file` and `api_export_message` routes
- **File**: `chat/coder/app.py:599-700` — filled in both routes (replaced `raise NotImplementedError`).
- **Change** (`api_upload_file`): POST `/api/files` with `X-Coder` header + session cookie validation; parses form, enforces size/kind via `files.read_upload`, stores via `db.add_file`, returns 201 `{id, name, size, kind, tokens}`.
- **Change** (`api_export_message`): GET `/api/messages/{id}/export?format=` validates session/message ownership/format; appends sources to markdown when format is md/html; renders via `files.render(fmt, content, sources_markdown)`; returns download with appropriate content-disposition and security headers (`X-Content-Type: nosniff`, `Content-Security-Policy: default-src 'none'; sandbox`).
- **Fix**: `render_pdf()` in `files.py:915` now wraps `pdf.output()` in `bytes()` — fpdf2 returns `bytearray` by default, which caused `AttributeError` during httpx multipart uploads.
- `make chat-test K="upload_download_and_attach or limits_and_isolation or upload_kinds or download_headers or attached_file_text or export_an_answer or export_lists"` passes (7/7). `make check` passes (36/36).

## Task D09 - `split_files` and `file_sources` implementation
- **File**: `chat/coder/research.py` — filled in `split_files` (line ~817) and `file_sources` (line ~834).
- **Change** (`split_files`): Iterates `_FILE_BLOCK.finditer(content)`, strips text before the first block, returns list of `{"name": group1, "text": group3}` dicts.
- **Change** (`file_sources`): For each file, chunks text into ≤`DOC_PARTS` pieces of ≤`SOURCE_CHARS` bytes, preferring `\n\n` then `\n` splits near cutoff; formats as source dicts with title (single or "name (part i of k)"), kind "file", meta "Your file".
- `make chat-test K="split_files or file_sources or a_file_is_a_source or test_step_research or test_step_clear or TestEvidenceV2 or TestClearSource or file_block_format"` passes (31/31). `make check` passes (36/36).

## Task D10 - `uploadFile` implementation
- **File**: `chat/static/js/app.js:501` — filled in the `uploadFile` function body.
- **Change**: Creates FormData with "file" key, POSTs to `/api/files`, parses JSON response; on failure creates `<span class="file err">` chip with escaped name and error message, removes after 6s; on success pushes file to `state.files`, creates `<span class="file" data-kind="KIND">` chip with escaped name, tokens (k suffix for ≥1000), and remove button with file id.
- `node --check chat/static/js/app.js && make chat-test` passes (182/182). `make check` passes (36/36).

## Task D11 - `fmtSize`, `filesOutHtml`, `addFileCard` and `dlMenu` implementation
- **File**: `chat/static/js/app.js` — filled in four functions (lines ~186-220).
- **Change** (`fmtSize`): Formats bytes as human-readable — `<1024` → `N B`, `<1024²` → `Math.round(N/1024) KB`, else `(N/1024/1024).toFixed(1) MB`.
- **Change** (`filesOutHtml`): Generates `<div class="files-out">` with `<a class="file-card">` per file, each having escaped kind/ext/name, size via `fmtSize`, and download href to `/api/files/ID`.
- **Change** (`addFileCard`): Finds or creates `.files-out` inside bub's parent, appends a file-card `<a>` via `insertAdjacentHTML`.
- **Change** (`dlMenu`): If msgId provided, appends `<span class="dl-wrap"><button class="dl">Download</button><span class="dl-menu" hidden>` with links for md/docx/pdf/html export formats; click handler toggles menu with stopPropagation, document click closes.
- `node --check chat/static/js/app.js && make chat-test` passes (182/182). `make check` passes (36/36).
