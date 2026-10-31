# Your manual switch. Desktop shortcuts: `powershell -File ai.ps1 pause` / `resume`.
param([Parameter(Mandatory)][ValidateSet('pause','resume','status')][string]$Action,
      [string]$Distro = 'Ubuntu')

switch ($Action) {
  'pause'  { wsl.exe -d $Distro -u root -- bash -c 'mkdir -p /run/ai && touch /run/ai/paused && systemctl stop llama-server'; 'paused: VRAM and RAM freed' }
  'resume' { wsl.exe -d $Distro -u root -- bash -c 'rm -f /run/ai/paused && systemctl start llama-server'; 'resumed: model loading' }
  'status' { wsl.exe -d $Distro -u root -- bash -c 'test -e /run/ai/paused && echo paused || systemctl is-active llama-server; test -e /run/ai/busy && echo busy || true' }
}
