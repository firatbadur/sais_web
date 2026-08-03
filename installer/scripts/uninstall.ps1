<#
.SYNOPSIS
    Remove the Envisoft WebX service and containers. Keeps data by default.

.PARAMETER PurgeData
    If given, also removes named volumes (including the DB - IRREVERSIBLE).

    NOTE: ASCII-only (English) on purpose (Windows PowerShell 5.1 encoding).
#>
param(
    [Parameter(Mandatory)] [string]$InstallDir,
    [string]$ServiceName = "EnvisoftWebX",
    [string]$SvcUser = "EnvisoftWebX",
    [string]$Distro = "Ubuntu",
    [switch]$PurgeData
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "_common.ps1")
$script:WslDistro = $Distro

# Remove the logon scheduled tasks (keepalive + any leftover install resume task).
Write-Step "Removing auto-start tasks: $ServiceName / $ServiceName-Install"
foreach ($task in @($ServiceName, "$ServiceName-Install")) {
    Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
}

# Disable Windows auto-login (clear the stored user + password).
Write-Step "Disabling Windows auto-login..."
$winlogon = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
Set-ItemProperty -Path $winlogon -Name "AutoAdminLogon" -Value "0" -ErrorAction SilentlyContinue
Remove-ItemProperty -Path $winlogon -Name "DefaultPassword" -ErrorAction SilentlyContinue
Remove-ItemProperty -Path $winlogon -Name "DefaultUserName" -ErrorAction SilentlyContinue

# Remove NSSM services: serial bridge + any legacy stack service.
$nssm = Join-Path $InstallDir "nssm.exe"
if (Test-Path $nssm) {
    Write-Step "Removing NSSM services: $ServiceName-SerialBridge / $ServiceName (legacy)"
    foreach ($svc in @("$ServiceName-SerialBridge", $ServiceName)) {
        & $nssm stop $svc *> $null
        & $nssm remove $svc confirm *> $null
    }
}

# Remove firewall rules created by the installer / stack loop.
Write-Step "Removing firewall rules..."
foreach ($rule in @("EnvisoftWebX SerialBridge", "EnvisoftWebX HTTP", "EnvisoftWebX HTTPS")) {
    Remove-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue
}

Write-Step "Stopping containers..."
try {
    if ($PurgeData) {
        Write-WarnLine "PurgeData: removing all data (including the DB)!"
        Invoke-Compose $InstallDir "down -v"
    } else {
        Invoke-Compose $InstallDir "down"
        Write-Ok "Containers removed; volumes (DB/redis/cert) were KEPT."
    }
} catch {
    Write-WarnLine "compose down failed (stack may already be down)."
}

# Remove the dedicated service account (it holds no data). Done last so the
# compose-down above still ran in a working session. The profile folder is left
# behind (Windows keeps it while in use); the SAM account is removed.
Write-Step "Removing service account: $SvcUser"
try {
    if (Get-LocalUser -Name $SvcUser -ErrorAction SilentlyContinue) {
        Remove-LocalUser -Name $SvcUser -ErrorAction SilentlyContinue
        Write-Ok "Service account '$SvcUser' removed."
    }
} catch {
    Write-WarnLine "Could not remove service account '$SvcUser' (it may be the current session)."
}

Write-Ok "Uninstall finished."
