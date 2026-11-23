# cv-skills GPU gatekeeper (`pc/gpu-gate/`)

The PC answers cv-skills requests on demand (uwv-hva/capaciteitsmanager#479). A small always-on gatekeeper
in WSL takes the requests. The first one swaps the GPU from the agent's model (`coder`) to the AI-Engineers
stack. After 10 min without requests it swaps back.

```
client (compose / kind, own Tailscale node tag:cv-skills-client)
  └─ https://cv-skills-gpu.<tailnet>.ts.net  (Windows: tailscale serve 443 → 127.0.0.1:8088)
       └─ gate.py  127.0.0.1:8088, user cv-gate, no GPU, always on
            ├─ sudo -n cv-gpu-swap in|out|beat   (the only privileged thing it can do)
            └─ proxy → cv-skills-api 127.0.0.1:8089 (internal key + X-Requester)
```

## Contract

| Call | Answer |
|---|---|
| `GET /v1/status` | 200 `{"state", "detail", "retry_after", "since", "idle_timeout_s", "active_until"}`; state is `idle`, `loading`, `active` or `unloading` |
| `POST /v1/warmup` | 202 + status; starts the swap in |
| `POST /v1/cooldown` | 202 + status; starts the swap back |
| any other `/v1/…` | active: proxied. Otherwise 503 + `Retry-After: 15` + the status body; idle/unloading also start a swap in |
| `GET /healthz` | 200, no key; local liveness only |

- **Keys:** every `/v1` call needs a personal key (`Authorization: Bearer …` or `X-API-Key`).
  - Only sha256 hashes are stored, in `/etc/cv-gate/keys`, re-read on every request.
  - To revoke a key, delete its line.
- **The stack:** it never sees personal keys. It gets the internal key (`/etc/cv-gate/internal-key`) plus
  `X-Requester: <name>`.
- **Logs:** method, path, status, duration and key name only (`journalctl -u cv-gpu-gate`).
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

## Install on the PC (needs Cédric's OK; not done yet)

1. **Tailnet admin (Cédric):**
   - Rename the machine to `cv-skills-gpu`.
   - Enable HTTPS certificates.
   - Save the policy with `tag:cv-skills-client` → `cv-skills-gpu:443`.
   - Create the reusable, ephemeral, tagged auth key.
2. **WSL:** NVIDIA Container Toolkit:
   - `apt install nvidia-container-toolkit`, then `nvidia-ctk runtime configure --runtime=docker`, then
     `systemctl restart docker`.
   - Check with `docker run --rm --gpus all nvidia/cuda:<tag> nvidia-smi` (with `coder` paused).
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
   touch /etc/cv-gate/keys && chgrp cv-gate /etc/cv-gate/keys && chmod 0640 /etc/cv-gate/keys
   python3 /opt/cv-gate/gate.py add-key <person>    # shows the key once
   install -m 0644 pc/gpu-gate/cv-gpu-gate.service /etc/systemd/system/ && systemctl enable --now cv-gpu-gate
   ```
   - Put the internal key in the stack's `API_KEYS`.
   - Pull the stack's images and model in advance.
4. **Windows:** `tailscale serve --bg --https=443 http://127.0.0.1:8088`. Check from the Mac with
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

## From the Mac

`ai gpu on|off|status`. It uses `CV_GATE_URL` and a key from `CV_GATE_KEY` or the Keychain:
`security add-generic-password -s cv-gate -a cedric -w`.

## Tests

`python3 -m unittest discover -s pc/gpu-gate/tests` (part of `make test`). The tests use a fake swap helper, a
dummy upstream, and stub docker/systemctl.
