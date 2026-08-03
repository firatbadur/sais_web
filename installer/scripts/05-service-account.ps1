<#
.SYNOPSIS
    Create the dedicated EnvisoftWebX service account and arm Windows auto-login
    for it BEFORE the WSL2 reboot, so the rest of the install (and every boot
    afterwards) runs in that account's interactive session.

.DESCRIPTION
    Why a dedicated account (instead of the user running the installer):
      - The stack runs in WSL2, which only works in a logged-in interactive
        session, so the box must auto-login SOME account at boot.
      - AutoAdminLogon stores a STATIC password in the registry. If we auto-login
        the human operator and they ever change their Windows password, the stored
        password goes stale -> auto-login fails -> the stack never comes up after a
        reboot/power-cut.
      - A dedicated account nobody signs into keeps a password the installer owns
        and manages, so auto-login never breaks. The WSL distro is registered
        per-user, so it MUST be set up under THIS account -> we switch the boot
        session to it here (auto-login), then a reboot lands us in its session and
        00-ensure-docker registers the distro under it.

    Two layers protect the password from drift:
      - Layer 1 (here): the account is flagged "user cannot change password"
        (net user /passwordchg:no) + password never expires.
      - Layer 2 (sais-stack.ps1 keepalive): each loop re-asserts the real account
        password to match the stored DefaultPassword, reverting any forced change.

    Security note: the generated password is stored in the registry
    (AutoAdminLogon DefaultPassword), same as the previous behaviour. Acceptable
    for a physically secured SCADA cabinet (kiosk / appliance pattern). The
    dashboard keeps its own separate login.

    NOTE: ASCII-only (English) on purpose - Windows PowerShell 5.1 reads BOM-less
    scripts as ANSI and would corrupt non-ASCII characters during parsing.
#>
param(
    [string]$SvcUser = "EnvisoftWebX"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

# Administrator rights are required (create user, registry, features).
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "This step requires Administrator rights."
    exit 1
}

# --- Generate a strong password from a SHELL-SAFE alphabet -------------------
# Avoid characters that complicate the later `net user <name> <pass>` self-heal
# call (quotes, spaces, &, |, <, >, %). Guarantee one of each class + length 24.
function New-ServicePassword {
    $upper = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    $lower = "abcdefghijkmnpqrstuvwxyz"
    $digit = "23456789"
    $sym   = "!@#%^*-_=+"
    $all   = $upper + $lower + $digit + $sym
    $rng   = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    function Pick([string]$set) {
        $b = New-Object 'byte[]' 1
        $rng.GetBytes($b)
        return $set[$b[0] % $set.Length]
    }
    $chars = @(Pick $upper; Pick $lower; Pick $digit; Pick $sym)
    for ($i = 0; $i -lt 20; $i++) { $chars += (Pick $all) }
    # Fisher-Yates shuffle so the guaranteed-class chars are not always first.
    for ($i = $chars.Count - 1; $i -gt 0; $i--) {
        $b = New-Object 'byte[]' 1
        $rng.GetBytes($b)
        $j = $b[0] % ($i + 1)
        $tmp = $chars[$i]; $chars[$i] = $chars[$j]; $chars[$j] = $tmp
    }
    $rng.Dispose()
    return -join $chars
}

$plainPass = New-ServicePassword
$securePass = ConvertTo-SecureString $plainPass -AsPlainText -Force

