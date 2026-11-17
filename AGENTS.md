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

## status/ (the agent-status service)
- Spec: `status/SPEC.md`. Judge: `status/tests/test_contract.py` and
  `status/tests/test_accept_history.py`, `status/tests/test_accept_pause.py` (all protected; don't edit) and
  `status/tests/test_unit_queue.py`, `status/tests/test_unit_gateway.py`.
- One file, `status/app.py`, Python 3.11 **standard library only** (no pip packages).
  Exception: `status/backfill.py`, the history import script (SPEC §12.2), same rules.
- Your own tests: `status/tests/test_unit_*.py` (`make check` runs them).
- Paths in `.agent-protected` are human-owned; changes there reject the attempt.

## Agent runs (agent-loop)
- Runs are non-interactive: nobody answers questions. Never end with a question or a plan; make the
  edits, run the task's accept command, fix what fails, then stop.
- Never read a large file whole (`status/app.py` is ~1700 lines): `grep -n`, then read the lines you
  need with `sed -n 'a,bp' <file>`.

## Commands
- `make lint` — must pass before committing.
- `make test` — harness self-test.
- `make check` — the agent loop's project check (compile + `status/tests/test_unit_*.py`).
- `make status-test` — all contract tests of the status service.
