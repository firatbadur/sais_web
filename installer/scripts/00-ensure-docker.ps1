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

# ALL wsl.exe probes below are TIME-BOXED via Invoke-NativeCapture (_common).
# Real field incident (Win11 26100): with the WSL platform not yet installed,
# EVERY wsl.exe invocation shows an interactive "Press any key to install
# Windows Subsystem for Linux" stub prompt. In the hidden service session
# nobody can press a key -> the first probe hung the install for hours with
# zero log output. A killed/timed-out probe simply counts as "not ready".

# Does a command actually run inside the distro? (filters out the WSL stub)
function Test-DistroUsable([string]$d) {
    $r = Invoke-NativeCapture "wsl.exe" "-d $d -- echo __WSLOK__" 60
    return ((-not $r.TimedOut) -and ($r.Output -match "__WSLOK__"))
}

# Is the distro REGISTERED at all? (wsl -l -q; strip the interleaved NULs that
# wsl.exe's UTF-16 output leaves when captured by Windows PowerShell)
function Test-DistroRegistered([string]$d) {
    $r = Invoke-NativeCapture "wsl.exe" "-l -q" 45
    if ($r.TimedOut) { return $false }
    $list = ($r.Output -split "[`r`n]+") | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    return (@($list) -contains $d)
}

# Dump WSL state into the install log so a failed site can be diagnosed from
# logs\install.log alone (no remote session needed).
function Write-WslDiag {
    Write-Host "   ---- wsl --status ----" -ForegroundColor DarkGray
    $r = Invoke-NativeCapture "wsl.exe" "--status" 30
    ($r.Output -split "[`r`n]+") | Where-Object { $_ } | ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray }
    Write-Host "   ---- wsl -l -v ----" -ForegroundColor DarkGray
    $r = Invoke-NativeCapture "wsl.exe" "-l -v" 30
    ($r.Output -split "[`r`n]+") | Where-Object { $_ } | ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray }
}

