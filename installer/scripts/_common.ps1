<#
.SYNOPSIS
    Shared helpers for installer scripts (dot-sourced).

.DESCRIPTION
    The Envisoft WebX stack runs on Docker CE inside WSL2 (no Docker Desktop
    license required). Compose files + .env live on the Windows side under
    InstallDir; WSL reaches them via /mnt/<drive>/... This module wraps docker
    calls so they run inside WSL.

    NOTE: This file is intentionally ASCII-only (English). Windows PowerShell 5.1
    reads BOM-less scripts as ANSI; non-ASCII characters would corrupt parsing.
#>

$script:WslDistro = "Ubuntu"

# Every WSL call that touches Docker runs as ROOT - explicitly, never relying on
# the distro's *default* user.
#
# WHY (real field incident): 00-ensure-docker installs the distro with
# `wsl --install -d Ubuntu --no-launch`, so Ubuntu's first-run OOBE never runs
# -> no Unix user exists -> the default user IS root -> docker works. But the
# moment ANYONE opens an interactive `wsl` on the box (a technician debugging),
# the OOBE fires, creates a user (named after the Windows account, e.g.
# "envisoftwebx") and makes it the DEFAULT. That user is not in the `docker`
# group, so every `docker compose` here dies with
# "permission denied ... unix:///var/run/docker.sock".
#
# The failure is SILENT and nasty: containers keep running (restart:
# unless-stopped) so the site looks fine, but sais-stack.ps1's `up -d` throws ->
# the loop never reaches Set-PortProxy (external 80/443 bridge goes stale on the
# next WSL IP change) nor `sleep infinity` (WSL2 VM keepalive). Pinning root
# makes the installer + service independent of whatever the default user is.
$script:WslUser = "root"
$script:ComposeFile = "docker-compose.prod.yml"

# Ask wsl.exe for UTF-8 output (default is UTF-16LE, which turns captured logs
# into NUL-riddled garbage). Inherited by every wsl.exe child we spawn; old WSL
# builds simply ignore it (we still strip NULs everywhere as a fallback).
$env:WSL_UTF8 = "1"

# LIVE progress side-channel. install.ps1 exports ENVISOFT_PROGRESS_FILE (a tiny
# single-line file next to install.log); every long-running helper below rewrites
# it on each beat and the progress window shows it as ONE updating status line.
#
# WHY a separate file: step output travels child-stdout -> pipe -> Out-Host ->
# Start-Transcript, and the TRANSCRIPT WRITER BUFFERS (measured: flushes only
# every few KB / on stop). Heartbeat lines therefore reach install.log in bursts,
# minutes late - useless as live feedback. This file is written directly
# (open-write-close, no buffering) so the UI label is always current. The
# transcript log keeps a THROTTLED heartbeat (every ~30s) as the permanent record.
$script:ProgressFile = $env:ENVISOFT_PROGRESS_FILE

function Write-LiveProgress([string]$Text) {
    if (-not $script:ProgressFile) { return }
    try {
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($script:ProgressFile, $Text, $utf8NoBom)
    } catch {}
}

# True when our stdout is a pipe (e.g. a step running as a child process whose
# output the installer captures via `| Out-Host`). In that case an in-place `\r`
# spinner does NOT rewrite the line - it floods the console one line per frame.
# When redirected we fall back to a throttled "still working (Ns)" heartbeat.
$script:SpinRedirected = $false
try { $script:SpinRedirected = [Console]::IsOutputRedirected } catch {}

# Draw one progress frame (in-place spinner on a real console; nothing here for
# the redirected case - the caller throttles heartbeats itself).
function Write-SpinFrame([string]$Char, [string]$Label, [int]$Elapsed) {
    if (-not $script:SpinRedirected) {
        Write-Host ("`r   [{0}] {1}  ({2}s)     " -f $Char, $Label, $Elapsed) -NoNewline -ForegroundColor Cyan
    }
}

# Final status line for a spinner section (OK / X / !), redirection-aware.
function Write-SpinEnd([string]$Tag, [string]$Label, [int]$Elapsed, [string]$Color) {
    $msg = "   [{0}] {1}  ({2}s)             " -f $Tag, $Label, $Elapsed
    if (-not $script:SpinRedirected) { $msg = "`r" + $msg }
    Write-Host $msg -ForegroundColor $Color
    Write-LiveProgress ("[{0}] {1} ({2}s)" -f $Tag, $Label, $Elapsed)
}

