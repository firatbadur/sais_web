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
