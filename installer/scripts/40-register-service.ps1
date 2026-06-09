<#
.SYNOPSIS
    Register the Windows service (NSSM) that starts the stack at boot.

.DESCRIPTION
    NSSM wraps sais-stack.ps1 (long-running) as a Windows service -> auto-starts
    at boot, restarts on crash. nssm.exe comes from the installer payload.

    NOTE: ASCII-only (English) on purpose (Windows PowerShell 5.1 encoding).
#>
param(
    [Parameter(Mandatory)] [string]$InstallDir,
    [string]$NssmPath = "",
    [string]$ServiceName = "EnvisoftWebX",
    [string]$Distro = "Ubuntu"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

if (-not $NssmPath) {
    $NssmPath = Join-Path $InstallDir "nssm.exe"
}
if (-not (Test-Path $NssmPath)) {
    Write-Error "nssm.exe not found: $NssmPath"
}

$stackScript = Join-Path $InstallDir "scripts\sais-stack.ps1"
$psExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$psArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$stackScript`" -InstallDir `"$InstallDir`" -Distro `"$Distro`""

# Clean up any existing service (idempotent re-install).
& $NssmPath stop $ServiceName *> $null
& $NssmPath remove $ServiceName confirm *> $null

Write-Step "Registering Windows service: $ServiceName"
& $NssmPath install $ServiceName $psExe $psArgs
& $NssmPath set $ServiceName AppDirectory $InstallDir
& $NssmPath set $ServiceName DisplayName "Envisoft WebX"
& $NssmPath set $ServiceName Description "Envisoft WebX Docker stack (web + worker + beat + db + redis + caddy)."
& $NssmPath set $ServiceName Start SERVICE_AUTO_START
& $NssmPath set $ServiceName AppStopMethodConsole 20000
& $NssmPath set $ServiceName AppExit Default Restart
& $NssmPath set $ServiceName AppRestartDelay 10000
& $NssmPath set $ServiceName AppStdout (Join-Path $InstallDir "logs\service.log")
& $NssmPath set $ServiceName AppStderr (Join-Path $InstallDir "logs\service.err.log")

New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "logs") | Out-Null

Write-Step "Starting service..."
& $NssmPath start $ServiceName

Write-Ok "Service installed and started: $ServiceName (auto-start at boot)."
