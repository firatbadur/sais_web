<#
.SYNOPSIS
    Envisoft WebX install orchestrator - two-phase flow built around a dedicated
    service account that the box auto-logs-into at every boot.

.DESCRIPTION
    The Inno Setup wizard writes the answers to a JSON file and calls this script
    with -AnswersFile.

    WHY TWO PHASES: the stack runs in WSL2 and the WSL distro is registered
    PER-USER, so it must be set up under the dedicated EnvisoftWebX service
    account - not under the (human) user running the installer. So:

      Phase 1 (this user, admin): 05-service-account creates the EnvisoftWebX
        account, enables WSL features, and arms Windows auto-login for it. We then
        register a one-shot logon task (EnvisoftWebX-Install, RunLevel Highest)
        and reboot. The box comes back up auto-logged-in as EnvisoftWebX.

      Phase 2 (EnvisoftWebX session, via -Resume): 00-ensure-docker registers the
        WSL distro under EnvisoftWebX, then 10/20/30/40 configure + start the
        stack and install the permanent keepalive task. On success the one-shot
        install task + answers file are removed.

    A WSL-kernel reboot inside 00-ensure-docker (exit 10) just reboots; the
    persistent EnvisoftWebX-Install logon task re-fires Phase 2 after auto-login.

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
    Passed by the EnvisoftWebX-Install logon task after the Phase 1 reboot; the
    install continues in the service account's session (Phase 2).

.PARAMETER NoPrompt
    Run headless: skip the final Read-Host. Set when launched (hidden) by
    progress-window.ps1, whose console is hidden - a Read-Host would hang forever.

.PARAMETER StatusFile
    Optional path; this worker stamps its lifecycle here (running / rebooting /
    deferred-reboot / done / failed) so the progress window can react.
