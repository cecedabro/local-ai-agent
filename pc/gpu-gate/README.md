# cv-skills GPU gatekeeper (`pc/gpu-gate/`)

The PC answers cv-skills requests on demand (uwv-hva/capaciteitsmanager#479). A small always-on gatekeeper
in WSL takes the requests. The first one swaps the GPU from the agent's model (`coder`) to the AI-Engineers
stack. After 10 min without requests it swaps back, unless `ai gpu on` pinned it (then it stays until `ai gpu off`).

```
kind: API → CV_AI_PROXY cv-skills-egress:1055 (tailscale userspace, ephemeral node, tag:cv-skills-client)
  └─ http://cv-skills-gpu:80 over the tailnet  (Windows: tailscale serve --tcp=80 → 127.0.0.1:8088)
       └─ gate.py  127.0.0.1:8088, user cv-gate, no GPU, always on
            ├─ sudo -n cv-gpu-swap in|out|beat   (the only privileged thing it can do)
            └─ proxy → cv-skills-api 127.0.0.1:8089 (internal key + X-Requester)
```

## Contract

| Call | Answer |
|---|---|
| `GET /v1/status` | 200 `{"state", "detail", "retry_after", "since", "idle_timeout_s", "active_until"}`; state is `idle`, `loading`, `active` or `unloading` |
| `POST /v1/warmup` | 202 + status; starts the swap in. `?pin=1` (`ai gpu on`) keeps it on: no idle timer until `/v1/cooldown` |
| `POST /v1/cooldown` | 202 + status; starts the swap back |
| any other `/v1/…` | active: proxied. Otherwise 503 + `Retry-After: 15` + the status body; idle/unloading also start a swap in |
| `GET /healthz` | 200, no key; local liveness only |

- **Access (`GATE_AUTH=tailnet`, the default):** the tailnet policy is the lock.
  - Only `tag:cv-skills-client` nodes (and Cédric's own devices) can reach `cv-skills-gpu:80`; the gatekeeper
    itself listens on loopback only. No per-person keys.
  - The one shared secret is the Tailscale OAuth client secret (tag `tag:cv-skills-client` only); see Clients.
    Revoke it in the admin console to cut everyone off.
  - `X-Requester` (optional, sanitised) is only a label for the logs.
  - `GATE_AUTH=keys` brings back per-person keys (`gate.py add-key <name>`, hashes in `/etc/cv-gate/keys`).
- **The stack:** it gets the internal key (`/etc/cv-gate/internal-key`) plus `X-Requester: <label>`.
- **Logs:** method, path, status, duration and requester only (`journalctl -u cv-gpu-gate`).
- **Clients:** a connect timeout means the PC is asleep or off. The gatekeeper never sends "offline".

## Swapping (`cv-gpu-swap`, root)

- **`in`:**
  - Checks first that every image of the compose files, and `MODEL_PATH`, is already there; otherwise it fails
    fast (exit 3). The containers have no internet.
  - Then the `ai pause` steps: `/run/ai/paused`, stop llama-server, free RAM.
  - Then `/run/ai/busy` so Windows stays awake.
  - Then `docker compose -p cv-skills -f compose.yaml -f compose.gpu.yml up -d`.
  - After that the gatekeeper waits for `/healthz` on 8089.
- **`out`:**
  - `compose down`, then starts llama-server again.
  - If `coder` was paused by hand before the swap, it stays paused.
  - The busy flag is left to expire (20 min TTL), so it never clears an agent job's flag.
- **`beat`:** touches the busy flag; the gatekeeper calls it every minute while active.
- **Model lease (optional, `STATUS_URL`/`STATUS_TOKEN` in gate.env):**
  - The gatekeeper takes the lease with `preempt`, falling back to `acquire` on an older status service.
  - It goes ahead either way: a cv-skills request wins over agent jobs.

## Install on the PC (done 2026-11-24)

1. **Tailnet admin (Cédric):**
   - Rename the machine to `cv-skills-gpu`; disable its key expiry.
   - Policy: `tag:cv-skills-client` (owner `autogroup:admin`) → `cv-skills-gpu` `tcp:80`; members keep `*`.
   - OAuth client, scope Auth Keys: write, tag `tag:cv-skills-client`; its secret is `CV_SKILLS_TS_KEY` (see Clients).
2. **WSL:** NVIDIA Container Toolkit:
   - NVIDIA's apt repo, `apt install nvidia-container-toolkit`, `nvidia-ctk runtime configure --runtime=docker`,
     `systemctl restart docker`.
   - Check: `docker run --rm --gpus all ubuntu nvidia-smi -L`.
3. **WSL, as root:**
   ```bash
   useradd --system --no-create-home --shell /usr/sbin/nologin cv-gate
   install -d -m 0750 -g cv-gate /etc/cv-gate /opt/cv-gate
   install -m 0644 pc/gpu-gate/gate.py /opt/cv-gate/gate.py
   install -m 0755 pc/gpu-gate/cv-gpu-swap /usr/local/sbin/cv-gpu-swap
   install -m 0440 pc/gpu-gate/cv-gpu-gate.sudoers /etc/sudoers.d/cv-gpu-gate && visudo -cf /etc/sudoers.d/cv-gpu-gate
   install -m 0640 -g cv-gate pc/gpu-gate/gate.env.example /etc/cv-gate/gate.env
   install -m 0644 pc/gpu-gate/swap.env.example /etc/cv-gate/swap.env      # set STACK_DIR
   (umask 027; head -c 32 /dev/urandom | base64 > /etc/cv-gate/internal-key); chgrp cv-gate /etc/cv-gate/internal-key
   install -m 0644 pc/gpu-gate/cv-gpu-gate.service /etc/systemd/system/ && systemctl enable --now cv-gpu-gate
   ```
   - Put the internal key in the stack's `API_KEYS`.
   - Pull the stack's images and model in advance.
4. **Windows:** `tailscale serve --bg --tcp=80 tcp://127.0.0.1:8088` (TCP, not `--http`: HTTP serve matches the
   Host header, so IP access and other Host values would get 404). Check from the Mac with
   `ai gpu status`.

**Undo:**
- Windows: `tailscale serve reset`.
- WSL:
  ```bash
  cv-gpu-swap out   # only if a swap is active; before deleting the helper
  systemctl disable --now cv-gpu-gate
  rm -r /etc/cv-gate /opt/cv-gate /usr/local/sbin/cv-gpu-swap /etc/sudoers.d/cv-gpu-gate /etc/systemd/system/cv-gpu-gate.service
  userdel cv-gate
  ```

## Clients

- **kind (uwv-hva/deployment#37):** a `cv-skills-egress` Deployment (stock `tailscale/tailscale`, userspace, outbound
  HTTP proxy on :1055, ephemeral `tag:cv-skills-client`). The API calls `CV_AI_URL=http://cv-skills-gpu` through
  `CV_AI_PROXY=http://cv-skills-egress:1055`. No key: the tailnet policy is the lock.
- **The secret:** a Tailscale OAuth client secret (scope Auth Keys: write, tag `tag:cv-skills-client`), kept as the
  GitHub Actions secret `CV_SKILLS_TS_KEY` in uwv-hva/deployment; `make kind-openbao` puts it in OpenBao at
  `secret/cap-app/cv-skills-tailscale` (`authkey`).
- **Compose:** later, the same egress proxy as a compose service.

## From the Mac

`ai gpu on|off|status` (`ai coder` = `ai gpu off`). `ai gpu on` pins the stack: it stays on until `ai gpu off`;
a stack started by a request (kind) still swaps back after 10 min idle. It calls `http://cv-skills-gpu` over Tailscale (`CV_GATE_URL` overrides); no key.

### A new AI-Engineers branch on the PC

```bash
ai cv deploy feature/366-koppelhulp-in-service   # copy the branch over, rebuild the image, restart the API if it runs
ai gpu on                                         # swap the stack in and keep it on (pauses coder); wait for "active"
ai cv status                                      # branch, gatekeeper state, containers
ai cv logs                                        # the API log (e.g. the "Catalogus … geladen" line)
ai coder                                          # swap back to coder (same as ai gpu off)
```

- `/opt/cv-skills/AI-Engineers` on the PC is a git checkout, but the PC has no GitHub access: `ai cv deploy` fetches
  the branch in your Mac clone (`CV_REPO`, default `~/HvA/UWV Project/AI-Engineers`) and sends it as a git bundle.
- The first build of a branch with new dependencies takes about 10 minutes (torch CPU); later builds use the cache.
- Don't deploy while an agent job runs if you will also `ai gpu on`: the swap stops `coder`.

## Tests

`python3 -m unittest discover -s pc/gpu-gate/tests` (part of `make test`). The tests use a fake swap helper, a
dummy upstream, and stub docker/systemctl.
