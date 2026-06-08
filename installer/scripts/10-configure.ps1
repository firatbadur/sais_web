<#
.SYNOPSIS
    env.template'i sihirbaz cevaplarıyla doldurup InstallDir\.env üretir.

.DESCRIPTION
    Güçlü rastgele DJANGO_SECRET_KEY + (verilmediyse) MSSQL şifresi üretir.
    ALLOWED_HOSTS/CSRF için domain'den fleet-wide wildcard türetir.
    Admin kimlik bilgileri .env'e YAZILMAZ (30-firstrun'a parametre geçilir).
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

Write-Step ".env üretiliyor..."

$secret = New-RandomSecret 50
if (-not $MssqlPassword) { $MssqlPassword = (New-RandomSecret 24) + "Aa1!" }

# Domain'den fleet-wide wildcard türet: sais-tesis1.envisoft.com.tr -> .envisoft.com.tr
$baseDomain = $Domain
$parts = $Domain.Split(".")
if ($parts.Count -ge 2) {
    $baseDomain = "." + ($parts[-($parts.Count - 1)..-1] -join ".")  # son iki+ etiket
    $baseDomain = "." + ($parts[1..($parts.Count - 1)] -join ".")     # ilk etiketi at
}
$allowedHosts = "localhost,127.0.0.1,web,$baseDomain"
$csrf = "https://*$baseDomain,https://$Domain"

$templatePath = Join-Path $PSScriptRoot "..\templates\env.template"
$content = Get-Content -Raw -Encoding UTF8 $templatePath

$dockerConfigDir = Join-Path $env:USERPROFILE ".docker"

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
# Docker/compose .env LF ister; UTF8 (BOM'suz) yaz.
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$content = $content -replace "`r`n", "`n"
[System.IO.File]::WriteAllText($envPath, $content, $utf8NoBom)

Write-Ok ".env üretildi: $envPath"

# WebSettings'in ilk apply'ı için domain/TLS bilgisini de döndür (firstrun kullanır).
$state = @{
    Domain  = $Domain
    TlsMode = $TlsMode
    LeEmail = $LeEmail
}
$state | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $InstallDir "web-bootstrap.json")
Write-Ok "web-bootstrap.json yazıldı (firstrun WebSettings'i dolduracak)."
