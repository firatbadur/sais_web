<#
  Envisoft WebX - SAHA GUNCELLEME (manuel, Windows sahalari)
  -----------------------------------------------------------
  Dashboard "Simdi Guncelle" calismadiginda (GHCR token'i olmus / degismis, saha eski
  surumde takili) sahayi elle son surume tasir. Tek komut, adim adim:

    1) On kontrol    : kurulum dizini + WSL/Docker erisimi + DNS (ghcr.io cozulmuyorsa
                       WSL resolv.conf gecici olarak 8.8.8.8/1.1.1.1 yapilir)
    2) Yedek         : backup_database --tier=manual (ESKI semayla; geri donus icin sart)
    3) GHCR login    : yeni token stdin ile (komut satirinda/process listesinde gorunmez)
    4) .env          : GHCR_TOKEN + IMAGE_TAG guncellenir (once .env yedeklenir)
    5) Imaj cekme    : YALNIZ uygulama imaji, kopunca otomatik tekrar (postgres/caddy vs.
                       yeniden indirilmez - zayif saha agi icin)
    6) Ayaga kaldir  : up -d --pull never + watchtower force-recreate (yeni login'i gorsun)
    7) Dogrulama     : APP_VERSION + container durumu + watchtower logu
    8) Ek duzeltmeler: fix-redis-persistence.ps1 (yaninda varsa) + fix_nonfinite_values

  Kullanim (saha makinesinde, YONETICI PowerShell; script klasorunde):
      powershell -ExecutionPolicy Bypass -File .\update-site.ps1
  Token sorulur (ekranda gorunmez). Unattended:
      powershell -ExecutionPolicy Bypass -File .\update-site.ps1 -Token ghp_xxx
  Token degismediyse (yalniz surum yukseltme):
      powershell -ExecutionPolicy Bypass -File .\update-site.ps1 -SkipLogin

  Tekrar calistirmak guvenlidir (idempotent). Cikti ayrica
  <InstallDir>\logs\update-<zaman>.txt dosyasina yazilir.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "C:\EnvisoftWebX",
    [string]$Distro     = "Ubuntu",
    [string]$GhcrUser   = "firatbadur",
    [string]$Token      = "",
    [string]$Tag        = "stable",
    [int]$PullRetries   = 10,
    [switch]$SkipLogin,
    [switch]$SkipBackup,
    [switch]$SkipRedisFix
)

$ErrorActionPreference = "Stop"
$env:WSL_UTF8 = "1"

# ---------------------------------------------------------------- yardimcilar
function ConvertTo-WslPath([string]$winPath) {
    $full  = [System.IO.Path]::GetFullPath($winPath)
    $drive = $full.Substring(0, 1).ToLower()
    $rest  = $full.Substring(2) -replace '\\', '/'
    return "/mnt/$drive$rest"
}

function Write-Step([string]$text) { Write-Host ""; Write-Host "=== $text" -ForegroundColor Cyan }
function Write-Ok([string]$text)   { Write-Host "[OK]   $text" -ForegroundColor Green }
function Write-Warn([string]$text) { Write-Host "[UYARI] $text" -ForegroundColor Yellow }
function Write-Fail([string]$text) { Write-Host "[HATA] $text" -ForegroundColor Red }

# Bash komutunu WSL'de root olarak calistirir; cikti ekrana, donus = exit kodu.
# 2>&1 bash ICINDE: PS 5.1'de native stderr'i PowerShell'de yonlendirmek
# NativeCommandError firlatir ($ErrorActionPreference=Stop ile script olur).
# Bash komutlarinda CIFT TIRNAK KULLANMA: PS 5.1 native arguman aktariminda bozar.
function Invoke-Wsl([string]$cmd) {
    wsl.exe -d $Distro -u root -- bash -lc "$cmd 2>&1" | Out-Host
    return $LASTEXITCODE
}

# Bash komutunu calistirip ciktiyi dondurur (son satirlar icin).
function Get-Wsl([string]$cmd) {
    $out = wsl.exe -d $Distro -u root -- bash -lc "$cmd 2>&1"
    $script:LastWslExit = $LASTEXITCODE
    return @($out)
}

# .env'de KEY=VALUE'yu degistir (yoksa sona ekle). Satir sonu stili korunur.
function Set-EnvValue([string]$path, [string]$key, [string]$value) {
    $body = [System.IO.File]::ReadAllText($path)
    $nl = "`n"
    if ($body -match "`r`n") { $nl = "`r`n" }
    $pattern = "(?m)^" + [regex]::Escape($key) + "=.*?(?=\r?$)"
    if ([regex]::IsMatch($body, $pattern)) {
        $body = [regex]::Replace($body, $pattern, { param($m) "$key=$value" })
    } else {
        if ($body.Length -gt 0 -and -not $body.EndsWith("`n")) { $body += $nl }
        $body += "$key=$value$nl"
    }
    [System.IO.File]::WriteAllText($path, $body, (New-Object System.Text.UTF8Encoding($false)))
}

