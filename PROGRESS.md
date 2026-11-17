# PROGRESS

- [T06] `status/backfill.py`: added `opencode_tokens(db_path: Path) -> list[dict]` — reads OpenCode's SQLite DB via `file:ro` URI, uses `json_valid` + `json_extract` to safely filter assistant rows with tokens, groups by UTC date, sums input/output+reasoning, returns sorted list.
- `make check` + `BackfillOpencodeTest` both pass; no new dependencies, stdlib only.
- [T09] `status/app.py`: refactored `App.status()` to batch `run_control` query before the run iteration loop, replacing N per-run SQL queries with a single `IN` query and dict lookup; added `"pause": not fin and pause_req` to run dicts; hardened finish to always clear `pause` to `False`. `make check` + `PauseApiTest` both pass.
