#!/usr/bin/env bash
# Sweep --n-cpu-moe to find the lowest value that fits in VRAM.
# Watch `nvidia-smi -l 1` in another terminal; keep ~1 GB VRAM free.
# Usage: bench.sh [model.gguf] [values...]
set -euo pipefail

MODEL="${1:-${HOME}/models/Qwen3.6-35B-A3B-MTP-UD-Q4_K_XL.gguf}"
shift || true
VALUES=("$@")
[[ ${#VALUES[@]} -gt 0 ]] || VALUES=(24 20 16 12)
BENCH="${HOME}/src/llama.cpp/current/bin/llama-bench"
OUT="${HOME}/bench-$(date +%Y%m%d-%H%M).md"

for n in "${VALUES[@]}"; do
  echo "== --n-cpu-moe ${n}" | tee -a "${OUT}"
  # -p 4096: prompt processing at agent-like length; -n 256: generation.
  "${BENCH}" -m "${MODEL}" -ngl 99 --n-cpu-moe "${n}" -fa 1 \
    -ctk q8_0 -ctv q8_0 -p 4096 -n 256 -r 2 -o md 2>&1 | tee -a "${OUT}" || \
    echo "failed at ${n} (likely out of VRAM)" | tee -a "${OUT}"
done
echo "Results: ${OUT}"
