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

Write-Step "Logging in to GHCR ($GhcrUser)..."
# Pass the token to WSL securely via stdin.
$login = "echo '$GhcrToken' | docker login ghcr.io -u '$GhcrUser' --password-stdin"
Invoke-Wsl $login
Write-Ok "GHCR login OK."

Write-Step "Pulling images (docker compose pull)..."
Invoke-Compose $InstallDir "pull"

Write-Step "Starting the stack (docker compose up -d)..."
Invoke-Compose $InstallDir "up -d"

Write-Step "Waiting for services to become healthy (max $HealthTimeoutSec s)..."
$wslDir = ConvertTo-WslPath $InstallDir
$deadline = (Get-Date).AddSeconds($HealthTimeoutSec)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    $ps = wsl.exe -d $script:WslDistro -- bash -lc "cd '$wslDir' && docker compose -f $script:ComposeFile ps --format '{{.Service}} {{.State}}'"
    if ($ps -match "web\s+running" -and $ps -match "db\s+running") {
        $healthy = $true
        break
    }
    Start-Sleep -Seconds 5
}

if ($healthy) {
    Write-Ok "Stack is running."
} else {
    Write-WarnLine "Health wait timed out; check logs: docker compose logs"
}
