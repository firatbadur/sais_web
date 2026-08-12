<#
.SYNOPSIS
    Branded progress window for the Envisoft WebX install. Hides the raw console
    and shows a clean WinForms window (step summary + progress bar + expandable
    full log) instead.

.DESCRIPTION
    Decoupled UI/worker design (no threading): this script is the VISIBLE UI. It
    launches install.ps1 HIDDEN as the worker (inheriting the elevated token),
    then tails the worker's transcript log (logs/install.log) and a small status
    file (logs/install-status.txt) on a UI-thread timer, updating the window.

    The worker runs with -NoPrompt so it never blocks on Read-Host in its hidden
    console. It writes the status file at each transition (running / rebooting /
    deferred-reboot / done / failed); this UI reacts to those.

    Used for BOTH phases: Inno [Run] launches it (Phase 1); after the reboot the
    EnvisoftWebX-Install logon task launches it with -Resume (Phase 2). Each phase
    gets its own window; this naturally survives the mid-install reboot.

    NOTE: ASCII-only (English) on purpose, like the rest of the installer
    (Windows PowerShell 5.1 corrupts non-ASCII in BOM-less scripts; the wizard is
    English too).
#>
param(
    [Parameter(Mandatory)] [string]$AnswersFile,
    [switch]$Resume
)

$ErrorActionPreference = "Continue"
$here = $PSScriptRoot
$psExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$installPs1 = Join-Path $here "install.ps1"

$installDir = Split-Path -Parent $AnswersFile
$logDir = Join-Path $installDir "logs"
try { New-Item -ItemType Directory -Force -Path $logDir | Out-Null } catch {}
$LogFile = Join-Path $logDir "install.log"
$StatusFile = Join-Path $logDir "install-status.txt"
$ProgressFile = Join-Path $logDir "install-progress.txt"
try { Remove-Item $ProgressFile -Force -ErrorAction SilentlyContinue } catch {}

# --- Hide our own console window (belt-and-suspenders with Inno runhidden) ----
try {
    Add-Type -Namespace Win32 -Name Native -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll")] public static extern System.IntPtr GetConsoleWindow();
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool ShowWindow(System.IntPtr hWnd, int nCmdShow);
'@
    $h = [Win32.Native]::GetConsoleWindow()
    if ($h -ne [System.IntPtr]::Zero) { [Win32.Native]::ShowWindow($h, 0) | Out-Null }  # 0 = SW_HIDE
} catch {}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

# --- Start tailing from the CURRENT end of the log (show only this phase) -----
$script:logPos = 0
try { if (Test-Path $LogFile) { $script:logPos = (Get-Item $LogFile).Length } } catch {}
# Clear any stale status from a previous phase before launching the worker.
try { Set-Content -Path $StatusFile -Value "running" -Encoding ASCII } catch {}

# --- Launch the hidden worker (inherits this process's elevated token) --------
$workerArgs = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $installPs1,
    "-AnswersFile", $AnswersFile, "-NoPrompt", "-StatusFile", $StatusFile
)
if ($Resume) { $workerArgs += "-Resume" }
$script:worker = Start-Process -FilePath $psExe -ArgumentList $workerArgs -WindowStyle Hidden -PassThru

# --- Friendly step text from the worker's "[n/5]" / "[Phase 1]" markers -------
$script:lastStep = if ($Resume) { "Resuming setup..." } else { "Starting setup..." }
$script:progress = -1     # -1 => marquee (unknown); 0..100 => determinate
$script:terminal = $false
$script:lastDetail = ""   # latest heartbeat/status line (live sub-step detail)

function Update-StepFromLine([string]$line) {
    if ($line -match '\[Phase 1\]')      { $script:lastStep = "Preparing service account and WSL..." }
    elseif ($line -match '\[1/5\]')      { $script:lastStep = "Setting up Docker (WSL2 + CE)...";          $script:progress = 15 }
    elseif ($line -match '\[2/5\]')      { $script:lastStep = "Generating configuration (.env)...";        $script:progress = 30 }
    elseif ($line -match '\[3/5\]')      { $script:lastStep = "Downloading and starting Docker images..."; $script:progress = 50 }
    elseif ($line -match '\[4/5\]')      { $script:lastStep = "Loading initial data (seed + admin)...";    $script:progress = 75 }
    elseif ($line -match '\[5/5\]')      { $script:lastStep = "Configuring automatic startup...";          $script:progress = 90 }
}