function Get-EnvValue([string]$path, [string]$key) {
    foreach ($line in [System.IO.File]::ReadAllLines($path)) {
        if ($line -match ("^" + [regex]::Escape($key) + "=(.*)$")) { return $Matches[1].Trim() }
    }
    return ""
}

# ---------------------------------------------------------------- baslangic
$envFile     = Join-Path $InstallDir ".env"
$composeFile = Join-Path $InstallDir "docker-compose.prod.yml"
$logDir      = Join-Path $InstallDir "logs"
$stamp       = Get-Date -Format "yyyyMMdd-HHmmss"

if (-not (Test-Path $composeFile)) { throw "$composeFile bulunamadi. -InstallDir dogru mu?" }
if (-not (Test-Path $envFile))     { throw "$envFile bulunamadi." }
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$logFile = Join-Path $logDir "update-$stamp.txt"
try { Start-Transcript -Path $logFile -Force | Out-Null } catch { }

$WslDir  = ConvertTo-WslPath $InstallDir
$Compose = "cd '$WslDir' && docker compose --env-file .env -f docker-compose.prod.yml"

$ghcrImage = Get-EnvValue $envFile "GHCR_IMAGE"
if (-not $ghcrImage) { $ghcrImage = "ghcr.io/firatbadur/sais_web" }
$image = "${ghcrImage}:$Tag"

