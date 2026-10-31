# Keep-awake loop. Started at boot by install-windows.ps1 (hidden, as your user, elevated).
# - Keeps the WSL distro running (a hidden `sleep infinity`), so systemd keeps llama-server up.
# - NAT networking (Windows 10, or no networkingMode=mirrored): forwards LAN ports 8080/2222 to
#   the VM's current address with netsh portproxy. The address changes on every WSL start, so
#   it is re-checked every few minutes. Mirrored mode needs none of this and is detected.
# - While /run/ai/busy exists and is fresher than $BusyTtlMinutes, tells Windows
#   the system is required (no sleep). Otherwise Windows' idle timer applies.
param(
  [string]$Distro = 'Ubuntu',
  [int]$PollSeconds = 30,
  [int]$BusyTtlMinutes = 20,
  [int[]]$Ports = @(8080, 2222)
)

$ErrorActionPreference = 'Continue'
Add-Type -Namespace Win32 -Name Power -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll")]
public static extern uint SetThreadExecutionState(uint esFlags);
'@
$ES_CONTINUOUS       = [uint32]'0x80000000'
$ES_SYSTEM_REQUIRED  = [uint32]'0x00000001'

# Next to this script: under the S4U boot task the profile folders were not writable/visible
# (%LOCALAPPDATA%\ai-power.log stayed empty after a reboot while a manual start did write it).
$log = Join-Path $PSScriptRoot 'ai-power.log'
function Log($m) {
  try { "$(Get-Date -Format s) $m" | Add-Content -Path $log -ErrorAction Stop }
  catch { Write-EventLog -LogName Application -Source 'Windows PowerShell' -EventId 4100 -EntryType Warning -Message "ai-power: $m ($_)" -ErrorAction SilentlyContinue }
}

$keepalive = $null
function Start-Keepalive {
  if ($script:keepalive -and -not $script:keepalive.HasExited) { return $false }
  $script:keepalive = Start-Process wsl.exe -ArgumentList "-d $Distro --exec /bin/sleep infinity" `
    -WindowStyle Hidden -PassThru
  Log "keepalive started (pid $($script:keepalive.Id))"
  Start-Sleep -Seconds 5
  return $true
}

$proxiedTo = ''
function Sync-PortProxy {
  $out = & wsl.exe -d $Distro --exec ip -4 -o addr show eth0 2>$null
  if (-not ("$out" -match 'inet (\d+\.\d+\.\d+\.\d+)')) { Log 'portproxy: no eth0 address'; return }
  $vm = $Matches[1]
  if ($vm -eq $script:proxiedTo) { return }
  $hostIps = (Get-NetIPAddress -AddressFamily IPv4).IPAddress
  if ($hostIps -contains $vm) { Log "portproxy: mirrored networking ($vm), not needed"; $script:proxiedTo = $vm; return }
  foreach ($p in $Ports) {
    & netsh interface portproxy delete v4tov4 listenport=$p listenaddress=0.0.0.0 2>$null | Out-Null
    & netsh interface portproxy add v4tov4 listenport=$p listenaddress=0.0.0.0 connectport=$p connectaddress=$vm | Out-Null
  }
  Log "portproxy: $($Ports -join ',') -> $vm"
  $script:proxiedTo = $vm
}

Log "started, distro=$Distro"
$busyPath = "\\wsl.localhost\$Distro\run\ai\busy"
$holding = $false
$tick = 0
while ($true) {
  # A restarted VM (wsl --shutdown, update) has a new address: re-sync right away.
  if (Start-Keepalive) { $proxiedTo = '' }
  if ($tick % 10 -eq 0 -or -not $proxiedTo) { Sync-PortProxy }
  $tick++

  $fresh = $false
  try {
    $item = Get-Item -LiteralPath $busyPath -ErrorAction Stop
    $fresh = ((Get-Date) - $item.LastWriteTime).TotalMinutes -lt $BusyTtlMinutes
  } catch { }

  if ($fresh) {
    [void][Win32.Power]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED)
    if (-not $holding) { Log 'busy: holding sleep off'; $holding = $true }
  } elseif ($holding) {
    [void][Win32.Power]::SetThreadExecutionState($ES_CONTINUOUS)
    Log 'idle: released'; $holding = $false
  }
  Start-Sleep -Seconds $PollSeconds
}
