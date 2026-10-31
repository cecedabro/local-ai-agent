#!/usr/bin/env bash
# Devbox init: first-boot config, SSH key for the PC, tailscaled (userspace,
# with Tailscale SSH), a long-lived tmux session. Runs as `agent`.
set -euo pipefail

TS_DIR="${HOME}/.tailscale"
TS_SOCK="${TS_DIR}/tailscaled.sock"
SECRETS=/etc/devbox/secrets

mkdir -p "${TS_DIR}" "${HOME}/repos" "${HOME}/.config/opencode" "${HOME}/.ssh"
chmod 700 "${HOME}/.ssh"

# Agent config: seeded once, then yours to edit on the PVC.
[[ -f "${HOME}/.config/opencode/opencode.json" ]] || cp /etc/devbox/opencode.json "${HOME}/.config/opencode/opencode.json"

# SSH key for the PC: a mounted secret wins (ssh refuses group-readable keys, so copy it with
# 0600); otherwise generate one once on the PVC. Its public half goes to pc/power/setup-wsl.sh.
if [[ -f "${SECRETS}/pc_ssh_key" ]]; then
  install -m 0600 "${SECRETS}/pc_ssh_key" "${HOME}/.ssh/pc_key"
elif [[ ! -f "${HOME}/.ssh/pc_key" ]]; then
  ssh-keygen -q -t ed25519 -N '' -C devbox -f "${HOME}/.ssh/pc_key"
fi
[[ -f "${HOME}/.ssh/pc_key.pub" ]] || ssh-keygen -y -f "${HOME}/.ssh/pc_key" > "${HOME}/.ssh/pc_key.pub"
echo "PC key (for pc/power/setup-wsl.sh): $(cat "${HOME}/.ssh/pc_key.pub")"

# The devbox also starts before the PC is set up; 'ssh pc' then just fails.
[[ -n "${PC_HOST:-}" && -n "${PC_SSH_USER:-}" ]] || echo "PC_HOST/PC_SSH_USER not set: 'ssh pc' won't work yet" >&2
cat > "${HOME}/.ssh/config" <<CONF
Host pc
  HostName ${PC_HOST:-pc.invalid}
  Port 2222
  User ${PC_SSH_USER:-agent}
  IdentityFile ~/.ssh/pc_key
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
  ConnectTimeout 5
CONF
chmod 600 "${HOME}/.ssh/config"

# Git identity for agent commits.
git config --global user.name "${GIT_AUTHOR_NAME:-local-agent}"
git config --global user.email "${GIT_AUTHOR_EMAIL:-agent@localhost}"
git config --global init.defaultBranch main
if [[ -n "${GITHUB_TOKEN:-}" ]]; then gh auth setup-git >/dev/null 2>&1 || true; fi

# Tailscale in userspace mode: no NET_ADMIN, no /dev/net/tun.
tailscaled --tun=userspace-networking --statedir="${TS_DIR}" --socket="${TS_SOCK}" &
TS_PID=$!
for _ in $(seq 1 30); do [[ -S "${TS_SOCK}" ]] && break; sleep 1; done
UP_ARGS=(--ssh --hostname="${TS_HOSTNAME:-devbox}")
if [[ -n "${TS_AUTHKEY:-}" ]] && ! tailscale --socket="${TS_SOCK}" status >/dev/null 2>&1; then
  UP_ARGS+=(--authkey="${TS_AUTHKEY}")
fi
tailscale --socket="${TS_SOCK}" up "${UP_ARGS[@]}" || echo "tailscale up failed; check auth key" >&2

tmux has-session -t agent 2>/dev/null || tmux new-session -d -s agent -c "${HOME}/repos"

echo "devbox ready"
wait "${TS_PID}"
