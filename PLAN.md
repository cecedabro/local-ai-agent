# PLAN: agent-status service

Build `status/app.py` per `status/SPEC.md`. Each task adds one group of contract tests to its
`accept:` and keeps the earlier groups passing. Python 3.11 standard library only.

- [ ] T01: Create status/app.py with make_server (SPEC §0), SQLite event storage, GET /healthz, POST /api/events with token auth (SPEC §1–3), and GET /api/status returning "now" and "events" (SPEC §5.3; other keys may be placeholders for now)
  accept: python3 -m unittest -q status/tests/test_contract.py -k EventsTest
- [ ] T02: Add the model lease: POST /api/lease and the "lease" key of GET /api/status (SPEC §4, §5)
  accept: python3 -m unittest -q status/tests/test_contract.py -k EventsTest -k LeaseTest
- [ ] T03: Add the "runs" key of GET /api/status: one entry per run with state, current task, done/skipped counts and plan (SPEC §5.2)
  accept: python3 -m unittest -q status/tests/test_contract.py -k EventsTest -k LeaseTest -k StatusRunsTest
- [ ] T04: Add the "stats" key of GET /api/status (SPEC §5.4)
  accept: python3 -m unittest -q status/tests/test_contract.py -k EventsTest -k LeaseTest -k StatusRunsTest -k StatsTest
- [ ] T05: Add the "model" key of GET /api/status: probe llama-server /health with a cache (SPEC §5.1)
  accept: python3 -m unittest -q status/tests/test_contract.py -k EventsTest -k LeaseTest -k StatusRunsTest -k StatsTest -k ModelTest
- [ ] T06: Serve the server-rendered status page at GET / with the six sections, escaping and 404 for other paths (SPEC §6, without the script)
  accept: python3 -m unittest -q status/tests/test_contract.py -k EventsTest -k LeaseTest -k StatusRunsTest -k StatsTest -k ModelTest -k PageTest
- [ ] T07: Add the inline script that refreshes the page from /api/status every 5 s, the viewport meta tag, and a clean dark, mobile-friendly style (SPEC §6)
  accept: python3 -m unittest -q status/tests/test_contract.py -k EventsTest -k LeaseTest -k StatusRunsTest -k StatsTest -k ModelTest -k PageTest -k PageScriptTest
- [ ] T08: Make python3 status/app.py runnable with the environment variables of SPEC §7, and delete events older than 30 days in make_server
  accept: python3 -m unittest -q status/tests/test_contract.py
- [ ] T09: Write status/README.md: what the service does, how to run it locally, the environment variables, and the endpoints with one curl example each
  accept: test -s status/README.md && grep -q STATUS_TOKEN status/README.md && grep -q /api/lease status/README.md
