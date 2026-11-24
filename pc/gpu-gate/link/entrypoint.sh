#!/bin/sh
# cv-gpu-link entrypoint (POSIX sh: the tailscale image is Alpine/busybox).
#   TS_AUTHKEY   OAuth client secret (tskey-client-…) limited to tag:cv-skills-client. From OpenBao, never baked in.
#   LINK_TARGET  tailnet host:port of the gatekeeper   (default cv-skills-gpu:80)
#   LINK_LISTEN  where the app connects                (default 127.0.0.1:8088; 0.0.0.0:8088 behind a Service)
#   LINK_NAME    tailnet hostname prefix               (default cv-gpu-link; Tailscale appends -1, -2 … on clashes)
set -eu
: "${TS_AUTHKEY:?TS_AUTHKEY is not set}"
target="${LINK_TARGET:-cv-skills-gpu:80}"
listen="${LINK_LISTEN:-127.0.0.1:8088}"
sock=/tmp/tailscaled.sock

mkdir -p /tmp/ts
tailscaled --tun=userspace-networking --state=mem: --socket="$sock" --statedir=/tmp/ts &
daemon=$!
i=0
until tailscale --socket="$sock" status --json >/dev/null 2>&1; do
  i=$((i + 1)); [ "$i" -lt 100 ] || { echo "tailscaled did not start" >&2; exit 1; }
  sleep 0.2
done
case "$TS_AUTHKEY" in *\?*) key="$TS_AUTHKEY" ;; *) key="$TS_AUTHKEY?ephemeral=true&preauthorized=true" ;; esac
# The key goes in through a file, not argv (argv is visible in ps).
umask 077
printf '%s' "$key" > /tmp/ts/authkey
tailscale --socket="$sock" up --auth-key=file:/tmp/ts/authkey --advertise-tags=tag:cv-skills-client \
  --hostname="${LINK_NAME:-cv-gpu-link}" --accept-dns=false
rm -f /tmp/ts/authkey
echo "cv-gpu-link: http://$listen -> $target over the tailnet"

host="${listen%:*}"; port="${listen##*:}"
socat "TCP-LISTEN:$port,bind=$host,fork,reuseaddr" "EXEC:tailscale --socket=$sock nc ${target%:*} ${target##*:}" &
fwd=$!
trap 'kill "$fwd" "$daemon" 2>/dev/null' INT TERM
wait "$fwd"