# Fallback when `wsl --update` cannot finish (interactive stub prompt, Store
# blocked, servicing stuck): download the official WSL MSI straight from the
# microsoft/WSL GitHub release and install it silently. Fully non-interactive,
# no Microsoft Store dependency, with live download progress.
function Install-WslFromMsi {
    Write-Step "Falling back to direct WSL MSI install (github.com/microsoft/WSL) ..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/microsoft/WSL/releases/latest" `
        -UseBasicParsing -Headers @{ "User-Agent" = "EnvisoftWebX-Installer" }
    $asset = $rel.assets | Where-Object { $_.name -match '\.x64\.msi$' } | Select-Object -First 1
    if (-not $asset) { throw "No x64 MSI asset found in the latest microsoft/WSL release." }
    $msi = Join-Path $env:TEMP $asset.name
    Invoke-DownloadSpin -Label "Downloading WSL package ($($asset.name))" `
        -Url $asset.browser_download_url -OutFile $msi -ExpectedBytes ([long]$asset.size)
    $r = Invoke-NativeSpin "Installing WSL package (msiexec /qn)" "msiexec.exe" "/i `"$msi`" /qn /norestart" -TimeoutSec 900
    # 3010 = success, reboot required (handled by the normal exit-10 path).
    if ($r.TimedOut -or ($r.ExitCode -ne 0 -and $r.ExitCode -ne 3010)) {
        throw "WSL MSI install failed (timeout=$($r.TimedOut) exit=$($r.ExitCode))."
    }
    Write-Ok "WSL platform installed from MSI."
}

# Register the distro WITHOUT the interactive OOBE.
#
# WHY (real field incident, Windows 10 19045): on Windows 10 the inbox wsl.exe's
# `wsl --install -d Ubuntu --no-launch` downloads/installs the Ubuntu Store
# package but NEVER registers the distro - on Win10 registration (rootfs
# extraction) only happens on the launcher's first run, which --no-launch
# deliberately skips. Result: the distro never exists, Test-DistroUsable never
# passes, and the installer reboot-loops forever ("keeps downloading WSL and
# asking to restart"). A reboot cannot fix this. On Windows 11 / Store WSL the
# install registers the distro directly, so the bug never showed there.
#
# Two OOBE-free registration paths:
#   (a) the Ubuntu appx launcher's headless mode: `ubuntu.exe install --root`
#       (registers with root as the only/default user - exactly what we want),
#   (b) rootfs import: download Ubuntu's official WSL rootfs tarball and
#       `wsl --import` it (no Microsoft Store dependency at all - also covers
#       Store-blocked corporate networks).
function Register-DistroNoOobe([string]$d) {
    # (a) Appx launcher (uses the package `wsl --install` already downloaded).
    $launcher = $null
    foreach ($name in @("ubuntu.exe", "ubuntu2404.exe", "ubuntu2204.exe")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { $launcher = $cmd.Source; break }
    }
    if ($launcher) {
        Write-Step "Registering $d headlessly via launcher: `"$launcher`" install --root"
        & $launcher install --root
        Write-Host "   launcher install exit=$LASTEXITCODE"
        if (Test-DistroUsable $d) { return }
    } else {
        Write-WarnLine "No Ubuntu appx launcher found (ubuntu.exe) - using rootfs import."
    }

    # (b) Rootfs import fallback.
    $work = Join-Path $env:LOCALAPPDATA "EnvisoftWebX"
    $vhdDir = Join-Path $work "wsl"
    New-Item -ItemType Directory -Force -Path $vhdDir | Out-Null
    $tar = Join-Path $work "ubuntu-jammy-rootfs.tar.gz"
    $url = "https://cloud-images.ubuntu.com/wsl/jammy/current/ubuntu-jammy-wsl-amd64-ubuntu22.04lts.rootfs.tar.gz"

    if (-not (Test-Path $tar) -or (Get-Item $tar).Length -lt 100MB) {
        Write-Step "Downloading Ubuntu rootfs (~450 MB) from cloud-images.ubuntu.com ..."
        Invoke-DownloadSpin -Label "Downloading Ubuntu rootfs" -Url $url -OutFile $tar
    }

    # A half-registered distro (launcher died mid-extraction) blocks --import
    # with "a distribution with the supplied name already exists" -> clear it.
    # Safe: we only reach this point during a fresh install of a broken distro.
    if (Test-DistroRegistered $d) {
        Write-WarnLine "Removing half-registered distro '$d' before import ..."
        wsl.exe --unregister $d *> $null
    }

    Write-Step "Importing $d from rootfs (wsl --import) ..."
    $imp = Invoke-NativeSpin "Importing $d rootfs" "wsl.exe" "--import $d `"$vhdDir`" `"$tar`" --version 2" -TimeoutSec 1800
    Write-Host "   wsl --import exit=$($imp.ExitCode)"
}

# Can the distro resolve a public host? (DNS sanity check, no hard fail)
function Test-WslDns([string]$d) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    try {
        wsl.exe -d $d -u root -- bash -lc "getent hosts get.docker.com >/dev/null 2>&1" | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $prev
    }
}

