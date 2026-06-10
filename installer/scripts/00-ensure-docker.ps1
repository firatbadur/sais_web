<#
.SYNOPSIS
    Ensure the WSL2 + Docker CE prerequisite (no Docker Desktop required).

.DESCRIPTION
    Strategy:
      - If Docker already runs inside the distro -> exit 0.
      - If WSL2 / the distro is not usable: enable WSL features and run
        `wsl --install -d Ubuntu`. If a reboot is required to activate it,
        exit 10 (install.ps1 re-arms RunOnce, reboots and resumes after login).
      - Once WSL + distro are usable, install Docker CE inside the distro -> exit 0.

    EXIT CODES: 0=ready, 10=reboot required (auto-resume).

    NOTE: ASCII-only (English) on purpose - Windows PowerShell 5.1 reads BOM-less
    scripts as ANSI and would corrupt non-ASCII characters during parsing.
#>
param(
    [string]$Distro = "Ubuntu"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")
$script:WslDistro = $Distro

# Administrator rights are required (feature enable + WSL install).
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "This step requires Administrator rights."
    exit 1
}

# Does a command actually run inside the distro? (filters out the WSL stub)
function Test-DistroUsable([string]$d) {
    try {
        $out = wsl.exe -d $d -- echo __WSLOK__ 2>$null
        return ($out -match "__WSLOK__")
    } catch {
        return $false
    }
}

if (Test-DockerReady) {
    Write-Ok "Docker is already available (running inside $Distro)."
    exit 0
}

if (-not (Test-DistroUsable $Distro)) {
    Write-Step "WSL2 not ready - checking Windows features..."

    $cpu = Get-CimInstance Win32_Processor
    if (-not $cpu.VirtualizationFirmwareEnabled -and -not (Get-CimInstance Win32_ComputerSystem).HypervisorPresent) {
        Write-WarnLine "Virtualization appears disabled in BIOS - WSL2 may not work (Intel VT-x / AMD-V)."
    }

    foreach ($feature in @("Microsoft-Windows-Subsystem-Linux", "VirtualMachinePlatform")) {
        $state = (Get-WindowsOptionalFeature -Online -FeatureName $feature).State
        if ($state -ne "Enabled") {
            Write-Step "Enabling $feature ..."
            Enable-WindowsOptionalFeature -Online -FeatureName $feature -NoRestart -All | Out-Null
        }
    }

    # Install WSL2 + distro (wsl --install also enables features). --no-launch
    # skips the interactive Ubuntu user setup (we use root). Output is NOT
    # suppressed so progress/diagnostics are visible.
    Write-Step "Installing WSL2 + $Distro (wsl --install) ..."
    wsl.exe --update; Write-Host "   wsl --update exit=$LASTEXITCODE"
    wsl.exe --set-default-version 2; Write-Host "   wsl --set-default-version exit=$LASTEXITCODE"
    wsl.exe --install -d $Distro --no-launch; Write-Host "   wsl --install exit=$LASTEXITCODE"
    Start-Sleep -Seconds 5

    if (-not (Test-DistroUsable $Distro)) {
        # Kernel/distro becomes active after a reboot -> install.ps1 re-arms
        # RunOnce, reboots and resumes automatically after login.
        Write-WarnLine "WSL2 installed - a REBOOT is required to activate it (will auto-resume)."
        exit 10
    }
}

Write-Ok "WSL2 + $Distro is usable."

$dockerInstall = @'
set -e
# Enable systemd so the docker service auto-starts on every distro boot
# (falls back to manual dockerd if systemd is unavailable).
if ! grep -q 'systemd=true' /etc/wsl.conf 2>/dev/null; then
  printf '[boot]\nsystemd=true\n' >> /etc/wsl.conf
fi
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sh /tmp/get-docker.sh
fi
# WSL needs iptables-legacy; Ubuntu's default nftables breaks dockerd network
# init ("failed to start daemon ... iptables") -> daemon never comes up.
update-alternatives --set iptables /usr/sbin/iptables-legacy 2>/dev/null || true
update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy 2>/dev/null || true
systemctl enable docker 2>/dev/null || true
echo "DOCKER_INSTALLED"
'@
Invoke-WslSpin "Installing Docker CE inside the distro (downloading from get.docker.com)" `
    $dockerInstall -User "root" -Distro $Distro

# Shut the distro down so the systemd=true setting takes effect on next launch.
Write-Step "Restarting WSL (so systemd takes effect)..."
wsl.exe --shutdown *> $null
Start-Sleep -Seconds 5

# Docker's service is socket-activated; on a cold systemd boot (slow disks on
# site) it can take a while to answer. Do the whole wait INSIDE one persistent
# WSL session: wait for systemd to finish booting, start docker.socket+service,
# then poll `docker info` until the daemon answers. Far more reliable than
# repeated external `wsl` calls racing a still-booting systemd.
$startDocker = @'
set +e
# 1) Wait for systemd to finish booting (running or degraded = usable).
for i in $(seq 1 45); do
  s=$(systemctl is-system-running 2>/dev/null)
  { [ "$s" = "running" ] || [ "$s" = "degraded" ]; } && break
  sleep 2
done
# 2) Start docker (socket-activated; fall back to SysV / raw dockerd).
systemctl reset-failed docker docker.socket 2>/dev/null
systemctl enable --now docker.socket 2>/dev/null
systemctl start docker 2>/dev/null || service docker start 2>/dev/null || (pgrep dockerd >/dev/null || (dockerd >/var/log/dockerd.log 2>&1 &))
# 3) Wait until the daemon actually answers (up to ~150s).
for i in $(seq 1 75); do
  docker info >/dev/null 2>&1 && { echo DOCKER_READY; exit 0; }
  sleep 2
done
echo DOCKER_NOT_READY
exit 1
'@
$ok = $true
try {
    Invoke-WslSpin "Starting Docker daemon (waiting for systemd + daemon)" $startDocker -User "root" -Distro $Distro
} catch {
    $ok = $false
}

if ($ok) {
    Write-Ok "WSL2 + Docker CE ready."
    exit 0
} else {
    Write-Error "Could not verify Docker. Distro log: wsl -d $Distro -- cat /var/log/dockerd.log"
    exit 1
}
