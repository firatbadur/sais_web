<#
  Envisoft WebX - SAHA TESHIS SCRIPTI (Windows / WSL2 kurulumlari icin)
  ---------------------------------------------------------------------
  "Panelden Simdi Guncelle yaptim, site acilmiyor" durumunda TEK komutla
  butun kaniti toplar, bilinen hata imzalarini tarar ve Turkce teshis basar.

  Kullanim (saha makinesinde, YONETICI PowerShell):
      powershell -ExecutionPolicy Bypass -File .\diagnose-site.ps1
      powershell -ExecutionPolicy Bypass -File .\diagnose-site.ps1 -Lines 600

  Cikti: ekrana ozet + C:\EnvisoftWebX\logs\diagnose-<zaman>.txt (destege gonder).
  SALT OKUMA: hicbir container'i durdurmaz/yeniden baslatmaz, .env'e dokunmaz.

  NOT: Butun docker cagrilari WSL distro'sunda ve ACIKCA -u root ile kosar
  (bkz. installer/scripts/_common.ps1 $WslUser aciklamasi: OOBE ile olusan
  varsayilan kullanici docker grubunda degildir -> permission denied).
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "C:\EnvisoftWebX",
    [string]$Distro     = "Ubuntu",
    [int]$Lines         = 300,
    [string]$OutFile    = ""
)

$ErrorActionPreference = "Continue"
$env:WSL_UTF8 = "1"

$SERVICES = @("web", "celery_worker", "celery_beat", "caddy", "db", "redis", "watchtower")

# ---------------------------------------------------------------- yardimcilar
function ConvertTo-WslPath([string]$winPath) {
    $full  = [System.IO.Path]::GetFullPath($winPath)
    $drive = $full.Substring(0, 1).ToLower()
    $rest  = $full.Substring(2) -replace '\\', '/'
    return "/mnt/$drive$rest"
}

# stderr'i BASH icinde birlestir (PowerShell tarafinda 2>&1 kullanma:
# NativeCommandError uretir ve saglikli ciktiyi hata gibi gosterir).
function Wsl([string]$bash) {
    $raw = wsl.exe -d $Distro -u root -- bash -lc "$bash 2>&1"
    if ($null -eq $raw) { return "" }
    return ((($raw) -join "`n") -replace "`0", "")
}

$script:Report = New-Object System.Text.StringBuilder
function Emit([string]$text, [string]$color = "Gray") {
    Write-Host $text -ForegroundColor $color
    [void]$script:Report.AppendLine($text)
}
function Section([string]$title) {
    Emit ""
    Emit ("=" * 78)
    Emit ("== " + $title)
    Emit ("=" * 78)
}
function Quiet([string]$text) { [void]$script:Report.AppendLine($text) }

$WslDir  = ConvertTo-WslPath $InstallDir
$Compose = "cd '$WslDir' && docker compose --env-file .env -f docker-compose.prod.yml"
$stamp   = Get-Date -Format "yyyyMMdd-HHmmss"
if (-not $OutFile) { $OutFile = Join-Path $InstallDir "logs\diagnose-$stamp.txt" }

Emit "Envisoft WebX teshis - $(Get-Date -Format 'dd.MM.yyyy HH:mm:ss')" "Cyan"
Emit "Kurulum dizini : $InstallDir  (WSL: $WslDir)"
Emit "WSL distro     : $Distro"

# ------------------------------------------------------------------- 0) WSL
Section "0) WSL / Docker daemon"
$wslList = Wsl "true"
if ($LASTEXITCODE -ne 0 -and -not $wslList) { Emit "[HATA] WSL calistirilamadi. 'wsl -l -v' ciktisina bak." "Red" }
Emit (wsl.exe -l -v | Out-String)
$dockerVer = Wsl "docker info --format '{{.ServerVersion}}'"
if ($dockerVer -match "permission denied") {
    Emit "[KRITIK] docker.sock izin hatasi -> WSL varsayilan kullanicisi root degil (OOBE tuzagi)." "Red"
    Emit "         Cozum: wsl -d $Distro -u root -- bash -lc \"sed -i '/^\[user\]/,+1d' /etc/wsl.conf\" ; wsl --terminate $Distro" "Yellow"
} elseif (-not $dockerVer) {
    Emit "[KRITIK] Docker daemon yanit vermiyor (dockerd kapali olabilir)." "Red"
    Emit "         Deneme: wsl -d $Distro -u root -- bash -lc 'service docker start'" "Yellow"
} else {
    Emit "Docker Server: $dockerVer" "Green"
}

# ------------------------------------------------------- 1) container durumu
Section "1) Container durumu (compose ps)"
$ps = Wsl "$Compose ps -a"
Emit $ps