# WSL's auto-generated /etc/resolv.conf often points at a nameserver that cannot
# resolve public hosts on some networks -> Docker install + GHCR image pulls fail
# with "Could not resolve host: get.docker.com". If DNS is broken, stop WSL from
# regenerating resolv.conf and pin public resolvers (Google + Cloudflare). This
# mirrors the manual fix that unblocked the first site install.
function Repair-WslDns([string]$d) {
    if (Test-WslDns $d) { return }   # DNS already works -> leave the network alone

    Write-Step "WSL cannot resolve public hosts - applying DNS fix ..."
    # 1) Disable resolv.conf auto-generation (append once; keep existing sections).
    wsl.exe -d $d -u root -- bash -lc `
        "grep -q generateResolvConf /etc/wsl.conf 2>/dev/null || printf '\n[network]\ngenerateResolvConf = false\n' >> /etc/wsl.conf" *> $null
    # 2) Restart WSL so the setting takes effect before we write resolv.conf.
    wsl.exe --shutdown *> $null
    Start-Sleep -Seconds 3
    # 3) Pin working public DNS servers.
    wsl.exe -d $d -u root -- bash -lc `
        "rm -f /etc/resolv.conf; printf 'nameserver 8.8.8.8\nnameserver 1.1.1.1\n' > /etc/resolv.conf" *> $null

    if (Test-WslDns $d) {
        Write-Ok "WSL DNS fixed (pinned 8.8.8.8 / 1.1.1.1)."
    } else {
        Write-WarnLine "WSL still cannot resolve public hosts - check firewall/proxy/DNS on this host."
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

    # After a reboot-resume the network stack may not be up yet. wsl --update /
    # --install download from Microsoft + GitHub and fail with
    # WININET_E_NAME_NOT_RESOLVED if DNS isn't ready -> wait (up to ~60s) for
    # host name resolution before starting the download.
    Write-Step "Waiting for network (DNS) before WSL download ..."
    for ($n = 0; $n -lt 20; $n++) {
        try {
            Resolve-DnsName -Name "raw.githubusercontent.com" -ErrorAction Stop | Out-Null
            break
        } catch { Start-Sleep -Seconds 3 }
    }

    # Install WSL2 + distro. All wsl.exe calls run through Invoke-NativeSpin:
    # heartbeat progress lines (elapsed + live output tail) land in the install
    # log, and a hard timeout kills a hung call (interactive stub prompt)
    # instead of blocking forever. --no-launch skips the interactive Ubuntu
    # user setup (we use root).
    Write-Step "Updating the WSL platform (first install downloads a few hundred MB) ..."
    $upd = Invoke-NativeSpin "Updating WSL platform (wsl --update)" "wsl.exe" "--update" -TimeoutSec 1500
    if ($upd.TimedOut -or $upd.ExitCode -ne 0) {
        Write-WarnLine "wsl --update did not complete (timeout=$($upd.TimedOut) exit=$($upd.ExitCode))."
        Install-WslFromMsi
    }
    $null = Invoke-NativeCapture "wsl.exe" "--set-default-version 2" 60

    Write-Step "Installing the $Distro distro (downloads ~350-700 MB) ..."
    $inst = Invoke-NativeSpin "Installing $Distro (wsl --install --no-launch)" "wsl.exe" `
        "--install -d $Distro --no-launch" -TimeoutSec 2400
    if ($inst.TimedOut -or $inst.ExitCode -ne 0) {
        Write-WarnLine "wsl --install failed (network not ready?) - retrying once in 8s ..."
        Start-Sleep -Seconds 8
        $null = Invoke-NativeSpin "Updating WSL platform (retry)" "wsl.exe" "--update" -TimeoutSec 1500
        $null = Invoke-NativeSpin "Installing $Distro (retry)" "wsl.exe" `
            "--install -d $Distro --no-launch" -TimeoutSec 2400
    }
    Start-Sleep -Seconds 5

    if (-not (Test-DistroUsable $Distro)) {
        if (-not (Test-DistroRegistered $Distro)) {
            # Windows 10 path: the distro was downloaded but never registered
            # (--no-launch skips registration there). A reboot can NOT fix this;
            # register it ourselves (launcher --root, then rootfs import).
            Register-DistroNoOobe $Distro
        }
        if (-not (Test-DistroUsable $Distro)) {
            Write-WslDiag
            if (Test-DistroRegistered $Distro) {
                # Registered but not runnable -> kernel/VM platform genuinely
                # needs the reboot. install.ps1 re-arms the resume task and
                # guards against looping (max 3).
                Write-WarnLine "WSL2 installed - a REBOOT is required to activate it (will auto-resume)."
                exit 10
            }
            # Still no distro: rebooting again would just loop. Fail loudly with
            # the diagnostics above in logs\install.log.
            Write-Error ("Could not register the WSL distro '$Distro' (Store blocked? download failed?). " +
                "See the wsl --status / wsl -l -v output above and logs\install.log.")
            exit 1
        }
    }
}

Write-Ok "WSL2 + $Distro is usable."

# Make sure the distro can resolve public hosts BEFORE we curl get.docker.com /
# pull GHCR images (the #1 cause of fresh-install failures on real networks).
Repair-WslDns $Distro

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
