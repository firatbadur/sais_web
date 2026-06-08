<#
.SYNOPSIS
    NSSM Windows servisinin çalıştırdığı uzun-ömürlü süreç.

.DESCRIPTION
    Makine açılışında: WSL Docker daemon'ını başlatır (WSL'de systemd yoksa),
    ardından `docker compose up -d` ile yığını kaldırır ve canlı kalır
    (NSSM süreç ölmedikçe servisi "çalışıyor" görür). Servis durdurulunca
    yığını nazikçe durdurur.
#>
param(
    [Parameter(Mandatory)] [string]$InstallDir,
    [string]$Distro = "Ubuntu"
)

. (Join-Path $PSScriptRoot "_common.ps1")
$script:WslDistro = $Distro

try {
    Write-Step "WSL Docker daemon başlatılıyor..."
    wsl.exe -d $Distro -u root -- bash -lc "service docker start || (dockerd >/var/log/dockerd.log 2>&1 &) ; sleep 3" *> $null

    Write-Step "Yığın başlatılıyor (up -d)..."
    Invoke-Compose $InstallDir "up -d"
    Write-Ok "SAIS yığını çalışıyor. Servis canlı kalıyor."

    # NSSM süreci canlı tutsun diye blokla. Servis durdurulduğunda finally devreye girer.
    while ($true) { Start-Sleep -Seconds 3600 }
}
finally {
    Write-Step "Servis durduruluyor — yığın durduruluyor (compose stop)..."
    try { Invoke-Compose $InstallDir "stop" } catch { }
}
