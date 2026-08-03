<#
.SYNOPSIS
    Configure unattended auto-start: register the logon scheduled task that runs
    sais-stack.ps1 (brings the stack up and keeps the WSL2 VM alive). Windows
    auto-login for the EnvisoftWebX service account was already configured in
    Phase 1 (05-service-account.ps1).

.DESCRIPTION
    Why not an NSSM/Windows service: a service runs as LOCAL SYSTEM (session 0),
    but the WSL2 distro is registered PER-USER, so the service cannot reach it
    (`wsl -d Ubuntu` fails) -> the stack never starts. WSL also needs an
    interactive user session to work reliably.

    So instead a scheduled task triggered "at logon" of the EnvisoftWebX service
    account runs sais-stack.ps1 hidden, with highest privileges, no time limit,
    auto-restart. The box auto-logs-into EnvisoftWebX at boot (set in Phase 1),
    so the stack comes up after a power cut with no operator present.

    Auto-login itself (AutoAdminLogon + DefaultPassword in the registry) is owned
    by 05-service-account.ps1; this step does NOT re-write it. The password belongs
    to a dedicated account nobody signs into, and the keepalive loop re-asserts it
    each cycle, so it never drifts. The dashboard keeps its own login.

    NOTE: ASCII-only (English) on purpose (Windows PowerShell 5.1 encoding).
#>
param(
    [Parameter(Mandatory)] [string]$InstallDir,
    [string]$Distro = "Ubuntu",
    [string]$SvcUser = "EnvisoftWebX",
    [string]$ServiceName = "EnvisoftWebX"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

$psExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$stackScript = Join-Path $InstallDir "scripts\sais-stack.ps1"
New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "logs") | Out-Null

# Bare account name for the logon trigger / principal (strip any DOMAIN\ prefix).
$bareUser = $SvcUser
if ($bareUser -match '\\') { $bareUser = $bareUser.Split('\')[-1] }

# --- Remove any old NSSM service from previous installer versions -------------
$nssm = Join-Path $InstallDir "nssm.exe"
if (Test-Path $nssm) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    & $nssm stop $ServiceName 2>&1 | Out-Null
    & $nssm remove $ServiceName confirm 2>&1 | Out-Null
    $ErrorActionPreference = $prev
}

# --- Logon scheduled task that runs the stack keepalive -----------------------
Write-Step "Registering logon task: $ServiceName (user '$bareUser')"
$taskArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$stackScript`" -InstallDir `"$InstallDir`" -Distro `"$Distro`" -SvcUser `"$bareUser`""
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

Write-Ok "Auto-start configured (logon task '$ServiceName' for '$bareUser')."

# --- Serial bridge service (COM -> TCP mirror for the containers) -------------
# Unlike the stack task, this is a real NSSM service under LOCAL SYSTEM: COM
# ports and TcpListener are session-independent and need no WSL, so the
# per-user constraint documented above does NOT apply here. NSSM gives crash
# restart supervision for free; nssm.exe is already shipped in the payload.
$bridgeSvc = "$ServiceName-SerialBridge"
$bridgeScript = Join-Path $InstallDir "scripts\serial-bridge.ps1"
$bridgeDir = Join-Path $InstallDir "bridge"
New-Item -ItemType Directory -Force -Path $bridgeDir | Out-Null

if (Test-Path $nssm) {
    Write-Step "Registering serial bridge service: $bridgeSvc"
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    & $nssm stop $bridgeSvc 2>&1 | Out-Null          # idempotent re-install
    & $nssm remove $bridgeSvc confirm 2>&1 | Out-Null
    $ErrorActionPreference = $prev

    $bridgeArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$bridgeScript`" -InstallDir `"$InstallDir`" -ConfigDir `"$bridgeDir`""
    & $nssm install $bridgeSvc $psExe $bridgeArgs | Out-Null
    & $nssm set $bridgeSvc AppDirectory $InstallDir | Out-Null
    & $nssm set $bridgeSvc AppStdout (Join-Path $InstallDir "logs\serial-bridge-svc.log") | Out-Null
    & $nssm set $bridgeSvc AppStderr (Join-Path $InstallDir "logs\serial-bridge-svc.log") | Out-Null
    & $nssm set $bridgeSvc AppRotateFiles 1 | Out-Null
    & $nssm set $bridgeSvc AppRotateBytes 5242880 | Out-Null
    & $nssm set $bridgeSvc Start SERVICE_AUTO_START | Out-Null
    & $nssm set $bridgeSvc Description "Envisoft WebX - mirrors serial (COM) connections to TCP for the Docker stack." | Out-Null
    & $nssm start $bridgeSvc 2>&1 | Out-Null

    # Inbound firewall for the bridge listen range (8900-18899 covers
    # SERIAL_BRIDGE_PORT_BASE + pk % 10000). Existence check on purpose --
    # do not accumulate duplicate rules across re-installs.
    if (-not (Get-NetFirewallRule -DisplayName "EnvisoftWebX SerialBridge" -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName "EnvisoftWebX SerialBridge" -Direction Inbound `
            -Protocol TCP -LocalPort "8900-18899" -Action Allow -ErrorAction SilentlyContinue | Out-Null
    }
    Write-Ok "Serial bridge service registered ($bridgeSvc)."
} else {
    Write-WarnLine "nssm.exe not found; serial bridge service NOT registered (serial connections will not work)."
}