function Write-Step([string]$msg) {
    Write-Host ">> $msg" -ForegroundColor Cyan
    Write-LiveProgress $msg
}

function Write-Ok([string]$msg) {
    Write-Host "   [OK] $msg" -ForegroundColor Green
    Write-LiveProgress "[OK] $msg"
}

function Write-WarnLine([string]$msg) {
    Write-Host "   [!] $msg" -ForegroundColor Yellow
}

# C:\EnvisoftWebX  ->  /mnt/c/EnvisoftWebX
function ConvertTo-WslPath([string]$winPath) {
    $full = [System.IO.Path]::GetFullPath($winPath)
    $drive = $full.Substring(0, 1).ToLower()
    $rest = $full.Substring(2) -replace '\\', '/'
    return "/mnt/$drive$rest"
}

# Default gateway as seen from INSIDE the distro = the Windows host's address
# on the WSL NAT switch = the address containers reach the host on (used by the
# serial bridge: SERIAL_BRIDGE_HOST). Parses "default via <ip> dev eth0 ...".
function Get-WslGatewayIp([string]$Distro = $script:WslDistro) {
    try {
        $out = (wsl.exe -d $Distro -u root -- ip route show default 2>$null | Out-String)
        if ($out -match 'via\s+(\d+\.\d+\.\d+\.\d+)') {
            $gw = $Matches[1]
            # Mirrored-networking guard: under networkingMode=mirrored the WSL
            # "gateway" is the LAN router, NOT this host -> using it would point
            # the containers at the router. Skip in that case.
            $hostGw = ""
            try {
                $route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
                    Sort-Object RouteMetric | Select-Object -First 1
                if ($route) { $hostGw = $route.NextHop }
            } catch { }
            if ($hostGw -and $gw -eq $hostGw) { return "" }
            return $gw
        }
    } catch { }
    return ""
}

# Replace-or-append KEY=VALUE in InstallDir\.env. Writes only when the value
# actually changes (avoids needless container recreate on `up -d`); UTF-8 no
# BOM + LF endings (the file is read inside WSL/Linux).
function Update-EnvVar([string]$InstallDir, [string]$Key, [string]$Value) {
    try {
        $envPath = Join-Path $InstallDir ".env"
        if (-not (Test-Path $envPath)) { return }
        $content = Get-Content -Raw -Encoding UTF8 $envPath
        $line = "$Key=$Value"
        $pattern = '(?m)^' + [regex]::Escape($Key) + '=.*$'
        if ($content -match $pattern) {
            $new = [regex]::Replace($content, $pattern, $line)
        } else {
            $sep = if ($content.EndsWith("`n")) { "" } else { "`n" }
            $new = $content + $sep + $line + "`n"
        }
        if ($new -ne $content) {
            $new = $new -replace "`r`n", "`n"
            $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
            [System.IO.File]::WriteAllText($envPath, $new, $utf8NoBom)
        }
    } catch { }
}

# 1234567 -> "1.2 MB" (for human-readable download progress lines).
function Format-Bytes([long]$b) {
    if ($b -ge 1GB) { return ("{0:0.0} GB" -f ($b / 1GB)) }
    if ($b -ge 1MB) { return ("{0:0} MB" -f ($b / 1MB)) }
    if ($b -ge 1KB) { return ("{0:0} KB" -f ($b / 1KB)) }
    return "$b B"
}

# Last meaningful line of a (possibly still-growing) log file - used by the
# heartbeat lines so the operator sees WHAT a long step is doing right now
# (e.g. docker's own "Downloading 12.3MB/45.6MB" layer lines). Shares the file
# with the writer, strips NULs (UTF-16 wsl output) and CR-progress frames.
function Get-TailLine([string]$Path) {
    try {
        if (-not (Test-Path $Path)) { return "" }
        $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        try {
            $take = [int][Math]::Min(8192, $fs.Length)
            if ($take -le 0) { return "" }
            $fs.Seek(-$take, [System.IO.SeekOrigin]::End) | Out-Null
            $buf = New-Object byte[] $take
            $fs.Read($buf, 0, $take) | Out-Null
            $txt = [Text.Encoding]::UTF8.GetString($buf) -replace "`0", ""
        } finally { $fs.Close() }
        $lines = @(($txt -split "[`r`n]+") | ForEach-Object { $_.Trim() } |
            Where-Object { $_ -and $_ -notmatch '^[\|\\/\-\.\s]+$' })
        if (-not $lines.Count) { return "" }
        $last = $lines[$lines.Count - 1]
        if ($last.Length -gt 110) { $last = $last.Substring(0, 110) }
        return $last
    } catch { return "" }
}

