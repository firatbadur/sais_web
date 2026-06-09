<#
.SYNOPSIS
    SAIS kurulum orkestratörü — 00→40 adımlarını sırayla çalıştırır.

.DESCRIPTION
    Inno Setup sihirbazı cevapları bir JSON answers dosyasına yazar ve bu
    script'i -AnswersFile ile çağırır.

    ÖNEMLI: Her adım AYRI bir powershell.exe sürecinde çalıştırılır. PowerShell'de
    `&` ile çağrılan bir .ps1 içindeki `exit`, çağıran süreci de öldürür; bu yüzden
    adımları child süreç olarak çalıştırıp yalnızca çıkış kodunu (`$LASTEXITCODE`)
    okuyoruz. Böylece 00-ensure-docker'ın `exit 10` (reboot) sinyali install.ps1'i
    öldürmeden yakalanır.

.PARAMETER AnswersFile
    Tüm kurulum parametrelerini içeren JSON.

.PARAMETER Resume
    Reboot sonrası RunOnce tarafından verilir; Docker adımından devam eder.
#>
param(
    [Parameter(Mandatory)] [string]$AnswersFile,
    [switch]$Resume
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$psExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

if (-not (Test-Path $AnswersFile)) {
    throw "Answers dosyası bulunamadı: $AnswersFile"
}
$a = Get-Content -Raw $AnswersFile | ConvertFrom-Json
$InstallDir = $a.InstallDir
$Distro = if ($a.Distro) { $a.Distro } else { "Ubuntu" }
$transcript = Join-Path $InstallDir "logs\install.log"
New-Item -ItemType Directory -Force -Path (Split-Path $transcript) | Out-Null
Start-Transcript -Path $transcript -Append | Out-Null

# Bir adım script'ini AYRI süreçte çalıştır, çıkış kodunu döndür.
function Invoke-Step([string]$scriptName, [string[]]$stepArgs) {
    $script = Join-Path $here $scriptName
    $allArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $script) + $stepArgs
    & $psExe @allArgs
    return $LASTEXITCODE
}

try {
    Write-Host "==== SAIS Kurulum $([DateTime]::Now) (Resume=$Resume) ====" -ForegroundColor Magenta

    # 1) Docker önkoşulu (WSL2 + Docker CE)
    Write-Host ">> [1/5] Docker (WSL2 + CE) sağlanıyor..." -ForegroundColor Cyan
    $code = Invoke-Step "00-ensure-docker.ps1" @("-Distro", $Distro)

    if ($code -eq 10) {
        # Reboot gerekli → RunOnce ile devam et, yeniden başlat.
        $resumeCmd = "`"$psExe`" -NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $here 'install.ps1')`" -AnswersFile `"$AnswersFile`" -Resume"
        Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce" `
            -Name "SAISInstallResume" -Value $resumeCmd
        Write-Host "   [!] WSL2 etkinleştirildi — REBOOT gerekiyor. Yeniden başlatma sonrası kurulum otomatik devam edecek." -ForegroundColor Yellow
        Stop-Transcript | Out-Null
        Restart-Computer -Force
        return
    }
    elseif ($code -eq 11) {
        # WSL hiç kurulu değil → kullanıcı elle kurmalı (otomatik reboot-resume güvenilmez).
        Write-Host ""
        Write-Host "  ============================================================" -ForegroundColor Yellow
        Write-Host "  WSL2 bu makinede kurulu değil. Lütfen şunları yapın:" -ForegroundColor Yellow
        Write-Host "    1) Yönetici PowerShell:  wsl --install" -ForegroundColor Yellow
        Write-Host "    2) Makineyi YENİDEN BAŞLATIN" -ForegroundColor Yellow
        Write-Host "    3) Bu kurulumu (sais-setup .exe) tekrar çalıştırın" -ForegroundColor Yellow
        Write-Host "  ============================================================" -ForegroundColor Yellow
        throw "WSL2 önkoşulu eksik (exit 11). Yukarıdaki adımlardan sonra tekrar deneyin."
    }
    elseif ($code -ne 0) {
        throw "Docker önkoşulu başarısız (exit $code). Log: $transcript"
    }

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

    # Hassas answers dosyasını sil (token/şifreler içerir).
    Remove-Item -Force $AnswersFile -ErrorAction SilentlyContinue

    Write-Host "==== KURULUM TAMAMLANDI ====" -ForegroundColor Green
    Write-Host "Dashboard: https://$($a.Domain)/dashboard/" -ForegroundColor Green
}
finally {
    Stop-Transcript | Out-Null
}
