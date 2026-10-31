# PC inference node (Phases 1–2)

Windows host with RTX 3080 Ti, WSL2 Ubuntu 24.04, llama-server as a systemd unit.

## Phase 1 — model server

1. Update the NVIDIA driver on Windows. Never install a Linux NVIDIA driver inside WSL.
2. Admin PowerShell: `wsl --install -d Ubuntu-24.04`.
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
8. `pc/llama/bench.sh` → pick the lowest `--n-cpu-moe` that leaves ~1 GB VRAM free.
9. Generate the devbox SSH key (on the devbox later, or now: `ssh-keygen -t ed25519 -f devbox_key -N ''`),
   then `pc/power/setup-wsl.sh devbox_key.pub`. Set `N_CPU_MOE` in `/etc/llama/llama-server.env`,
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
- [ ] Note the NIC MAC (`getmac /v`) for `k8s/waker/`.

Then elevated PowerShell in `pc/power/`:
```powershell
.\install-windows.ps1 -LanSubnet 192.168.1.0/24
```
Reboot once. Desktop shortcuts for `ai.ps1 pause` and `ai.ps1 resume`.

**Check:** sleep the PC, `curl -X POST` the waker from the devbox (see `k8s/waker/`),
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
| `power/ai-power.ps1` | stays here; boot task points at it |