# Docker-pull layer counters from a step log: " [layers done/total]". Works with
# compose's non-TTY plain output ("Pulling fs layer" / "Pull complete" lines).
function Get-PullStats([string]$Path) {
    try {
        $c = (Get-Content -Raw $Path -ErrorAction SilentlyContinue)
        if (-not $c) { return "" }
        $c = $c -replace "`0", ""
        $total = ([regex]::Matches($c, 'Pulling fs layer')).Count
        $done = ([regex]::Matches($c, 'Pull complete')).Count
        if ($total -gt 0) { return (" [layers {0}/{1}]" -f $done, $total) }
    } catch {}
    return ""
}

# Run a native exe TIME-BOXED, stdin redirected from an empty file, output
# captured to temp files. Returns @{ TimedOut; ExitCode; Output }.
#
# WHY (real field incident, Win11 26100): when the WSL platform is not yet
# installed/updated, EVERY wsl.exe invocation shows an interactive
# "Press any key to install Windows Subsystem for Linux" prompt. In the hidden
# service-account session nobody can press a key -> the very first wsl call
# (Test-DockerReady) blocked the install for HOURS with zero log output. Any
# probe that can hit that stub must be killable.
function Invoke-NativeCapture([string]$Exe, [string]$Arguments, [int]$TimeoutSec = 60) {
    $id = [guid]::NewGuid().ToString('N').Substring(0, 8)
    $out = Join-Path $env:TEMP "envisoft-cap-$id.out"
    $err = Join-Path $env:TEMP "envisoft-cap-$id.err"
    $stdin = Join-Path $env:TEMP "envisoft-empty-stdin.txt"
    try { if (-not (Test-Path $stdin)) { Set-Content -Path $stdin -Value "" -Encoding ASCII } } catch {}
    try {
        $p = Start-Process -FilePath $Exe -ArgumentList $Arguments -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $out -RedirectStandardError $err -RedirectStandardInput $stdin
        $null = $p.Handle   # PS 5.1 quirk: without touching Handle, ExitCode reads as $null
    } catch {
        return @{ TimedOut = $false; ExitCode = -1; Output = "$_" }
    }
    $finished = $p.WaitForExit($TimeoutSec * 1000)
    if (-not $finished) {
        try { taskkill.exe /T /F /PID $p.Id *> $null } catch {}
        return @{ TimedOut = $true; ExitCode = -1; Output = "" }
    }
    $txt = ""
    foreach ($f in @($out, $err)) {
        try { $txt += ((Get-Content -Raw $f -ErrorAction SilentlyContinue) -replace "`0", "") } catch {}
    }
    return @{ TimedOut = $false; ExitCode = $p.ExitCode; Output = $txt }
}

