; ============================================================================
;  SAIS SCADA — Windows Installer (Inno Setup)
;
;  "next-next-next" kurulum: WSL2 + Docker CE'yi kurar, GHCR'dan image çeker,
;  compose yığınını başlatır, ilk veriyi tohumlar, Windows servisi kaydeder.
;
;  Derleme:
;    iscc /DGHCR_USER=<kullanici> /DGHCR_TOKEN=<read:packages PAT> ^
;         /DAPP_VERSION=v1.2.0 installer\sais_setup.iss
;
;  GHCR_TOKEN derleme sırasında enjekte edilir (repo'ya commitlenmez).
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
AppName=SAIS SCADA
AppVersion={#APP_VERSION}
AppPublisher=Envisoft
DefaultDirName=C:\SAIS
DefaultGroupName=SAIS SCADA
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=sais-setup-{#APP_VERSION}
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Languages]
Name: "tr"; MessagesFile: "compiler:Languages\Turkish.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Files]
; Installer script'leri ve şablonlar
Source: "scripts\*";   DestDir: "{app}\scripts";   Flags: recursesubdirs ignoreversion
Source: "templates\*"; DestDir: "{app}\templates"; Flags: recursesubdirs ignoreversion
; Saha compose dosyası (repo kökünden)
Source: "..\docker-compose.prod.yml"; DestDir: "{app}"; Flags: ignoreversion
; NSSM (payload'a derleme öncesi yerleştirilir)
Source: "payload\nssm.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\SAIS Dashboard"; Filename: "https://{code:GetDomain}/dashboard/"
Name: "{group}\SAIS Kaldır"; Filename: "{uninstallexe}"

[Run]
; runhidden YOK — kurulum ilerlemesi ve olası hatalar GÖRÜNÜR konsolda akar
; (install.ps1 hata olursa pencereyi Enter'a kadar açık tutar).
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\install.ps1"" -AnswersFile ""{app}\install-answers.json"""; \
  StatusMsg: "Docker kuruluyor, image çekiliyor ve yığın başlatılıyor (konsol penceresini izleyin)..."; \
  Check: WriteAnswers; Flags: waituntilterminated

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\uninstall.ps1"" -InstallDir ""{app}"""; \
  Flags: runhidden waituntilterminated; RunOnceId: "SAISDown"

[Code]
var
  LicensePage: TInputQueryWizardPage;
  DbPage: TInputQueryWizardPage;
  SitePage: TInputQueryWizardPage;
  AdminPage: TInputQueryWizardPage;
  TlsCombo: TNewComboBox;

procedure InitializeWizard;
begin
  { Lisans }
  LicensePage := CreateInputQueryPage(wpSelectDir,
    'Lisans', 'Kurulum lisans bilgileri',
    'Bu sahaya ait imzalı lisans bilgilerini girin.');
  LicensePage.Add('Lisans Anahtarı (LICENSE_KEY):', False);
  LicensePage.Add('Lisans Manifest URL (LICENSE_URL):', False);

  { Veritabanı }
  DbPage := CreateInputQueryPage(LicensePage.ID,
    'Veritabanı', 'SQL Server ayarları',
    'Bundled SQL Server Standard için SA şifresi. Boş bırakılırsa güçlü bir şifre üretilir.');
  DbPage.Add('SA Şifresi (boş = otomatik):', True);
  DbPage.Add('SQL Server Edition (MSSQL_PID):', False);
  DbPage.Values[1] := 'Standard';

  { Saha / domain }
  SitePage := CreateInputQueryPage(DbPage.ID,
    'Web Erişimi', 'Domain ve SSL',
    'Dışarıdan erişilecek domain. DNS A kaydı + 443 yönlendirmesi önceden yapılmalı.');
  SitePage.Add('Domain (örn. sais-tesis1.envisoft.com.tr):', False);
  SitePage.Add('Let''s Encrypt E-postası:', False);
  { TLS modu için combo box ekle }
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

  { Admin kullanıcı }
  AdminPage := CreateInputQueryPage(SitePage.ID,
    'Yönetici Hesabı', 'İlk admin kullanıcısı',
    'Dashboard''a giriş için Sistem Yöneticisi (rol=1) hesabı.');
  AdminPage.Add('Kullanıcı adı:', False);
  AdminPage.Add('E-posta:', False);
  AdminPage.Add('Şifre:', True);
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
  if CurPageID = SitePage.ID then begin
    if (TlsCombo.Text = 'letsencrypt') and (Trim(SitePage.Values[0]) = '') then begin
      MsgBox('Let''s Encrypt için domain zorunludur.', mbError, MB_OK);
      Result := False;
    end;
  end;
  if CurPageID = AdminPage.ID then begin
    if (Trim(AdminPage.Values[0]) = '') or (Trim(AdminPage.Values[2]) = '') then begin
      MsgBox('Yönetici kullanıcı adı ve şifresi zorunludur.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

{ install.ps1 [Run] entry'sinden hemen önce (dosyalar kopyalandıktan sonra)
  answers JSON'ını yaz. Check fonksiyonu True dönerek entry'nin çalışmasını sağlar. }
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
    '  "AdminPassword": "' + JsonEscape(AdminPage.Values[2]) + '"' + #13#10 +
    '}';
  path := ExpandConstant('{app}\install-answers.json');
  Result := SaveStringToFile(path, json, False);
end;
