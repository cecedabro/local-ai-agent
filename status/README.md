# agent-status

The status page for the local coding agent, plus its job queue and the lease on the model.
One stdlib-only Python file (`app.py`); the full contract is in [SPEC.md](SPEC.md).

What it shows: the model state, the running run and task, live model activity (every request
through the gateway, per client), the job queue and the model lease, all-time
commits/lines/tokens/PRs, token speed, commits per day, recent events and runs. Plus a quick
chat with the model on the page and a full one at `/chat`. Reading is open (it is only
reachable over Tailscale); the queue and the chat need the token.

**Gateway:** clients that use the model (agent-loop/OpenCode, scrum-agent, the chat) point at
`http://<status>/v1` with their own key instead of at llama-server. Interactive clients go
first; batch ones wait. Only sizes and timings are recorded, never prompts or answers.

## Run it locally

```bash
STATUS_DB=/tmp/status.db STATUS_TOKEN=dev python3 status/app.py
```

Then open http://localhost:8080 and paste `dev` to unlock the controls.

| Variable | Default | |
| --- | --- | --- |
| `STATUS_PORT` | `8080` | |
| `STATUS_DB` | `/data/status.db` | SQLite file |
| `STATUS_TOKEN` | (empty) | bearer token for every POST; empty = all writes refused |
| `LLAMA_BASE_URL` | (unset) | e.g. `http://pc:8080/v1`; model badge and token speed |
| `LLAMA_API_KEY` | (unset) | llama-server key, for `/metrics` |
| `METRICS_INTERVAL` | `60` | seconds between token samples |
| `LEASE_TTL`, `PROBE_INTERVAL`, `JOB_TTL` | `180`, `30`, `900` | seconds |
| `JOB_OWNERS` | (unset) | e.g. `cecedabro`: only those repos can be queued |
| `GATEWAY_CLIENTS` | (unset) | `agent-loop:batch:<key>,scrum-agent:interactive:<key>` |
| `GATEWAY_CONCURRENCY` | `1` | parallel requests to llama-server |

Token speed needs llama-server started with `--metrics` (in `pc/llama/llama-server.env`).

## The pieces that talk to it

- `agent-loop` (`harness/loop.py`) with `STATUS_URL` + `STATUS_TOKEN`: sends events and
  takes the model lease (`/api/events`, `/api/lease`).
- `agent-queue` (`harness/worker.py`) on the devbox, started by the entrypoint in tmux window
  `queue` (needs `STATUS_URL`, `STATUS_TOKEN`, `QUEUE_OWNERS`): takes jobs, runs `agent-loop`
  for each, sends heartbeats and the result. Pause: `touch ~/.agent-queue-STOP`.
- Anything OpenAI-compatible, through the gateway: `LLAMA_BASE_URL=http://<status>/v1` and
  its own key as the API key.

## Endpoints

```bash
T=dev; U=http://localhost:8080
curl $U/healthz
curl $U/api/status
curl -H "Authorization: Bearer $T" -d '{"repo":"cecedabro/woodshed","note":"next batch"}' $U/api/jobs
curl -H "Authorization: Bearer $T" -d '{"to":"top"}' $U/api/jobs/3/move
curl -H "Authorization: Bearer $T" -d '{}' $U/api/jobs/3/cancel
curl -H "Authorization: Bearer $T" -d '{"action":"acquire","run_id":"r1","repo":"woodshed"}' $U/api/lease
curl -H "Authorization: Bearer $T" -d '{"model":"coder","messages":[{"role":"user","content":"hi"}]}' $U/v1/chat/completions
curl -H "Authorization: Bearer $T" -d '{"run_id":"r1","repo":"woodshed","kind":"ci","ts":"2026-11-16T10:00:00Z","detail":"hi"}' $U/api/events
```

Tests: `make check` (queue, totals, tokens, gateway) and `make status-test` (the original contract).
