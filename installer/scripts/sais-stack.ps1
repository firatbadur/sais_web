<#
.SYNOPSIS
    Long-running process driven by the NSSM Windows service.

.DESCRIPTION
    At boot: starts the WSL Docker daemon (if WSL has no systemd), then brings
    the stack up with `docker compose up -d` and stays alive (NSSM treats the
    service as running while this process lives). When the service stops, the
    stack is stopped gracefully.

    NOTE: ASCII-only (English) on purpose (Windows PowerShell 5.1 encoding).
#>
param(
    [Parameter(Mandatory)] [string]$InstallDir,
    [string]$Distro = "Ubuntu"
)

. (Join-Path $PSScriptRoot "_common.ps1")
$script:WslDistro = $Distro

try {
    Write-Step "Starting WSL Docker daemon..."
    wsl.exe -d $Distro -u root -- bash -lc "service docker start || (dockerd >/var/log/dockerd.log 2>&1 &) ; sleep 3" *> $null

    Write-Step "Starting the stack (up -d)..."
    Invoke-Compose $InstallDir "up -d"
    Write-Ok "Envisoft WebX stack is running. Service stays alive."

    # Block so NSSM keeps the process alive. finally runs on service stop.
    while ($true) { Start-Sleep -Seconds 3600 }
}
finally {
    Write-Step "Service stopping - stopping the stack (compose stop)..."
    try { Invoke-Compose $InstallDir "stop" } catch { }
}
