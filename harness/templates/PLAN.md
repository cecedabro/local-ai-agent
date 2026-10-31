# PLAN

One task per line. `accept:` is a command that exits 0 when the task is done.
The harness marks tasks `[x]` (done) or `[~]` (skipped, with reason). Keep tasks small:
one file or one endpoint each.

- [ ] T01: Create the project skeleton per AGENTS.md with a passing `make check`
  accept: make check
- [ ] T02: Implement GET /health per SPEC §1
  accept: pytest tests/test_health.py -q
