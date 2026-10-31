# AGENTS.md

Context for any coding agent (Claude Code, OpenCode, Pi) working in this repo.

## What this repo is
Infra + harness for a local-LLM coding agent. Read `docs/PLAN.md` before
changing architecture. Decisions below are settled unless the user says otherwise.

## Settled decisions
- Model: Qwen3.6-35B-A3B, `UD-Q4_K_XL` GGUF (MTP variant), served by llama-server
  in WSL2 on the user's PC (RTX 3080 Ti 12 GB, 32 GB DDR4). Always exposed as
  alias `coder`; clients never reference the real model name.
- PC sleeps between jobs (S3). No passwordless login. Wake via magic packet from
  a hostNetwork waker pod. Keep-awake via `/run/ai/busy` flag in WSL.
- Agent runs in a devbox pod on k3s, never on the PC. Tests run in the pod.
- Agent CLI: OpenCode first, Pi as fallback. Not Aider.
- Access: Tailscale SSH inside the devbox container (userspace networking).
- Loop: Ralph-style, one task per fresh session, harness decides "done" by
  running acceptance commands. Protected paths may not be edited by the agent.
- Firmware for the weather station: Arduino C++ via PlatformIO (not Rust).
- Frontend for the weather station: server-rendered Jinja + HTMX.

## Conventions
- Shell: `bash`, `set -euo pipefail`, must pass `shellcheck`.
- Python: stdlib only in `harness/` (it runs in a minimal image), 3.11+.
- Kubernetes: manifests live in the homelab repo (`apps/ai-agent/`), not here; deployed by Flux.
- Secrets never committed. They live in OpenBao (`secret/ai-agent/*`) and reach the pods via External Secrets.
- Placeholders are literally `REPLACE_ME`.

## Commands
- `make lint` — must pass before committing.
- `make test` — harness self-test.
