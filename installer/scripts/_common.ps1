<#
.SYNOPSIS
    Shared helpers for installer scripts (dot-sourced).

.DESCRIPTION
    The Envisoft WebX stack runs on Docker CE inside WSL2 (no Docker Desktop
    license required). Compose files + .env live on the Windows side under
    InstallDir; WSL reaches them via /mnt/<drive>/... This module wraps docker
    calls so they run inside WSL.

    NOTE: This file is intentionally ASCII-only (English). Windows PowerShell 5.1
    reads BOM-less scripts as ANSI; non-ASCII characters would corrupt parsing.
#>

$script:WslDistro = "Ubuntu"

# Every WSL call that touches Docker runs as ROOT — explicitly, never relying on
# the distro's *default* user.
#
# WHY (real field incident): 00-ensure-docker installs the distro with
# `wsl --install -d Ubuntu --no-launch`, so Ubuntu's first-run OOBE never runs
# -> no Unix user exists -> the default user IS root -> docker works. But the
# moment ANYONE opens an interactive `wsl` on the box (a technician debugging),
# the OOBE fires, creates a user (named after the Windows account, e.g.
# "envisoftwebx") and makes it the DEFAULT. That user is not in the `docker`
# group, so every `docker compose` here dies with
# "permission denied ... unix:///var/run/docker.sock".
#
# The failure is SILENT and nasty: containers keep running (restart:
# unless-stopped) so the site looks fine, but sais-stack.ps1's `up -d` throws ->
# the loop never reaches Set-PortProxy (external 80/443 bridge goes stale on the
# next WSL IP change) nor `sleep infinity` (WSL2 VM keepalive). Pinning root
# makes the installer + service independent of whatever the default user is.
$script:WslUser = "root"
$script:ComposeFile = "docker-compose.prod.yml"

# True when our stdout is a pipe (e.g. a step running as a child process whose
# output the installer captures via `| Out-Host`). In that case an in-place `\r`
# spinner does NOT rewrite the line - it floods the console one line per frame.
# When redirected we fall back to a throttled "still working (Ns)" heartbeat.
$script:SpinRedirected = $false
try { $script:SpinRedirected = [Console]::IsOutputRedirected } catch {}

# Draw one progress frame (in-place spinner on a real console; nothing here for
# the redirected case - the caller throttles heartbeats itself).
function Write-SpinFrame([string]$Char, [string]$Label, [int]$Elapsed) {
    if (-not $script:SpinRedirected) {
        Write-Host ("`r   [{0}] {1}  ({2}s)     " -f $Char, $Label, $Elapsed) -NoNewline -ForegroundColor Cyan
    }
}

# Final status line for a spinner section (OK / X / !), redirection-aware.
function Write-SpinEnd([string]$Tag, [string]$Label, [int]$Elapsed, [string]$Color) {
    $msg = "   [{0}] {1}  ({2}s)             " -f $Tag, $Label, $Elapsed
    if (-not $script:SpinRedirected) { $msg = "`r" + $msg }
    Write-Host $msg -ForegroundColor $Color
}

function Write-Step([string]$msg) {
    Write-Host ">> $msg" -ForegroundColor Cyan
}

function Write-Ok([string]$msg) {
    Write-Host "   [OK] $msg" -ForegroundColor Green
}

function Write-WarnLine([string]$msg) {
    Write-Host "   [!] $msg" -ForegroundColor Yellow
}

# C:\EnvisoftWebX  ->  /mnt/c/EnvisoftWebX
function ConvertTo-WslPath([string]$winPath) {
    $full = [System.IO.Path]::GetFullPath($winPath)
    $drive = $full.Substring(0, 1).ToLower()
    $rest = $full.Substring(2) -replace '\\', '/'
    return "/mnt/$drive$rest"
}

# Is Docker running inside the WSL distro?
# IMPORTANT: redirect docker's stderr INSIDE bash (docker info >/dev/null 2>&1),
# never via a PowerShell-side `*>`/`2>`. Under ErrorActionPreference='Stop' a
# PowerShell redirect of a native command's stderr raises NativeCommandError,
# so this would wrongly return $false even when the daemon is UP (docker info
# always prints warnings to stderr). That false-negative made the installer
# loop on "Starting Docker daemon" until timeout although Docker was running.
function Test-DockerReady {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    try {
        wsl.exe -d $script:WslDistro -u $script:WslUser -- bash -lc "docker info >/dev/null 2>&1" | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $prev
    }
}