# Run a long native exe with heartbeat progress lines (elapsed + live tail of
# its output) and a hard timeout (process tree killed). Does NOT throw - the
# caller inspects @{ TimedOut; ExitCode; Log } and decides (retry/fallback).
function Invoke-NativeSpin {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][string]$Exe,
        [string]$Arguments = "",
        [int]$TimeoutSec = 1800
    )
    $id = [guid]::NewGuid().ToString('N').Substring(0, 8)
    $out = Join-Path $env:TEMP "envisoft-native-$id.out"
    $err = Join-Path $env:TEMP "envisoft-native-$id.err"
    $stdin = Join-Path $env:TEMP "envisoft-empty-stdin.txt"
    try { if (-not (Test-Path $stdin)) { Set-Content -Path $stdin -Value "" -Encoding ASCII } } catch {}

    if ($Arguments) {
        $p = Start-Process -FilePath $Exe -ArgumentList $Arguments -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $out -RedirectStandardError $err -RedirectStandardInput $stdin
    } else {
        $p = Start-Process -FilePath $Exe -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $out -RedirectStandardError $err -RedirectStandardInput $stdin
    }
    $null = $p.Handle   # PS 5.1 quirk: without touching Handle, ExitCode reads as $null

    $spin = @('|', '/', '-', '\')
    $i = 0
    $t0 = Get-Date
    $lastBeat = 0   # intro line already printed; first log heartbeat at +30s
    $lastLive = -999
    if ($script:SpinRedirected) { Write-Host ("   ... {0} ..." -f $Label) -ForegroundColor Cyan }
    while (-not $p.HasExited) {
        $el = [int]((Get-Date) - $t0).TotalSeconds
        if ($el -ge $TimeoutSec) {
            try { taskkill.exe /T /F /PID $p.Id *> $null } catch {}
            Write-SpinEnd "!" "$Label TIMED OUT after ${el}s (process killed)" $el "Yellow"
            return @{ TimedOut = $true; ExitCode = -1; Log = $out }
        }
        if (($el - $lastLive) -ge 2) {
            $tail = Get-TailLine $out
            if (-not $tail) { $tail = Get-TailLine $err }
            $live = "{0} ({1}s)" -f $Label, $el
            if ($tail) { $live += " :: $tail" }
            Write-LiveProgress $live
            $lastLive = $el
            if ($script:SpinRedirected -and ($el - $lastBeat) -ge 30) {
                Write-Host "   ... $live" -ForegroundColor DarkCyan
                $lastBeat = $el
            }
        }
        if (-not $script:SpinRedirected) { Write-SpinFrame $spin[$i % 4] $Label $el }
        Start-Sleep -Milliseconds 250
        $i++
    }
    $el = [int]((Get-Date) - $t0).TotalSeconds
    $code = $p.ExitCode
    if ($code -eq 0) { Write-SpinEnd "OK" $Label $el "Green" }
    else {
        Write-SpinEnd "!" "$Label exited with code $code" $el "Yellow"
        foreach ($f in @($err, $out)) {
            $t = Get-TailLine $f
            if ($t) { Write-Host "      last output: $t" -ForegroundColor DarkGray; break }
        }
    }
    return @{ TimedOut = $false; ExitCode = $code; Log = $out }
}

# Download a file with LIVE progress lines: "123 MB / 450 MB (27%)". Total size
# comes from -ExpectedBytes or a HEAD request; without either, bytes-only lines.
# Uses curl.exe (resumable, -C -) when available, Invoke-WebRequest otherwise.
# Throws on failure/timeout.
function Invoke-DownloadSpin {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][string]$Url,
        [Parameter(Mandatory)][string]$OutFile,
        [long]$ExpectedBytes = 0,
        [int]$TimeoutSec = 3600
    )
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    if ($ExpectedBytes -le 0) {
        try {
            $req = [Net.WebRequest]::Create($Url)
            $req.Method = "HEAD"; $req.Timeout = 15000; $req.AllowAutoRedirect = $true
            $resp = $req.GetResponse()
            $ExpectedBytes = [long]$resp.ContentLength
            $resp.Close()
        } catch {}
    }

    $job = Start-Job -ScriptBlock {
        param($u, $o)
        $curl = Get-Command curl.exe -ErrorAction SilentlyContinue   # ships with Win10 1803+
        if ($curl) {
            & $curl.Source -fL -sS --retry 3 --retry-delay 5 -C - -o $o $u 2>&1 | Out-String
            $LASTEXITCODE
        } else {
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            $ProgressPreference = "SilentlyContinue"
            try { Invoke-WebRequest -Uri $u -OutFile $o -UseBasicParsing; 0 } catch { "$_"; 1 }
        }
    } -ArgumentList $Url, $OutFile

    $t0 = Get-Date
    $lastBeat = 0   # intro line already printed; first log heartbeat at +30s
    $lastLive = -999
    $spin = @('|', '/', '-', '\')
    $i = 0
    if ($script:SpinRedirected) { Write-Host ("   ... {0} ..." -f $Label) -ForegroundColor Cyan }
    while ($job.State -eq 'Running') {
        $el = [int]((Get-Date) - $t0).TotalSeconds
        if ($el -ge $TimeoutSec) {
            Stop-Job $job -ErrorAction SilentlyContinue
            Remove-Job $job -Force -ErrorAction SilentlyContinue
            throw "$Label timed out after ${el}s."
        }
        $cur = 0
        try { $cur = [long](Get-Item $OutFile -ErrorAction SilentlyContinue).Length } catch {}
        $prog = Format-Bytes $cur
        if ($ExpectedBytes -gt 0) {
            $pct = [int](100 * $cur / $ExpectedBytes)
            $prog = "{0} / {1} ({2}%)" -f (Format-Bytes $cur), (Format-Bytes $ExpectedBytes), $pct
        }
        if (($el - $lastLive) -ge 1) {
            Write-LiveProgress ("{0} - {1} ({2}s)" -f $Label, $prog, $el)
            $lastLive = $el
            if ($script:SpinRedirected -and ($el - $lastBeat) -ge 30) {
                Write-Host ("   ... {0} - {1} ({2}s)" -f $Label, $prog, $el) -ForegroundColor DarkCyan
                $lastBeat = $el
            }
        }
        if (-not $script:SpinRedirected) { Write-SpinFrame $spin[$i % 4] "$Label - $prog" $el }
        Start-Sleep -Milliseconds 500
        $i++
    }

    $jobOut = Receive-Job $job
    Remove-Job $job -Force -ErrorAction SilentlyContinue
    $exit = 1
    if ($null -ne $jobOut) { try { $exit = [int]($jobOut | Select-Object -Last 1) } catch { $exit = 1 } }
    $el = [int]((Get-Date) - $t0).TotalSeconds
    $size = 0
    try { $size = [long](Get-Item $OutFile -ErrorAction SilentlyContinue).Length } catch {}

    if ($exit -eq 0 -and $size -gt 0) {
        Write-SpinEnd "OK" ("{0} - {1}" -f $Label, (Format-Bytes $size)) $el "Green"
        return
    }
    Write-SpinEnd "X" "$Label FAILED" $el "Red"
    $detail = ($jobOut | Where-Object { $_ -is [string] } | Select-Object -First 3) -join " | "
    throw "$Label failed (exit $exit). $detail"
}

