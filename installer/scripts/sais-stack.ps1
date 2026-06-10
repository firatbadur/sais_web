<#
.SYNOPSIS
    Long-running process started at auto-login by the EnvisoftWebX scheduled task.

.DESCRIPTION
    Brings the Docker stack up and then HOLDS THE WSL2 VM OPEN.

    CRITICAL: WSL2 idle-shuts-down its VM when no wsl.exe client is connected
    (after vmIdleTimeout), which kills dockerd and every container -> the
    dashboard "keeps crashing/restarting". An open `wsl ... sleep infinity`
    client keeps the VM (and the whole stack) alive. The outer loop re-brings
    the stack up if the distro is ever shut down/restarted.

    Runs in the auto-logged-in user's interactive session (so WSL is available;
    a LOCAL SYSTEM service cannot reach the per-user WSL distro).

    NOTE: ASCII-only (English) on purpose (Windows PowerShell 5.1 encoding).
#>
param(
    [Parameter(Mandatory)] [string]$InstallDir,
    [string]$Distro = "Ubuntu"
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "_common.ps1")
$script:WslDistro = $Distro

while ($true) {
    try {
        # 1) Make sure the Docker daemon is up inside the distro.
        wsl.exe -d $Distro -u root -- bash -lc "service docker start 2>/dev/null || systemctl start docker 2>/dev/null || (pgrep dockerd >/dev/null || (dockerd >/var/log/dockerd.log 2>&1 &)); sleep 2" *> $null

        # 2) Bring the stack up (idempotent).
        Invoke-Compose $InstallDir "up -d"

        # 3) HOLD THE VM OPEN. This blocks until the distro is shut down or
        #    restarted; while it blocks, the WSL2 VM stays alive so dockerd and
        #    all containers keep running. When it returns, the loop re-ups.
        wsl.exe -d $Distro -u root -- sleep infinity
    } catch {
        # Docker not ready yet / transient WSL error -> back off and retry.
    }
    Start-Sleep -Seconds 5
}
