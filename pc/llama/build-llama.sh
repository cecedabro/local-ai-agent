#!/usr/bin/env bash
# Build llama.cpp with CUDA into ~/src/llama.cpp/build-<ref>, then repoint the
# ~/src/llama.cpp/current symlink. Previous builds are kept for rollback.
# Usage: build-llama.sh [git-ref]   (pin a release tag, e.g. b7xxx)
set -euo pipefail

REF="${1:-${LLAMA_CPP_REF:-master}}"
SRC="${HOME}/src/llama.cpp"

if [[ ! -d "${SRC}/.git" ]]; then
  git clone https://github.com/ggml-org/llama.cpp "${SRC}"
fi

git -C "${SRC}" fetch --tags --prune origin
git -C "${SRC}" checkout --detach "${REF}"
SHA="$(git -C "${SRC}" rev-parse --short HEAD)"
BUILD="${SRC}/build-${SHA}"

if ! command -v nvcc >/dev/null 2>&1 && [[ -x /usr/local/cuda/bin/nvcc ]]; then
  export PATH="/usr/local/cuda/bin:${PATH}"
fi
command -v nvcc >/dev/null || { echo "nvcc not found: install the CUDA toolkit (WSL-Ubuntu repo)" >&2; exit 1; }

# Only the local GPU's architecture (much faster than all of them). CUDA_ARCH=86 for an
# RTX 30xx when the GPU can't be queried at build time.
cmake -S "${SRC}" -B "${BUILD}" -DGGML_CUDA=ON -DBUILD_SHARED_LIBS=OFF -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCH:-native}"
cmake --build "${BUILD}" --config Release -j "$(nproc)" --target llama-server llama-bench

"${BUILD}/bin/llama-server" --version
ln -sfn "${BUILD}" "${SRC}/current"
echo "Active build: ${BUILD} (${REF} @ ${SHA})"
echo "Restart with: sudo systemctl restart llama-server"