Section "1b) Restart sayaci / cikis kodu (restart loop tespiti)"
$insp = Wsl "docker ps -a --filter 'label=com.docker.compose.project=sais_web' --format '{{.Names}}' | while read n; do echo \"\$n | restart=\$(docker inspect -f '{{.RestartCount}}' \$n) | exit=\$(docker inspect -f '{{.State.ExitCode}}' \$n) | health=\$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}}' \$n) | image=\$(docker inspect -f '{{.Config.Image}}' \$n)\"; done"
Emit $insp

# --------------------------------------------------------------- 2) surumler
Section "2) Calisan imaj / uygulama surumu"
$imgs = Wsl "docker ps -a --filter 'label=com.docker.compose.project=sais_web' --format '{{.Names}}\t{{.Image}}\t{{.Status}}'"
Emit $imgs
$ver = Wsl ($Compose + " exec -T web printenv APP_VERSION")
Emit ("web container APP_VERSION: " + ($ver | Out-String).Trim())
Emit "-- imaj etiketi + digest (guncelleme gercekten indi mi) --"
Emit (Wsl "docker images --digests --format '{{.Repository}}:{{.Tag}}\t{{.Digest}}\t{{.CreatedSince}}' | grep -i sais_web")

# --------------------------------------------------------------- 3) .env
Section "3) .env anahtar kontrolu (degerler GIZLI, sadece dolu/bos)"
$envKeys = @(
    "DJANGO_SECRET_KEY","DJANGO_DEBUG","DJANGO_ALLOWED_HOSTS","DJANGO_CSRF_TRUSTED_ORIGINS",
    "POSTGRES_DB","POSTGRES_USER","POSTGRES_PASSWORD","POSTGRES_PORT",
    "GHCR_IMAGE","IMAGE_TAG","WATCHTOWER_API_TOKEN",
    "LICENSE_KEY","LICENSE_URL","CABINET_FERNET_KEY",
    "REPORTS_DIR","BACKUP_DIR","SERIAL_BRIDGE_HOST","SERIAL_BRIDGE_HOST_DIR","MACHINE_FINGERPRINT"
)
foreach ($k in $envKeys) {
    $v = Wsl "cd '$WslDir' && grep -E '^$k=' .env | head -1 | cut -d= -f2-"
    $v = ($v | Out-String).Trim()
    if (-not $v) { Emit ("  {0,-30} : YOK/BOS" -f $k) "Yellow" }
    else {
        $shown = if ($k -match "SECRET|PASSWORD|TOKEN|KEY|FINGERPRINT") { "<dolu>" } else { $v }
        Emit ("  {0,-30} : {1}" -f $k, $shown)
    }
}

# ------------------------------------------------ 4) compose dosyasi guncel mi
Section "4) Host'taki compose dosyasi guncel mi? (Watchtower SADECE imaji yeniler)"
$composeFile = Join-Path $InstallDir "docker-compose.prod.yml"
if (Test-Path $composeFile) {
    $body = Get-Content $composeFile -Raw
    Emit ("  Dosya tarihi : " + (Get-Item $composeFile).LastWriteTime)
    $needles = @{
        "caddy servisi (reverse proxy / HTTPS)" = "caddy:2"
        "report_files volume (Rapor Studyosu)"  = "report_files"
        "/etc/machine-id (lisans node-lock)"    = "machine-id"
        "/bridge (seri kopru)"                  = "SERIAL_BRIDGE_HOST_DIR"
        "ensure_database (acilis zinciri)"      = "ensure_database"
        "watchtower 1.7.x (pinned)"             = "watchtower:1.7"
    }
    foreach ($n in $needles.Keys) {
        if ($body -match [regex]::Escape($needles[$n])) { Emit ("  [VAR ] " + $n) "Green" }
        else { Emit ("  [EKSIK] " + $n + "  -> compose dosyasi ESKI") "Yellow" }
    }
} else {
    Emit "[HATA] $composeFile bulunamadi." "Red"
}

# --------------------------------------------- 4b) DB MOTORU UYUMU (KRITIK)
# Sistem v0.4.0'da MSSQL -> PostgreSQL 16'ya gecti. MSSQL doneminden kalma bir
# sahada "Simdi Guncelle" yeni (PG) imaji cekerse app DB'ye HIC baglanamaz:
# psycopg localhost:5432'ye gider, MSSQL 1433'te durur -> gunicorn hic ayaga
# kalkmaz -> Caddy her istege 502 verir. MSSQL verisine dokunulmaz (migrate
# calisamadigi icin sema bozulmaz) -> IMAGE_TAG ile geri donmek guvenlidir.
Section "4b) Veritabani motoru <-> imaj uyumu"
$dbImage = (Wsl "docker ps -a --filter 'name=sais_web-db-1' --format '{{.Image}}'" | Out-String).Trim()
Emit "  db container imaji : $dbImage"
if ($dbImage -match "mssql") {
    Emit "[KRITIK] Saha MSSQL kullaniyor ama v0.4.0+ imajlari PostgreSQL bekler." "Red"
    Emit "         Bu sahada 'Simdi Guncelle' app'i DB'siz birakir (502)." "Red"
    Emit "         Cozum: .env'de IMAGE_TAG=v0.3.8 (son MSSQL surumu) -> up -d." "Yellow"
    Emit "         Kalici cozum: export_config/import_config ile PostgreSQL'e planli gocus." "Yellow"
} elseif ($dbImage -match "postgres") {
    Emit "  [OK] PostgreSQL - guncel imajlarla uyumlu." "Green"
}

