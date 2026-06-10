; ============================================================================
;  Envisoft WebX - Windows Installer (Inno Setup)
;
;  "next-next-next" install: sets up WSL2 + Docker CE, pulls the image from
;  GHCR, starts the compose stack, seeds initial data, registers a service.
;
;  Build:
;    iscc /DGHCR_USER=<user> /DGHCR_TOKEN=<read:packages PAT> ^
;         /DAPP_VERSION=v1.2.0 installer\sais_setup.iss
;
;  GHCR_TOKEN is injected at build time (never committed to the repo).
;  This file is ASCII-only (English) on purpose to avoid encoding issues.
; ============================================================================

#ifndef GHCR_USER
  #define GHCR_USER "firatbadur"
#endif
#ifndef GHCR_TOKEN
  #define GHCR_TOKEN "__INJECT_AT_BUILD__"
#endif
#ifndef GHCR_IMAGE
  #define GHCR_IMAGE "ghcr.io/firatbadur/sais_web"
#endif
#ifndef IMAGE_TAG
  #define IMAGE_TAG "stable"
#endif
#ifndef APP_VERSION
  #define APP_VERSION "dev"
#endif
#define WSL_DISTRO "Ubuntu"

[Setup]
AppName=Envisoft WebX
AppVersion={#APP_VERSION}
AppPublisher=Envisoft
DefaultDirName=C:\EnvisoftWebX
DefaultGroupName=Envisoft WebX
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=EnvisoftWebX-Setup-{#APP_VERSION}
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
; Corporate branding (Envisoft WebX)
SetupIconFile=assets\EnvisoftWebX.ico
WizardImageFile=assets\WizardImage.bmp
WizardSmallImageFile=assets\WizardSmallImage.bmp
UninstallDisplayIcon={app}\EnvisoftWebX.ico

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Files]
; Installer scripts and templates
Source: "scripts\*";   DestDir: "{app}\scripts";   Flags: recursesubdirs ignoreversion
Source: "templates\*"; DestDir: "{app}\templates"; Flags: recursesubdirs ignoreversion
; Production compose file (from repo root)
Source: "..\docker-compose.prod.yml"; DestDir: "{app}"; Flags: ignoreversion
; NSSM (placed into payload before build)
Source: "payload\nssm.exe"; DestDir: "{app}"; Flags: ignoreversion
; Brand icon (used by shortcuts + uninstall entry)
Source: "assets\EnvisoftWebX.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Envisoft WebX Dashboard"; Filename: "https://{code:GetDomain}/dashboard/"; IconFilename: "{app}\EnvisoftWebX.ico"
Name: "{group}\Uninstall Envisoft WebX"; Filename: "{uninstallexe}"
; Desktop shortcut that opens the dashboard in the default browser.
Name: "{commondesktop}\Envisoft WebX"; Filename: "https://{code:GetDomain}/dashboard/"; IconFilename: "{app}\EnvisoftWebX.ico"

[Run]
; No runhidden -> the install runs in a VISIBLE console; install.ps1 keeps the
; window open until Enter. waituntilterminated -> Inno waits for completion.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\install.ps1"" -AnswersFile ""{app}\install-answers.json"""; \
  StatusMsg: "Installing Docker, pulling images and starting the stack (watch the console window)..."; \
  Check: WriteAnswers; Flags: waituntilterminated

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\uninstall.ps1"" -InstallDir ""{app}"""; \
  Flags: runhidden waituntilterminated; RunOnceId: "EnvisoftWebXDown"

[Code]
var
  LicensePage: TInputQueryWizardPage;
  DbPage: TInputQueryWizardPage;
  SitePage: TInputQueryWizardPage;
  AdminPage: TInputQueryWizardPage;
  WinPage: TInputQueryWizardPage;
  TlsCombo: TNewComboBox;

procedure InitializeWizard;
begin
  { License }
  LicensePage := CreateInputQueryPage(wpSelectDir,
    'License', 'Installation license information',
    'Enter the signed license information for this site.');
  LicensePage.Add('License Key (LICENSE_KEY):', False);
  LicensePage.Add('License Manifest URL (LICENSE_URL):', False);

  { Database }
  DbPage := CreateInputQueryPage(LicensePage.ID,
    'Database', 'SQL Server settings',
    'SA password for the bundled SQL Server Standard. Leave blank to auto-generate a strong one.');
  DbPage.Add('SA password (blank = auto):', True);
  DbPage.Add('SQL Server edition (MSSQL_PID):', False);
  DbPage.Values[1] := 'Standard';

  { Site / domain }
  SitePage := CreateInputQueryPage(DbPage.ID,
    'Web Access', 'Domain and SSL',
    'Public domain for external access. A DNS A record + 443 forwarding must be set up beforehand.');
  SitePage.Add('Domain (e.g. site1.envisoft.com.tr):', False);
  SitePage.Add('Let''s Encrypt email:', False);
  { TLS mode combo box }
  TlsCombo := TNewComboBox.Create(SitePage);
  TlsCombo.Parent := SitePage.Surface;
  TlsCombo.Style := csDropDownList;
  TlsCombo.Items.Add('letsencrypt');
  TlsCombo.Items.Add('internal');
  TlsCombo.Items.Add('manual');
  TlsCombo.ItemIndex := 0;
  TlsCombo.Top := SitePage.Edits[1].Top + SitePage.Edits[1].Height + 24;
  TlsCombo.Left := SitePage.Edits[1].Left;
  TlsCombo.Width := SitePage.Edits[1].Width;

  { Admin user }
  AdminPage := CreateInputQueryPage(SitePage.ID,
    'Administrator Account', 'First admin user',
    'System Administrator (role=1) account used to sign in to the dashboard.');
  AdminPage.Add('Username:', False);
  AdminPage.Add('Email:', False);
  AdminPage.Add('Password:', True);

  { Windows account for unattended auto-login. The stack runs in WSL, which only
    works in a logged-in interactive session; a Windows service (session 0)
    cannot reach the per-user WSL distro. So the machine auto-logs-in this
    account at boot and a logon task starts the stack - the dashboard comes up
    after a power cut WITHOUT anyone signing in. The password is stored in the
    registry (acceptable on a physically secured SCADA cabinet). }
  WinPage := CreateInputQueryPage(AdminPage.ID,
    'Automatic Startup', 'Windows auto-login (no operator needed)',
    'Windows account to auto-login at boot so the stack starts unattended (e.g. after a power outage). Leave the password blank to skip auto-login (you would then have to log in manually after a reboot).');
  WinPage.Add('Windows username (auto, locked):', False);
  WinPage.Add('Windows password:', True);
  { Auto-fill the username with the account running the installer and LOCK it:
    the auto-login account MUST be this user (the one whose WSL distro is
    registered), so it cannot be changed. }
  WinPage.Values[0] := GetEnv('USERNAME');
  WinPage.Edits[0].Enabled := False;
end;

function GetDomain(Param: String): String;
begin
  Result := SitePage.Values[0];
end;

function JsonEscape(const S: String): String;
begin
  Result := S;
  StringChangeEx(Result, '\', '\\', True);
  StringChangeEx(Result, '"', '\"', True);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;

  { Site page: domain is always required; Let's Encrypt also needs an email. }
  if CurPageID = SitePage.ID then begin
    if Trim(SitePage.Values[0]) = '' then begin
      MsgBox('A domain is required (e.g. site1.envisoft.com.tr).', mbError, MB_OK);
      Result := False;
    end else if Pos('.', SitePage.Values[0]) = 0 then begin
      MsgBox('The domain looks invalid (it must contain a dot).', mbError, MB_OK);
      Result := False;
    end else if (TlsCombo.Text = 'letsencrypt') and (Trim(SitePage.Values[1]) = '') then begin
      MsgBox('A Let''s Encrypt email is required when the TLS mode is letsencrypt.', mbError, MB_OK);
      Result := False;
    end;
  end;

  { Admin page: username and password are required. }
  if CurPageID = AdminPage.ID then begin
    if Trim(AdminPage.Values[0]) = '' then begin
      MsgBox('An administrator username is required.', mbError, MB_OK);
      Result := False;
    end else if Trim(AdminPage.Values[2]) = '' then begin
      MsgBox('An administrator password is required.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

{ Just before the install.ps1 [Run] entry (after files are copied), write the
  answers JSON. Returning True from the Check lets the entry run. }
function WriteAnswers: Boolean;
var
  json: String;
  path: String;
begin
  json :=
    '{' + #13#10 +
    '  "InstallDir": "' + JsonEscape(ExpandConstant('{app}')) + '",' + #13#10 +
    '  "Distro": "{#WSL_DISTRO}",' + #13#10 +
    '  "Domain": "' + JsonEscape(SitePage.Values[0]) + '",' + #13#10 +
    '  "TlsMode": "' + TlsCombo.Text + '",' + #13#10 +
    '  "LeEmail": "' + JsonEscape(SitePage.Values[1]) + '",' + #13#10 +
    '  "MssqlPassword": "' + JsonEscape(DbPage.Values[0]) + '",' + #13#10 +
    '  "MssqlPid": "' + JsonEscape(DbPage.Values[1]) + '",' + #13#10 +
    '  "LicenseKey": "' + JsonEscape(LicensePage.Values[0]) + '",' + #13#10 +
    '  "LicenseUrl": "' + JsonEscape(LicensePage.Values[1]) + '",' + #13#10 +
    '  "GhcrUser": "{#GHCR_USER}",' + #13#10 +
    '  "GhcrToken": "{#GHCR_TOKEN}",' + #13#10 +
    '  "GhcrImage": "{#GHCR_IMAGE}",' + #13#10 +
    '  "ImageTag": "{#IMAGE_TAG}",' + #13#10 +
    '  "AdminUser": "' + JsonEscape(AdminPage.Values[0]) + '",' + #13#10 +
    '  "AdminEmail": "' + JsonEscape(AdminPage.Values[1]) + '",' + #13#10 +
    '  "AdminPassword": "' + JsonEscape(AdminPage.Values[2]) + '",' + #13#10 +
    '  "WinUser": "' + JsonEscape(WinPage.Values[0]) + '",' + #13#10 +
    '  "WinPass": "' + JsonEscape(WinPage.Values[1]) + '"' + #13#10 +
    '}';
  path := ExpandConstant('{app}\install-answers.json');
  Result := SaveStringToFile(path, json, False);
end;
