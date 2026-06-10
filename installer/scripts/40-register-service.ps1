<#
.SYNOPSIS
    Configure unattended auto-start: Windows auto-login + a logon scheduled task
    that runs sais-stack.ps1 (brings the stack up and keeps the WSL2 VM alive).

.DESCRIPTION
    Why not an NSSM/Windows service: a service runs as LOCAL SYSTEM (session 0),
    but the WSL2 distro is registered PER-USER, so the service cannot reach it
    (`wsl -d Ubuntu` fails) -> the stack never starts. WSL also needs an
    interactive user session to work reliably.

    So instead:
      1) AutoAdminLogon -> Windows signs the operator account in automatically at
         boot (no password screen). Needed because WSL only works in a logged-in
         session and SCADA sites have no operator to log in after a power cut.
      2) A scheduled task triggered "at logon" of that user runs sais-stack.ps1
         hidden, with highest privileges, no time limit, auto-restart. It brings
         the stack up and holds the WSL2 VM open (keepalive).

    Security note: the Windows password is stored (AutoAdminLogon DefaultPassword
    in the registry). Acceptable for a physically secured SCADA cabinet (kiosk /
    appliance pattern). The dashboard keeps its own login.

    NOTE: ASCII-only (English) on purpose (Windows PowerShell 5.1 encoding).
#>
param(
    [Parameter(Mandatory)] [string]$InstallDir,
    [string]$Distro = "Ubuntu",
    [string]$WinUser = "",
    [string]$WinPass = "",
    [string]$ServiceName = "EnvisoftWebX"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

$psExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$stackScript = Join-Path $InstallDir "scripts\sais-stack.ps1"
New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "logs") | Out-Null

# Default the auto-login account to the user running the installer.
if (-not $WinUser) { $WinUser = $env:USERNAME }
# Bare account name for the logon trigger / principal (strip any DOMAIN\ prefix).
$bareUser = $WinUser
if ($bareUser -match '\\') { $bareUser = $bareUser.Split('\')[-1] }
$domain = $env:COMPUTERNAME

# --- Remove any old NSSM service from previous installer versions -------------
$nssm = Join-Path $InstallDir "nssm.exe"
if (Test-Path $nssm) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    & $nssm stop $ServiceName 2>&1 | Out-Null
    & $nssm remove $ServiceName confirm 2>&1 | Out-Null
    $ErrorActionPreference = $prev
}

# --- 1) Windows auto-login ----------------------------------------------------
Write-Step "Configuring Windows auto-login for '$bareUser'..."
$winlogon = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
if ($WinPass) {
    Set-ItemProperty -Path $winlogon -Name "AutoAdminLogon"    -Value "1"        -Type String
    Set-ItemProperty -Path $winlogon -Name "DefaultUserName"   -Value $bareUser  -Type String
    Set-ItemProperty -Path $winlogon -Name "DefaultPassword"   -Value $WinPass   -Type String
    Set-ItemProperty -Path $winlogon -Name "DefaultDomainName" -Value $domain    -Type String
    # Don't auto-relock after auto-login.
    Set-ItemProperty -Path $winlogon -Name "ForceAutoLogon"    -Value "0"        -Type String
    Write-Ok "Auto-login enabled for $domain\$bareUser."
} else {
    Write-WarnLine "No Windows password provided -> auto-login NOT set. After a reboot you must log in manually for the stack to start."
}

# --- 2) Logon scheduled task that runs the stack keepalive --------------------
Write-Step "Registering logon task: $ServiceName"
$taskArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$stackScript`" -InstallDir `"$InstallDir`" -Distro `"$Distro`""
$action  = New-ScheduledTaskAction -Execute $psExe -Argument $taskArgs
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $bareUser
$principal = New-ScheduledTaskPrincipal -UserId $bareUser -RunLevel Highest -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

Unregister-ScheduledTask -TaskName $ServiceName -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
Register-ScheduledTask -TaskName $ServiceName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Description "Envisoft WebX - start Docker stack + keep WSL2 VM alive at logon." -Force | Out-Null

# Start it now so the operator does not have to reboot after install.
Start-ScheduledTask -TaskName $ServiceName -ErrorAction SilentlyContinue

Write-Ok "Auto-start configured (auto-login + logon task '$ServiceName')."
