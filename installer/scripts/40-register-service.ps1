<#
.SYNOPSIS
    SAIS yığınını açılışta başlatan Windows servisini (NSSM) kaydeder.

.DESCRIPTION
    NSSM, sais-stack.ps1'i (uzun-ömürlü) bir Windows servisi olarak sarar →
    makine açılışında otomatik kalkar, çökerse yeniden başlar. nssm.exe
    installer payload'undan gelir.
#>
param(
    [Parameter(Mandatory)] [string]$InstallDir,
    [string]$NssmPath = "",
    [string]$ServiceName = "SAISScada",
    [string]$Distro = "Ubuntu"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

if (-not $NssmPath) {
    $NssmPath = Join-Path $InstallDir "nssm.exe"
}
if (-not (Test-Path $NssmPath)) {
    Write-Error "nssm.exe bulunamadı: $NssmPath"
}

$stackScript = Join-Path $InstallDir "scripts\sais-stack.ps1"
$psExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$psArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$stackScript`" -InstallDir `"$InstallDir`" -Distro `"$Distro`""

# Var olan servisi temizle (idempotent yeniden kurulum).
& $NssmPath stop $ServiceName *> $null
& $NssmPath remove $ServiceName confirm *> $null

Write-Step "Windows servisi kaydediliyor: $ServiceName"
& $NssmPath install $ServiceName $psExe $psArgs
& $NssmPath set $ServiceName AppDirectory $InstallDir
& $NssmPath set $ServiceName DisplayName "SAIS SCADA"
& $NssmPath set $ServiceName Description "SAIS SCADA Docker yığını (web + worker + beat + db + redis + caddy)."
& $NssmPath set $ServiceName Start SERVICE_AUTO_START
& $NssmPath set $ServiceName AppStopMethodConsole 20000
& $NssmPath set $ServiceName AppExit Default Restart
& $NssmPath set $ServiceName AppRestartDelay 10000
& $NssmPath set $ServiceName AppStdout (Join-Path $InstallDir "logs\service.log")
& $NssmPath set $ServiceName AppStderr (Join-Path $InstallDir "logs\service.err.log")

New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "logs") | Out-Null

Write-Step "Servis başlatılıyor..."
& $NssmPath start $ServiceName

Write-Ok "Servis kuruldu ve başlatıldı: $ServiceName (açılışta otomatik)."