# ============================ Build the window ===============================
$form = New-Object System.Windows.Forms.Form
$form.Text = "Envisoft WebX Setup"
$form.FormBorderStyle = "FixedDialog"
$form.StartPosition = "CenterScreen"
$form.MaximizeBox = $false
$form.MinimizeBox = $true
$form.ClientSize = New-Object System.Drawing.Size(560, 185)
$form.Font = New-Object System.Drawing.Font("Segoe UI", 9)
try {
    $icoPath = Join-Path $installDir "EnvisoftWebX.ico"
    if (Test-Path $icoPath) { $form.Icon = New-Object System.Drawing.Icon($icoPath) }
} catch {}

$lblHeader = New-Object System.Windows.Forms.Label
$lblHeader.Text = "Installing Envisoft WebX"
$lblHeader.Font = New-Object System.Drawing.Font("Segoe UI", 13, [System.Drawing.FontStyle]::Bold)
$lblHeader.Location = New-Object System.Drawing.Point(20, 15)
$lblHeader.AutoSize = $true
$form.Controls.Add($lblHeader)

$lblStep = New-Object System.Windows.Forms.Label
$lblStep.Text = $script:lastStep
$lblStep.Location = New-Object System.Drawing.Point(22, 52)
$lblStep.Size = New-Object System.Drawing.Size(516, 20)
$form.Controls.Add($lblStep)

$bar = New-Object System.Windows.Forms.ProgressBar
$bar.Location = New-Object System.Drawing.Point(22, 80)
$bar.Size = New-Object System.Drawing.Size(516, 20)
$bar.Style = "Marquee"
$bar.MarqueeAnimationSpeed = 30
$form.Controls.Add($bar)

# Live sub-step detail: latest heartbeat line from the worker log (elapsed,
# MB/GB downloaded, docker layer counters) - visible WITHOUT opening details.
$lblDetail = New-Object System.Windows.Forms.Label
$lblDetail.Text = ""
$lblDetail.Location = New-Object System.Drawing.Point(22, 104)
$lblDetail.Size = New-Object System.Drawing.Size(516, 16)
$lblDetail.Font = New-Object System.Drawing.Font("Segoe UI", 8)
$lblDetail.ForeColor = [System.Drawing.Color]::DimGray
$lblDetail.AutoEllipsis = $true
$form.Controls.Add($lblDetail)

$btnDetails = New-Object System.Windows.Forms.Button
$btnDetails.Text = "Show details"
$btnDetails.Location = New-Object System.Drawing.Point(22, 138)
$btnDetails.Size = New-Object System.Drawing.Size(110, 28)
$form.Controls.Add($btnDetails)

$btnClose = New-Object System.Windows.Forms.Button
$btnClose.Text = "Close"
$btnClose.Location = New-Object System.Drawing.Point(438, 138)
$btnClose.Size = New-Object System.Drawing.Size(100, 28)
$btnClose.Enabled = $false
$form.Controls.Add($btnClose)

$txtLog = New-Object System.Windows.Forms.TextBox
$txtLog.Multiline = $true
$txtLog.ReadOnly = $true
$txtLog.ScrollBars = "Vertical"
$txtLog.WordWrap = $false
$txtLog.Font = New-Object System.Drawing.Font("Consolas", 8)
$txtLog.Location = New-Object System.Drawing.Point(22, 178)
$txtLog.Size = New-Object System.Drawing.Size(516, 280)
$txtLog.Visible = $false
$txtLog.BackColor = [System.Drawing.Color]::FromArgb(30, 30, 30)
$txtLog.ForeColor = [System.Drawing.Color]::Gainsboro
$form.Controls.Add($txtLog)

$btnDetails.Add_Click({
    if ($txtLog.Visible) {
        $txtLog.Visible = $false
        $form.ClientSize = New-Object System.Drawing.Size(560, 185)
        $btnDetails.Text = "Show details"
    } else {
        $txtLog.Visible = $true
        $form.ClientSize = New-Object System.Drawing.Size(560, 475)
        $btnDetails.Text = "Hide details"
    }
})

