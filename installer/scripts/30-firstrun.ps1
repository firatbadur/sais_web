<#
.SYNOPSIS
    İlk kurulum: veri tohumlama + admin kullanıcı + WebSettings bootstrap.

.DESCRIPTION
    Idempotent — bir marker dosyasıyla korunur, tekrar çalıştırılırsa atlanır.
    seed_initial_data + seed_sais_data + seed_admin_user (non-interactive).
    web-bootstrap.json varsa WebSettings'i domain/TLS ile doldurur ve Caddyfile
    üretir (panel yapılandırması installer'dan gelir).
#>
param(
    [Parameter(Mandatory)] [string]$InstallDir,
    [Parameter(Mandatory)] [string]$AdminUser,
    [Parameter(Mandatory)] [string]$AdminPassword,
    [string]$AdminEmail = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

$marker = Join-Path $InstallDir ".firstrun-done"
if (Test-Path $marker) {
    Write-Ok "İlk kurulum daha önce yapılmış (marker var) — atlanıyor."
    exit 0
}

Write-Step "Çekirdek veri tohumlanıyor (seed_initial_data)..."
Invoke-Manage $InstallDir "seed_initial_data"

Write-Step "SAIS verisi tohumlanıyor (seed_sais_data)..."
Invoke-Manage $InstallDir "seed_sais_data"

Write-Step "Admin kullanıcı oluşturuluyor ($AdminUser)..."
$envInline = "DJANGO_SUPERUSER_USERNAME='$AdminUser' DJANGO_SUPERUSER_EMAIL='$AdminEmail' DJANGO_SUPERUSER_PASSWORD='$AdminPassword'"
Invoke-Manage $InstallDir "seed_admin_user" $envInline

# WebSettings bootstrap — installer'dan gelen domain/TLS panele yazılır.
$bootstrapPath = Join-Path $InstallDir "web-bootstrap.json"
if (Test-Path $bootstrapPath) {
    Write-Step "WebSettings (domain + TLS) uygulanıyor..."
    $b = Get-Content -Raw $bootstrapPath | ConvertFrom-Json
    $py = @"
from api.models import WebSettings
from api import web_proxy
ws = WebSettings.load()
ws.enabled = True
ws.domain = '$($b.Domain)'
ws.tls_mode = '$($b.TlsMode)'
ws.letsencrypt_email = '$($b.LeEmail)'
ws.save()
ok, err = web_proxy.apply(ws)
print('WEBSETTINGS_OK' if ok else ('WEBSETTINGS_ERR ' + err))
"@
    $oneLine = ($py -replace "`r`n", "; " -replace "`n", "; ")
    Invoke-Compose $InstallDir "exec -T web python manage.py shell -c `"$oneLine`""
}

New-Item -ItemType File -Path $marker -Force | Out-Null
Write-Ok "İlk kurulum tamamlandı."
