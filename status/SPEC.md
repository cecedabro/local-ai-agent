# SPEC: agent-status service

A small web service that shows what the local coding agent is doing: is the model up, which
run is active, which task it is on, what is queued, recent events, all-time numbers and token
speed. It also hands out the **lease** on the model (one GPU, so one `agent-loop` run at a
time; the others wait in line) and keeps a **job queue** that `agent-queue`
(`harness/worker.py`) works through. `harness/loop.py` is the event and lease client (class
`Status`).

It is also the **model gateway** (§11): an OpenAI-compatible front door to llama-server with
per-client keys and a priority queue, so chat and scrum replies go before agent-loop work,
and every request shows up on the page.

The tests are the judge: `status/tests/test_contract.py` (§1–§7),
`status/tests/test_unit_queue.py` (§8–§10), `status/tests/test_unit_gateway.py` (§8, §11) and
`status/tests/test_accept_history.py` (§8 repo list, §11 `n_ctx`, §12) and
`status/tests/test_accept_pause.py` (§13).
This file explains them.

## 0. Constraints

- One file: `status/app.py`. **Python 3.11, standard library only** (`http.server`,
  `sqlite3`, `json`, `threading`, `urllib`, `html`). No pip packages, no build step.
  Static files (the stylesheet, later scripts) live in `status/static/` and are served at
  `GET /static/<name>` (`.css`/`.js` only, read once at start); pages link them with a
  `?v=<content hash>` so a new image never shows a stale stylesheet.
- Must expose:

  ```python
  def make_server(host: str, port: int, *, db_path: str, token: str,
                  llama_url: str | None = None, lease_ttl: float = 180,
                  probe_interval: float = 30, clock=time.time,
                  llama_key: str | None = None, job_ttl: float = 900) -> ThreadingHTTPServer
  ```

  It creates the database if needed, binds `(host, port)` (port 0 = any free port) and
  returns the server **without** starting it; the caller runs `serve_forever()`.
  `server.app.sample_metrics()` takes one token sample (§9); `main()` calls it on a timer.
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
`max_hours`, `fix_ci`, `job_id`, and on `task_done` the commit: `commit` (short sha),
`files`, `insertions`, `deletions`. Store the **whole** body; ignore fields you don't know.

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

Besides these keys, `/api/status` has `jobs` (§8), `totals` and `daily` (§10) and `tokens`
(§9). A run also carries `job_id`, `max_hours`, `task_since` and `retries`.

## 6. `GET /` (the page)

