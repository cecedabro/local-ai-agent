# Keep-awake loop. Started at boot by install-windows.ps1 (hidden, as your user).
# - Starts WSL once so systemd brings up llama-server.
# - While /run/ai/busy exists and is fresher than $BusyTtlMinutes, tells Windows
#   the system is required (no sleep). Otherwise Windows' idle timer applies.
param(
  [string]$Distro = 'Ubuntu-24.04',
  [int]$PollSeconds = 30,
  [int]$BusyTtlMinutes = 20
)

$ErrorActionPreference = 'Continue'
Add-Type -Namespace Win32 -Name Power -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll")]
public static extern uint SetThreadExecutionState(uint esFlags);
'@
$ES_CONTINUOUS       = [uint32]'0x80000000'
$ES_SYSTEM_REQUIRED  = [uint32]'0x00000001'

$log = Join-Path $env:LOCALAPPDATA 'ai-power.log'
function Log($m) { "$(Get-Date -Format s) $m" | Add-Content -Path $log }

& wsl.exe -d $Distro --exec /bin/true
Log "started, distro=$Distro"

$busyPath = "\\wsl.localhost\$Distro\run\ai\busy"
$holding = $false
while ($true) {
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
