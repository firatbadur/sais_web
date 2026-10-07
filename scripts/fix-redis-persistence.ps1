<#
  Envisoft WebX - REDIS KALICILIK DUZELTMESI (mevcut Windows sahalari icin, tek seferlik)
  ---------------------------------------------------------------------------------------
  Sorun: Eski compose dosyalarinda redis "--appendonly yes" + redis_data volume ile
  calisiyordu. Elektrik kesintisi / sert kapanmada AOF dosyasi yarim kaliyor, Redis her
  acilista "Bad file format reading the append only file" ile cokuyor; celery_worker ve
  celery_beat "Error -2 connecting to redis:6379. Name or service not known" ile bekliyor
  -> polling + SIM gonderimi SESSIZCE durur (dashboard acik gorunur). Saha: Reyhanli.

  Cozum: Redis'te kalici veri yok (kuyruk + cache + kilit; kalici veri PostgreSQL'de) ->
  kalicilik kapatilir, volume mount'u kaldirilir. Bozulacak dosya kalmaz.

  Watchtower yalniz imaji yeniler, host'taki compose dosyasini DEGIL -> mevcut sahalarda
  bu script bir kez calistirilmali. Yeni kurulumlar duzeltilmis compose ile gelir.

  Kullanim (saha makinesinde, YONETICI PowerShell):
      powershell -ExecutionPolicy Bypass -File .\fix-redis-persistence.ps1

  Idempotent: zaten duzeltilmis dosyada hicbir sey degistirmez. Once yedek alir
  (docker-compose.prod.yml.bak-<zaman>), compose dogrulamasi basarisizsa yedege doner.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "C:\EnvisoftWebX",
    [string]$Distro     = "Ubuntu"
)

$ErrorActionPreference = "Stop"
$env:WSL_UTF8 = "1"

function ConvertTo-WslPath([string]$winPath) {
    $full  = [System.IO.Path]::GetFullPath($winPath)
    $drive = $full.Substring(0, 1).ToLower()
    $rest  = $full.Substring(2) -replace '\\', '/'
    return "/mnt/$drive$rest"
}

$composeFile = Join-Path $InstallDir "docker-compose.prod.yml"
if (-not (Test-Path $composeFile)) { throw "$composeFile bulunamadi." }

$WslDir  = ConvertTo-WslPath $InstallDir
$Compose = "cd '$WslDir' && docker compose --env-file .env -f docker-compose.prod.yml"

function Invoke-Compose([string]$composeArgs) {
    # Cikti ekrana; donus degeri YALNIZ exit kodu (aksi halde cikti satirlari
    # donus dizisine karisir ve "-ne 0" karsilastirmasi yanlis sonuc verir).
    wsl.exe -d $Distro -u root -- bash -lc "$Compose $composeArgs 2>&1" | Out-Host
    return $LASTEXITCODE
}

$body = [System.IO.File]::ReadAllText($composeFile)
$newCmd = 'command: ["redis-server", "--appendonly", "no", "--save", ""]'

if ($body -match [regex]::Escape($newCmd)) {
    Write-Host "[OK] Compose dosyasi zaten duzeltilmis - degisiklik yok." -ForegroundColor Green
} else {
    $pattern = '(?m)^(?<ind>[ \t]*)command:[ \t]*\["redis-server",[ \t]*"--appendonly",[ \t]*"yes"\][ \t]*\r?\n[ \t]*volumes:[ \t]*\r?\n[ \t]*-[ \t]*redis_data:/data[ \t]*(?<nl>\r?\n)'
    $m = [regex]::Match($body, $pattern)
    if (-not $m.Success) {
        throw "Beklenen redis blogu bulunamadi (compose dosyasi elle degistirilmis olabilir). Elle duzenleyin: redis 'command' satirini '$newCmd' yapin ve redis altindaki 'volumes: - redis_data:/data' satirlarini silin."
    }
    $replacement = $m.Groups["ind"].Value + $newCmd + $m.Groups["nl"].Value
    $patched = $body.Substring(0, $m.Index) + $replacement + $body.Substring($m.Index + $m.Length)

    $backup = "$composeFile.bak-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    Copy-Item $composeFile $backup
    [System.IO.File]::WriteAllText($composeFile, $patched, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "[OK] Compose dosyasi duzeltildi. Yedek: $backup" -ForegroundColor Green

    if ((Invoke-Compose "config -q") -ne 0) {
        Copy-Item $backup $composeFile -Force
        throw "Compose dogrulamasi basarisiz - dosya yedekten geri yuklendi."
    }
}

Write-Host "Redis yeniden olusturuluyor..." -ForegroundColor Cyan
if ((Invoke-Compose "up -d redis") -ne 0) { throw "redis baslatilamadi." }

$pong = ""
for ($i = 0; $i -lt 15; $i++) {
    $pong = (wsl.exe -d $Distro -u root -- bash -lc "$Compose exec -T redis redis-cli ping 2>/dev/null" | Out-String).Trim()
    if ($pong -eq "PONG") { break }
    Start-Sleep -Seconds 2
}
if ($pong -ne "PONG") { throw "Redis PING yanit vermedi. Kontrol: logs --tail 50 redis" }
Write-Host "[OK] Redis: PONG" -ForegroundColor Green

Write-Host "celery_worker + celery_beat yeniden baslatiliyor..." -ForegroundColor Cyan
[void](Invoke-Compose "restart celery_worker celery_beat")
[void](Invoke-Compose "ps")

Write-Host ""
Write-Host "Tamam. Eski redis_data volume'u artik kullanilmiyor (zararsiz; istenirse silinebilir):" -ForegroundColor Cyan
Write-Host "  wsl -d $Distro -u root -- docker volume ls | findstr redis_data" -ForegroundColor Gray
