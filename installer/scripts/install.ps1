<#
.SYNOPSIS
    Envisoft WebX install orchestrator - runs steps 00..40 in order.

.DESCRIPTION
    The Inno Setup wizard writes the answers to a JSON file and calls this script
    with -AnswersFile.

    RELIABILITY: logging starts FIRST (before parsing answers) so even early
    errors are logged. The whole body is wrapped in try/catch; on error it is
    printed and the window stays open until Enter (it runs in a visible console).
    Each step runs in a SEPARATE powershell.exe process (so an `exit` in a
    sub-script does not kill the orchestrator).

    NOTE: ASCII-only (English) on purpose - Windows PowerShell 5.1 reads BOM-less
    scripts as ANSI and would corrupt non-ASCII characters during parsing.

.PARAMETER AnswersFile
    JSON file with all install parameters.

.PARAMETER Resume
    Passed by RunOnce after a reboot; the install continues where it left off.
#>
param(
    [Parameter(Mandatory)] [string]$AnswersFile,
    [switch]$Resume
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$psExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

# --- Start logging FIRST: before parsing answers, into the install dir ---
$baseDir = Split-Path -Parent $AnswersFile          # = install dir (e.g. C:\EnvisoftWebX)
$logDir = Join-Path $baseDir "logs"
try { New-Item -ItemType Directory -Force -Path $logDir | Out-Null } catch {}
try { Start-Transcript -Path (Join-Path $logDir "install.log") -Append | Out-Null } catch {}

$rebooting = $false
$success = $false

# Run a step script in a SEPARATE process and return its exit code.
# NOTE: pipe the child's output to Out-Host. Without it, the child's stdout
# (every Write-Host line) is returned alongside the exit code, so the caller's
# $code becomes an array like @("   [OK] ...", 0) and `$code -ne 0` wrongly
# reports failure even when the step succeeded with exit 0.
function Invoke-Step([string]$scriptName, [string[]]$stepArgs) {
    $script = Join-Path $here $scriptName
    $allArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $script) + $stepArgs
    & $psExe @allArgs | Out-Host
    return $LASTEXITCODE
}

# Build a -Name Value argument array, OMITTING any param whose value is empty/
# null. Windows PowerShell drops empty-string arguments when invoking a native
# exe (& powershell.exe -File ... -LeEmail '' ...), which leaves the parameter
# without a value -> "Missing an argument for parameter". The step scripts give
# these optional params sane defaults, so omitting an empty one is correct.
function Build-Args([System.Collections.Specialized.OrderedDictionary]$params) {
    $a = @()
    foreach ($k in $params.Keys) {
        $v = $params[$k]
        if ($null -ne $v -and "$v" -ne "") { $a += @("-$k", "$v") }
    }
    return ,$a
}

