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

# The pull downloads hundreds of MB; make sure the network is actually up first
# (right after a reboot-resume it may not be yet).
if (-not (Wait-ForInternet -Reason "GHCR login + image pull")) {
    Write-Error ("No internet connection (waited 30 min). Fix the network, then resume the install with: " +
        "Start-ScheduledTask -TaskName EnvisoftWebX-Install")
    exit 1
}

# Pass the token to WSL securely via stdin.
$login = "echo '$GhcrToken' | docker login ghcr.io -u '$GhcrUser' --password-stdin"
Invoke-WslSpin "Logging in to GHCR ($GhcrUser)" $login

# Pull with network-drop resilience (real field incident: the connection died
# mid-download -> "short read ... unexpected EOF" killed the whole install).
# Docker keeps completed layers in its cache, so every retry RESUMES where the
# previous attempt stopped. Between attempts we wait for the internet to come
# back (loud warning + up to 30 min each) instead of failing outright.
$maxPull = 8
for ($try = 1; $try -le $maxPull; $try++) {
    try {
        Invoke-WslSpin "Pulling images (web/db/redis/caddy - attempt $try/$maxPull, resumes from cache)" `
            (Get-ComposeBash $InstallDir "pull")
        break
    } catch {
        if ($try -eq $maxPull) {
            Write-Error ("Image pull failed $maxPull times. Already-downloaded layers are KEPT; fix the " +
                "connection and resume with: Start-ScheduledTask -TaskName EnvisoftWebX-Install")
            exit 1
        }
        Write-WarnLine "Pull interrupted (network drop?) - downloaded layers are cached; retrying ..."
        Start-Sleep -Seconds 5
        if (-not (Wait-ForInternet -Reason "resume image pull")) {
            Write-Error ("No internet connection (waited 30 min). Already-downloaded layers are KEPT; " +
                "resume later with: Start-ScheduledTask -TaskName EnvisoftWebX-Install")
            exit 1
        }
    }
}

Invoke-WslSpin "Starting the stack (docker compose up -d)" `
    (Get-ComposeBash $InstallDir "up -d")

$wslDir = ConvertTo-WslPath $InstallDir
$psBash = "cd '$wslDir' && docker compose -f $script:ComposeFile ps --format '{{.Service}} {{.State}}'"
$healthy = Wait-WithSpin "Waiting for services to become healthy" -TimeoutSec $HealthTimeoutSec -Check {
    try { $ps = wsl.exe -d $script:WslDistro -u $script:WslUser -- bash -lc $psBash 2>$null } catch { $ps = "" }
    return ($ps -match "web\s+running" -and $ps -match "db\s+running")
}

if (-not $healthy) {
    Write-WarnLine "Health wait timed out; check logs: docker compose logs"
}
