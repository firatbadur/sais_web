# SAIS SCADA — Windows Installer

"next-next-next" kurulum paketi. Inno Setup sihirbazı WSL2 + Docker CE'yi kurar,
GHCR'dan image çeker, compose yığınını başlatır, ilk veriyi tohumlar ve açılışta
otomatik kalkan bir Windows servisi (NSSM) kaydeder.

## Mimari

```
sais-setup-vX.Y.Z.exe (Inno Setup)
   │  sihirbaz: lisans · DB şifresi · domain/TLS · admin
   ▼
install.ps1  (orkestratör; reboot gerekirse RunOnce ile devam)
   ├─ 00-ensure-docker.ps1   WSL2 + Docker CE (Docker Desktop YOK)
   ├─ 10-configure.ps1       env.template → .env (+ secret/şifre üret)
   ├─ 20-up.ps1              GHCR login (gömülü read-only token) → pull → up -d
   ├─ 30-firstrun.ps1        seed_initial/sais/admin + WebSettings bootstrap
   └─ 40-register-service.ps1  NSSM "SAISScada" servisi (sais-stack.ps1)
```

Tüm Docker işlemleri **WSL2 içindeki Docker CE** üzerinde çalışır (Docker Desktop
lisansı gerekmez). Compose dosyaları + `.env` Windows tarafında `C:\SAIS`'te durur;
WSL bunlara `/mnt/c/SAIS` üzerinden erişir. Named volume'ler Docker tarafından WSL
içinde yönetilir.

## Derleme

Önkoşullar (derleyen makinede):
- [Inno Setup 6](https://jrsoftware.org/isdl.php) (`iscc` PATH'te).
- `payload\nssm.exe` — [nssm.cc](https://nssm.cc/download)'den indir, `installer\payload\` içine koy.

```powershell
# GHCR_TOKEN bir read:packages scope'lu PAT; repo'ya commitlenmez, build'de enjekte edilir.
iscc /DGHCR_USER=firatbadur /DGHCR_TOKEN=<PAT> /DAPP_VERSION=v1.2.0 installer\sais_setup.iss
# Çıktı: installer\dist\sais-setup-v1.2.0.exe
```

CI'da bu, GitHub Actions `release.yml` içindeki `windows-installer` job'ı tarafından
otomatik yapılır (`GHCR_TOKEN` repo secret'ından gelir) ve Release'e eklenir.

## Önkoşullar (kurulacak makinede)

- Windows 10/11 x64, **yönetici** hakları.
- BIOS'ta sanallaştırma (Intel VT-x / AMD-V) açık — WSL2 için.
- İnternet erişimi (GHCR pull + WSL/Docker indirme).
- **Panel dışı, önceden yapılmalı:** domain için DNS A kaydı (public IP'ye) +
  modem/firewall'da 80/443 yönlendirmesi (Let's Encrypt ve dış erişim için).

İlk WSL2 kurulumu **reboot** gerektirebilir; installer otomatik yeniden başlatıp
RunOnce ile kaldığı yerden devam eder.

## Kaldırma

Denetim Masası → Programlar → "SAIS SCADA" → Kaldır. Servis + container'lar
kaldırılır; **DB/redis/cert volume'leri korunur**. Tüm veriyi silmek için:

```powershell
powershell -ExecutionPolicy Bypass -File "C:\SAIS\scripts\uninstall.ps1" -InstallDir "C:\SAIS" -PurgeData
```

## Test (gerçek Windows makinesinde)

Bu paket dev ortamında doğrulanamaz; temiz bir Windows VM'de manuel test edilir:
1. `sais-setup.exe` çalıştır → sihirbazı doldur.
2. (Gerekirse reboot) → kurulum otomatik devam eder.
3. `https://<domain>/dashboard/` → admin ile giriş.
4. Makineyi reboot et → `SAISScada` servisi yığını otomatik kaldırır.
5. Loglar: `C:\SAIS\logs\install.log`, `C:\SAIS\logs\service.log`.
