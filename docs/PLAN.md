# Plan (condensed)

Full version with sources: <https://claude.ai/artifact/VtJfAgPfNyxZrbmQnLrLMp> (as of 2026-10-30).

## Short version
The PC serves one local model from WSL2. An always-on devbox pod on k3s runs the
agent, wakes the PC over Wake-on-LAN when there is work, and lets Windows put it
back to sleep when done.

- **Model:** Qwen3.6-35B-A3B (Apache 2.0, Apr 2026, agentic-coding tuned), Q4 GGUF
  with MTP speculative decoding. Experts partly in RAM (`--n-cpu-moe`), 48k
  context, q8 KV cache. Served as alias `coder`.
- **Inference node:** Windows + WSL2 Ubuntu 24.04, llama-server as systemd unit,
  mirrored networking, firewall open to the LAN only.
- **Control plane:** devbox pod (tmux, OpenCode, Pi, git, toolchains) reached
  over Tailscale SSH; waker pod on host network sends the magic packet.
- **Loop:** one task per fresh agent session, `PLAN.md` is the only state,
  acceptance commands decide done, commit or reset, 3 retries then skip, ntfy
  notifications, `REPORT.md` + PR at the end.
- **Safety:** own namespace, no SA token, egress policy, repo-scoped GitHub
  token, branch protection on `main`, protected paths.

## Model choice
| Model | Verdict |
| --- | --- |
| Qwen3.6-35B-A3B | Pick: MoE fits 12 GB VRAM + 32 GB RAM with expert offload |
| Qwen3.8-27B (dense) | Needs 24 GB+ VRAM; first choice on the future rig |
| Qwen3.8-Flash-Next (~180B/6B) | Too big for 32 GB RAM; future-rig candidate (check licence) |
| Qwen3-Coder-30B-A3B | Fallback if 3.6 tool calls misbehave |

Expected output speed on a 3080 Ti: roughly 30–45 tok/s (estimate; measure with `pc/llama/bench.sh`).

## Phases
1. **Inference node** — `pc/README.md`. Done: `curl` from a k3s node gets a completion.
2. **Power control** — `pc/power/`, waker (homelab `apps/ai-agent/`). Done: sleeping PC answers in ~90 s,
   stays awake through a 30-min job, sleeps ~20 min after.
3. **Devbox** — `devbox/`, homelab `apps/ai-agent/`. Done: phone → Tailscale SSH → tmux →
   agent edits a toy repo against the PC.
4. **Loop** — `harness/`. Done: 10-task toy spec runs unattended overnight.
5. **Weather station** — new repo from `harness/templates/`, order:
   backend → gateway → deploy → frontend → firmware.

## Risks
| Risk | Mitigation |
| --- | --- |
| CUDA broken after resume | `pc-up` restarts llama-server when a completion fails |
| WoL unreliable (Modern Standby / NIC) | Keep PC awake for planned runs |
| Malformed tool calls | `--jinja`; try Pi instead of OpenCode |
| Model loops / claims done | Harness decides done; retry cap; smaller tasks |
| Windows Update reboot | Startup task brings WSL back; loop resumes from `PLAN.md` |
| Agent misbehaves | Branch-only token, egress policy, protected paths |

## Upgrade path
New rig → only the inference node changes. Serve the bigger model under alias
`coder`, point `LLAMA_URL` at it. Everything else stays.
