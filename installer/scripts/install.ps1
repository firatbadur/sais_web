<#
.SYNOPSIS
    SAIS kurulum orkestratörü — 00→40 adımlarını sırayla çalıştırır.

.DESCRIPTION
    Inno Setup sihirbazı cevapları bir JSON answers dosyasına yazar ve bu
    script'i -AnswersFile ile çağırır.

    GÜVENİLİRLİK: Loglama EN BAŞTA başlar (answers parse'ından önce) → erken
    hatalar bile loglanır. Tüm gövde try/catch ile sarılı; hata olursa ekrana
    yazılır ve pencere Enter'a kadar açık kalır (görünür konsolda çalıştığı için
    kullanıcı sebebi görür). Her adım AYRI powershell.exe sürecinde çalışır
    (alt-script'teki `exit` orkestratörü öldürmesin).

.PARAMETER AnswersFile
    Tüm kurulum parametrelerini içeren JSON.

.PARAMETER Resume
    Reboot sonrası RunOnce tarafından verilir; kurulum kaldığı yerden devam eder.
#>
param(
    [Parameter(Mandatory)] [string]$AnswersFile,
    [switch]$Resume
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$psExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

# --- Loglama EN BAŞTA: answers parse'ından önce, garanti var olan kurulum dizinine ---
$baseDir = Split-Path -Parent $AnswersFile          # = kurulum dizini (ör. C:\SAIS)
$logDir = Join-Path $baseDir "logs"
try { New-Item -ItemType Directory -Force -Path $logDir | Out-Null } catch {}
try { Start-Transcript -Path (Join-Path $logDir "install.log") -Append | Out-Null } catch {}

$rebooting = $false
$success = $false

# Bir adım script'ini AYRI süreçte çalıştır, çıkış kodunu döndür.
function Invoke-Step([string]$scriptName, [string[]]$stepArgs) {
    $script = Join-Path $here $scriptName
    $allArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $script) + $stepArgs
    & $psExe @allArgs
    return $LASTEXITCODE
}

try {
    Write-Host "==== SAIS Kurulum $([DateTime]::Now) (Resume=$Resume) ====" -ForegroundColor Magenta
    Write-Host "Answers: $AnswersFile" -ForegroundColor DarkGray

    if (-not (Test-Path $AnswersFile)) {
        throw "Answers dosyası bulunamadı: $AnswersFile"
    }
    $a = Get-Content -Raw $AnswersFile | ConvertFrom-Json
    $InstallDir = $a.InstallDir
    $Distro = if ($a.Distro) { $a.Distro } else { "Ubuntu" }
    Write-Host "InstallDir=$InstallDir  Distro=$Distro  Domain=$($a.Domain)" -ForegroundColor DarkGray

    # 1) Docker önkoşulu (WSL2 + Docker CE)
    Write-Host ">> [1/5] Docker (WSL2 + CE) sağlanıyor..." -ForegroundColor Cyan
    $code = Invoke-Step "00-ensure-docker.ps1" @("-Distro", $Distro)
    Write-Host "   00-ensure-docker exit=$code" -ForegroundColor DarkGray

    if ($code -eq 10) {
        # WSL2 reboot gerektiriyor → RunOnce ile login sonrası devam. Guard: max 3 reboot.
        $counterFile = Join-Path $InstallDir ".reboot-count"
        $count = 0
        if (Test-Path $counterFile) { $count = [int](Get-Content $counterFile -Raw) }
        if ($count -ge 3) {
            throw "WSL2 birkaç reboot sonrası hâlâ hazır değil ($count). Sanallaştırma (BIOS VT-x/AMD-V) açık mı kontrol edin."
        }
        ($count + 1) | Set-Content $counterFile

        $resumeCmd = "`"$psExe`" -NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $here 'install.ps1')`" -AnswersFile `"$AnswersFile`" -Resume"
        Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce" `
            -Name "SAISInstallResume" -Value $resumeCmd

        $msg = "SAIS kurulumu için WSL2 etkinleştirildi; devam etmek için Windows'un " +
               "yeniden başlatılması gerekiyor.`n`nŞimdi yeniden başlatılsın mı?`n`n" +
               "• Evet: makine yeniden başlar, tekrar giriş yaptığınızda kurulum OTOMATİK devam eder.`n" +
               "• Hayır: daha sonra makineyi elle yeniden başlatın; kurulum yine kaldığı yerden devam eder."
        $reboot = $true
        try {
            $wsh = New-Object -ComObject WScript.Shell
            $ans = $wsh.Popup($msg, 0, "SAIS SCADA — Yeniden Başlatma", 4 + 32 + 256)
            $reboot = ($ans -eq 6)
        } catch { $reboot = $false }

        if ($reboot) {
            $rebooting = $true
            Write-Host "   Yeniden başlatılıyor..." -ForegroundColor Yellow
            Start-Sleep -Seconds 2
            Restart-Computer -Force
        } else {
            Write-Host "   Yeniden başlatma ertelendi. Makineyi elle yeniden başlatınca kurulum devam edecek." -ForegroundColor Yellow
        }
        return
    }
    elseif ($code -ne 0) {
        throw "Docker önkoşulu başarısız (exit $code)."
    }

    Remove-Item (Join-Path $InstallDir ".reboot-count") -ErrorAction SilentlyContinue

    # 2) .env üret
    Write-Host ">> [2/5] Yapılandırma (.env) üretiliyor..." -ForegroundColor Cyan
    $code = Invoke-Step "10-configure.ps1" @(
        "-InstallDir", $InstallDir, "-Domain", $a.Domain, "-TlsMode", $a.TlsMode,
        "-LeEmail", $a.LeEmail, "-MssqlPassword", $a.MssqlPassword, "-MssqlPid", $a.MssqlPid,
        "-LicenseKey", $a.LicenseKey, "-LicenseUrl", $a.LicenseUrl,
        "-GhcrImage", $a.GhcrImage, "-ImageTag", $a.ImageTag)
    if ($code -ne 0) { throw "Yapılandırma başarısız (exit $code)." }

    # 3) Çek + başlat
    Write-Host ">> [3/5] Image çekme + başlatma..." -ForegroundColor Cyan
    $code = Invoke-Step "20-up.ps1" @(
        "-InstallDir", $InstallDir, "-GhcrUser", $a.GhcrUser, "-GhcrToken", $a.GhcrToken)
    if ($code -ne 0) { throw "Image çekme/başlatma başarısız (exit $code)." }

    # 4) İlk kurulum (seed + admin + WebSettings)
    Write-Host ">> [4/5] İlk kurulum (tohumlama + admin)..." -ForegroundColor Cyan
    $code = Invoke-Step "30-firstrun.ps1" @(
        "-InstallDir", $InstallDir, "-AdminUser", $a.AdminUser,
        "-AdminPassword", $a.AdminPassword, "-AdminEmail", $a.AdminEmail)
    if ($code -ne 0) { throw "İlk kurulum başarısız (exit $code)." }

    # 5) Windows servisi
    Write-Host ">> [5/5] Windows servisi kaydı..." -ForegroundColor Cyan
    $code = Invoke-Step "40-register-service.ps1" @("-InstallDir", $InstallDir, "-Distro", $Distro)
    if ($code -ne 0) { throw "Servis kaydı başarısız (exit $code)." }

    $success = $true
    Write-Host ""
    Write-Host "==== KURULUM TAMAMLANDI ====" -ForegroundColor Green
    Write-Host "Dashboard: https://$($a.Domain)/dashboard/" -ForegroundColor Green
}
catch {
    Write-Host ""
    Write-Host "================  KURULUM HATASI  ================" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray }
    Write-Host "Tam log: $(Join-Path $logDir 'install.log')" -ForegroundColor Yellow
}
finally {
    # Reboot sırasında answers gerekli (resume); başarıda token'ı sil.
    if ($success -and -not $rebooting) {
        Remove-Item -Force $AnswersFile -ErrorAction SilentlyContinue
    }
    try { Stop-Transcript | Out-Null } catch {}
    # Görünür konsolda çalışır; reboot olmayacaksa pencere kapanmasın ki kullanıcı sonucu okusun.
    if (-not $rebooting) {
        Write-Host ""
        Read-Host "Bu pencereyi kapatmak için Enter'a basın"
    }
}