# Is Docker running inside the WSL distro?
# IMPORTANT: redirect docker's stderr INSIDE bash (docker info >/dev/null 2>&1),
# never via a PowerShell-side `*>`/`2>`. Under ErrorActionPreference='Stop' a
# PowerShell redirect of a native command's stderr raises NativeCommandError,
# so this would wrongly return $false even when the daemon is UP (docker info
# always prints warnings to stderr). That false-negative made the installer
# loop on "Starting Docker daemon" until timeout although Docker was running.
function Test-DockerReady {
    # TIME-BOXED (90s): if the WSL platform is missing, wsl.exe shows an
    # interactive "Press any key to install" stub prompt that would hang a
    # hidden session forever (see Invoke-NativeCapture). Timeout => not ready.
    $r = Invoke-NativeCapture "wsl.exe" `
        ("-d {0} -u {1} -- bash -lc `"docker info >/dev/null 2>&1`"" -f $script:WslDistro, $script:WslUser) 90
    return ((-not $r.TimedOut) -and ($r.ExitCode -eq 0))
}

# Run an arbitrary bash command inside the WSL distro (as root - see $WslUser).
function Invoke-Wsl([string]$bash) {
    wsl.exe -d $script:WslDistro -u $script:WslUser -- bash -lc "$bash"
    if ($LASTEXITCODE -ne 0) {
        throw "WSL command failed (exit $LASTEXITCODE): $bash"
    }
}

