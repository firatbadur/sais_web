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

Assert-Docker

# The web container runs `ensure_database && migrate && ...` on startup; that can
# take a couple of minutes (SQL Server cold start + migrations). Seeding before
# it finishes hits a still-initializing/crash-looping container -> exec is killed
# (exit 137) or tables don't exist yet. Wait until `migrate --check` reports all
# migrations applied (redirect INSIDE bash so no NativeCommandError under Stop).
$wslDir = ConvertTo-WslPath $InstallDir
$checkCmd = "cd '$wslDir' && docker compose --env-file .env -f $script:ComposeFile exec -T web python manage.py migrate --check >/dev/null 2>&1"
$ready = Wait-WithSpin "Waiting for web to finish migrations" -TimeoutSec 480 -CheckEverySec 5 -Check {
    wsl.exe -d $script:WslDistro -- bash -lc $checkCmd
    return ($LASTEXITCODE -eq 0)
}
if (-not $ready) {
    throw "Web container did not finish migrations in time. Check: docker compose logs web"
}

# These exec into the web container; a freshly started container occasionally
# fails the FIRST docker/runc exec ("write init-p: broken pipe", exit 128).
# All three commands are idempotent (get_or_create / update_or_create), so a
# few retries make the step robust against that transient failure.
Invoke-WslSpin "Seeding core data (seed_initial_data)" `
    (Get-ComposeBash $InstallDir "exec -T web python manage.py seed_initial_data") -Retries 3

Invoke-WslSpin "Seeding SAIS data (seed_sais_data)" `
    (Get-ComposeBash $InstallDir "exec -T web python manage.py seed_sais_data") -Retries 3

$envInline = "DJANGO_SUPERUSER_USERNAME='$AdminUser' DJANGO_SUPERUSER_EMAIL='$AdminEmail' DJANGO_SUPERUSER_PASSWORD='$AdminPassword'"
Invoke-WslSpin "Creating admin user ($AdminUser)" `
    (Get-ComposeBash $InstallDir "exec -T web env $envInline python manage.py seed_admin_user") -Retries 3

# WebSettings bootstrap - the installer's domain/TLS is written into the panel.
$bootstrapPath = Join-Path $InstallDir "web-bootstrap.json"
if (Test-Path $bootstrapPath) {
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
    # Use Invoke-WslSpin (base64 transport), NOT Invoke-Compose. The python here
    # is passed to `shell -c "..."` and already contains quotes + parentheses;
    # routing it through bash -lc "...shell -c "..."..." double-nests the quotes
    # and bash fails ("syntax error near unexpected token `('"). Base64 avoids it.
    Invoke-WslSpin "Applying WebSettings (domain + TLS)" `
        (Get-ComposeBash $InstallDir "exec -T web python manage.py shell -c `"$oneLine`"") -Retries 2
}

New-Item -ItemType File -Path $marker -Force | Out-Null
Write-Ok "First run completed."
