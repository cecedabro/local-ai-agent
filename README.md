# local-ai-agent

Infrastructure and harness for running a local LLM as an autonomous coding agent:
the model runs on a Windows PC (WSL2 + llama.cpp), the agent runs in an always-on
devbox pod on the k3s homelab, wakes the PC over Wake-on-LAN when there is work,
and lets it sleep again afterwards.

**Status (2026-11-01): running.** The model serves at ~43 tok/s (generation) and ~790 tok/s (prompt
reading), the devbox runs the loop, and the loop has already built a real project (the weather
station website: 13 of 15 tasks passed unattended in 4.9 h). Open items are at the end of this file.

Full plan with rationale and sources: [docs/PLAN.md](docs/PLAN.md) and the
[plan doc](https://claude.ai/artifact/VtJfAgPfNyxZrbmQnLrLMp).

```
k3s (always on)                                 PC (Windows + WSL2)
┌──────────────────────────────────┐            ┌───────────────────────────┐
│ devbox pod  tmux · opencode ·    │── API ────▶│ llama-server  (alias      │
│             loop.py · tests      │            │   "coder", :8080)         │
│     │              │             │── SSH ────▶│ ai-power-remote (:2222)   │
│     ▼              ▼             │            │   busy / status / restart │
│  ntfy          waker (hostNet)───┼─ magic ───▶│ ai-power.ps1 keeps PC     │
└─────┬────────────────────────────┘  packet    │   awake while busy        │
      ▼                                         └───────────────────────────┘
   phone (Tailscale SSH, ntfy)        GitHub (branches + PRs only)
```

## Layout

| Path | What | Runs on |
| --- | --- | --- |
| `pc/wsl/` | `.wslconfig`, `wsl.conf` | PC |
| `pc/llama/` | build, bench, systemd unit, server flags | PC (WSL) |
| `pc/power/` | keep-awake, pause/resume, SSH forced command, setup | PC (Windows + WSL) |
| `devbox/` | Container image for the agent pod (toolchains, QA tools, Chromium, `fetch-docs`) | k3s |
| `mac/` | `ai pause\|resume\|status`: control the model from the Mac over `ssh pc` | Mac |
| (homelab repo) `apps/ai-agent/` | Cluster side: namespaces, devbox StatefulSet, waker, NetworkPolicy, ExternalSecrets | k3s (via Flux) |
| `harness/` | `loop.py` and its prompt; project templates | devbox pod |
| `docs/` | Plan, runbook notes | — |

## Rollout

| Phase | Do | Done when | Status |
| --- | --- | --- | --- |
| 1 | `pc/README.md` steps 1–9 | `curl` from a k3s node gets a completion | done |
| 2 | `pc/power/` + waker in the homelab repo, `apps/ai-agent/` | Sleeping PC answers within ~90 s, sleeps again after idle | built; the sleep/wake test through the waker is still to do |
| 3 | Build `devbox/` (CI), devbox in the homelab repo, `apps/ai-agent/` | Phone → Tailscale SSH → tmux → agent edits a toy repo | done (`ssh agent@devbox`) |
| 4 | `harness/loop.py` on a toy spec | 10-task spec runs unattended overnight | done, on the real weather station repo instead of a toy |
| 5 | Weather station repo from `harness/templates/` | Backend + frontend pass tests on agent branches | in progress: website first, gateway/firmware/deploy later |

Values you must fill in are marked `REPLACE_ME` (`grep -rn REPLACE_ME .`).

The Kubernetes manifests live in the homelab repo (github.com/cecedabro/homelab, `apps/ai-agent/`),
deployed by Flux like everything else there; secrets come from OpenBao via External Secrets.
Reason: one GitOps source for the cluster, and this repo (which agents work on) gets no power over
cluster objects such as the privileged waker namespace.

## Day to day

| I want to | Command |
| --- | --- |
| Open the agent | `ssh agent@devbox` (lands in tmux session `agent`; `NO_TMUX=1` skips tmux) |
| Run the loop | in the project repo on the devbox: `agent-loop` (stop after the current task: `touch .agent/STOP`) |
| Tell it what to do | edit `PLAN.md` / `SPEC.md` / `AGENTS.md` in the project repo; the agent cannot edit them |
| Free the GPU for myself | on the Mac: `ai pause`; give it back with `ai resume`; `ai status` |
| Watch the GPU | `ssh -t pc "nvidia-smi -l 2"` |
| Read the model log | `ssh pc 'wsl -d Ubuntu -u root -- journalctl -u llama-server -f'` |
| Get offline docs for the agent | on the devbox: `fetch-docs` (into `~/docs`, searched with `rg`) |

Tools available to the agent in the devbox image: git, gh, make, ripgrep, jq, uv, node, platformio,
kubeconform, kustomize, shellcheck, psql, system Chromium (`CHROMIUM_PATH`), bandit, pip-audit,
schemathesis. A Postgres sidecar listens on `localhost:5432` (`DATABASE_URL` is set; data is wiped
on pod restart). No Docker, nothing privileged. Memory is tight: the node has 7.6 GB, so run one
browser and no parallel heavy jobs.

## Safety as built

- Own namespace, no service account token, repo-scoped GitHub token (the agent works on branches and opens PRs; you merge).
- Egress policy: DNS, the public internet on 80/443 (plus Tailscale ports), the PC (8080, 2222) and
  the waker. Private ranges and other pods are blocked. The cluster network cannot filter by
  hostname, so GitHub/PyPI/npm are not singled out. Ingress is closed.
- The devbox key on the PC only runs `ai-power-remote` (busy/status/restart). It cannot pause or
  resume the model: that switch is yours (`ai pause`), and while paused nothing restarts the server.
- Protected paths the agent may not change (checked by the harness): `tests/`, `contract/`,
  `PLAN.md`, `SPEC.md`, `AGENTS.md` (set with `PROTECTED`).

## Checks

```
make lint     # shellcheck, python compile, yaml parse
make test     # harness self-test against a throwaway repo with a fake agent
```

## Open items

- Sleep/wake test of the PC through the waker (sleep, wake from the devbox, back within ~90 s).
- Occasional slow first request after idle (~13 tok/s prompt reading); unexplained, investigate if it recurs.
- Open WebUI in the homelab still points at the removed Ollama; repoint it to llama-server (needs the key via OpenBao).
- Router DHCP reservation for the PC.
- Netdata in WSL for GPU/CPU graphs (planned, not installed).
- Weather station: gateway, firmware and deploy tasks are not in its `PLAN.md` yet.
