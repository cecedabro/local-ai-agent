# local-ai-agent

Infrastructure and harness for running a local LLM as an autonomous coding agent:
the model runs on a Windows PC (WSL2 + llama.cpp), the agent runs in an always-on
devbox pod on the k3s homelab, wakes the PC over Wake-on-LAN when there is work,
and lets it sleep again afterwards.

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
| `devbox/` | Container image for the agent pod | k3s |
| `k8s/` | Kustomize manifests: namespace, devbox, waker, policies | k3s (via Flux) |
| `harness/` | `loop.py` and its prompt; project templates | devbox pod |
| `docs/` | Plan, runbook notes | — |

## Rollout

| Phase | Do | Done when |
| --- | --- | --- |
| 1 | `pc/README.md` steps 1–9 | `curl` from a k3s node gets a completion |
| 2 | `pc/power/` + `k8s/waker/` | Sleeping PC answers within ~90 s, sleeps again after idle |
| 3 | Build `devbox/`, apply `k8s/devbox/` | Phone → Tailscale SSH → tmux → agent edits a toy repo |
| 4 | `harness/loop.py` on a toy spec | 10-task spec runs unattended overnight |
| 5 | Weather station repo from `harness/templates/` | Backend + gateway pass tests on agent branches |

Values you must fill in are marked `REPLACE_ME` (`grep -rn REPLACE_ME .`).

## Checks

```
make lint     # shellcheck, python compile, yaml parse
make test     # harness self-test against a throwaway repo with a fake agent
```
