# Envisoft WebX - Windows Installer

A "next-next-next" install package. The Inno Setup wizard sets up WSL2 + Docker CE,
pulls the image from GHCR, starts the compose stack, seeds initial data and
registers a Windows service (NSSM) that auto-starts at boot.

## Architecture

```
EnvisoftWebX-Setup-vX.Y.Z.exe (Inno Setup)
   |  wizard: license - DB password - domain/TLS - admin
   v
install.ps1  (orchestrator; resumes via RunOnce if a reboot is needed)
   |- 00-ensure-docker.ps1   WSL2 + Docker CE (no Docker Desktop)
   |- 10-configure.ps1       env.template -> .env (+ generates secret/password)
   |- 20-up.ps1              GHCR login (embedded read-only token) -> pull -> up -d
   |- 30-firstrun.ps1        seed_initial/sais/admin + WebSettings bootstrap
   |- 40-register-service.ps1  NSSM "EnvisoftWebX" service (sais-stack.ps1)
```

All docker work runs on **Docker CE inside WSL2** (no Docker Desktop license).
Compose files + `.env` live on the Windows side under `C:\EnvisoftWebX`; WSL reaches
them via `/mnt/c/EnvisoftWebX`. Named volumes are managed by Docker inside WSL.

The installer is **English / ASCII-only** on purpose: Windows PowerShell 5.1 reads
BOM-less scripts as ANSI, so non-ASCII characters would corrupt parsing.

## Build

Prerequisites (on the build machine):
- [Inno Setup 6](https://jrsoftware.org/isdl.php) (`iscc` on PATH).
- `payload\nssm.exe` - download from [nssm.cc](https://nssm.cc/download) into `installer\payload\`.

```powershell
# GHCR_TOKEN is a read:packages PAT; not committed, injected at build time.
iscc /DGHCR_USER=firatbadur /DGHCR_TOKEN=<PAT> /DAPP_VERSION=v1.2.0 installer\sais_setup.iss
# Output: installer\dist\EnvisoftWebX-Setup-v1.2.0.exe
```

In CI this is done by the `windows-installer` job in GitHub Actions `release.yml`
(`GHCR_TOKEN` comes from the `INSTALLER_GHCR_TOKEN` repo secret) and attached to the Release.

## Prerequisites (on the target machine)

- Windows 10/11 x64, **Administrator** rights.
- **Virtualization enabled in BIOS** (Intel VT-x / AMD-V) - required for WSL2.
- Internet access (GHCR pull + WSL/Docker downloads).
- **Outside the installer, set up beforehand:** a DNS A record for the domain (to the
  public IP) + 80/443 forwarding on the modem/firewall (for Let's Encrypt and external access).

The first WSL2 install requires a **reboot**; the installer reboots (with confirmation)
and resumes automatically via RunOnce after you log back in.

## Uninstall

Control Panel -> Programs -> "Envisoft WebX" -> Uninstall. The service + containers are
removed; **DB/redis/cert volumes are kept**. To remove all data:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\EnvisoftWebX\scripts\uninstall.ps1" -InstallDir "C:\EnvisoftWebX" -PurgeData
```

## Test (on a real Windows machine)

This package cannot be verified in the dev environment; test it manually on a clean
Windows VM/PC (with virtualization enabled):
1. Run `EnvisoftWebX-Setup.exe` -> fill in the wizard.
2. (If needed) reboot -> the install resumes automatically.
3. `https://<domain>/dashboard/` -> sign in with the admin account.
4. Reboot the machine -> the `EnvisoftWebX` service brings the stack up automatically.
5. Logs: `C:\EnvisoftWebX\logs\install.log`, `C:\EnvisoftWebX\logs\service.log`.
