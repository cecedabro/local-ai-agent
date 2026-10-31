# shellcheck shell=sh
# Sourced by login shells in the devbox.
alias tailscale='tailscale --socket="$HOME/.tailscale/tailscaled.sock"'
export PATH="/usr/local/bin:$PATH"
# Attach to the agent session on SSH login (skip with NO_TMUX=1).
if [ -n "$SSH_CONNECTION" ] && [ -z "$TMUX" ] && [ -z "$NO_TMUX" ]; then
  exec tmux attach -t agent
fi
