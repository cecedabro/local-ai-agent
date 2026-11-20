# How it fits together

A map of every moving part, how they talk, and where to look when something breaks. Design
choices and their history: `docs/PLAN.md` (the setup) and `docs/design/` (the looks). More detail:
`docs/DEWDROP.md` (the chat app), `docs/CLEAR-SOURCE.md` (the research profile), `docs/STATUS.md` (where things stand).

## The big picture

```
                    phone / Mac (Tailscale)                     the internet (Cloudflare Tunnel)
                           │                                                  │
             ┌─────────────┴─────────────┐                                    │
             ▼                           ▼                                    ▼
   agent-status page              devbox-0 (SSH, tmux)                 Dewdrop chat (chat/)
   status/app.py                  agent-queue  ──► agent-loop ──► OpenCode
     │  queue, history, plans        (worker.py)     (loop.py)       │
     │  model lease, metrics            │  claims jobs                │ model requests
     │  /v1 gateway ◄───────────────────┼─────────────────────────────┘
     │      ▲   ▲                       │  ssh pc: keep awake, wake, restart
     │      │   └── chat app ───────────┼─────────────── (later, through the gateway too)
     │      ▼                           ▼
     └──► llama-server on the PC (WSL2, RTX 3080 Ti), alias "coder"
                ▲
                └── waker pod (host network) sends the Wake-on-LAN packet
```

Everything except the PC runs on the k3s homelab in namespace `ai-agent`, deployed by Flux from the
homelab repo (`apps/ai-agent/`). Secrets live in OpenBao (`secret/ai-agent/*`) and reach the pods
through External Secrets; nothing secret is in this repo.

## The parts

| Part | Code | Runs as | Job |
|---|---|---|---|
| Model server | `pc/llama/` | systemd unit in WSL2 on the PC | Serves Qwen3.6-35B-A3B (4-bit, MTP) as `coder` on :8080, `/metrics` on |
| Power control | `pc/power/`, `mac/ai` | Windows tasks + an SSH forced command (:2223) | Keeps the PC awake while `/run/ai/busy` exists; `ai pause/resume` from the Mac |
| Waker | homelab `apps/ai-agent/` | pod on the host network | Sends the magic packet that wakes the sleeping PC |
| agent-status | `status/app.py` (stdlib) | Deployment `agent-status`, Tailscale only | The status page, the job queue, run history, the model lease, token metrics, and the `/v1` gateway |
| Devbox | `devbox/` | StatefulSet `devbox-0` (+ Postgres sidecar) | The agent's workplace: repos in `~/repos`, OpenCode, toolchains, Chromium |
| agent-queue | `harness/worker.py` | tmux window `agent:queue` in the devbox | Polls the queue, checks out the job's branch, runs agent-loop; lists repos and plans for the page |
| agent-loop | `harness/loop.py`, `harness/prompt.md` | started by the worker per job | Works through `PLAN.md` one task at a time (below) |
| Dewdrop | `chat/` (FastAPI) | its own image and pod (namespace `dewdrop`), public via Cloudflare Tunnel | The chat app: invite-only accounts with passkeys, chats and folders, streaming, web search, Clear Source (`docs/DEWDROP.md`) |
| devbox-rollout | homelab CronJob | every 5 min | Restarts `devbox-0` on a new image, only when the status service says it is idle |

## Path 1: an agent loop

1. You pick a repo and a plan on the status page ("Start an agent loop") → `POST /api/jobs`.
   The plans come from the worker, which reads `PLAN.md` on the default branch and every `agent/*`
   branch of your repos (SPEC §16).
2. agent-queue claims the job (`/api/jobs/claim`), fetches the repo in `~/repos/<name>`, checks out
   the branch and starts `agent-loop`. On the default branch the loop first makes its own branch,
   `agent/<plan-title>`, so it never commits to main.
3. agent-loop takes the model lease (`/api/lease`, one user of the GPU at a time), wakes the PC if it
   sleeps (`ssh pc`), and sets the busy flag so Windows doesn't sleep mid-run.
4. For each open task (`- [ ] T01: …` or `S01`, `CI1`: one to three capitals and a number):
   a fresh OpenCode session gets only that task line plus the rules in `prompt.md`; the agent edits
   the code; the loop runs the task's `accept:` command and `make check`. Pass → commit and push.
   Fail → retry with the failure output, at most 3 attempts, then mark it `[~]` skipped.
   Changes to protected paths (`.agent-protected`, tests, specs) reject the attempt.
