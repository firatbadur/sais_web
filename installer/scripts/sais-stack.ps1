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

# --- WSL2 port forwarding (external access fix) ------------------------------
# Docker runs inside WSL2; in NAT mode WSL only mirrors published ports to the
# host's 127.0.0.1, NOT to the LAN/public interface -> external HTTPS (Caddy
# 80/443) times out even when the modem/firewall forwards correctly. We bridge
# the host's outbound interface to the WSL2 VM with `netsh portproxy`. The WSL2
# IP changes on every boot, so this re-runs each loop with the CURRENT IP. The
# logon task runs with Highest privileges, so netsh/firewall calls succeed.
function Set-PortProxy([string]$Distro) {
    try {
        $wslIp = (wsl.exe -d $Distro -- hostname -I 2>$null)
        if ($wslIp) { $wslIp = $wslIp.Trim().Split(" ")[0] }
        if ($wslIp -notmatch '^\d+\.\d+\.\d+\.\d+$') { return }

        # IP of the interface that carries the default route (internet-facing).
        $ifIndex = (Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
            Sort-Object RouteMetric | Select-Object -First 1).ifIndex
        $lanIp = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $ifIndex -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notlike '169.*' } | Select-Object -First 1).IPAddress
        if (-not $lanIp) { $lanIp = '0.0.0.0' }

        netsh interface portproxy reset 2>$null | Out-Null
        foreach ($port in 80, 443) {
            netsh interface portproxy add v4tov4 listenaddress=$lanIp listenport=$port `
                connectaddress=$wslIp connectport=$port 2>$null | Out-Null
        }
        New-NetFirewallRule -DisplayName "EnvisoftWebX HTTP"  -Direction Inbound -Protocol TCP -LocalPort 80  -Action Allow -ErrorAction SilentlyContinue | Out-Null
        New-NetFirewallRule -DisplayName "EnvisoftWebX HTTPS" -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow -ErrorAction SilentlyContinue | Out-Null
    } catch {
        # netsh / cmdlet failure -> non-fatal; retried next loop.
    }
}

while ($true) {
    try {
        # 1) Make sure the Docker daemon is up inside the distro.
        wsl.exe -d $Distro -u root -- bash -lc "service docker start 2>/dev/null || systemctl start docker 2>/dev/null || (pgrep dockerd >/dev/null || (dockerd >/var/log/dockerd.log 2>&1 &)); sleep 2" *> $null

        # 2) Bring the stack up (idempotent).
        Invoke-Compose $InstallDir "up -d"

        # 3) Bridge the host's external interface to the WSL2 VM (80/443) with the
        #    CURRENT WSL IP, so the site is reachable from outside (not just
        #    127.0.0.1). Re-runs every loop because the WSL IP changes on reboot.
        Set-PortProxy $Distro

        # 4) HOLD THE VM OPEN. This blocks until the distro is shut down or
        #    restarted; while it blocks, the WSL2 VM stays alive so dockerd and
        #    all containers keep running. When it returns, the loop re-ups.
        wsl.exe -d $Distro -u root -- sleep infinity
    } catch {
        # Docker not ready yet / transient WSL error -> back off and retry.
    }
    Start-Sleep -Seconds 5
}
