<#
.SYNOPSIS
    Render env.template with the wizard answers into InstallDir\.env.

.DESCRIPTION
    Generates a strong random DJANGO_SECRET_KEY + (if not supplied) MSSQL
    password. Derives fleet-wide wildcard ALLOWED_HOSTS/CSRF from the domain.
    Admin credentials are NOT written to .env (passed to 30-firstrun instead).

    NOTE: ASCII-only (English) on purpose (Windows PowerShell 5.1 encoding).
#>
param(
    [Parameter(Mandatory)] [string]$InstallDir,
    [Parameter(Mandatory)] [string]$Domain,
    [string]$TlsMode = "letsencrypt",
    [string]$LeEmail = "",
    [string]$MssqlPassword = "",
    [string]$MssqlPid = "Standard",
    [string]$LicenseKey = "",
    [string]$LicenseUrl = "",
    [string]$GhcrImage = "ghcr.io/firatbadur/sais_web",
    [string]$ImageTag = "stable"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

function New-RandomSecret([int]$len = 50) {
    $chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#%^*-_=+".ToCharArray()
    $bytes = New-Object byte[] $len
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}

Write-Step "Generating .env ..."

$secret = New-RandomSecret 50
if (-not $MssqlPassword) { $MssqlPassword = (New-RandomSecret 24) + "Aa1!" }

# Derive a fleet-wide wildcard from the domain:
# sais-tesis1.envisoft.com.tr -> .envisoft.com.tr
$baseDomain = $Domain
$parts = $Domain.Split(".")
if ($parts.Count -ge 2) {
    $baseDomain = "." + ($parts[1..($parts.Count - 1)] -join ".")  # drop the first label
}
$allowedHosts = "localhost,127.0.0.1,web,$baseDomain"
$csrf = "https://*$baseDomain,https://$Domain"

$templatePath = Join-Path $PSScriptRoot "..\templates\env.template"
$content = Get-Content -Raw -Encoding UTF8 $templatePath

# Docker runs inside WSL and the installer runs `docker login` there as root, so
# the GHCR registry creds live at /root/.docker/config.json. Watchtower must
# mount THAT (a WSL path), NOT the Windows %USERPROFILE%\.docker - mounting a
# Windows path fails with "invalid volume specification".
$dockerConfigDir = "/root/.docker"

$map = @{
    "__GHCR_IMAGE__"          = $GhcrImage
    "__IMAGE_TAG__"           = $ImageTag
    "__DOCKER_CONFIG_DIR__"   = ($dockerConfigDir -replace '\\', '/')
    "__SECRET_KEY__"          = $secret
    "__ALLOWED_HOSTS__"       = $allowedHosts
    "__CSRF_TRUSTED_ORIGINS__"= $csrf
    "__MSSQL_PASSWORD__"      = $MssqlPassword
    "__MSSQL_PID__"           = $MssqlPid
    "__LICENSE_KEY__"         = $LicenseKey
    "__LICENSE_URL__"         = $LicenseUrl
}
foreach ($k in $map.Keys) {
    $content = $content.Replace($k, $map[$k])
}

$envPath = Join-Path $InstallDir ".env"
# Docker/compose .env wants LF; write UTF-8 (no BOM).
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$content = $content -replace "`r`n", "`n"
[System.IO.File]::WriteAllText($envPath, $content, $utf8NoBom)

Write-Ok ".env generated: $envPath"

# Also stash the domain/TLS info for the first-run WebSettings bootstrap.
$state = @{
    Domain  = $Domain
    TlsMode = $TlsMode
    LeEmail = $LeEmail
}
$state | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $InstallDir "web-bootstrap.json")
Write-Ok "web-bootstrap.json written (first-run will fill WebSettings)."