# Make sure the Docker daemon is running inside the distro (systemd fallback).
function Assert-Docker {
    wsl.exe -d $script:WslDistro -u root -- bash -lc `
        "docker info >/dev/null 2>&1 || service docker start 2>/dev/null || systemctl start docker 2>/dev/null || (pgrep dockerd >/dev/null || (dockerd >/var/log/dockerd.log 2>&1 &)); sleep 2" *> $null
}

# Run `docker compose ...` in the InstallDir context.
function Invoke-Compose([string]$InstallDir, [string]$composeArgs) {
    Assert-Docker
    $wslDir = ConvertTo-WslPath $InstallDir
    $cmd = "cd '$wslDir' && docker compose --env-file .env -f $script:ComposeFile $composeArgs"
    wsl.exe -d $script:WslDistro -u $script:WslUser -- bash -lc "$cmd"
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed (exit $LASTEXITCODE): $composeArgs"
    }
}

# Run a manage.py command inside the web container.
function Invoke-Manage([string]$InstallDir, [string]$manageArgs, [string]$envInline = "") {
    $prefix = ""
    if ($envInline) { $prefix = "$envInline " }
    Invoke-Compose $InstallDir "exec -T web env $prefix python manage.py $manageArgs"
}

# Build the `docker compose ...` bash command for a given InstallDir (no exec).
function Get-ComposeBash([string]$InstallDir, [string]$composeArgs) {
    $wslDir = ConvertTo-WslPath $InstallDir
    return "cd '$wslDir' && docker compose --env-file .env -f $script:ComposeFile $composeArgs"
}

# Quick reachability probe: DNS-resolve + TCP-connect to a public host (5s cap).
# TcpClient.BeginConnect with a hostname covers BOTH failure modes (dead DNS and
# dead route), which is exactly what an image pull needs to work.
function Test-Internet([string]$TestHost = "ghcr.io", [int]$TestPort = 443) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect($TestHost, $TestPort, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(5000)
        if ($ok -and $c.Connected) { $c.EndConnect($iar); $c.Close(); return $true }
        $c.Close()
        return $false
    } catch {
        return $false
    }
}

# Block until the internet is reachable, with a LOUD on-screen warning so the
# operator knows the install is WAITING for the network - not stuck/crashed
# (real field incident: the connection dropped mid image-pull and the install
# died with "short read / unexpected EOF"). Returns $false after TimeoutSec.
function Wait-ForInternet([int]$TimeoutSec = 1800, [string]$Reason = "") {
    if (Test-Internet) { return $true }
    $why = ""
    if ($Reason) { $why = " ($Reason)" }
    Write-WarnLine "NO INTERNET CONNECTION$why - the install now WAITS for the network to come back."
    Write-WarnLine "This is not an error yet: it resumes AUTOMATICALLY as soon as the connection returns (up to $([int]($TimeoutSec/60)) min)."
    return (Wait-WithSpin "Waiting for internet (ghcr.io:443)" -TimeoutSec $TimeoutSec -CheckEverySec 10 -Check { Test-Internet })
}

# ----------------------------------------------------------------------------
# Invoke-WslSpin: run a long WSL/bash command while showing an ANIMATED spinner
# with elapsed seconds, so the console never looks frozen during downloads or
# image pulls. The command runs in a background job; its full output is captured
# to a per-step log (shown only if the step fails). Throws on non-zero exit.
#
# Why a job + log instead of live output: live native output (docker pull layer
# noise, get.docker.com) interleaves with the spinner and looks messy. The
# spinner is the "loader" the operator watches; the log keeps the detail.
# ----------------------------------------------------------------------------
function Invoke-WslSpin {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][string]$Bash,
        [string]$User = "",
        [string]$Distro = "",
        [string]$LogDir = "",
        [int]$Retries = 0,         # transient docker/runc exec failures (e.g.
                                   # "write init-p: broken pipe", exit 128) ->
                                   # retry. Only use for idempotent commands.
        [string]$ProgressHint = "" # "docker-pull" => heartbeat lines include a
                                   # "[layers done/total]" counter parsed from
                                   # the step log (compose pull progress).
    )
    if (-not $Distro) { $Distro = $script:WslDistro }
    # Default to root: callers (20-up pull/up, 30-firstrun exec) all touch Docker
    # and must not depend on the distro's default user. See $script:WslUser.
    if (-not $User) { $User = $script:WslUser }
    if (-not $LogDir) { $LogDir = $env:TEMP }
    $log = Join-Path $LogDir ("envisoft-step-" + ([guid]::NewGuid().ToString('N').Substring(0, 8)) + ".log")

    # CRLF-safe transport: a multi-line bash script passed straight to `bash -lc`
    # carries Windows CR (\r) bytes that break bash parsing ("set: usage",
    # "unexpected end of file from 'if'"). Base64-encode the (CR-stripped) script
    # and decode inside WSL -> no quoting/newline/CR pitfalls at all.
    $clean = ($Bash -replace "`r", "")
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($clean))
    $wrapped = "echo $b64 | base64 --decode | bash"

    for ($attempt = 0; $attempt -le $Retries; $attempt++) {
        $lbl = if ($attempt -gt 0) { "$Label (retry $attempt/$Retries)" } else { $Label }
        $job = Start-Job -ScriptBlock {
            param($d, $u, $b, $lg)
            if ($u) { wsl.exe -d $d -u $u -- bash -lc $b *> $lg }
            else    { wsl.exe -d $d -- bash -lc $b *> $lg }
            $LASTEXITCODE
        } -ArgumentList $Distro, $User, $wrapped, $log

        $spin = @('|', '/', '-', '\')
        $i = 0
        $t0 = Get-Date
        $lastBeat = 0   # intro line already printed; first log heartbeat at +30s
        if ($script:SpinRedirected) { Write-Host ("   ... {0} ..." -f $lbl) -ForegroundColor Cyan }
        $lastLive = -999
        while ($job.State -eq 'Running') {
            $el = [int]((Get-Date) - $t0).TotalSeconds
            # ONE updating status line for the UI (live file, every 2s): label +
            # elapsed + the step log's current last line (docker's own byte
            # counters, apt progress, ...). The transcript gets the same line
            # only every 30s - a permanent record without line-spam.
            if (($el - $lastLive) -ge 2) {
                $live = "{0} ({1}s)" -f $lbl, $el
                if ($ProgressHint -eq 'docker-pull') { $live += (Get-PullStats $log) }
                $tail = Get-TailLine $log
                if ($tail) { $live += " :: $tail" }
                Write-LiveProgress $live
                $lastLive = $el
                if ($script:SpinRedirected -and ($el - $lastBeat) -ge 30) {
                    Write-Host "   ... $live" -ForegroundColor DarkCyan
                    $lastBeat = $el
                }
            }
            if (-not $script:SpinRedirected) { Write-SpinFrame $spin[$i % 4] $lbl $el }
            Start-Sleep -Milliseconds 200
            $i++
        }

        $jobOut = Receive-Job $job
        Remove-Job $job -Force -ErrorAction SilentlyContinue
        $exit = 0
        if ($null -ne $jobOut) { try { $exit = [int]($jobOut | Select-Object -Last 1) } catch { $exit = 1 } }
        $el = [int]((Get-Date) - $t0).TotalSeconds

        if ($exit -eq 0) {
            Write-SpinEnd "OK" $lbl $el "Green"
            return
        }

        if ($attempt -lt $Retries) {
            Write-SpinEnd "!" "$lbl failed (exit $exit) - retrying" $el "Yellow"
            Start-Sleep -Seconds 3
            continue
        }

        Write-SpinEnd "X" "$lbl FAILED" $el "Red"
        if (Test-Path $log) {
            Write-Host "      ---- last lines of step log ($log) ----" -ForegroundColor DarkGray
            Get-Content $log -Tail 25 -ErrorAction SilentlyContinue | ForEach-Object {
                Write-Host "      $_" -ForegroundColor DarkGray
            }
        }
        throw "$Label failed (exit $exit). Full log: $log"
    }
}

