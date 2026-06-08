<#
.SYNOPSIS
    Yeni saha açılış scripti (Windows / Docker Desktop).

.DESCRIPTION
    Bir SCADA istasyonunu sıfırdan ayağa kaldırır:
      1. GHCR'a login (image'ları çekebilmek için)
      2. Image'ları çek + container'ları başlat
      3. İlk kurulumda bir kez veri tohumlama (seed_initial_data + seed_sais_data)

    Ön koşul: Docker Desktop kurulu; bu dizinde docker-compose.prod.yml ve
    saha-özel doldurulmuş .env mevcut.

.PARAMETER FirstRun
    İlk kurulum: veri tohumlama + superuser oluşturma adımlarını da çalıştırır.

.EXAMPLE
    .\scripts\bootstrap-site.ps1
    .\scripts\bootstrap-site.ps1 -FirstRun
#>
param(
    [switch]$FirstRun
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$compose = @("compose", "-f", "docker-compose.prod.yml", "--env-file", ".env")

if (-not (Test-Path ".env")) {
    Write-Error "HATA: .env bulunamadı. .env.example'dan kopyalayıp saha-özel doldurun."
}

# 1) GHCR login — image private ise gerekli. read:packages kapsamlı PAT kullan.
$dockerConfig = Join-Path $env:USERPROFILE ".docker\config.json"
$needLogin = $true
if (Test-Path $dockerConfig) {
    if (Select-String -Path $dockerConfig -Pattern "ghcr.io" -Quiet) { $needLogin = $false }
}
if ($needLogin) {
    Write-Host ">> GHCR'a login olun (username = GitHub kullanıcı adı, password = read:packages PAT):"
    docker login ghcr.io
}

# 2) Çek + başlat
Write-Host ">> Image'lar çekiliyor..."
docker @compose pull
Write-Host ">> Container'lar başlatılıyor..."
docker @compose up -d

# 3) İlk kurulum: çekirdek + SAIS veri tohumlama
if ($FirstRun) {
    Write-Host ">> İlk kurulum: veri tohumlanıyor (web container'ında)..."
    docker @compose exec -T web python manage.py seed_initial_data
    docker @compose exec -T web python manage.py seed_sais_data
    Write-Host ">> Bir superuser oluşturun:"
    docker @compose exec web python manage.py createsuperuser
    Write-Host ">> Sonraki adım: admin panelinden bu sahaya özel Station / Connection /"
    Write-Host "   Sensor ve SaisCabinet (Bakanlık SIM ID) kayıtlarını girin."
}

Write-Host ">> Tamamlandı. Durum:"
docker @compose ps
