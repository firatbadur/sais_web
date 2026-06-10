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
$script:ComposeFile = "docker-compose.prod.yml"

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

# Is the WSL distro present and is Docker running inside it?
function Test-DockerReady {
    try {
        $distros = (wsl.exe --list --quiet) -replace "`0", ""
        if ($distros -notmatch [regex]::Escape($script:WslDistro)) { return $false }
        wsl.exe -d $script:WslDistro -- bash -lc "docker info" *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

# Run an arbitrary bash command inside the WSL distro.
function Invoke-Wsl([string]$bash) {
    wsl.exe -d $script:WslDistro -- bash -lc "$bash"
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
    wsl.exe -d $script:WslDistro -- bash -lc "$cmd"
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
        [string]$LogDir = ""
    )
    if (-not $Distro) { $Distro = $script:WslDistro }
    if (-not $LogDir) { $LogDir = $env:TEMP }
    $log = Join-Path $LogDir ("envisoft-step-" + ([guid]::NewGuid().ToString('N').Substring(0, 8)) + ".log")

    # CRLF-safe transport: a multi-line bash script passed straight to `bash -lc`
    # carries Windows CR (\r) bytes that break bash parsing ("set: usage",
    # "unexpected end of file from 'if'"). Base64-encode the (CR-stripped) script
    # and decode inside WSL -> no quoting/newline/CR pitfalls at all.
    $clean = ($Bash -replace "`r", "")
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($clean))
    $wrapped = "echo $b64 | base64 --decode | bash"

    $job = Start-Job -ScriptBlock {
        param($d, $u, $b, $lg)
        if ($u) { wsl.exe -d $d -u $u -- bash -lc $b *> $lg }
        else    { wsl.exe -d $d -- bash -lc $b *> $lg }
        $LASTEXITCODE
    } -ArgumentList $Distro, $User, $wrapped, $log

    $spin = @('|', '/', '-', '\')
    $i = 0
    $t0 = Get-Date
    while ($job.State -eq 'Running') {
        $el = [int]((Get-Date) - $t0).TotalSeconds
        Write-Host ("`r   [{0}] {1}  ({2}s)     " -f $spin[$i % 4], $Label, $el) -NoNewline -ForegroundColor Cyan
        Start-Sleep -Milliseconds 200
        $i++
    }

    $jobOut = Receive-Job $job
    Remove-Job $job -Force -ErrorAction SilentlyContinue
    $exit = 0
    if ($null -ne $jobOut) { try { $exit = [int]($jobOut | Select-Object -Last 1) } catch { $exit = 1 } }
    $el = [int]((Get-Date) - $t0).TotalSeconds

    if ($exit -ne 0) {
        Write-Host ("`r   [X] {0}  ({1}s) FAILED        " -f $Label, $el) -ForegroundColor Red
        if (Test-Path $log) {
            Write-Host "      ---- last lines of step log ($log) ----" -ForegroundColor DarkGray
            Get-Content $log -Tail 25 -ErrorAction SilentlyContinue | ForEach-Object {
                Write-Host "      $_" -ForegroundColor DarkGray
            }
        }
        throw "$Label failed (exit $exit). Full log: $log"
    }
    Write-Host ("`r   [OK] {0}  ({1}s)             " -f $Label, $el) -ForegroundColor Green
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
    while ((Get-Date) -lt $deadline) {
        if ((Get-Date) -ge $nextCheck) {
            try { $ready = [bool](& $Check) } catch { $ready = $false }
            if ($ready) { break }
            $nextCheck = (Get-Date).AddSeconds($CheckEverySec)
        }
        $el = [int]((Get-Date) - $t0).TotalSeconds
        Write-Host ("`r   [{0}] {1}  ({2}s)     " -f $spin[$i % 4], $Label, $el) -NoNewline -ForegroundColor Cyan
        Start-Sleep -Milliseconds 250
        $i++
    }
    $el = [int]((Get-Date) - $t0).TotalSeconds
    if ($ready) {
        Write-Host ("`r   [OK] {0}  ({1}s)             " -f $Label, $el) -ForegroundColor Green
    } else {
        Write-Host ("`r   [!] {0} - timed out ({1}s)        " -f $Label, $el) -ForegroundColor Yellow
    }
    return $ready
}