# --------------------------------------------------------------- 5) loglar
Section "5) Container loglari (son $Lines satir)"
$allLogs = ""
foreach ($svc in $SERVICES) {
    $log = Wsl "$Compose logs --tail=$Lines --no-color $svc"
    $allLogs += "`n" + $log
    Emit ""
    Emit ("--- [$svc] " + ("-" * 60)) "Cyan"
    # Ekrani bogmamak icin ekrana son 40 satir, dosyaya TAMAMI.
    $lines = $log -split "`n"
    $tail  = if ($lines.Count -gt 40) { $lines[($lines.Count - 40)..($lines.Count - 1)] } else { $lines }
    Write-Host ($tail -join "`n")
    Quiet $log
}

# ------------------------------------------------------- 6) Django saglik
Section "6) Django / migration durumu (web container icinde)"
$mig = Wsl "$Compose exec -T web python manage.py migrate --check"
if (-not $mig) { $mig = Wsl "$Compose run --rm --no-deps -T web python manage.py migrate --check" }
Emit ("migrate --check: " + $mig)
$pending = Wsl "$Compose exec -T web python manage.py showmigrations --plan | grep -c '\[ \]'"
Emit ("Uygulanmamis migration sayisi: " + ($pending | Out-String).Trim())
Emit (Wsl "$Compose exec -T web python manage.py showmigrations --plan | grep '\[ \]' | head -20")
Emit "-- django check --"
Emit (Wsl "$Compose exec -T web python manage.py check")

