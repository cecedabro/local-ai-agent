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
