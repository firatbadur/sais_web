<#
.SYNOPSIS
    First run: seed data + admin user + WebSettings bootstrap.

.DESCRIPTION
    Idempotent - guarded by a marker file, skipped on re-runs.
    seed_initial_data + seed_sais_data + seed_admin_user (non-interactive).
    If web-bootstrap.json exists, fills WebSettings with the domain/TLS and
    renders the Caddyfile (panel config comes from the installer).

    NOTE: ASCII-only (English) on purpose (Windows PowerShell 5.1 encoding).
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
    Write-Ok "First run already completed (marker present) - skipping."
    exit 0
}

Write-Step "Seeding core data (seed_initial_data)..."
Invoke-Manage $InstallDir "seed_initial_data"

Write-Step "Seeding SAIS data (seed_sais_data)..."
Invoke-Manage $InstallDir "seed_sais_data"

Write-Step "Creating admin user ($AdminUser)..."
$envInline = "DJANGO_SUPERUSER_USERNAME='$AdminUser' DJANGO_SUPERUSER_EMAIL='$AdminEmail' DJANGO_SUPERUSER_PASSWORD='$AdminPassword'"
Invoke-Manage $InstallDir "seed_admin_user" $envInline

# WebSettings bootstrap - the installer's domain/TLS is written into the panel.
$bootstrapPath = Join-Path $InstallDir "web-bootstrap.json"
if (Test-Path $bootstrapPath) {
    Write-Step "Applying WebSettings (domain + TLS)..."
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
Write-Ok "First run completed."
