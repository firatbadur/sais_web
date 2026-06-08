<#
.SYNOPSIS
    SAIS kurulum orkestratörü — 00→40 adımlarını sırayla çalıştırır.

.DESCRIPTION
    Inno Setup sihirbazı cevapları bir JSON answers dosyasına yazar ve bu
    script'i -AnswersFile ile çağırır. Docker önkoşulu reboot gerektirirse
    RunOnce kaydı yazılır, makine yeniden başlatılır ve reboot sonrası bu
    script -Resume ile kaldığı yerden devam eder.

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
. (Join-Path $here "_common.ps1")

if (-not (Test-Path $AnswersFile)) {
    Write-Error "Answers dosyası bulunamadı: $AnswersFile"
}
$a = Get-Content -Raw $AnswersFile | ConvertFrom-Json
$InstallDir = $a.InstallDir
$Distro = if ($a.Distro) { $a.Distro } else { "Ubuntu" }
$transcript = Join-Path $InstallDir "logs\install.log"
New-Item -ItemType Directory -Force -Path (Split-Path $transcript) | Out-Null
Start-Transcript -Path $transcript -Append | Out-Null

try {
    Write-Host "==== SAIS Kurulum $([DateTime]::Now) (Resume=$Resume) ====" -ForegroundColor Magenta

    # 1) Docker önkoşulu
    Write-Step "[1/5] Docker (WSL2 + CE) sağlanıyor..."
    & (Join-Path $here "00-ensure-docker.ps1") -Distro $Distro
    $code = $LASTEXITCODE
    if ($code -eq 10) {
        # Reboot gerekli → RunOnce ile devam et, yeniden başlat.
        $resumeCmd = "`"$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe`" -NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $here 'install.ps1')`" -AnswersFile `"$AnswersFile`" -Resume"
        Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce" `
            -Name "SAISInstallResume" -Value $resumeCmd
        Write-WarnLine "Reboot gerekiyor. Yeniden başlatma sonrası kurulum otomatik devam edecek."
        Stop-Transcript | Out-Null
        Restart-Computer -Force
        exit 0
    } elseif ($code -ne 0) {
        throw "Docker önkoşulu başarısız (exit $code)."
    }

    # 2) .env üret
    Write-Step "[2/5] Yapılandırma (.env) üretiliyor..."
    & (Join-Path $here "10-configure.ps1") -InstallDir $InstallDir -Domain $a.Domain `
        -TlsMode $a.TlsMode -LeEmail $a.LeEmail -MssqlPassword $a.MssqlPassword `
        -MssqlPid $a.MssqlPid -LicenseKey $a.LicenseKey -LicenseUrl $a.LicenseUrl `
        -GhcrImage $a.GhcrImage -ImageTag $a.ImageTag

    # 3) Çek + başlat
    Write-Step "[3/5] Image çekme + başlatma..."
    & (Join-Path $here "20-up.ps1") -InstallDir $InstallDir -GhcrUser $a.GhcrUser -GhcrToken $a.GhcrToken

    # 4) İlk kurulum (seed + admin + WebSettings)
    Write-Step "[4/5] İlk kurulum (tohumlama + admin)..."
    & (Join-Path $here "30-firstrun.ps1") -InstallDir $InstallDir -AdminUser $a.AdminUser `
        -AdminPassword $a.AdminPassword -AdminEmail $a.AdminEmail

    # 5) Windows servisi
    Write-Step "[5/5] Windows servisi kaydı..."
    & (Join-Path $here "40-register-service.ps1") -InstallDir $InstallDir -Distro $Distro

    # Hassas answers dosyasını sil (token/şifreler içerir).
    Remove-Item -Force $AnswersFile -ErrorAction SilentlyContinue

    Write-Host "==== KURULUM TAMAMLANDI ====" -ForegroundColor Green
    Write-Host "Dashboard: https://$($a.Domain)/dashboard/" -ForegroundColor Green
}
finally {
    Stop-Transcript | Out-Null
}