# Run an arbitrary bash command inside the WSL distro (as root — see $WslUser).
function Invoke-Wsl([string]$bash) {
    wsl.exe -d $script:WslDistro -u $script:WslUser -- bash -lc "$bash"
    if ($LASTEXITCODE -ne 0) {
        throw "WSL command failed (exit $LASTEXITCODE): $bash"
    }
}

# Make sure the Docker daemon is running inside the distro (systemd fallback).
function Assert-Docker {
    wsl.exe -d $script:WslDistro -u root -- bash -lc `
        "docker info >/dev/null 2>&1 || service docker start 2>/dev/null || systemctl start docker 2>/dev/null || (pgrep dockerd >/dev/null || (dockerd >/var/log/dockerd.log 2>&1 &)); sleep 2" *> $null
}

# Run `docker compose ...` in the InstallDir context.
function Invoke-Compose([string]$InstallDir, [string]$composeArgs) {
    Assert-Docker
    $wslDir = ConvertTo-WslPath $InstallDir
    $cmd = "cd '$wslDir' && docker compose --env-file .env -f $script:ComposeFile $composeArgs"
    wsl.exe -d $script:WslDistro -u $script:WslUser -- bash -lc "$cmd"
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed (exit $LASTEXITCODE): $composeArgs"
    }
}

# Run a manage.py command inside the web container.
function Invoke-Manage([string]$InstallDir, [string]$manageArgs, [string]$envInline = "") {
    $prefix = ""
    if ($envInline) { $prefix = "$envInline " }
    Invoke-Compose $InstallDir "exec -T web env $prefix python manage.py $manageArgs"
}

# Build the `docker compose ...` bash command for a given InstallDir (no exec).
function Get-ComposeBash([string]$InstallDir, [string]$composeArgs) {
    $wslDir = ConvertTo-WslPath $InstallDir
    return "cd '$wslDir' && docker compose --env-file .env -f $script:ComposeFile $composeArgs"
}

# ----------------------------------------------------------------------------
# Invoke-WslSpin: run a long WSL/bash command while showing an ANIMATED spinner
# with elapsed seconds, so the console never looks frozen during downloads or
# image pulls. The command runs in a background job; its full output is captured
# to a per-step log (shown only if the step fails). Throws on non-zero exit.
#
# Why a job + log instead of live output: live native output (docker pull layer
# noise, get.docker.com) interleaves with the spinner and looks messy. The
# spinner is the "loader" the operator watches; the log keeps the detail.
# ----------------------------------------------------------------------------
function Invoke-WslSpin {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][string]$Bash,
        [string]$User = "",
        [string]$Distro = "",
        [string]$LogDir = "",
        [int]$Retries = 0          # transient docker/runc exec failures (e.g.
                                   # "write init-p: broken pipe", exit 128) ->
                                   # retry. Only use for idempotent commands.
    )
    if (-not $Distro) { $Distro = $script:WslDistro }
    # Default to root: callers (20-up pull/up, 30-firstrun exec) all touch Docker
    # and must not depend on the distro's default user. See $script:WslUser.
    if (-not $User) { $User = $script:WslUser }
    if (-not $LogDir) { $LogDir = $env:TEMP }
    $log = Join-Path $LogDir ("envisoft-step-" + ([guid]::NewGuid().ToString('N').Substring(0, 8)) + ".log")

    # CRLF-safe transport: a multi-line bash script passed straight to `bash -lc`
    # carries Windows CR (\r) bytes that break bash parsing ("set: usage",
    # "unexpected end of file from 'if'"). Base64-encode the (CR-stripped) script
    # and decode inside WSL -> no quoting/newline/CR pitfalls at all.
    $clean = ($Bash -replace "`r", "")
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($clean))
    $wrapped = "echo $b64 | base64 --decode | bash"

    for ($attempt = 0; $attempt -le $Retries; $attempt++) {
        $lbl = if ($attempt -gt 0) { "$Label (retry $attempt/$Retries)" } else { $Label }
        $job = Start-Job -ScriptBlock {
            param($d, $u, $b, $lg)
            if ($u) { wsl.exe -d $d -u $u -- bash -lc $b *> $lg }
            else    { wsl.exe -d $d -- bash -lc $b *> $lg }
            $LASTEXITCODE
        } -ArgumentList $Distro, $User, $wrapped, $log

        $spin = @('|', '/', '-', '\')
        $i = 0
        $t0 = Get-Date
        $lastBeat = -999
        if ($script:SpinRedirected) { Write-Host ("   ... {0} ..." -f $lbl) -ForegroundColor Cyan }
        while ($job.State -eq 'Running') {
            $el = [int]((Get-Date) - $t0).TotalSeconds
            if ($script:SpinRedirected) {
                if (($el - $lastBeat) -ge 12) {
                    Write-Host ("   ... {0} ({1}s)" -f $lbl, $el) -ForegroundColor DarkCyan
                    $lastBeat = $el
                }
            } else {
                Write-SpinFrame $spin[$i % 4] $lbl $el
            }
            Start-Sleep -Milliseconds 200
            $i++
        }

        $jobOut = Receive-Job $job
        Remove-Job $job -Force -ErrorAction SilentlyContinue
        $exit = 0
        if ($null -ne $jobOut) { try { $exit = [int]($jobOut | Select-Object -Last 1) } catch { $exit = 1 } }
        $el = [int]((Get-Date) - $t0).TotalSeconds

        if ($exit -eq 0) {
            Write-SpinEnd "OK" $lbl $el "Green"
            return
        }

        if ($attempt -lt $Retries) {
            Write-SpinEnd "!" "$lbl failed (exit $exit) - retrying" $el "Yellow"
            Start-Sleep -Seconds 3
            continue
        }

        Write-SpinEnd "X" "$lbl FAILED" $el "Red"
        if (Test-Path $log) {
            Write-Host "      ---- last lines of step log ($log) ----" -ForegroundColor DarkGray
            Get-Content $log -Tail 25 -ErrorAction SilentlyContinue | ForEach-Object {
                Write-Host "      $_" -ForegroundColor DarkGray
            }
        }
        throw "$Label failed (exit $exit). Full log: $log"
    }
}

