#!/usr/bin/env bash
# One-shot setup inside WSL Ubuntu (run as your normal user; uses sudo).
# Installs: /run/ai flags dir, llama-server unit + env + API key, sshd on 2222
# (key-only), ai-power-remote forced command, narrow sudoers rule.
# Usage: setup-wsl.sh <devbox-ssh-public-key-file>
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"
ME="$(id -un)"
PUBKEY_FILE="${1:?usage: setup-wsl.sh <devbox-ssh-public-key-file>}"

# Flags dir on tmpfs, recreated every boot, writable by you.
echo "d /run/ai 0775 ${ME} ${ME} -" | sudo tee /etc/tmpfiles.d/ai.conf >/dev/null
sudo systemd-tmpfiles --create /etc/tmpfiles.d/ai.conf

# llama-server config, key and unit (REPLACE_ME → your username).
sudo install -d -m 0755 /etc/llama
if [[ ! -f /etc/llama/llama-server.env ]]; then
  sed "s/REPLACE_ME/${ME}/g" "${REPO}/pc/llama/llama-server.env" | sudo tee /etc/llama/llama-server.env >/dev/null
fi
if [[ ! -f /etc/llama/api-key ]]; then
  openssl rand -hex 32 | sudo tee /etc/llama/api-key >/dev/null
  sudo chown "${ME}:${ME}" /etc/llama/api-key
  sudo chmod 0600 /etc/llama/api-key
  echo "Generated /etc/llama/api-key — copy it into the devbox secret (LLAMA_API_KEY)."
fi
sed "s/REPLACE_ME/${ME}/g" "${REPO}/pc/llama/llama-server.service" | sudo tee /etc/systemd/system/llama-server.service >/dev/null

# Forced-command script + sudo rule limited to llama-server.
sudo install -m 0755 "${REPO}/pc/power/ai-power-remote" /usr/local/bin/ai-power-remote
echo "${ME} ALL=(root) NOPASSWD: /usr/bin/systemctl restart llama-server, /usr/bin/systemctl start llama-server, /usr/bin/systemctl stop llama-server" \
  | sudo tee /etc/sudoers.d/ai-power >/dev/null
sudo chmod 0440 /etc/sudoers.d/ai-power
sudo visudo -cf /etc/sudoers.d/ai-power

# sshd on 2222 (22 belongs to Windows in mirrored mode), keys only.
sudo apt-get install -y --no-install-recommends openssh-server
sudo tee /etc/ssh/sshd_config.d/ai.conf >/dev/null <<CONF
Port 2222
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
AllowUsers ${ME}
CONF
install -d -m 0700 "${HOME}/.ssh"
KEY="$(cat "${PUBKEY_FILE}")"
LINE="command=\"/usr/local/bin/ai-power-remote\",restrict ${KEY}"
touch "${HOME}/.ssh/authorized_keys"
grep -qxF "${LINE}" "${HOME}/.ssh/authorized_keys" || echo "${LINE}" >> "${HOME}/.ssh/authorized_keys"
chmod 0600 "${HOME}/.ssh/authorized_keys"

# Ubuntu 24.04 uses socket activation for ssh; the drop-in port needs a reload of both.
sudo systemctl daemon-reload
sudo systemctl restart ssh.socket 2>/dev/null || true
sudo systemctl enable --now ssh
sudo systemctl enable --now llama-server

echo "Done. Check: systemctl status llama-server; ssh -p 2222 ${ME}@localhost status (from a host with the devbox key)"
