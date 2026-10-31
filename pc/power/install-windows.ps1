# Run once in an elevated PowerShell from this folder.
# - Boot task running ai-power.ps1 as you (also starts WSL), no logon needed.
# - Hyper-V firewall rule: llama-server (8080) and WSL sshd (2222) from your LAN only.
# - Disables Fast Startup (breaks Wake-on-LAN from shutdown).
param(
  [string]$LanSubnet = 'REPLACE_ME',   # homelab LAN: 192.168.50.0/24
  [string]$Distro = 'Ubuntu-24.04'
)
$ErrorActionPreference = 'Stop'
if ($LanSubnet -eq 'REPLACE_ME') { throw 'Pass -LanSubnet, e.g. 192.168.50.0/24' }

$script = Join-Path $PSScriptRoot 'ai-power.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`" -Distro $Distro"
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$cred = Get-Credential -UserName "$env:USERDOMAIN\$env:USERNAME" -Message 'Your Windows password (task runs without logon)'
Register-ScheduledTask -TaskName 'ai-power' -Action $action -Trigger $trigger -Settings $settings `
  -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null

# WSL VM creator ID from Microsoft's WSL Hyper-V firewall docs. Verify with Get-NetFirewallHyperVVMCreator.
$wsl = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'
Get-NetFirewallHyperVRule -Name 'ai-wsl-in' -ErrorAction SilentlyContinue | Remove-NetFirewallHyperVRule
New-NetFirewallHyperVRule -Name 'ai-wsl-in' -DisplayName 'WSL llama-server + sshd (LAN)' -Direction Inbound `
  -VMCreatorId $wsl -Protocol TCP -LocalPorts 8080,2222 -RemoteAddresses $LanSubnet -Action Allow | Out-Null

powercfg /hibernate off   # also turns off Fast Startup
powercfg /change standby-timeout-ac 20

Write-Host 'Installed. Reboot once, then check %LOCALAPPDATA%\ai-power.log.'
Write-Host 'Manual: NIC wake settings and BIOS Wake-on-LAN (see pc/README.md).'
