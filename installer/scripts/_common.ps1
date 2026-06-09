<#
.SYNOPSIS
    Installer scriptleri için ortak yardımcılar (dot-source edilir).

.DESCRIPTION
    SAIS yığını WSL2 içindeki Docker CE üzerinde çalışır (Docker Desktop
    lisansı GEREKMEZ). Compose dosyaları + .env Windows tarafında InstallDir'de
    durur; WSL bunlara /mnt/<sürücü>/... yolundan erişir. Bu modül docker
    çağrılarını WSL'e yönlendiren sarmalayıcıları sağlar.
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

# C:\SAIS  ->  /mnt/c/SAIS
function ConvertTo-WslPath([string]$winPath) {
    $full = [System.IO.Path]::GetFullPath($winPath)
    $drive = $full.Substring(0, 1).ToLower()
    $rest = $full.Substring(2) -replace '\\', '/'
    return "/mnt/$drive$rest"
}

# WSL distro mevcut ve Docker çalışıyor mu?
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

# WSL distro içinde keyfi bash komutu çalıştır.
function Invoke-Wsl([string]$bash) {
    wsl.exe -d $script:WslDistro -- bash -lc "$bash"
    if ($LASTEXITCODE -ne 0) {
        throw "WSL komutu başarısız (exit $LASTEXITCODE): $bash"
    }
}

# Docker daemon'ının distro içinde çalıştığından emin ol (systemd yoksa fallback).
function Assert-Docker {
    wsl.exe -d $script:WslDistro -u root -- bash -lc `
        "docker info >/dev/null 2>&1 || service docker start 2>/dev/null || systemctl start docker 2>/dev/null || (pgrep dockerd >/dev/null || (dockerd >/var/log/dockerd.log 2>&1 &)); sleep 2" *> $null
}

# InstallDir bağlamında `docker compose ...` çalıştır.
function Invoke-Compose([string]$InstallDir, [string]$composeArgs) {
    Assert-Docker
    $wslDir = ConvertTo-WslPath $InstallDir
    $cmd = "cd '$wslDir' && docker compose --env-file .env -f $script:ComposeFile $composeArgs"
    wsl.exe -d $script:WslDistro -- bash -lc "$cmd"
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose başarısız (exit $LASTEXITCODE): $composeArgs"
    }
}

# web container'ında manage.py komutu çalıştır.
function Invoke-Manage([string]$InstallDir, [string]$manageArgs, [string]$envInline = "") {
    $prefix = ""
    if ($envInline) { $prefix = "$envInline " }
    Invoke-Compose $InstallDir "exec -T web env $prefix python manage.py $manageArgs"
}