$exitCode = 0
try {
    # ------------------------------------------------------------ 1) on kontrol
    Write-Step "1/8 On kontrol"
    $null = Get-Wsl "docker info --format '{{.ServerVersion}}'"
    if ($script:LastWslExit -ne 0) {
        throw "WSL '$Distro' icinde Docker'a erisilemiyor. 'wsl -l -v' ile distro adini kontrol edin (-Distro ile verin)."
    }
    Write-Ok "Docker erisilebilir (distro: $Distro)."

    $oldVersion = (Get-Wsl "$Compose exec -T web python -c 'from django.conf import settings; print(settings.APP_VERSION)'" | Select-Object -Last 1)
    $webUp = ($script:LastWslExit -eq 0)
    if ($webUp) { Write-Ok "Mevcut surum: $oldVersion" } else { Write-Warn "web container'i yanit vermiyor (surum okunamadi)." }

    $null = Get-Wsl "getent hosts ghcr.io"
    if ($script:LastWslExit -ne 0) {
        Write-Warn "WSL icinde ghcr.io cozulemiyor -> resolv.conf gecici olarak 8.8.8.8 / 1.1.1.1 yapiliyor (WSL yeniden baslayinca eski haline doner)."
        $null = Invoke-Wsl "rm -f /etc/resolv.conf && printf 'nameserver 8.8.8.8\nnameserver 1.1.1.1\n' > /etc/resolv.conf"
        $null = Get-Wsl "getent hosts ghcr.io"
        if ($script:LastWslExit -ne 0) { throw "DNS duzeltmesine ragmen ghcr.io cozulemiyor. Sahanin internet baglantisini kontrol edin." }
    }
    Write-Ok "ghcr.io cozuluyor."

    # ------------------------------------------------------------ 2) yedek
    Write-Step "2/8 Veritabani yedegi"
    if ($SkipBackup) {
        Write-Warn "-SkipBackup verildi, yedek atlandi."
    } elseif (-not $webUp) {
        Write-Warn "web calismadigi icin yedek alinamadi. Devam etmek riskli (geri donus icin eski semali yedek gerekir)."
        $ans = Read-Host "Yedeksiz devam edilsin mi? (e/h)"
        if ($ans -notmatch '^(e|E|y|Y)') { throw "Kullanici iptal etti." }
    } else {
        if ((Invoke-Wsl "$Compose exec -T web python manage.py backup_database --tier=manual --force") -ne 0) {
            throw "Yedek alinamadi. Sorunu giderin veya bilerek -SkipBackup ile calistirin."
        }
        Write-Ok "Yedek alindi (Dashboard > Yedekleme sayfasinda gorunur)."
    }

    # ------------------------------------------------------------ 3) GHCR login
    Write-Step "3/8 GHCR girisi"
    if ($SkipLogin) {
        Write-Warn "-SkipLogin verildi; mevcut docker login kullanilacak."
    } else {
        if (-not $Token) {
            $sec = Read-Host "Yeni GHCR token (read:packages)" -AsSecureString
            $Token = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
        }
        $Token = $Token.Trim()
        if (-not $Token) { throw "Token bos." }
        # Token stdin'den gider: process listesinde / bash gecmisinde gorunmez.
        $Token | wsl.exe -d $Distro -u root -- bash -lc "docker login ghcr.io -u $GhcrUser --password-stdin 2>&1" | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "GHCR girisi basarisiz. Token'in read:packages yetkisi var mi / suresi dolmus mu?" }
        Write-Ok "GHCR girisi basarili."
    }

    # ------------------------------------------------------------ 4) .env
    Write-Step "4/8 .env guncelleme"
    $envBackup = "$envFile.bak-$stamp"
    Copy-Item $envFile $envBackup
    if (-not $SkipLogin) { Set-EnvValue $envFile "GHCR_TOKEN" $Token }
    $oldTag = Get-EnvValue $envFile "IMAGE_TAG"
    Set-EnvValue $envFile "IMAGE_TAG" $Tag
    Write-Ok "IMAGE_TAG: '$oldTag' -> '$Tag'$(if (-not $SkipLogin) { ', GHCR_TOKEN guncellendi' }). Yedek: $envBackup"

    # ------------------------------------------------------------ 5) imaj
    Write-Step "5/8 Imaj cekiliyor: $image"
    $pulled = $false
    for ($i = 1; $i -le $PullRetries; $i++) {
        Write-Host "--- deneme $i / $PullRetries ---"
        if ((Invoke-Wsl "docker pull $image") -eq 0) { $pulled = $true; break }
        if ($i -lt $PullRetries) { Write-Warn "Cekme yarida kaldi, 10 sn sonra tekrar (biten katmanlar korunur)."; Start-Sleep -Seconds 10 }
    }
    if (-not $pulled) { throw "Imaj $PullRetries denemede cekilemedi. Ag stabil oldugunda scripti tekrar calistirin (indirilen katmanlar korunur)." }
    Write-Ok "Imaj hazir."

    # ------------------------------------------------------------ 6) up
    Write-Step "6/8 Servisler yeniden baslatiliyor"
    if ((Invoke-Wsl "$Compose up -d --pull never") -ne 0) { throw "docker compose up basarisiz." }
    # docker login config.json'i yeni dosya (inode) olarak yazar; calisan
    # watchtower'in bind-mount'u eskisini gormeye devam eder -> recreate sart.
    if ((Invoke-Wsl "$Compose up -d --pull never --force-recreate watchtower") -ne 0) { Write-Warn "watchtower yeniden olusturulamadi." }
    Write-Ok "Servisler baslatildi."

    # ------------------------------------------------------------ 7) dogrulama
    Write-Step "7/8 Dogrulama (web hazir olana kadar bekleniyor, en fazla 5 dk)"
    $newVersion = ""
    for ($i = 0; $i -lt 60; $i++) {
        $v = (Get-Wsl "$Compose exec -T web python -c 'from django.conf import settings; print(settings.APP_VERSION)'" | Select-Object -Last 1)
        if ($script:LastWslExit -eq 0 -and $v) { $newVersion = "$v".Trim(); break }
        Start-Sleep -Seconds 5
    }
    $null = Invoke-Wsl "$Compose ps"
    if ($newVersion) {
        Write-Ok "Calisan surum: $newVersion (onceki: $oldVersion)"
    } else {
        Write-Fail "web 5 dk icinde yanit vermedi. Teshis: scripts\diagnose-site.ps1"
        $exitCode = 2
    }

    # ------------------------------------------------------------ 8) ek duzeltmeler
    Write-Step "8/8 Ek duzeltmeler"
    $redisFix = Join-Path $PSScriptRoot "fix-redis-persistence.ps1"
    if ($SkipRedisFix) {
        Write-Warn "-SkipRedisFix verildi."
    } elseif (Test-Path $redisFix) {
        try {
            & $redisFix -InstallDir $InstallDir -Distro $Distro
            Write-Ok "Redis kalicilik kontrolu tamam."
        } catch {
            Write-Warn "fix-redis-persistence basarisiz: $($_.Exception.Message)"
        }
    } else {
        Write-Warn "fix-redis-persistence.ps1 bu scriptin yaninda yok - Redis duzeltmesi atlandi (eski sahalar icin onerilir)."
    }

    if ($newVersion) {
        # v1.5.8+ komutu; eski imajda yoksa sessizce atlanir.
        if ((Invoke-Wsl "$Compose exec -T web python manage.py fix_nonfinite_values") -ne 0) {
            Write-Warn "fix_nonfinite_values calismadi (bu surumde olmayabilir) - atlandi."
        }
    }

    Write-Host ""
    Write-Host "--- watchtower son loglar ---" -ForegroundColor Cyan
    $null = Invoke-Wsl "$Compose logs --tail 15 watchtower"

    Write-Host ""
    if ($exitCode -eq 0) {
        Write-Host "GUNCELLEME TAMAMLANDI: $oldVersion -> $newVersion" -ForegroundColor Green
        Write-Host "Not: ilk ~40 sn web acilirken 502 gorulmesi normaldir." -ForegroundColor Gray
    }
} catch {
    Write-Fail $_.Exception.Message
    $exitCode = 1
} finally {
    Write-Host "Log: $logFile" -ForegroundColor Gray
    try { Stop-Transcript | Out-Null } catch { }
}
exit $exitCode
