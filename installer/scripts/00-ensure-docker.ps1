<#
.SYNOPSIS
    WSL2 + Docker CE önkoşulunu sağlar (Docker Desktop GEREKMEZ).

.DESCRIPTION
    1. WSL2 özelliklerini etkinleştirir (VirtualMachinePlatform + WSL).
    2. Ubuntu distro'sunu kurar (yoksa).
    3. Distro içine Docker CE kurar (get.docker.com), servisi başlatır.

    Sanallaştırma (BIOS/Hyper-V) kapalıysa veya WSL ilk kez kuruluyorsa
    REBOOT gerekebilir. Bu durumda script 10 (RebootRequired) exit code'u ile
    döner; installer reboot sonrası RunOnce ile devam eder.

.PARAMETER Distro
    WSL distro adı (varsayılan Ubuntu).
#>
param(
    [string]$Distro = "Ubuntu"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")
$script:WslDistro = $Distro

# Yönetici hakları şart (özellik etkinleştirme + WSL kurulumu).
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "Bu adım yönetici hakları gerektirir."
}

if (Test-DockerReady) {
    Write-Ok "Docker zaten hazır ($Distro içinde çalışıyor)."
    exit 0
}

Write-Step "Sanallaştırma kontrol ediliyor..."
$cpu = Get-CimInstance Win32_Processor
if (-not $cpu.VirtualizationFirmwareEnabled -and -not (Get-CimInstance Win32_ComputerSystem).HypervisorPresent) {
    Write-WarnLine "Sanallaştırma BIOS'ta kapalı görünüyor. WSL2 çalışmayabilir."
    Write-WarnLine "BIOS'ta Intel VT-x / AMD-V (SVM) açıp tekrar deneyin."
}

Write-Step "WSL2 özellikleri etkinleştiriliyor..."
$rebootNeeded = $false
foreach ($feature in @("Microsoft-Windows-Subsystem-Linux", "VirtualMachinePlatform")) {
    $state = (Get-WindowsOptionalFeature -Online -FeatureName $feature).State
    if ($state -ne "Enabled") {
        $r = Enable-WindowsOptionalFeature -Online -FeatureName $feature -NoRestart -All
        if ($r.RestartNeeded) { $rebootNeeded = $true }
    }
}

if ($rebootNeeded) {
    Write-WarnLine "WSL2 özellikleri etkinleştirildi — REBOOT gerekiyor."
    exit 10   # installer bunu yakalar ve reboot sonrası devam eder (RunOnce)
}

Write-Step "WSL2 varsayılan sürüm 2 yapılıyor..."
wsl.exe --set-default-version 2 *> $null

# Distro kurulu mu?
$distros = (wsl.exe --list --quiet) -replace "`0", ""
if ($distros -notmatch [regex]::Escape($Distro)) {
    Write-Step "$Distro kuruluyor (wsl --install)..."
    wsl.exe --install -d $Distro --no-launch
    if ($LASTEXITCODE -ne 0) {
        Write-WarnLine "wsl --install başarısız olabilir; reboot sonrası tekrar denenecek."
        exit 10
    }
    # İlk başlatma + root kullanıcı initialize
    Start-Sleep -Seconds 5
}

Write-Step "Docker CE distro içine kuruluyor..."
$dockerInstall = @'
set -e
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sh /tmp/get-docker.sh
fi
# WSL'de systemd yoksa docker daemon'ı service ile başlat
sudo service docker start || (sudo dockerd >/var/log/dockerd.log 2>&1 &)
# root olmayan kullanıcıyı docker grubuna ekle (varsa)
if id -nG "$USER" | grep -qvw docker; then sudo usermod -aG docker "$USER" || true; fi
sleep 3
docker info >/dev/null 2>&1 && echo "DOCKER_OK"
'@
wsl.exe -d $Distro -u root -- bash -lc "$dockerInstall"

if (Test-DockerReady) {
    Write-Ok "WSL2 + Docker CE hazır."
    exit 0
} else {
    Write-Error "Docker kurulumu doğrulanamadı. Distro loglarını kontrol edin: wsl -d $Distro -- cat /var/log/dockerd.log"
}
