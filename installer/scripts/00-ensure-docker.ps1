<#
.SYNOPSIS
    WSL2 + Docker CE önkoşulunu sağlar (Docker Desktop GEREKMEZ).

.DESCRIPTION
    Strateji:
      - Docker zaten distro içinde çalışıyorsa → exit 0.
      - WSL2 çekirdeği/distrosu kullanılabilir değilse: WSL özelliklerini etkinleştir.
        Reboot gerekiyorsa → exit 10 (install.ps1 RunOnce ile reboot+devam eder).
        Hâlâ kullanılamıyorsa → exit 11 (kullanıcı elle `wsl --install` + reboot yapmalı;
        OS-seviyesi WSL bootstrap'ı reboot gerektirdiği için bunu otomatik zorlamıyoruz).
      - WSL+distro hazırsa Docker CE'yi distro içine kurar → exit 0.

    ÇIKIŞ KODLARI: 0=hazır, 10=reboot gerekli (oto-devam), 11=WSL elle kurulmalı.
#>
param(
    [string]$Distro = "Ubuntu"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")
$script:WslDistro = $Distro

# Yönetici hakları şart.
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "Bu adım yönetici hakları gerektirir."
    exit 1
}

# Distro içinde komut çalışıp gerçekten yanıt veriyor mu? (stub/eksik WSL'i eler)
function Test-DistroUsable([string]$d) {
    try {
        $out = wsl.exe -d $d -- echo __WSLOK__ 2>$null
        return ($out -match "__WSLOK__")
    } catch {
        return $false
    }
}

if (Test-DockerReady) {
    Write-Ok "Docker zaten hazır ($Distro içinde çalışıyor)."
    exit 0
}

if (-not (Test-DistroUsable $Distro)) {
    Write-Step "WSL2 hazır değil — özellikler kontrol ediliyor..."

    $cpu = Get-CimInstance Win32_Processor
    if (-not $cpu.VirtualizationFirmwareEnabled -and -not (Get-CimInstance Win32_ComputerSystem).HypervisorPresent) {
        Write-WarnLine "Sanallaştırma BIOS'ta kapalı görünüyor — WSL2 çalışmayabilir (Intel VT-x / AMD-V)."
    }

    $rebootNeeded = $false
    foreach ($feature in @("Microsoft-Windows-Subsystem-Linux", "VirtualMachinePlatform")) {
        $state = (Get-WindowsOptionalFeature -Online -FeatureName $feature).State
        if ($state -ne "Enabled") {
            Write-Step "$feature etkinleştiriliyor..."
            $r = Enable-WindowsOptionalFeature -Online -FeatureName $feature -NoRestart -All
            if ($r.RestartNeeded) { $rebootNeeded = $true }
        }
    }
    # WSL2 + distro'yu kur (wsl --install özellikleri de açar). --no-launch ile
    # interaktif Ubuntu kullanıcı kurulumunu atlıyoruz (root olarak kullanacağız).
    # Çıktılar GİZLENMİYOR — teşhis için görünür olmalı.
    Write-Step "WSL2 + $Distro kuruluyor (wsl --install)..."
    wsl.exe --update; Write-Host "   wsl --update exit=$LASTEXITCODE"
    wsl.exe --set-default-version 2; Write-Host "   wsl --set-default-version exit=$LASTEXITCODE"
    wsl.exe --install -d $Distro --no-launch; Write-Host "   wsl --install exit=$LASTEXITCODE"
    Start-Sleep -Seconds 5

    if (-not (Test-DistroUsable $Distro)) {
        # Çekirdek/distro reboot sonrası aktif olur → install.ps1 RunOnce ile
        # yeniden başlatıp login sonrası OTOMATİK devam eder.
        Write-WarnLine "WSL2 kuruldu — etkinleşmesi için REBOOT gerekiyor (otomatik devam edilecek)."
        exit 10
    }
}

Write-Ok "WSL2 + $Distro kullanılabilir."

Write-Step "Docker CE distro içine kuruluyor (systemd ile kalıcı servis)..."
$dockerInstall = @'
set -e
# systemd'yi etkinleştir → docker servisi distro her açıldığında otomatik kalkar
# (systemd yoksa manuel dockerd fallback'ine düşülür).
if ! grep -q 'systemd=true' /etc/wsl.conf 2>/dev/null; then
  printf '[boot]\nsystemd=true\n' >> /etc/wsl.conf
fi
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sh /tmp/get-docker.sh
fi
systemctl enable docker 2>/dev/null || true
echo "DOCKER_INSTALLED"
'@
wsl.exe -d $Distro -u root -- bash -lc "$dockerInstall"

# systemd'nin devreye girmesi için distro'yu kapat; sonraki launch systemd+docker başlatır.
Write-Step "WSL yeniden başlatılıyor (systemd devreye girsin)..."
wsl.exe --shutdown *> $null
Start-Sleep -Seconds 4

# docker hazır olana dek birkaç kez dene (systemd boot + docker.service start)
$ok = $false
for ($i = 0; $i -lt 12; $i++) {
    wsl.exe -d $Distro -u root -- bash -lc "service docker start 2>/dev/null || systemctl start docker 2>/dev/null || (pgrep dockerd >/dev/null || (dockerd >/var/log/dockerd.log 2>&1 &)); sleep 2; docker info >/dev/null 2>&1" *> $null
    if (Test-DockerReady) { $ok = $true; break }
    Start-Sleep -Seconds 3
}

if ($ok) {
    Write-Ok "WSL2 + Docker CE hazır."
    exit 0
} else {
    Write-Error "Docker kurulumu doğrulanamadı. Distro logu: wsl -d $Distro -- cat /var/log/dockerd.log"
    exit 1
}
