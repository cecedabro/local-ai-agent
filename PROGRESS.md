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