#>
param(
    [Parameter(Mandatory)] [string]$AnswersFile,
    [switch]$Resume,
    [switch]$NoPrompt,
    [string]$StatusFile = ""
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$psExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$progressUi = Join-Path $here "progress-window.ps1"
$SvcUser = "EnvisoftWebX"          # dedicated service / auto-login account
$ResumeTask = "EnvisoftWebX-Install"

# Stamp the lifecycle state for the progress window (no-op if no StatusFile).
function Set-Status([string]$state) {
    if (-not $StatusFile) { return }
    try { Set-Content -Path $StatusFile -Value $state -Encoding ASCII } catch {}
}

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

# Register (or refresh) the one-shot logon task that resumes the install in the
# EnvisoftWebX session. AtLogOn + RunLevel Highest guarantees an ELEVATED run
# (00-ensure-docker requires admin; a RunOnce entry under UAC may not elevate).
# Window is VISIBLE so the operator can watch Phase 2 progress.
function Register-ResumeTask {
    # Resume via the progress window (visible) which re-launches this worker
    # hidden with -Resume -NoPrompt. So Phase 2 shows the same branded UI.
    $resumeArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$progressUi`" -AnswersFile `"$AnswersFile`" -Resume"
    $action  = New-ScheduledTaskAction -Execute $psExe -Argument $resumeArgs
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $SvcUser
    $principal = New-ScheduledTaskPrincipal -UserId $SvcUser -RunLevel Highest -LogonType Interactive
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
    Unregister-ScheduledTask -TaskName $ResumeTask -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
    Register-ScheduledTask -TaskName $ResumeTask -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings `
        -Description "Envisoft WebX - resume install in the service account session." -Force | Out-Null
}

# Ask to reboot now (Yes) or later (No); either way auto-login + the resume task
# continue the install. Returns when the user defers; never returns on reboot.
function Request-Reboot([string]$msg) {
    $reboot = $true
    try {
        $wsh = New-Object -ComObject WScript.Shell
        $ans = $wsh.Popup($msg, 0, "Envisoft WebX - Restart", 4 + 32 + 256)
        $reboot = ($ans -eq 6)
    } catch { $reboot = $false }
    if ($reboot) {
        $script:rebooting = $true
        Set-Status "rebooting"
        Write-Host "   Restarting..." -ForegroundColor Yellow
        Start-Sleep -Seconds 2
        Restart-Computer -Force
    } else {
        $script:rebooting = $true   # not a failure; the resume task continues it
        Set-Status "deferred-reboot"
        Write-Host "   Restart deferred. The install resumes automatically after you restart." -ForegroundColor Yellow
    }
}

try {
    Write-Host "==== Envisoft WebX Setup $([DateTime]::Now) (Resume=$Resume) ====" -ForegroundColor Magenta
    Write-Host "Answers: $AnswersFile" -ForegroundColor DarkGray
    Set-Status "running"

    if (-not (Test-Path $AnswersFile)) {
        throw "Answers file not found: $AnswersFile"
    }
    $a = Get-Content -Raw $AnswersFile | ConvertFrom-Json
    $InstallDir = $a.InstallDir
    $Distro = if ($a.Distro) { $a.Distro } else { "Ubuntu" }
    Write-Host "InstallDir=$InstallDir  Distro=$Distro  Domain=$($a.Domain)" -ForegroundColor DarkGray

    if (-not $Resume) {
        # ============================ PHASE 1 (this admin) ====================
        # Create the service account + auto-login + WSL features, then reboot so
        # the rest runs in the EnvisoftWebX session.
        Write-Host ">> [Phase 1] Creating service account + enabling WSL..." -ForegroundColor Cyan
        $code = Invoke-Step "05-service-account.ps1" @("-SvcUser", $SvcUser)
        if ($code -ne 0) { throw "Service account setup failed (exit $code)." }

        Write-Host ">> Arming resume task '$ResumeTask' and rebooting..." -ForegroundColor Cyan
        Register-ResumeTask

        $msg = "Envisoft WebX has created its service account and enabled WSL2; " +
               "Windows must restart to continue.`n`n" +
               "After the restart the machine signs in automatically as the " +
               "Envisoft WebX service account and the install CONTINUES on its own.`n`n" +
               "Restart now?`n`n" +
               "- Yes: restart and continue automatically.`n" +
               "- No: restart later yourself; the install still resumes automatically."
        Request-Reboot $msg
        return
    }

    # ============================== PHASE 2 (EnvisoftWebX) ====================
    # 1) Docker prerequisite (WSL2 + Docker CE) - registers the distro under this
    #    (service) account.
    Write-Host ">> [1/5] Ensuring Docker (WSL2 + CE)..." -ForegroundColor Cyan
    $code = Invoke-Step "00-ensure-docker.ps1" @("-Distro", $Distro)
    Write-Host "   00-ensure-docker exit=$code" -ForegroundColor DarkGray

    if ($code -eq 10) {
        # WSL2 needs a reboot -> just reboot. The persistent EnvisoftWebX-Install
        # logon task re-fires Phase 2 after auto-login. Loop guard: at most 3.
        $counterFile = Join-Path $InstallDir ".reboot-count"
        $count = 0
        if (Test-Path $counterFile) { $count = [int](Get-Content $counterFile -Raw) }
        if ($count -ge 3) {
            throw ("WSL2 still not ready after several reboots ($count). Check that virtualization " +
                "(BIOS VT-x / AMD-V) is enabled, then inspect the 'wsl --status' / 'wsl -l -v' " +
                "diagnostics in logs\install.log (run as the $SvcUser account).")
        }
        ($count + 1) | Set-Content $counterFile

        $msg = "WSL2 needs one more restart to activate its kernel.`n`nRestart now?`n`n" +
               "- Yes: restart; the install continues automatically after sign-in.`n" +
               "- No: restart later; the install still resumes automatically."
        Request-Reboot $msg
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
        LeEmail = $a.LeEmail; PgPassword = $a.PgPassword;
        LicenseMode = $a.LicenseMode; LicenseKey = $a.LicenseKey; LicenseUrl = $a.LicenseUrl;
        GhcrImage = $a.GhcrImage; ImageTag = $a.ImageTag; GhcrToken = $a.GhcrToken }))
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

    # 5) Auto-start (logon keepalive task; auto-login was set in Phase 1)
    Write-Host ">> [5/5] Configuring unattended auto-start..." -ForegroundColor Cyan
    $code = Invoke-Step "40-register-service.ps1" (Build-Args ([ordered]@{
        InstallDir = $InstallDir; Distro = $Distro; SvcUser = $SvcUser }))
    if ($code -ne 0) { throw "Auto-start configuration failed (exit $code)." }

    # Phase 2 done -> remove the one-shot resume task so it does not fire again.
    Unregister-ScheduledTask -TaskName $ResumeTask -Confirm:$false -ErrorAction SilentlyContinue | Out-Null

    $success = $true
    Set-Status "done"
    Write-Host ""
    Write-Host "==== INSTALL COMPLETE ====" -ForegroundColor Green
    Write-Host "Dashboard (local): http://localhost/dashboard/" -ForegroundColor Green
    if ($a.Domain) { Write-Host "Dashboard (domain): https://$($a.Domain)/dashboard/" -ForegroundColor Green }
}
catch {
    Set-Status "failed"
    Write-Host ""
    Write-Host "================  INSTALL ERROR  ================" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray }
    Write-Host "Full log: $(Join-Path $logDir 'install.log')" -ForegroundColor Yellow
}
finally {
    # Answers are needed across reboots (Phase 1 -> Phase 2, and WSL reboots);
    # delete on success only.
    if ($success -and -not $rebooting) {
        Remove-Item -Force $AnswersFile -ErrorAction SilentlyContinue
    }
    try { Stop-Transcript | Out-Null } catch {}
    # Keep the window open only when running with a visible console (legacy/manual
    # debug). Under the progress window the console is hidden + -NoPrompt is set,
    # so a Read-Host would hang forever.
    if (-not $rebooting -and -not $NoPrompt) {
        Write-Host ""
        Read-Host "Press Enter to close this window"
    }
}