# Spinner-driven wait: poll $Check (a scriptblock returning $true when ready)
# while showing an animated spinner. Returns $true if ready before timeout.
function Wait-WithSpin {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][scriptblock]$Check,
        [int]$TimeoutSec = 600,
        [int]$CheckEverySec = 4
    )
    $spin = @('|', '/', '-', '\')
    $i = 0
    $t0 = Get-Date
    $deadline = $t0.AddSeconds($TimeoutSec)
    $nextCheck = $t0
    $ready = $false
    $lastBeat = 0   # intro line already printed; first log heartbeat at +30s
    $lastLive = -999
    if ($script:SpinRedirected) { Write-Host ("   ... {0} ..." -f $Label) -ForegroundColor Cyan }
    while ((Get-Date) -lt $deadline) {
        if ((Get-Date) -ge $nextCheck) {
            try { $ready = [bool](& $Check) } catch { $ready = $false }
            if ($ready) { break }
            $nextCheck = (Get-Date).AddSeconds($CheckEverySec)
        }
        $el = [int]((Get-Date) - $t0).TotalSeconds
        if (($el - $lastLive) -ge 2) {
            Write-LiveProgress ("{0} ({1}s)" -f $Label, $el)
            $lastLive = $el
            if ($script:SpinRedirected -and ($el - $lastBeat) -ge 30) {
                Write-Host ("   ... {0} ({1}s)" -f $Label, $el) -ForegroundColor DarkCyan
                $lastBeat = $el
            }
        }
        if (-not $script:SpinRedirected) { Write-SpinFrame $spin[$i % 4] $Label $el }
        Start-Sleep -Milliseconds 250
        $i++
    }
    $el = [int]((Get-Date) - $t0).TotalSeconds
    if ($ready) {
        Write-SpinEnd "OK" $Label $el "Green"
    } else {
        Write-SpinEnd "!" "$Label - timed out" $el "Yellow"
    }
    return $ready
}

