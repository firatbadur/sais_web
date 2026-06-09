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
        # WSL2 reboot gerektiriyor → RunOnce ile login sonrası OTOMATİK devam et.
        # Sonsuz döngü guard'ı: en fazla 3 reboot dene.
        $counterFile = Join-Path $InstallDir ".reboot-count"
        $count = 0
        if (Test-Path $counterFile) { $count = [int](Get-Content $counterFile -Raw) }
        if ($count -ge 3) {
            Write-Host "   [X] WSL2 birkaç reboot sonrası hâlâ hazır değil." -ForegroundColor Red
            Write-Host "       Sanallaştırma (BIOS VT-x/AMD-V) açık mı kontrol edin, sonra:" -ForegroundColor Yellow
            Write-Host "       'wsl --install' + reboot + installer'ı tekrar çalıştırın." -ForegroundColor Yellow
            Remove-Item $counterFile -ErrorAction SilentlyContinue
            throw "WSL2 hazırlanamadı ($count reboot denendi)."
        }
        ($count + 1) | Set-Content $counterFile

        $resumeCmd = "`"$psExe`" -NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $here 'install.ps1')`" -AnswersFile `"$AnswersFile`" -Resume"
        Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce" `
            -Name "SAISInstallResume" -Value $resumeCmd

        # Kullanıcıya onay sor (installer gizli çalıştığı için GUI penceresi).
        $msg = "SAIS kurulumu için WSL2 etkinleştirildi; devam etmek için Windows'un " +
               "yeniden başlatılması gerekiyor.`n`nŞimdi yeniden başlatılsın mı?`n`n" +
               "• Evet: makine yeniden başlar, tekrar giriş yaptığınızda kurulum OTOMATİK devam eder.`n" +
               "• Hayır: daha sonra makineyi elle yeniden başlatın; kurulum yine kaldığı yerden devam eder."
        $reboot = $true
        try {
            $wsh = New-Object -ComObject WScript.Shell
            # 4=YesNo, 32=Question, 256=ikinci buton (Hayır) varsayılan
            $ans = $wsh.Popup($msg, 0, "SAIS SCADA — Yeniden Başlatma", 4 + 32 + 256)
            $reboot = ($ans -eq 6)   # 6=Evet, 7=Hayır
        } catch {
            $reboot = $false   # GUI gösterilemezse otomatik reboot etme, güvenli taraf
        }

        Stop-Transcript | Out-Null
        if ($reboot) {
            Start-Sleep -Seconds 2
            Restart-Computer -Force
        }
        # Hayır → RunOnce kurulu kaldı; elle reboot'ta kurulum devam edecek.
        return
    }
    elseif ($code -ne 0) {
        throw "Docker önkoşulu başarısız (exit $code). Log: $transcript"
    }

    # Başarılı geçiş → reboot sayacını temizle.
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

    # Hassas answers dosyasını sil (token/şifreler içerir).
    Remove-Item -Force $AnswersFile -ErrorAction SilentlyContinue

    Write-Host "==== KURULUM TAMAMLANDI ====" -ForegroundColor Green
    Write-Host "Dashboard: https://$($a.Domain)/dashboard/" -ForegroundColor Green
}
finally {
    Stop-Transcript | Out-Null
}
