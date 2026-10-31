# Run once in an elevated PowerShell from this folder.
# - Boot task running ai-power.ps1 as you, elevated (portproxy needs admin), no logon needed.
#   -LogonType S4U (default) stores no password; Password asks for it (use that if the log
#   shows WSL failing to start under S4U).
# - Firewall: llama-server (8080) and WSL sshd (2222) from your LAN only. Windows 11 also gets
#   the Hyper-V rule for mirrored networking; Windows 10 (NAT) goes through portproxy.
# - Disables Fast Startup (breaks Wake-on-LAN from shutdown).
param(
  [string]$LanSubnet = 'REPLACE_ME',   # homelab LAN: 192.168.50.0/24
  [string]$Distro = 'Ubuntu',
  [ValidateSet('S4U', 'Password')][string]$LogonType = 'S4U'
)
$ErrorActionPreference = 'Stop'
if ($LanSubnet -eq 'REPLACE_ME') { throw 'Pass -LanSubnet, e.g. 192.168.50.0/24' }

$script = Join-Path $PSScriptRoot 'ai-power.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`" -Distro $Distro"
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name   # $env:USERDOMAIN is unset over SSH
if ($LogonType -eq 'S4U') {
  $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Highest
  Register-ScheduledTask -TaskName 'ai-power' -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force | Out-Null
} else {
  $cred = Get-Credential -UserName $user -Message 'Your Windows password (task runs without logon)'
  Register-ScheduledTask -TaskName 'ai-power' -Action $action -Trigger $trigger -Settings $settings `
    -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Highest -Force | Out-Null
}

# portproxy runs in the IP Helper service.
Set-Service iphlpsvc -StartupType Automatic
Start-Service iphlpsvc

Get-NetFirewallRule -Name 'ai-wsl-in' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -Name 'ai-wsl-in' -DisplayName 'WSL llama-server + sshd (LAN)' -Direction Inbound `
  -Protocol TCP -LocalPort 8080,2222 -RemoteAddress $LanSubnet -Action Allow | Out-Null

if (Get-Command New-NetFirewallHyperVRule -ErrorAction SilentlyContinue) {
  # WSL VM creator ID from Microsoft's WSL Hyper-V firewall docs. Verify with Get-NetFirewallHyperVVMCreator.
  $wsl = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'
  Get-NetFirewallHyperVRule -Name 'ai-wsl-in' -ErrorAction SilentlyContinue | Remove-NetFirewallHyperVRule
  New-NetFirewallHyperVRule -Name 'ai-wsl-in' -DisplayName 'WSL llama-server + sshd (LAN)' -Direction Inbound `
    -VMCreatorId $wsl -Protocol TCP -LocalPorts 8080,2222 -RemoteAddresses $LanSubnet -Action Allow | Out-Null
}

powercfg /hibernate off   # also turns off Fast Startup
powercfg /change standby-timeout-ac 20

Write-Host 'Installed. Start now with Start-ScheduledTask ai-power (or reboot), then check pc\power\ai-power.log.'
Write-Host 'Manual: NIC wake settings and BIOS Wake-on-LAN (see pc/README.md).'
