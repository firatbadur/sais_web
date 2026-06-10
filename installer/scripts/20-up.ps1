<#
.SYNOPSIS
    Log in to GHCR, pull images, start the stack, wait until healthy.

.DESCRIPTION
    Logs in to GHCR for the private image using the embedded read-only token
    (--password-stdin). All docker work runs inside WSL2 Docker CE.

    NOTE: ASCII-only (English) on purpose (Windows PowerShell 5.1 encoding).
#>
param(
    [Parameter(Mandatory)] [string]$InstallDir,
    [Parameter(Mandatory)] [string]$GhcrUser,
    [Parameter(Mandatory)] [string]$GhcrToken,
    [int]$HealthTimeoutSec = 600
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

Assert-Docker

# Pass the token to WSL securely via stdin.
$login = "echo '$GhcrToken' | docker login ghcr.io -u '$GhcrUser' --password-stdin"
Invoke-WslSpin "Logging in to GHCR ($GhcrUser)" $login

Invoke-WslSpin "Pulling images (web/db/redis/caddy - this can take several minutes)" `
    (Get-ComposeBash $InstallDir "pull")

Invoke-WslSpin "Starting the stack (docker compose up -d)" `
    (Get-ComposeBash $InstallDir "up -d")

$wslDir = ConvertTo-WslPath $InstallDir
$psBash = "cd '$wslDir' && docker compose -f $script:ComposeFile ps --format '{{.Service}} {{.State}}'"
$healthy = Wait-WithSpin "Waiting for services to become healthy" -TimeoutSec $HealthTimeoutSec -Check {
    try { $ps = wsl.exe -d $script:WslDistro -- bash -lc $psBash 2>$null } catch { $ps = "" }
    return ($ps -match "web\s+running" -and $ps -match "db\s+running")
}

if (-not $healthy) {
    Write-WarnLine "Health wait timed out; check logs: docker compose logs"
}