5. Every step is reported to the status service (`/api/events`) and shows on the page. Pause (the
   Now card) stops after the current task and frees the model and the PC; Stop ends it at once.
6. At the end the loop waits for CI, writes `REPORT.md`, opens a PR and releases the lease and the PC.

Writing plans that the 48k-token model can finish: small tasks, one or two functions each, the
steps in the function's docstring, and a small test per task (see the chat plan on
`agent/chat-app-foundation` for the pattern).

## Path 2: a chat message (Dewdrop)

1. The browser posts the message (`POST /api/chats/<id>/messages`, with the `X-Coder` header and
   the session cookie).
2. Dewdrop stores it and asks the status gateway (`<STATUS>/v1/chat/completions`, its own key in
   `GATEWAY_CLIENTS`). The gateway puts chat in the interactive lane, ahead of agent work; if the model
   is busy it answers 503 and Dewdrop shows "Waiting for the model… you're next" and retries.
3. The gateway forwards to llama-server and streams the tokens back; Dewdrop turns them into its own
   events (phase / delta / done), stores the answer and the token counts.
4. In the browser, `stream.js` buffers the text and writes it at a steady pace; the dewdrop shows the
   phase (clouds while thinking, water while writing).
5. When the context passes 75 % of the model's window, older messages are summarised first.
6. In the Clear Source profile the app runs the research itself and the model only plans, reads and
   writes; every quote is checked word for word (`docs/CLEAR-SOURCE.md`).

The status page's own quick chat uses the same gateway.

## Path 3: the status page

`GET /` renders the page on the server; every 5 s the browser swaps in `/partial`. All data is also
at `/api/status` (JSON, no token needed). Changing things (queue, pause, hide repos) needs the status
token, stored in the browser after "Unlock". The service keeps its state in SQLite on a volume:
jobs, events, token samples, daily totals, imported history (`status/backfill.py`).

## Deploying and updating

| What | How it updates |
|---|---|
| agent-status | Merge to main → CI tests → image `X.Y.Z` (git tag `status-vX.Y.Z`) → Flux rolls it in ~3 min |
| Dewdrop | Same pattern (`chat-vX.Y.Z`, image `local-ai-agent-chat`, ImagePolicy `dewdrop`) |
| devbox | Merge to main (devbox/ or harness/) → image `X.Y.Z` (`devbox-vX.Y.Z`) → Flux writes it into the StatefulSet (`OnDelete`) → devbox-rollout restarts the pod at the next idle moment; the queue starts no new job while an update waits (SPEC §15). Roll back by pinning an older tag in the homelab repo |
| the PC | By hand: `pc/llama/build-llama.sh`, unit and flags in `pc/llama/` |

## Security boundaries

- The status page and the devbox are reachable over Tailscale only. Dewdrop is the only public part,
  with invite-only accounts (argon2 passwords, rate-limited login, CSRF header, strict CSP) and
  Cloudflare Access in front.
- The devbox has no Kubernetes token, egress is limited (internet on 80/443, the PC, the waker), and
  its GitHub token can push branches and open PRs but the agent never merges.
- The agent cannot edit protected paths, cannot pause the model (only you can, `ai pause`), and the
  model is never reachable without a gateway key or the PC's LAN firewall.

## When something breaks

| Symptom | Look at |
|---|---|
| Page says "model unreachable" | Is the PC asleep or paused (`ai status` on the Mac)? `ssh pc`, `systemctl status llama-server` in WSL |
| "no worker" on the page | tmux window `agent:queue` in `devbox-0`; `~/.agent-queue-STOP` pauses it |
| A job never starts | "devbox update waiting" on the Queue card (it starts after the restart); `jobs.running` in `/api/status` |
| Tasks keep getting skipped | `~/repos/<repo>/.agent/logs/<task>-a<n>.log` in the devbox; the `accept:` command output is at the end; usually the task is too big |
| Push or PR fails | The GitHub token's repo access (`gh auth status` in the devbox) |
| Chat says the model is busy for long | An agent run holds the model; pause it from the Now card |
| Clear Source takes minutes | Normal while an agent loop runs: each of its ~10 model calls waits for the agent's current request |
| Clear Source: "PubMed answered 429" | NCBI's rate limit; `tools.ncbi_get` spaces calls out and retries, an `NCBI_API_KEY` raises the limit |
| Status page not updated after a merge | The `status-image` workflow on GitHub, then the Flux ImagePolicy `agent-status` in the homelab |
| Devbox not updated | The `devbox-image` workflow, ImagePolicy `agent-devbox`, CronJob `devbox-rollout` logs |
