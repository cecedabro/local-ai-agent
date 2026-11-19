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
