# SPEC: agent-status service

A small web service that shows what the local coding agent is doing: is the model up, which
run is active, which task it is on, what is queued, recent events, and a few stats. It also
hands out the **lease** on the model: one GPU, so one `agent-loop` run at a time; the others
wait in line. `harness/loop.py` is the client (class `Status`); it already speaks this API.

The contract tests in `status/tests/test_contract.py` are the judge. This file explains them.

## 0. Constraints

- One file: `status/app.py`. **Python 3.11, standard library only** (`http.server`,
  `sqlite3`, `json`, `threading`, `urllib`, `html`). No pip packages, no build step.
- Must expose:

  ```python
  def make_server(host: str, port: int, *, db_path: str, token: str,
                  llama_url: str | None = None, lease_ttl: float = 180,
                  probe_interval: float = 30, clock=time.time) -> ThreadingHTTPServer
  ```

  It creates the database if needed, binds `(host, port)` (port 0 = any free port) and
  returns the server **without** starting it; the caller runs `serve_forever()`.
  Every notion of "now" (lease expiry, stale runs, stats windows, probe cache, retention)
  must come from `clock()`, never from `time.time()` directly. The tests move the clock.
- `python3 status/app.py` runs it (see §7).
- Your own extra tests go in `status/tests/test_unit_*.py` (`make check` runs those).

## 1. Auth

Write endpoints (`POST`) need `Authorization: Bearer <token>`. Missing or wrong → **401**.
If the configured token is the empty string, every write is refused with 401.
Read endpoints (`GET`) are open; the service is only reachable over Tailscale.
Compare tokens with `hmac.compare_digest`.

## 2. `GET /healthz`

200, body `ok`.

## 3. `POST /api/events`

Body: a JSON object (max 256 KiB, else **413**). Required: `run_id`, `repo`, `kind`, `ts`
(all strings). `kind` is one of:

`run_started`, `task_started`, `task_done`, `task_skipped`, `push_failed`, `ci_waiting`,
`ci`, `run_finished`

Optional fields the harness sends: `branch`, `task_id`, `title`, `attempt`, `attempts`,
`retries`, `seconds`, `reason`, `detail`, `plan`, `ended`, `done`, `skipped`, `pushed`, `pr`,
`max_hours`, `fix_ci`. Store the **whole** body; ignore fields you don't know.

`plan` is a list of `{"id": "T01", "title": "...", "state": "todo" | "done" | "skipped"}`.

Invalid JSON, a non-object, a missing/non-string required field or an unknown kind → **400**.
Success → **201** `{"ok": true}`.

Store events in SQLite (`db_path`) with the time they were **received** (`clock()`); that
time, not the client's `ts`, drives ordering, stale detection and stats. Events survive a
restart.

## 4. `POST /api/lease`

Body: `{"action": "acquire" | "renew" | "release", "run_id": str, "repo": str, "branch": str}`.
`branch` may be missing (treat as `""`). Unknown action or missing `run_id`/`repo` → 400.

State (in memory; losing it on restart is fine, clients re-acquire): one **holder** or none,
and a FIFO list of **waiters**. Each entry: `run_id`, `repo`, `branch`, `since` (ISO time it
first asked), `seen` (last `clock()` it called).

First drop expired entries: holder and waiters whose `seen` is older than `lease_ttl`.

- `acquire` and `renew` (identical):
  - caller is the holder → refresh `seen`; granted.
  - no holder and (no waiters, or caller is the first waiter) → caller becomes holder,
    leaves the waiters; granted.
  - otherwise → add caller to the end of the waiters if not already there (keep its place
    if it is), refresh `seen`; not granted.
- `release` → remove the caller as holder or waiter. Never an error, also for unknown runs.

Response 200:

```json
{"granted": true, "position": 0, "holder": {"run_id": "...", "repo": "...", "branch": "...", "since": "..."}}
```

`position` is 0 when granted (or after a release), else the caller's 1-based place among the
waiters. `holder` is the current holder after the action, or `null`.

## 5. `GET /api/status`

```json
{
  "now": "2026-11-16T10:00:00+00:00",
  "model": {"state": "ready", "checked": "2026-11-16T09:59:40+00:00"},
  "lease": {"holder": {...} | null, "queue": [{"run_id": "...", "repo": "...", "branch": "...", "since": "..."}]},
  "runs": [...],
  "events": [...],
  "stats": {...}
}
```

