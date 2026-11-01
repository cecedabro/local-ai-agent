# Plan (condensed)

Full version with sources: <https://claude.ai/artifact/VtJfAgPfNyxZrbmQnLrLMp> (as of 2026-10-30).

## Status (2026-11-01)
Phases 1, 3 and 4 are done; phase 2 is built but its sleep/wake test is still open; phase 5 is
under way. Measured on the 3080 Ti: ~43 tok/s generation, ~790 tok/s prompt reading, VRAM
~11.9 of 12.3 GB (do not add VRAM use: a spill is ~10x slower). Settings in use: `--n-cpu-moe 26`,
48k context, `-ub 2048 --spec-draft-n-ubatch 512`, MTP draft.

## Short version
The PC serves one local model from WSL2. An always-on devbox pod on k3s runs the
agent, wakes the PC over Wake-on-LAN when there is work, and lets Windows put it
back to sleep when done.

- **Model:** Qwen3.6-35B-A3B (Apache 2.0, Apr 2026, agentic-coding tuned), Q4 GGUF
  with MTP speculative decoding. Experts partly in RAM (`--n-cpu-moe`), 48k
  context, q8 KV cache. Served as alias `coder`.
- **Inference node:** Windows 10 + WSL2 Ubuntu (NAT; `ai-power.ps1` forwards 8080/2222 and keeps WSL up
  via a boot task), llama-server as systemd unit, firewall open to the LAN only.
- **Control plane:** devbox pod (tmux, OpenCode, Pi, git, toolchains) reached
  over Tailscale SSH; waker pod on host network sends the magic packet.
- **Loop:** one task per fresh agent session, `PLAN.md` is the only state,
  acceptance commands decide done, commit or reset, 3 retries then skip, ntfy
  notifications, `REPORT.md` + PR at the end.
- **Safety:** own namespace, no SA token, default-deny egress (public internet on 80/443 plus the PC and
  waker; no hostname filtering, the CNI cannot), repo-scoped GitHub token, PRs into `main`, protected paths.
  The devbox cannot pause the model; only the user can (`ai pause`).
- **Agent environment:** a toolbox in the image (uv, node, platformio, kustomize, shellcheck, Chromium,
  bandit, pip-audit, schemathesis), a Postgres sidecar, offline docs via `fetch-docs`. No Docker.

## Model choice
| Model | Verdict |
| --- | --- |
| Qwen3.6-35B-A3B | Pick: MoE fits 12 GB VRAM + 32 GB RAM with expert offload |
| Qwen3.8-27B (dense) | Needs 24 GB+ VRAM; first choice on the future rig |
| Qwen3.8-Flash-Next (~180B/6B) | Too big for 32 GB RAM; future-rig candidate (check licence) |
| Qwen3-Coder-30B-A3B | Fallback if 3.6 tool calls misbehave |

Measured on the 3080 Ti: ~43 tok/s generation, ~790 tok/s prompt reading (`pc/llama/bench.sh`).

## Phases
1. **Inference node** — `pc/README.md`. Done: `curl` from a k3s node gets a completion.
2. **Power control** — `pc/power/`, waker (homelab `apps/ai-agent/`). Done: sleeping PC answers in ~90 s,
   stays awake through a 30-min job, sleeps ~20 min after.
3. **Devbox** — `devbox/`, homelab `apps/ai-agent/`. Done: phone → Tailscale SSH → tmux →
   agent edits a toy repo against the PC.
4. **Loop** — `harness/`. Done: it ran the weather station plan unattended overnight
   (13 of 15 tasks passed, 2 skipped, 4.9 h, ~10 min per task). The skipped ones were too big or too
   vague, and one model reply asked for confirmation instead of working.
5. **Weather station** — repo `cecedabro/weatherstation`, docs written from `harness/templates/` style
   (SPEC, AGENTS, PLAN, contract, area docs). Order changed: **website first** (backend API + frontend +
   admin), then gateway, deploy, firmware once the hardware is decided. Gates in `make check`: contract
   tests, 90% coverage, bandit.

## Risks
| Risk | Mitigation |
| --- | --- |
| CUDA broken after resume | `pc-up` restarts llama-server when a completion fails |
| WoL unreliable (Modern Standby / NIC) | Keep PC awake for planned runs |
| Malformed tool calls | `--jinja`; try Pi instead of OpenCode |
| Model loops / claims done | Harness decides done; retry cap; smaller tasks |
| Windows Update reboot | Startup task brings WSL back; loop resumes from `PLAN.md` |
| Agent misbehaves | Branch-only token, egress policy, protected paths |
| Weak tests that pass trivially | `docs/testing.md` rules in the project, coverage floor; mutation testing is a candidate |
| Task too big or vague (skipped) | Small tasks with a named test file in `accept:`; "never ask a human" rule in AGENTS.md |
| Pod memory (node has 7.6 GB) | One browser, no parallel heavy jobs; node RAM upgrade is possible (2 SO-DIMM slots) |

## Upgrade path
New rig → only the inference node changes. Serve the bigger model under alias
`coder`, point `LLAMA_URL` at it. Everything else stays.

## Lessons so far
- Write the project docs for a small context (48k): a short AGENTS.md, one area doc per task, SPEC lines
  that tests can check. The model follows them well.
- The tool environment must match what the docs assume (no Docker or Postgres in the pod meant the first
  draft could not run its own tests; fixed with `pgserver`, later a Postgres sidecar).
- `PLAN.md` is the only way to steer a run. Add tasks, reset `[~]` to `[ ]` to retry, keep tasks ordered.

## Open items
See the end of `README.md`.