try {
    Write-Host "==== Envisoft WebX Setup $([DateTime]::Now) (Resume=$Resume) ====" -ForegroundColor Magenta
    Write-Host "Answers: $AnswersFile" -ForegroundColor DarkGray

    if (-not (Test-Path $AnswersFile)) {
        throw "Answers file not found: $AnswersFile"
    }
    $a = Get-Content -Raw $AnswersFile | ConvertFrom-Json
    $InstallDir = $a.InstallDir
    $Distro = if ($a.Distro) { $a.Distro } else { "Ubuntu" }
    Write-Host "InstallDir=$InstallDir  Distro=$Distro  Domain=$($a.Domain)" -ForegroundColor DarkGray

    # 1) Docker prerequisite (WSL2 + Docker CE)
    Write-Host ">> [1/5] Ensuring Docker (WSL2 + CE)..." -ForegroundColor Cyan
    $code = Invoke-Step "00-ensure-docker.ps1" @("-Distro", $Distro)
    Write-Host "   00-ensure-docker exit=$code" -ForegroundColor DarkGray

    if ($code -eq 10) {
        # WSL2 needs a reboot -> resume automatically after login via RunOnce.
        # Loop guard: at most 3 reboots.
        $counterFile = Join-Path $InstallDir ".reboot-count"
        $count = 0
        if (Test-Path $counterFile) { $count = [int](Get-Content $counterFile -Raw) }
        if ($count -ge 3) {
            throw "WSL2 still not ready after several reboots ($count). Check that virtualization (BIOS VT-x / AMD-V) is enabled."
        }
        ($count + 1) | Set-Content $counterFile

        $resumeCmd = "`"$psExe`" -NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $here 'install.ps1')`" -AnswersFile `"$AnswersFile`" -Resume"
        Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce" `
            -Name "EnvisoftWebXInstallResume" -Value $resumeCmd

        $msg = "WSL2 has been enabled for the Envisoft WebX install; Windows must be " +
               "restarted to continue.`n`nRestart now?`n`n" +
               "- Yes: the machine restarts, and the install continues AUTOMATICALLY after you log back in.`n" +
               "- No: restart the machine later yourself; the install will still resume where it left off."
        $reboot = $true
        try {
            $wsh = New-Object -ComObject WScript.Shell
            $ans = $wsh.Popup($msg, 0, "Envisoft WebX - Restart", 4 + 32 + 256)
            $reboot = ($ans -eq 6)
        } catch { $reboot = $false }

        if ($reboot) {
            $rebooting = $true
            Write-Host "   Restarting..." -ForegroundColor Yellow
            Start-Sleep -Seconds 2
            Restart-Computer -Force
        } else {
            Write-Host "   Restart deferred. The install will resume after you restart the machine." -ForegroundColor Yellow
        }
        return
    }
    elseif ($code -ne 0) {
        throw "Docker prerequisite failed (exit $code)."
    }

    Remove-Item (Join-Path $InstallDir ".reboot-count") -ErrorAction SilentlyContinue

    # 2) Generate .env
    Write-Host ">> [2/5] Generating configuration (.env)..." -ForegroundColor Cyan
    $code = Invoke-Step "10-configure.ps1" (Build-Args ([ordered]@{
        InstallDir = $InstallDir; Domain = $a.Domain; TlsMode = $a.TlsMode;
        LeEmail = $a.LeEmail; MssqlPassword = $a.MssqlPassword; MssqlPid = $a.MssqlPid;
        LicenseKey = $a.LicenseKey; LicenseUrl = $a.LicenseUrl;
        GhcrImage = $a.GhcrImage; ImageTag = $a.ImageTag }))
    if ($code -ne 0) { throw "Configuration failed (exit $code)." }

    # 3) Pull + start
    Write-Host ">> [3/5] Pulling images + starting..." -ForegroundColor Cyan
    $code = Invoke-Step "20-up.ps1" (Build-Args ([ordered]@{
        InstallDir = $InstallDir; GhcrUser = $a.GhcrUser; GhcrToken = $a.GhcrToken }))
    if ($code -ne 0) { throw "Pull/start failed (exit $code)." }

    # 4) First run (seed + admin + WebSettings)
    Write-Host ">> [4/5] First run (seed + admin)..." -ForegroundColor Cyan
    $code = Invoke-Step "30-firstrun.ps1" (Build-Args ([ordered]@{
        InstallDir = $InstallDir; AdminUser = $a.AdminUser;
        AdminPassword = $a.AdminPassword; AdminEmail = $a.AdminEmail }))
    if ($code -ne 0) { throw "First run failed (exit $code)." }

    # 5) Auto-start (Windows auto-login + logon task; keeps the WSL2 VM alive)
    Write-Host ">> [5/5] Configuring unattended auto-start..." -ForegroundColor Cyan
    $code = Invoke-Step "40-register-service.ps1" (Build-Args ([ordered]@{
        InstallDir = $InstallDir; Distro = $Distro;
        WinUser = $a.WinUser; WinPass = $a.WinPass }))
    if ($code -ne 0) { throw "Auto-start configuration failed (exit $code)." }

    $success = $true
    Write-Host ""
    Write-Host "==== INSTALL COMPLETE ====" -ForegroundColor Green
    Write-Host "Dashboard: https://$($a.Domain)/dashboard/" -ForegroundColor Green
}
catch {
    Write-Host ""
    Write-Host "================  INSTALL ERROR  ================" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray }
    Write-Host "Full log: $(Join-Path $logDir 'install.log')" -ForegroundColor Yellow
}
finally {
    # Answers are needed across reboot (resume); delete on success only.
    if ($success -and -not $rebooting) {
        Remove-Item -Force $AnswersFile -ErrorAction SilentlyContinue
    }
    try { Stop-Transcript | Out-Null } catch {}
    # Visible console; if not rebooting, keep the window open so the result is read.
    if (-not $rebooting) {
        Write-Host ""
        Read-Host "Press Enter to close this window"
    }
}
