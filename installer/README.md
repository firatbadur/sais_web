# Envisoft WebX - Windows Installer

A "next-next-next" install package. The Inno Setup wizard sets up WSL2 + Docker CE,
pulls the image from GHCR, starts the compose stack, seeds initial data and
registers a logon task that auto-starts the stack at boot.

## Architecture

The install runs in **two phases** around a dedicated **`EnvisoftWebX` service
account**. The WSL2 distro is registered per-user, so it must be created under the
account the box auto-logs-into at boot - not under the human running the installer.
Auto-login stores a static password in the registry; if a human's password ever
changed it would break. A dedicated account nobody signs into keeps a password the
installer owns, so auto-login never drifts.

```
EnvisoftWebX-Setup-vX.Y.Z.exe (Inno Setup)
   |  wizard: license - DB password - domain/TLS - dashboard admin
   |  (no Windows account is asked - it is created automatically)
   v
install.ps1  (orchestrator)
   |
   |== Phase 1 (installing admin) =====================================
   |- 05-service-account.ps1  create 'EnvisoftWebX' local admin + generated
   |                          password, enable WSL features, arm auto-login
   |- (register EnvisoftWebX-Install logon task, RunLevel Highest) -> REBOOT
   |
   |== Phase 2 (auto-logged-in EnvisoftWebX session, via -Resume) ======
   |- 00-ensure-docker.ps1   WSL2 + Docker CE under this account (no Docker Desktop)
   |- 10-configure.ps1       env.template -> .env (+ generates secret/password)
   |- 20-up.ps1              GHCR login (embedded read-only token) -> pull -> up -d
   |- 30-firstrun.ps1        seed_initial/sais/admin + WebSettings bootstrap
   |- 40-register-service.ps1  logon task "EnvisoftWebX" -> sais-stack.ps1
   `- remove the one-shot install task + answers file
```

### Auto-login password protection (two layers)

The service account must be a local admin (logon task RunLevel Highest, netsh
portproxy, WSL/Docker). So one flag is not enough:

- **Layer 1** - `05-service-account.ps1` sets *user cannot change password*
  (`net user /passwordchg:no`) + *password never expires*. Blocks the casual
  Settings / Ctrl-Alt-Del change.
- **Layer 2** - `sais-stack.ps1` keepalive re-asserts the real account password to
  match the registry `DefaultPassword` every loop, reverting any forced change.
  So even a deliberate admin reset is undone and auto-login never breaks.

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

The install reboots once (with confirmation) at the end of Phase 1; the machine then
auto-logs-into the `EnvisoftWebX` service account and the install **resumes
automatically** (via the `EnvisoftWebX-Install` logon task). A first WSL2 kernel
activation may trigger one more reboot - also resumed automatically.

> **Kiosk note:** after install the machine auto-logs-into the `EnvisoftWebX`
> service account at every boot (not the installer's desktop). This is the intended
> appliance pattern for an unattended SCADA cabinet.

## Uninstall

Control Panel -> Programs -> "Envisoft WebX" -> Uninstall. The logon tasks, auto-login,
the `EnvisoftWebX` service account and the containers are removed; **DB/redis/cert
volumes are kept**. To remove all data:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\EnvisoftWebX\scripts\uninstall.ps1" -InstallDir "C:\EnvisoftWebX" -PurgeData
```

## Test (on a real Windows machine)

This package cannot be verified in the dev environment; test it manually on a clean
Windows VM/PC (with virtualization enabled):
1. Run `EnvisoftWebX-Setup.exe` -> fill in the wizard (no Windows account is asked).
2. Phase 1 reboots -> the box auto-logs-into `EnvisoftWebX` and Phase 2 resumes on its
   own (a WSL kernel reboot may resume once more).
3. `http://localhost/dashboard/` (or `https://<domain>/dashboard/`) -> sign in with the
   dashboard admin account.
4. Reboot the machine -> the `EnvisoftWebX` logon task brings the stack up automatically
   with no operator sign-in.
5. **Password drift test:** force `net user EnvisoftWebX <new>` -> within a keepalive
   loop it is reverted to the stored value; reboot still auto-logs-in.
6. Logs: `C:\EnvisoftWebX\logs\install.log`, `C:\EnvisoftWebX\logs\service.log`.