Section "7) HTTP probe"
# Container ici saglik: imajin HEALTHCHECK'i zaten localhost:8000'i yoklar ->
# ayrica python one-liner gondermeye gerek yok (tirnak kacisi WSL'de kirilgan).
$health = Wsl "docker inspect -f '{{if .State.Health}}{{.State.Health.Status}} | son: {{with index .State.Health.Log 0}}{{.Output}}{{end}}{{else}}healthcheck yok{{end}}' sais_web-web-1"
Emit ("Container ici saglik (web): " + ($health | Out-String).Trim())
$viaCaddy = Wsl "curl -s -o /dev/null -w 'caddy(80)=%{http_code}\n' -m 10 http://127.0.0.1/ || echo 'caddy(80)=BAGLANAMADI'"
Emit ($viaCaddy | Out-String).Trim()
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1/dashboard/login/" -UseBasicParsing -TimeoutSec 10
    Emit ("Windows host -> http://127.0.0.1/dashboard/login/ : HTTP " + $r.StatusCode) "Green"
} catch {
    Emit ("Windows host -> http://127.0.0.1/dashboard/login/ : HATA " + $_.Exception.Message) "Yellow"
}
Emit "-- 80/443'u tutan process (IIS vb.) --"
Emit (Get-NetTCPConnection -State Listen -LocalPort 80,443 -ErrorAction SilentlyContinue |
      Select-Object LocalAddress,LocalPort,OwningProcess,
        @{n='Process';e={(Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName}} |
      Format-Table -AutoSize | Out-String)

# ------------------------------------------------------------- 8) TESHIS
Section "8) OTOMATIK TESHIS (log imzalari)"
$rules = @(
    @{ p = 'ImproperlyConfigured.*SECRET_KEY|DJANGO_SECRET_KEY tanimli';       m = "[KRITIK] .env'de DJANGO_SECRET_KEY yok/varsayilan. Yeni surum uretimde fail-hard (sais_web/settings.py:37). Cozum: .env'e guclu DJANGO_SECRET_KEY yaz, 'up -d'." },
    @{ p = 'DisallowedHost|Invalid HTTP_HOST header';                          m = "[KRITIK] Domain DJANGO_ALLOWED_HOSTS'ta yok (panelden domain eklemek Django'yu GUNCELLEMEZ). .env'e '.envisoft.com.tr' + CSRF icin 'https://*.envisoft.com.tr' ekle, web'i recreate et." },
    @{ p = 'InconsistentMigrationHistory|Conflicting migrations|NodeNotFound'; m = "[KRITIK] Migration grafigi tutarsiz (cok eski surumden atlama). Once YEDEK al, sonra migrate loglarini oku." },
    @{ p = 'duplicate key value violates unique constraint';                   m = "[KRITIK] Migration veri catismasi (or. Sensor.tag benzersizlik doldurma). Ilgili migration adi loglarda hemen ustte." },
    @{ p = 'column .* does not exist|relation .* does not exist|UndefinedTable|UndefinedColumn'; m = "[KRITIK] Kod semadan ILERI: migrate tamamlanmadi ya da worker/beat eski sema ile kosuyor. Once web'in migrate adimini tamamla." },
    @{ p = 'password authentication failed|FATAL:  role .* does not exist';    m = "[KRITIK] DB kimlik hatasi: .env POSTGRES_PASSWORD ile pg_data volume'undeki parola uyusmuyor (parola ancak ILK acilista yazilir)." },
    @{ p = 'port 5432 failed: Connection refused|Veritabani .* olusturulamadi'; m = "[KRITIK] App PostgreSQL'e (localhost:5432) baglanamiyor. Bolum 3'te POSTGRES_* bos ve bolum 4b'de db=mssql ise: saha MSSQL doneminden kalma, yeni imaj uyumsuz -> IMAGE_TAG=v0.3.8 ile geri don." },
    @{ p = 'could not translate host name|Connection refused.*5432|db.*not ready'; m = "[UYARI] DB container'a ulasilamiyor; 'db' saglikli mi (bolum 1) bak." },
    @{ p = 'Missing staticfiles manifest entry';                               m = "[KRITIK] collectstatic eksik/yarim -> her sayfa 500. Cozum: exec web python manage.py collectstatic --noinput --clear" },
    @{ p = 'Expected str, got SafeString|Expected .*, got ';                   m = "[KRITIK] Cython exact-type regresyonu (release imaji). build/cythonize_app.py '-X annotation_typing=False' ile derlenmeli; bu imaj hatali." },
    @{ p = 'unauthorized|manifest unknown|denied: |pull access denied';        m = "[KRITIK] GHCR pull yetkisi/etiket hatasi -> guncelleme YARIM kaldi. Sahada: docker login ghcr.io (read:packages PAT), sonra 'up -d'." },
    @{ p = 'no space left on device';                                          m = "[KRITIK] Disk dolu (eski imajlar). Cozum: docker image prune -af" },
    @{ p = 'bind: address already in use|port is already allocated';           m = "[KRITIK] 80/443 baska process'te (cogunlukla IIS). W3SVC+WAS durdur/Disabled yap." },
    @{ p = 'Lisans|license_expired|LicenseLock';                               m = "[UYARI] Lisans kilidi olabilir: site 'acilmiyor' degil, kilit ekranina yonleniyor olabilir. /admin/ superuser ile acik kalir; exec web python manage.py refresh_license" },
    @{ p = 'InvalidToken|Fernet|cryptography.fernet';                          m = "[KRITIK] Kabin sirri cozulemiyor: SECRET_KEY degistiyse CABINET_FERNET_KEY turetimi bozulur. Eski SECRET_KEY'i geri koy ya da kabin sifresini yeniden gir." },
    @{ p = 'Killed|OOMKilled|MemoryError';                                     m = "[KRITIK] Bellek yetmedi (OOM). WSL .wslconfig memory limitini yukselt." },
    @{ p = 'permission denied.*docker.sock';                                   m = "[KRITIK] WSL varsayilan kullanicisi root degil -> stack yonetilemiyor (portproxy de kurulamaz)." },
    @{ p = 'exec /usr/local/bin|exec format error|no such file or directory';  m = "[KRITIK] Imaj/komut uyusmazligi: host'taki compose dosyasi imajin bekledigi komut/mount ile uyumsuz (bolum 4'e bak)." }
)
$hit = $false
foreach ($r in $rules) {
    if ($allLogs -match $r.p) {
        $hit = $true
        Emit $r.m "Red"
        $sample = ($allLogs -split "`n" | Select-String -Pattern $r.p | Select-Object -First 2)
        foreach ($s in $sample) { Emit ("      > " + $s.ToString().Trim()) "DarkGray" }
    }
}
if (-not $hit) {
    Emit "Bilinen imza bulunamadi. Bolum 1b'de restart sayaci artan container'in bolum 5'teki" "Yellow"
    Emit "son 40 satirini oku: gercek hata her zaman 'Traceback' bloklarinin SON satirindadir." "Yellow"
}

# --------------------------------------------------------------- 9) kaydet
$logDir = Split-Path $OutFile -Parent
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
[System.IO.File]::WriteAllText($OutFile, $script:Report.ToString(), (New-Object System.Text.UTF8Encoding($false)))
Write-Host ""
Write-Host "Tam rapor: $OutFile" -ForegroundColor Cyan
Write-Host "Destege bu dosyayi gonderin." -ForegroundColor Cyan
