# PROGRESS

- [T06] `status/backfill.py`: added `opencode_tokens(db_path: Path) -> list[dict]` — reads OpenCode's SQLite DB via `file:ro` URI, uses `json_valid` + `json_extract` to safely filter assistant rows with tokens, groups by UTC date, sums input/output+reasoning, returns sorted list.
- `make check` + `BackfillOpencodeTest` both pass; no new dependencies, stdlib only.