`text/html; charset=utf-8`, rendered on the server from the same data as `/api/status`, so it
is useful without JavaScript. No external resources: no CDN, no web fonts, no `http(s)://`
in any `src` or `href` (a run's `pr` link is the only allowed exception). Escape every
value with `html.escape`; event bodies come from the network.

Sections, top to bottom:

1. **Header**: a coloured badge with the model state (`ready` green, `loading` amber,
   `unreachable` red, `unknown` grey), always with its text.
2. **Controls** (outside the refreshed part, so typing isn't interrupted): read-only until
   the viewer pastes the token; it is kept in that browser's `localStorage` and checked with
   `POST /api/auth`. Unlocked: a form to add a job (§8).
3. **Now**: the running run (repo, branch, current task id + title + attempt, time on the
   task and in the run, plan progress bar), or "Idle" when nothing runs.
4. **Queue**: the running job (Stop) and the queued jobs in order (↑ ↓ ✕); the lease holder
   and waiters; recent jobs (requeue); next to it the running run's `todo` plan tasks.
5. **All time**: commits, lines, tokens written/read, PRs, agent hours, first-try rate.
6. **Token speed**: writing/reading tok/s now (15 min) and on average, and a 24-bar chart of
   tokens written per hour.
7. **Commits per day**: 14 bars, plus a per-repo table.
8. **Recent**: the newest 20 events (received time, repo, kind, task id, title/detail/reason,
   commit sha and size).
9. **Runs**: the 10 runs with state, done/skipped, branch, PR link if any.

`GET /partial` returns sections 3–9 only. The inline script fetches it every 5 s and swaps
it in (same renderer as the full page, same data as `/api/status`), then shows times in the
viewer's time zone (`<time datetime>`; UTC without JavaScript). `GET /api/status/head` gives
the header badge. Mobile friendly (single column under 700 px; wide tables scroll inside
their card), dark by default with a light variant, no horizontal page scroll. Other paths → 404.

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
| `LLAMA_API_KEY` | (unset) | sent to llama-server's `/metrics` |
| `METRICS_INTERVAL` | `60` | seconds between token samples; `0` = off |
| `JOB_TTL` | `900` | a running job without heartbeat for this long becomes `lost` |
| `STATUS_QUIET` | (unset) | `1` = no request log |
| `JOB_OWNERS` | (unset) | GitHub owners whose repos may be queued; unset = any |
| `GATEWAY_CLIENTS` | (unset) | `name:lane:key,...`, lane `interactive` or `batch`, key ≥ 8 chars |
| `GATEWAY_CONCURRENCY` | `1` | requests sent to llama-server at the same time |

On start, delete events received more than 30 days ago (by `clock()`). This happens in
`make_server`, so it applies to tests too. Log one line per request to stderr is fine;
never log the token.

## 8. Job queue

Jobs are what `agent-queue` (on the devbox) runs, one at a time: clone or update the repo,
check out the branch, run `agent-loop` with `AGENT_JOB_ID` set. All endpoints need the token.

A job: `id`, `repo` (`owner/name`), `ref` (branch, `""` = the repo's default), `note`,
`max_hours`, `ci`, `state` (`queued`, `running`, `done`, `failed`, `cancelled`, `lost`),
`created`, `started`, `finished`, `worker`, `stopping`, `detail`, `exit_code`, `run_id`
(the newest run whose events carry this `job_id`).

- `POST /api/jobs` `{"repo", "ref"?, "note"? (≤200), "max_hours"? (0.1–24, default 24),
  "ci"? (bool, default true)}` → **201** with the job. `repo` must match
  `owner/name` (letters, digits, `_.-`); `ref` letters, digits, `._/-`. Else **400**.
- `POST /api/jobs/claim` `{"worker"}` → `{"job": job | null}`. Null while any job is running
  (one GPU) or none is queued; else the first queued job becomes `running`.
- `POST /api/jobs/<id>/heartbeat` → `{"cancel": bool}`. Refreshes `seen`; a `lost` job comes
  back to `running`. `cancel` is true once Stop was pressed or the job is no longer running.
- `POST /api/jobs/<id>/finish` `{"state": "done"|"failed"|"cancelled", "detail"?, "exit_code"?}`;
  only for `running`/`lost` jobs, else **409**.
- `POST /api/jobs/<id>/cancel`: queued → `cancelled`; running → `stopping` (the worker
  touches `.agent/STOP`, so the run ends after its current task); else **409**.
- `POST /api/jobs/<id>/move` `{"to": "top"|"up"|"down"|"bottom"}`: queued only (**409**),
  unknown `to` → **400**.
- `POST /api/jobs/<id>/requeue`: a finished job is queued again as a new job; **409** while
  it is queued or running.
- Unknown id → **404**. A running job whose `seen` is older than `job_ttl` becomes `lost`.
- `JOB_OWNERS` (comma-separated, case-insensitive): when set, only repos of those owners can
  be queued (else **400**). The worker checks its own `QUEUE_OWNERS` again before running.
- `POST /api/workers/ping` `{"worker", "paused"}`: a paused worker (`~/.agent-queue-STOP`
  exists) says so instead of claiming. Claims count as pings. `jobs.workers` lists workers
  seen in the last 10 minutes: `{name, seen, paused}`; the page warns when there are none.

- **Repo list.** Pings and claims may carry `"repos": ["owner/name", ...]` (the worker asks
  `gh repo list` for its `QUEUE_OWNERS` every 10 minutes). The newest non-empty list a worker
  sent is kept with that worker (in memory; a restart forgets it until the next ping, ≤ 30 s).
  Items that are not strings matching `owner/name`, or (with `JOB_OWNERS` set) belong to
  another owner, are dropped; at most 500 are kept.

`/api/status` → `jobs`: `{"running": [...], "queued": [... in order], "recent": [10 newest
finished in the last 7 days], "workers": [...], "repos": [...]}`. Jobs live in SQLite and
survive a restart. `repos` is the sorted (case-insensitive), de-duplicated union of the repo
lists of the workers in `workers` and the `repo` of every stored job, without repos of other
owners when `JOB_OWNERS` is set.

Page: the add-job form picks the repo from a `<select name="repo">` with one `<option>` per
`jobs.repos` entry (value and text `owner/name`), in that order. When `jobs.repos` is empty it
shows `<input name="repo">` instead, as before.

## 9. Token speed

With `llama_url` set, `sample_metrics()` reads `<root>/metrics` (llama-server needs
`--metrics`; `Authorization: Bearer <llama_key>` when set): the counters
`llamacpp:tokens_predicted_total`, `…tokens_predicted_seconds_total`,
`…prompt_tokens_total`, `…prompt_seconds_total` and the gauge `…requests_processing`.
Each sample stores the increase since the previous one; a counter that went down
(llama-server restarted) counts from zero. Samples older than 30 days are dropped; the
all-time totals are kept separately.

`/api/status` → `tokens`: `available`, `error` (why the last sample failed, e.g.
"llama-server runs without --metrics"), `last_sample`, `busy` (null if no sample in 5 min),
`generated_total`, `prompt_total`, `gen_tps` and `prompt_tps` (all-time tokens ÷ seconds),
`gen_tps_recent`, `prompt_tps_recent` (last 15 min), `generated_24h`, `hourly` (24 ×
`{hour, generated, gen_tps}`, oldest first).

## 10. All-time numbers

Kept in their own tables when events arrive, so the 30-day event retention doesn't touch them.
`/api/status` → `totals`: `since`, `commits` (`task_done` events), `tasks_skipped`,
`lines_added`/`lines_removed` (sum of `insertions`/`deletions`), `runs` (`run_started`),
`prs` (`run_finished` with a `pr`), `push_failures`, `agent_hours` (sum of task `seconds`),
`first_try_rate`, and `repos` (the same per repo, newest activity first).
`daily`: the last 14 days (UTC), oldest first: `{day, commits, skipped, lines_added,
lines_removed, generated}`.

## 11. Model gateway

Active when `llama_url` is set (else `/v1/*` → **404**).

- `POST /v1/chat/completions`, `POST /v1/completions`: the body (≤ 16 MiB, a JSON object)
  goes unchanged to `<root>/v1/...` with `Authorization: Bearer <llama_key>`; the answer
  comes back unchanged, streamed line by line when it is `text/event-stream`.
  `GET /v1/models` is passed through without queueing. Other `/v1/` paths → 404.
- Callers authenticate with `Authorization: Bearer <key>`: a key from `GATEWAY_CLIENTS`
  (`name:lane:key`), or the status token, which is client `chat` in lane `interactive` (the
  page's chat). Unknown key → **401**. Errors use OpenAI's shape `{"error": {"message"}}`.
- Queue: at most `concurrency` requests upstream at once. The next one is the oldest
  `interactive` request, else the oldest `batch` one. A caller that hangs up while waiting
  is dropped (recorded as 499).
- llama-server unreachable → **502** "model unreachable (PC asleep?)". Upstream HTTP errors
  pass through with their status.
- Every request is recorded: client, lane, queued/started/finished, status, prompt and
  generated tokens and their milliseconds (from llama-server's `timings`, or `usage`).
  **Prompts and answers are never stored.** Per-client all-time totals are kept separately
  from the 30-day request retention.

`/api/status` → `gateway`: `enabled`, `clients`, `waiting` (in the order they will run:
`{id, client, lane, waited_s}`), `running` (`{id, client, lane, stream, for_s, tokens}`;
`tokens` counts streamed chunks so far), `recent` (15 newest: `client, lane, finished,
waited_s, took_s, status, prompt, generated, gen_tps, error`), `by_client` (all-time
`requests, prompt, generated, errors, gen_tps` plus `requests_24h`, `generated_24h`).

`gateway` also has `n_ctx`: the model's context size in tokens, from llama-server's
`GET <root>/props` (`default_generation_settings.n_ctx`, falling back to a top-level `n_ctx`;
`Authorization: Bearer <llama_key>` when set; 2 s timeout). It is fetched at most once per
`probe_interval` while unknown, and kept once known (a later failure keeps the last value).
`null` until the first success, and when the gateway is off.

Theme: an Auto / Light / Dark switch in the header of `/` and `/chat` (kept in the browser's
`localStorage`, applied before the first paint; Auto follows the device).

Page: a **Model activity** section after Now (running, waiting in order, last requests, per
client). A **chat** card above the live sections (only when unlocked and the gateway is on)
and a full-height `GET /chat` page: it streams from `/v1/chat/completions` with the status
token, keeps the history in the browser's `localStorage` only, has Stop and Clear, and a
**Quick / Think** switch (`chat_template_kwargs.enable_thinking`; the choice is remembered
in the browser). While it waits, the answer bubble shows an animated orb with the step:
waiting for the model, reading your message, thinking (Think only); the text replaces it.

## 12. History import

The page only counts what was reported to it. `status/backfill.py` (run by hand on the devbox)
rebuilds the earlier history from git and from OpenCode's own database and sends it here.

### 12.1 `POST /api/import`

Token required (§1). Body (≤ 8 MiB, else **413**; not a JSON object → **400**):

```json
{"events": [{"key": "woodshed:f16676e", "repo": "woodshed", "kind": "task_done",
             "ts": "2026-11-16T19:41:43+00:00", "task_id": "T77", "title": "Cover art",
             "commit": "f16676e", "insertions": 120, "deletions": 4, "attempts": 1}],
 "tokens": [{"day": "2026-11-15", "source": "opencode", "generated": 51234, "prompt": 2345678}]}
```

Both lists are optional (missing = `[]`), each at most 5000 items. Validation happens first;
any bad item → **400** `{"error": "..."}` naming the list and index, and nothing is stored.

- An **event** needs `key` (non-empty string ≤ 200), `repo` (non-empty string), `kind`
  (`run_started`, `task_done`, `task_skipped` or `run_finished`) and `ts` (ISO 8601 with a
  time zone, e.g. `2026-11-16T19:41:43+00:00` or `...Z`). Other fields as in §3.
- A **tokens** row needs `day` (`YYYY-MM-DD`), `source` (non-empty string ≤ 50), and
  `generated`, `prompt` (integers ≥ 0).

Events: a `key` that was imported before is skipped (keys are kept forever, in SQLite). A new
event updates the all-time numbers of §10 exactly like a live event of that kind would
(`tally` of its repo, `daily` of **the UTC day of its `ts`**, not of now), and `since`
becomes the earlier of `since` and `ts`; the repo's last activity becomes the later of the
stored one and `ts`. Imported events are **not** stored as events: they do not show in
`events`, `runs` or `stats` (§5), which describe the live window.

Tokens: each `(day, source)` row replaces the earlier import of the same `(day, source)`, so
running the import twice counts once. Rows for a day on or after the UTC day of the first
live token sample (§9; remembered even after the samples are pruned) are ignored, because the
live counters cover those days. Imported tokens add to `tokens.generated_total`,
`tokens.prompt_total` and to `generated` of the matching `daily` days; they do not change any
tok/s figure, `generated_24h` or `hourly`.

Answer **200**:
`{"events_added": 3, "events_skipped": 1, "token_days": 2, "token_days_ignored": 0}`.

### 12.2 `status/backfill.py`

Python 3.11, standard library only, runnable as `python3 status/backfill.py`:

```
backfill.py [--repos DIR] [--opencode-db FILE] [--dry-run]
```

- `--repos` (default `~/repos`): every direct subdirectory with a `.git` is one repo, named
  by the directory name (the same `repo` agent-loop reports).
- `--opencode-db` (default `~/.local/share/opencode/opencode.db`): skipped when missing.
- Sends to `STATUS_URL` + `/api/import` with `Authorization: Bearer $STATUS_TOKEN`, in
  requests of at most 1000 events each (the tokens go with the first request). Prints one line
  per request with the answer's numbers. `--dry-run` prints the counts and sends nothing.
  Missing `STATUS_URL`/`STATUS_TOKEN` (without `--dry-run`) → exit 2 with a message.

The module exposes these functions, which the tests call directly:

- `git_events(repo_dir: Path, name: str, gh=None) -> list[dict]`
  - From `git log --all` (each commit once): every commit whose author name is `local-agent`
    and whose subject matches `^(T\d+): (.*)` becomes a `task_done` event: `key`
    `"<name>:<full sha>"`, `repo` name, `ts` the author date as ISO 8601 UTC
    (`+00:00`), `task_id`, `title` (the rest of the subject, cut to 200 characters), `commit`
    (the first 7 characters of the sha), `insertions`, `deletions` (from `--shortstat`; 0
    when absent), and `attempts`: the highest `N` among files `.agent/logs/<task_id>-a<N>.log`
    and `.agent/logs-*/<task_id>-a<N>.log` in the repo, or 1 when there are none.
  - Every commit by `local-agent` with the subject `agent run report` becomes a
    `run_started` event with key `"<name>:run:<full sha>"` and its `ts`.
  - `gh` is a callable `gh(args: list[str], cwd: Path) -> str` returning stdout (default: run
    the `gh` CLI; any failure → `"[]"`). It is called once with
    `["pr", "list", "--state", "all", "--limit", "500", "--json", "number,headRefName,createdAt,url"]`;
    every PR whose `headRefName` starts with `agent/` becomes a `run_finished` event with key
    `"<name>:pr:<number>"`, `ts` its `createdAt`, and `pr` its `url`.
  - Sorted by `ts`, then `key`.
- `opencode_tokens(db_path: Path) -> list[dict]`: opens the SQLite file read-only, reads the
  `data` JSON of every row of table `message` whose `role` is `assistant` and that has
  `tokens`, and sums per UTC day of `time.created` (milliseconds since the epoch):
  `generated` = `tokens.output` + `tokens.reasoning`, `prompt` = `tokens.input` (missing
  numbers count as 0). Returns `[{"day", "source": "opencode", "generated", "prompt"}]`,
  oldest day first. Rows whose `data` is not valid JSON are skipped.
- `send(url: str, token: str, events: list[dict], tokens: list[dict]) -> list[dict]`: does
  the requests described above and returns the parsed answers in order. An HTTP error raises.

## 13. Pause a run

Pause is pressed on the page; `agent-loop` obeys it **between tasks** (`harness/loop.py`,
`Run.pause_point`): before each task it calls the control endpoint below. When `pause` is true
it sends a `paused` event, releases the lease (§4) and the PC, polls until `pause` is false
(or `.agent/STOP` appears), then queues for the lease again and sends `resumed`.

- `KINDS` (§3) gains `paused` and `resumed`.
- `POST /api/runs/<run_id>/pause` and `POST /api/runs/<run_id>/resume` (token, §1): set or
  clear the pause request of that run. Answer **200** `{"run_id": "...", "pause": true|false}`.
  A run with no stored event → **404**; a run with a `run_finished` event → **409**.
  `<run_id>` is URL-encoded on the page and decoded by the server (it may contain `-` and `.`).
- `POST /api/runs/<run_id>/control` (token): `{"pause": bool}`; an unknown run → `{"pause": false}`
  (200). This is what the loop polls.
- The request is kept in SQLite (table `run_control(run_id text primary key, pause int)`), so a
  restart of the service does not resume a paused run. A `run_finished` event of the run
  deletes its row.

`/api/status` → each run (§5.2) gains `pause` (the request, bool). Its `state` becomes
`paused` when its latest `paused`/`resumed` event is a `paused` one and it has no `run_finished`;
`paused` wins over `stale`, so a long pause does not look like a dead run.

Page (§6): the **Now** card shows the newest run whose state is `running` or `paused`
(running first). For that run, when unlocked (class `ctl`, hidden while locked):
- state `running`, no request: a `Pause` button
  (`<button class="ctl" data-run="<run_id>" data-act="pause">Pause after this task</button>`);
- state `running` with a request: a `<span class="pill warn">pausing after this task</span>`
  and a `Resume` button (`data-act="resume"`, which cancels the request);
- state `paused`: `<span class="pill warn">paused</span>` and the `Resume` button.
The page script posts `data-act` to `/api/runs/<encodeURIComponent(run_id)>/<act>` and refreshes.
