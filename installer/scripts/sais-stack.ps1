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
    [string]$Distro = "Ubuntu",
    [string]$SvcUser = "EnvisoftWebX"
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "_common.ps1")
$script:WslDistro = $Distro

# --- Auto-login password self-heal (Layer 2) ---------------------------------
# The box auto-logs-into the EnvisoftWebX service account at boot, using the
# password stored in the registry (Winlogon\DefaultPassword). That registry value
# is the SINGLE SOURCE OF TRUTH. If anyone with admin rights forcibly changes the
# real account password (net user / lusrmgr), auto-login would break on the next
# reboot. To prevent that, each loop we re-assert the real account password to
# match the stored DefaultPassword - reverting any drift. Non-fatal on failure
# (e.g. a tightened password policy); the loop keeps running.
function Sync-ServicePassword([string]$user) {
    try {
        $winlogon = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
        $stored = (Get-ItemProperty -Path $winlogon -Name "DefaultPassword" -ErrorAction SilentlyContinue).DefaultPassword
        if (-not $stored) { return }
        # Pass the password as a separate argv element so special characters are
        # not re-parsed by a shell. /passwordchg:no re-asserts Layer 1 too.
        & net.exe user $user $stored /passwordchg:no /active:yes /expires:never *> $null
    } catch {
        # Non-fatal; retried next loop.
    }
}

# --- WSL2 port forwarding (external access fix) ------------------------------
# Docker runs inside WSL2; in NAT mode WSL only mirrors published ports to the
# host's 127.0.0.1, NOT to the LAN/public interface -> external HTTPS (Caddy
# 80/443) times out even when the modem/firewall forwards correctly. We bridge
# the host to the WSL2 VM with `netsh portproxy`. The WSL2 IP changes on every
# boot, so this re-runs each loop with the CURRENT IP. The logon task runs with
# Highest privileges, so netsh/firewall calls succeed.
#
# listenaddress=0.0.0.0 (ALL interfaces) on purpose: binding to a specific LAN
# IP proved unreliable on real sites (e.g. 10.x corporate NICs) - the portproxy
# rule shows up in `show all` but never actually forwards, so external HTTPS
# stays closed even though Caddy works locally. 0.0.0.0 catches every interface
# and removes any listen-IP mismatch (verified fix at first site).
function Set-PortProxy([string]$Distro) {
    try {
        $wslIp = (wsl.exe -d $Distro -- hostname -I 2>$null)
        if ($wslIp) { $wslIp = $wslIp.Trim().Split(" ")[0] }
        if ($wslIp -notmatch '^\d+\.\d+\.\d+\.\d+$') { return }

        netsh interface portproxy reset 2>$null | Out-Null
        foreach ($port in 80, 443) {
            netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=$port `
                connectaddress=$wslIp connectport=$port 2>$null | Out-Null
        }
        New-NetFirewallRule -DisplayName "EnvisoftWebX HTTP"  -Direction Inbound -Protocol TCP -LocalPort 80  -Action Allow -ErrorAction SilentlyContinue | Out-Null
        New-NetFirewallRule -DisplayName "EnvisoftWebX HTTPS" -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow -ErrorAction SilentlyContinue | Out-Null
    } catch {
        # netsh / cmdlet failure -> non-fatal; retried next loop.
    }
}

# --- Machine fingerprint (license node-lock) ---------------------------------
# The app license can be locked to THIS machine. The fingerprint must be derived
# from the REAL host hardware at runtime (not just read from a copied .env), so
# we re-compute it every boot and write it into .env. If the whole install is
# copied to another PC, this overwrites the copied value with the NEW machine's
# fingerprint -> it no longer matches the signed token -> the app locks. The
# fingerprint reaches the container via docker-compose env_file: .env.
function Get-MachineFingerprint() {
    try { $guid = (Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Cryptography" -Name "MachineGuid" -ErrorAction Stop).MachineGuid } catch { $guid = "" }
    try { $board = (Get-CimInstance -ClassName Win32_BaseBoard -ErrorAction Stop).SerialNumber } catch { $board = "" }
    $bytes = [System.Security.Cryptography.SHA256]::Create().ComputeHash([System.Text.Encoding]::UTF8.GetBytes("$guid|$board"))
    return (($bytes | ForEach-Object { $_.ToString("x2") }) -join "")
}

function Update-EnvFingerprint([string]$InstallDir) {
    try {
        $fp = Get-MachineFingerprint
        if (-not $fp) { return }
        $envPath = Join-Path $InstallDir ".env"
        if (-not (Test-Path $envPath)) { return }
        $content = Get-Content -Raw -Encoding UTF8 $envPath
        $line = "MACHINE_FINGERPRINT=$fp"
        if ($content -match '(?m)^MACHINE_FINGERPRINT=.*$') {
            $new = [regex]::Replace($content, '(?m)^MACHINE_FINGERPRINT=.*$', $line)
        } else {
            $sep = if ($content.EndsWith("`n")) { "" } else { "`n" }
            $new = $content + $sep + $line + "`n"
        }
        # Only rewrite if changed (avoids needless container recreate on `up -d`).
        if ($new -ne $content) {
            $new = $new -replace "`r`n", "`n"
            $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
            [System.IO.File]::WriteAllText($envPath, $new, $utf8NoBom)
        }
    } catch {
        # non-fatal; retried next loop.
    }
}

while ($true) {
    try {
        # 0) Keep the auto-login password in sync (Layer 2 self-heal).
        Sync-ServicePassword $SvcUser

        # 0b) Refresh the live machine fingerprint into .env (license node-lock).
        #     Must run BEFORE `up -d` so a changed value recreates the containers.
        Update-EnvFingerprint $InstallDir

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