All times are ISO 8601 UTC strings made from `clock()` values.

### 5.1 `model`

`llama_url` is the `LLAMA_BASE_URL` the agent uses, e.g. `http://pc:8080/v1`. Probe
`GET <root>/health` where `<root>` is `llama_url` without a trailing `/v1` (2 s timeout,
no auth). 200 → `ready`, 503 → `loading`, any other answer or a connection error →
`unreachable`. No `llama_url` → `unknown` with `checked: null`.
Cache the result for `probe_interval` seconds (by `clock()`): several status requests within
that window cause **one** probe. Probe lazily inside the request; no background thread.

### 5.2 `runs`

One entry per `run_id`, newest **last event** first, at most 10:

```json
{"run_id": "...", "repo": "woodshed", "branch": "agent/...", "state": "running",
 "started": "...", "last_event": "...",
 "current": {"task_id": "T01", "title": "...", "attempt": 2} | null,
 "done": 1, "skipped": 0, "plan": [...], "ended": null, "pushed": null, "pr": null}
```

- `started`: received time of the run's first event. `last_event`: of its latest.
- `branch`: from the latest event that has one.
- `state`: `finished` if it has a `run_finished` event; else `running` if its latest event is
  at most 3600 s old **or** it holds the lease; else `stale`.
- `current`: the latest `task_started` of the run if no `task_done`/`task_skipped` with the
  same `task_id` came after it; else `null`. Always `null` once finished.
- `done` / `skipped`: number of `task_done` / `task_skipped` events of the run.
- `plan`: the `plan` of the latest event of the run that has one; `[]` if none.
- `ended`, `pushed`, `pr`: from its `run_finished` event; `null` before that.

### 5.3 `events`

The newest 50 events over all runs, newest first. Each: the stored body plus
`"received": "<ISO time>"`.

### 5.4 `stats`

Over all stored events:

```json
{"tasks_done": 2, "tasks_skipped": 1, "first_try_rate": 0.5,
 "avg_task_minutes": 15.0, "tasks_per_hour_24h": 0.083}
```

- `tasks_done` / `tasks_skipped`: counts of those event kinds.
- `first_try_rate`: share of `task_done` events with `attempts == 1`; `null` if none done.
- `avg_task_minutes`: mean `seconds` of `task_done` events / 60, rounded to 1 decimal;
  `null` if none done.
- `tasks_per_hour_24h`: `task_done` events received in the last 24 h (by `clock()`) / 24,
  rounded to 3 decimals.

## 6. `GET /` (the page)

`text/html; charset=utf-8`, rendered on the server from the same data as `/api/status`, so it
is useful without JavaScript. No external resources: no CDN, no web fonts, no `http(s)://`
in any `src` or `href` (a run's `pr` link is the only allowed exception). Escape every
value with `html.escape`; event bodies come from the network.

Sections, top to bottom:

1. **Model**: a coloured badge with the state (`ready` green, `loading` amber,
   `unreachable` red, `unknown` grey).
2. **Now**: the running run (repo, branch, current task id + title + attempt, minutes since
   `started`), or "Idle" when nothing runs.
3. **Queue**: the lease holder and the waiters in order; then the running run's plan tasks
   that are still `todo`, in order.
4. **Recent**: the newest 20 events (received time, repo, kind, task id, title/detail/reason).
5. **Stats**: the numbers from §5.4.
6. **Runs**: the 10 runs with state, done/skipped, branch, PR link if any.

A small inline script fetches `/api/status` every 5 s and redraws (sections update
without a full reload). Mobile friendly (single column under 700 px), dark by default, no
horizontal scroll. Other paths → 404.

## 7. Running it

`python3 status/app.py` reads:

| Variable | Default | |
| --- | --- | --- |
| `STATUS_PORT` | `8080` | listens on `0.0.0.0` |
| `STATUS_DB` | `/data/status.db` | |
| `STATUS_TOKEN` | (empty) | empty = all writes refused |
| `LLAMA_BASE_URL` | (unset) | model probe off when unset |
| `LEASE_TTL` | `180` | seconds |
| `PROBE_INTERVAL` | `30` | seconds |

On start, delete events received more than 30 days ago (by `clock()`). This happens in
`make_server`, so it applies to tests too. Log one line per request to stderr is fine;
never log the token.
