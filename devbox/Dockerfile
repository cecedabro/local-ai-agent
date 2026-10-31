# Agent devbox: tmux + coding agents + toolchains + tailscale (userspace).
# Build from the repo root: docker build -f devbox/Dockerfile .
FROM node:22-bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive
# Verify package names when bumping; see docs/PLAN.md for why both agents.
ARG OPENCODE_PKG=opencode-ai
ARG PI_PKG=@earendil-works/pi-coding-agent

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg git tmux ripgrep jq make less vim-tiny procps \
      openssh-client iproute2 build-essential python3 python3-venv python3-pip \
 && rm -rf /var/lib/apt/lists/*

# GitHub CLI and Tailscale from their official apt repos.
RUN install -d -m 0755 /etc/apt/keyrings \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli.gpg \
 && echo "deb [signed-by=/etc/apt/keyrings/githubcli.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
 && curl -fsSL https://pkgs.tailscale.com/stable/debian/bookworm.noarmor.gpg -o /etc/apt/keyrings/tailscale.gpg \
 && echo "deb [signed-by=/etc/apt/keyrings/tailscale.gpg] https://pkgs.tailscale.com/stable/debian bookworm main" > /etc/apt/sources.list.d/tailscale.list \
 && apt-get update && apt-get install -y --no-install-recommends gh tailscale \
 && rm -rf /var/lib/apt/lists/*

# uv (Python tooling) and kubeconform (manifest validation).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
RUN curl -fsSL https://github.com/yannh/kubeconform/releases/latest/download/kubeconform-linux-amd64.tar.gz \
    | tar -xz -C /usr/local/bin kubeconform

# Tools outside $HOME, because /home/agent is a PVC mount that would hide them.
ENV UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/usr/local/bin
RUN uv tool install platformio \
 && npm install -g "${OPENCODE_PKG}" "${PI_PKG}" \
 && npm cache clean --force

# Replace the image's `node` user with `agent` at the same uid.
RUN userdel -r node && useradd -m -u 1000 -s /bin/bash agent

COPY devbox/entrypoint.sh /usr/local/bin/devbox-entrypoint
COPY devbox/opencode.json /etc/devbox/opencode.json
COPY devbox/profile.sh /etc/profile.d/devbox.sh
COPY harness/ /opt/harness/
RUN chmod +x /usr/local/bin/devbox-entrypoint /opt/harness/loop.py \
 && ln -s /opt/harness/loop.py /usr/local/bin/agent-loop

USER agent
WORKDIR /home/agent
ENTRYPOINT ["/usr/local/bin/devbox-entrypoint"]