$btnClose.Add_Click({ $script:terminal = $true; $form.Close() })
# Block closing the window while the install is still running.
$form.Add_FormClosing({ param($s, $e) if (-not $script:terminal) { $e.Cancel = $true } })

# --- Append new log bytes (shared read; worker holds the file open) ----------
function Read-NewLog {
    try {
        if (-not (Test-Path $LogFile)) { return "" }
        $fs = New-Object System.IO.FileStream($LogFile, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        if ($script:logPos -gt $fs.Length) { $script:logPos = 0 }   # log rotated/truncated
        $fs.Seek($script:logPos, [System.IO.SeekOrigin]::Begin) | Out-Null
        $sr = New-Object System.IO.StreamReader($fs)
        $txt = $sr.ReadToEnd()
        $script:logPos = $fs.Position
        $sr.Close(); $fs.Close()
        return $txt
    } catch { return "" }
}

function Set-Final([string]$state) {
    $script:terminal = $true
    $bar.Style = "Blocks"
    $bar.Value = 100
    $btnClose.Enabled = $true
    $lblDetail.Text = ""
    switch ($state) {
        "done"            { $lblHeader.Text = "Installation complete"; $lblStep.Text = "Envisoft WebX is ready. You can close this window." }
        "failed"          { $lblHeader.Text = "Installation failed";   $lblStep.Text = "Something went wrong. Click 'Show details' for the full log."; $bar.ForeColor = [System.Drawing.Color]::Firebrick }
        "deferred-reboot" { $lblHeader.Text = "Restart required";      $lblStep.Text = "Please restart the machine; setup resumes automatically after sign-in." }
    }
}

# --- Poll timer: tail log + watch status, update UI on the UI thread ---------
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 500
$timer.Add_Tick({
    if ($script:terminal) { return }

    $new = Read-NewLog
    if ($new) {
        foreach ($line in ($new -split "`r?`n")) {
            if ($line -match '^\>\> ') { Update-StepFromLine $line }
            elseif ($line -match '^\s+\.\.\.\s+(.+)$') { $script:lastDetail = $Matches[1] }
            elseif ($line -match '^\s+\[(OK|!)\]\s+(.+)$') { $script:lastDetail = $Matches[2] }
        }
        $lblStep.Text = $script:lastStep
        if ($script:progress -ge 0 -and $bar.Style -ne "Blocks") { $bar.Style = "Blocks" }
        if ($script:progress -ge 0) { $bar.Value = [Math]::Min(100, $script:progress) }
        $txtLog.AppendText($new)
    }

    # Live status: prefer the progress side-channel file (rewritten every 1-2s
    # by the running step, bypasses transcript buffering); fall back to the
    # latest heartbeat line parsed from the log above.
    try {
        if (Test-Path $ProgressFile) {
            $live = [System.IO.File]::ReadAllText($ProgressFile).Trim()
            if ($live) { $script:lastDetail = $live }
        }
    } catch {}
    $lblDetail.Text = $script:lastDetail

    $status = ""
    try { if (Test-Path $StatusFile) { $status = (Get-Content -Raw $StatusFile -ErrorAction SilentlyContinue).Trim() } } catch {}

    switch ($status) {
        "rebooting" {
            $lblHeader.Text = "Restarting"
            $lblStep.Text = "Restarting to continue the installation..."
            $btnDetails.Enabled = $true
            $timer.Stop()
            return
        }
        "deferred-reboot" { $timer.Stop(); Set-Final "deferred-reboot"; return }
        "done"            { $timer.Stop(); Set-Final "done"; return }
        "failed"          { $timer.Stop(); Set-Final "failed"; return }
    }

    # Safety net: worker vanished without writing a terminal status.
    if ($script:worker -and $script:worker.HasExited) {
        Start-Sleep -Milliseconds 300
        $new2 = Read-NewLog
        if ($new2) { $txtLog.AppendText($new2) }
        $timer.Stop()
        if ($script:worker.ExitCode -eq 0) { Set-Final "done" } else { Set-Final "failed" }
    }
})

$form.Add_Shown({ $timer.Start() })
[System.Windows.Forms.Application]::Run($form)
