# PC inference node (Phases 1–2)

Windows host with RTX 3080 Ti, WSL2 Ubuntu (24.04 or newer), llama-server as a systemd unit.
Works on Windows 10 (NAT networking, `ai-power.ps1` forwards the ports) and Windows 11
(optionally `networkingMode=mirrored`, no forwarding needed).

## Phase 1 — model server

1. Update the NVIDIA driver on Windows (the CUDA 13 toolkit needs driver ≥ 580; check with `nvidia-smi`).
   Never install a Linux NVIDIA driver inside WSL. Remotely (over SSH) the full installer hangs; extract
   it and install only the driver: `pnputil /add-driver <extracted>\Display.Driver\nv_dispi.inf /install`,
   then `wsl --shutdown` so WSL picks up the new libraries.
2. Admin PowerShell: `wsl --install -d Ubuntu`. Keep the distro on an SSD with ~60 GB free (model +
   CUDA + builds); move it with `wsl --shutdown; wsl --manage Ubuntu --move F:\WSL\Ubuntu`.
   A distro with another name: pass `-Distro <name>` to the PowerShell scripts.
3. Copy `wsl/.wslconfig` to `%UserProfile%\.wslconfig`.
4. In Ubuntu: `sudo cp pc/wsl/wsl.conf /etc/wsl.conf`, then `wsl --shutdown` from PowerShell and reopen.
5. Install the CUDA toolkit from NVIDIA's **WSL-Ubuntu** repo (toolkit only), plus
   `sudo apt install build-essential cmake git openssl`. Check `nvidia-smi` works in WSL.
6. `pc/llama/build-llama.sh <release-tag>`.
7. Model (Linux filesystem, not `/mnt/c`):
   ```bash
   pipx install huggingface_hub  # provides `hf`
   hf download unsloth/Qwen3.6-35B-A3B-MTP-GGUF --include "*UD-Q4_K_XL*" --local-dir ~/models
   ```
   Check the filename matches `MODEL` in `pc/llama/llama-server.env`.
8. `pc/llama/bench.sh` → pick the lowest `--n-cpu-moe` that leaves ~1 GB VRAM free, then confirm on the
   running server (`nvidia-smi` while it answers a request): the server's 48k context + MTP draft need
   ~0.7 GB more than the bench. Past the edge Windows spills VRAM into RAM and speed drops ~10x.
9. `pc/power/setup-wsl.sh` (no key yet is fine), then again with the devbox's key once it printed it:
   `kubectl -n ai-agent logs devbox-0 | grep "PC key"` → save to `devbox_key.pub` → `setup-wsl.sh devbox_key.pub`. Set `N_CPU_MOE` in `/etc/llama/llama-server.env`,
   `sudo systemctl restart llama-server`.

**Check from a k3s node:**
```bash
KEY=...   # contents of /etc/llama/api-key
curl -s -H "Authorization: Bearer $KEY" http://<pc-ip>:8080/v1/models
curl -s -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  http://<pc-ip>:8080/v1/chat/completions \
  -d '{"model":"coder","messages":[{"role":"user","content":"Say hi in 3 words"}],"max_tokens":16}'
```

## Phase 2 — power

Once, by hand:
- [ ] PC on wired Ethernet.
- [ ] BIOS: Wake-on-LAN / "Power on by PCI-E" on; ErP off.
- [ ] Device Manager → NIC → Power Management: "Allow this device to wake the computer" + "Only allow a magic packet".
      Advanced: "Wake on Magic Packet" enabled.
- [ ] `powercfg /a` lists **S3**. If only S0 (Modern Standby), test before relying on WoL.
- [ ] Note the NIC MAC (`getmac /v`) for the waker (OpenBao `secret/ai-agent/waker`, key `PC_MAC`).

Then elevated PowerShell in `pc/power/`:
```powershell
.\install-windows.ps1 -LanSubnet 192.168.50.0/24
```
`Start-ScheduledTask ai-power` (or reboot) and check `pc\power\ai-power.log`: it should show the
keepalive and, on Windows 10, `portproxy: 8080,2222 -> <WSL address>` (`netsh interface portproxy show all`).
Desktop shortcuts for `ai.ps1 pause` and `ai.ps1 resume`.

**Check:** sleep the PC, `curl -X POST` the waker from the devbox (`curl -X POST -H "X-Waker-Token: $WAKER_TOKEN" $WAKER_URL/wake`),
PC answers within ~90 s. `ssh -p 2222 <you>@<pc-ip> busy on` keeps it awake;
`busy off` (or 20 min without a heartbeat) lets Windows sleep it again.

## Files

| File | Installed to |
| --- | --- |
| `wsl/.wslconfig` | `%UserProfile%\.wslconfig` |
| `wsl/wsl.conf` | `/etc/wsl.conf` |
| `llama/llama-server.env` | `/etc/llama/llama-server.env` (by setup-wsl.sh) |
| `llama/llama-server.service` | `/etc/systemd/system/` (by setup-wsl.sh) |
| `power/ai-power-remote` | `/usr/local/bin/` (by setup-wsl.sh) |
| `power/ai-power.ps1` | stays here; boot task points at it (keeps WSL up, portproxy on Windows 10) |
