<#
.SYNOPSIS
    SAIS servisini ve container'ları kaldırır. Veriyi varsayılan KORUR.

.PARAMETER PurgeData
    Verilirse named volume'leri de siler (DB dahil — GERİ ALINAMAZ).
#>
param(
    [Parameter(Mandatory)] [string]$InstallDir,
    [string]$ServiceName = "SAISScada",
    [string]$Distro = "Ubuntu",
    [switch]$PurgeData
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "_common.ps1")
$script:WslDistro = $Distro

$nssm = Join-Path $InstallDir "nssm.exe"
if (Test-Path $nssm) {
    Write-Step "Servis durduruluyor + kaldırılıyor: $ServiceName"
    & $nssm stop $ServiceName *> $null
    & $nssm remove $ServiceName confirm *> $null
}

Write-Step "Container'lar durduruluyor..."
try {
    if ($PurgeData) {
        Write-WarnLine "PurgeData: tüm veriler (DB dahil) siliniyor!"
        Invoke-Compose $InstallDir "down -v"
    } else {
        Invoke-Compose $InstallDir "down"
        Write-Ok "Container'lar kaldırıldı; volume'ler (DB/redis/cert) KORUNDU."
    }
} catch {
    Write-WarnLine "compose down başarısız (yığın zaten kapalı olabilir)."
}

Write-Ok "Kaldırma tamamlandı."
