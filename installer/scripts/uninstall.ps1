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
    [string]$Distro = "Ubuntu",
    [switch]$PurgeData
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "_common.ps1")
$script:WslDistro = $Distro

$nssm = Join-Path $InstallDir "nssm.exe"
if (Test-Path $nssm) {
    Write-Step "Stopping + removing service: $ServiceName"
    & $nssm stop $ServiceName *> $null
    & $nssm remove $ServiceName confirm *> $null
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

Write-Ok "Uninstall finished."