# Spinner-driven wait: poll $Check (a scriptblock returning $true when ready)
# while showing an animated spinner. Returns $true if ready before timeout.
function Wait-WithSpin {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][scriptblock]$Check,
        [int]$TimeoutSec = 600,
        [int]$CheckEverySec = 4
    )
    $spin = @('|', '/', '-', '\')
    $i = 0
    $t0 = Get-Date
    $deadline = $t0.AddSeconds($TimeoutSec)
    $nextCheck = $t0
    $ready = $false
    $lastBeat = -999
    if ($script:SpinRedirected) { Write-Host ("   ... {0} ..." -f $Label) -ForegroundColor Cyan }
    while ((Get-Date) -lt $deadline) {
        if ((Get-Date) -ge $nextCheck) {
            try { $ready = [bool](& $Check) } catch { $ready = $false }
            if ($ready) { break }
            $nextCheck = (Get-Date).AddSeconds($CheckEverySec)
        }
        $el = [int]((Get-Date) - $t0).TotalSeconds
        if ($script:SpinRedirected) {
            if (($el - $lastBeat) -ge 12) {
                Write-Host ("   ... {0} ({1}s)" -f $Label, $el) -ForegroundColor DarkCyan
                $lastBeat = $el
            }
        } else {
            Write-SpinFrame $spin[$i % 4] $Label $el
        }
        Start-Sleep -Milliseconds 250
        $i++
    }
    $el = [int]((Get-Date) - $t0).TotalSeconds
    if ($ready) {
        Write-SpinEnd "OK" $Label $el "Green"
    } else {
        Write-SpinEnd "!" "$Label - timed out" $el "Yellow"
    }
    return $ready
}