# --- Create or update the local service account ------------------------------
Write-Step "Creating/updating local service account '$SvcUser'..."
$existing = Get-LocalUser -Name $SvcUser -ErrorAction SilentlyContinue
if ($existing) {
    Set-LocalUser -Name $SvcUser -Password $securePass -PasswordNeverExpires $true
    Enable-LocalUser -Name $SvcUser -ErrorAction SilentlyContinue
    Write-Ok "Existing account '$SvcUser' updated (password reset)."
} else {
    # NOT: New-LocalUser -Description en fazla 48 karakter kabul eder; daha uzunu
    # "character length ... too long" ile patlar. Bu metni 48 altinda tut.
    New-LocalUser -Name $SvcUser -Password $securePass `
        -FullName "Envisoft WebX Service" `
        -Description "Envisoft WebX service account (auto-login)" `
        -PasswordNeverExpires -AccountNeverExpires | Out-Null
    Write-Ok "Account '$SvcUser' created."
}

# Layer 1: the account may not change its own password (blocks the casual
# Settings / Ctrl-Alt-Del change). net user switches are locale-independent.
& net.exe user $SvcUser /passwordchg:no /active:yes /expires:never *> $null

# Add to the local Administrators group (needed: WSL/Docker management, the
# keepalive task's RunLevel Highest, and netsh/firewall in sais-stack.ps1).
# Resolve the group by well-known SID so this works on localized Windows
# (e.g. "Yoneticiler" on Turkish installs).
$adminGroup = (Get-LocalGroup -SID 'S-1-5-32-544').Name
$alreadyMember = Get-LocalGroupMember -Group $adminGroup -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "*\$SvcUser" -or $_.Name -eq $SvcUser }
if (-not $alreadyMember) {
    Add-LocalGroupMember -Group $adminGroup -Member $SvcUser -ErrorAction SilentlyContinue
}
Write-Ok "Account '$SvcUser' is a member of '$adminGroup'."

# --- Windows auto-login for the service account ------------------------------
# Source of truth for the password is the registry DefaultPassword from here on
# (the keepalive self-heal re-asserts the account password to match it).
Write-Step "Configuring Windows auto-login for '$SvcUser'..."
$winlogon = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
Set-ItemProperty -Path $winlogon -Name "AutoAdminLogon"    -Value "1"             -Type String
Set-ItemProperty -Path $winlogon -Name "DefaultUserName"   -Value $SvcUser        -Type String
Set-ItemProperty -Path $winlogon -Name "DefaultPassword"   -Value $plainPass      -Type String
Set-ItemProperty -Path $winlogon -Name "DefaultDomainName" -Value $env:COMPUTERNAME -Type String
Set-ItemProperty -Path $winlogon -Name "ForceAutoLogon"    -Value "0"             -Type String
Write-Ok "Auto-login enabled for $($env:COMPUTERNAME)\$SvcUser."

# --- Enable WSL2 Windows features (system-wide) ------------------------------
# Done here as the installing admin so the service account's `wsl --install`
# (in 00-ensure-docker, after the reboot) does not need to enable features.
#
# Field incident (reinstall after uninstall): if setup is launched from the
# still-open session of the DELETED service account (uninstall removed it, the
# session stayed logged in, setup recreated the account with a NEW SID), DISM
# returns "access denied" (COMException) even though the process is elevated
# (the HKLM writes above succeed). In that ghost-session state we cannot fix
# DISM - but on a REINSTALL WSL is typically already functional, so the
# features are clearly enabled: warn and continue instead of failing the
# whole install. If WSL does not work either, fail with a clear reboot hint.
Write-Step "Enabling WSL2 Windows features (system-wide)..."
$dismFailed = $false
foreach ($feature in @("Microsoft-Windows-Subsystem-Linux", "VirtualMachinePlatform")) {
    try {
        $state = (Get-WindowsOptionalFeature -Online -FeatureName $feature -ErrorAction Stop).State
        if ($state -ne "Enabled") {
            Write-Step "Enabling $feature ..."
            Enable-WindowsOptionalFeature -Online -FeatureName $feature -NoRestart -All -ErrorAction Stop | Out-Null
        }
    } catch {
        $dismFailed = $true
        Write-WarnLine "DISM failed for ${feature}: $($_.Exception.Message)"
    }
}
if ($dismFailed) {
    $wslWorks = $false
    try {
        wsl.exe --status *> $null
        if ($LASTEXITCODE -eq 0) { $wslWorks = $true }
    } catch { }
    if ($wslWorks) {
        Write-WarnLine "DISM query failed but WSL is already functional (reinstall) - continuing."
    } else {
        Write-Host ""
        Write-Host "================  INSTALL ERROR  ================" -ForegroundColor Red
        Write-Host "Could not enable the WSL2 Windows features (DISM: access denied)." -ForegroundColor Red
        Write-Host "If you launched setup right after an uninstall from the EnvisoftWebX" -ForegroundColor Red
        Write-Host "session, that session belongs to the DELETED account: REBOOT the" -ForegroundColor Red
        Write-Host "machine first, then run setup again from a fresh session." -ForegroundColor Red
        exit 1
    }
}
Write-Ok "WSL2 features enabled (a reboot activates them + switches to '$SvcUser')."

exit 0
