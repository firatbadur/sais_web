<#
.SYNOPSIS
    GHCR'a login olur, image'ları çeker, yığını başlatır, sağlıklı olmasını bekler.

.DESCRIPTION
    GHCR private image için gömülü read-only token ile login (--password-stdin).
    Tüm docker işlemleri WSL2 Docker CE içinde çalışır.
#>
param(
    [Parameter(Mandatory)] [string]$InstallDir,
    [Parameter(Mandatory)] [string]$GhcrUser,
    [Parameter(Mandatory)] [string]$GhcrToken,
    [int]$HealthTimeoutSec = 600
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

Write-Step "GHCR'a login olunuyor ($GhcrUser)..."
# Token'ı WSL'e güvenli geçir: stdin üzerinden docker login.
$login = "echo '$GhcrToken' | docker login ghcr.io -u '$GhcrUser' --password-stdin"
Invoke-Wsl $login
Write-Ok "GHCR login başarılı."

Write-Step "Image'lar çekiliyor (docker compose pull)..."
Invoke-Compose $InstallDir "pull"

Write-Step "Yığın başlatılıyor (docker compose up -d)..."
Invoke-Compose $InstallDir "up -d"

Write-Step "Servisler sağlıklı olana dek bekleniyor (max $HealthTimeoutSec sn)..."
$wslDir = ConvertTo-WslPath $InstallDir
$deadline = (Get-Date).AddSeconds($HealthTimeoutSec)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    # web container'ı çalışıyor + DB healthy mı?
    $ps = wsl.exe -d $script:WslDistro -- bash -lc "cd '$wslDir' && docker compose -f $script:ComposeFile ps --format '{{.Service}} {{.State}} {{.Health}}'"
    if ($ps -match "web\s+running" -and $ps -match "db\s+running") {
        $healthy = $true
        break
    }
    Start-Sleep -Seconds 5
}

if ($healthy) {
    Write-Ok "Yığın çalışıyor."
} else {
    Write-WarnLine "Sağlık beklemesi zaman aşımına uğradı; loglara bakın: docker compose logs"
}
