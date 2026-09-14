; livecap installer - ships the app and downloads the models on first run.
; Build with:  ISCC.exe packaging\installer.iss
; Payload: dist\livecap (exe + _internal + tools\ffmpeg). The models (~4 GB) are
; NOT bundled; the installer offers to download them straight after installing.

#define AppName        "日文直播实时字幕"
#define AppNameEn      "livecap"
#define AppVersion     "1.0.0"
#define AppPublisher   "livecap"
#define AppExe         "livecap.exe"
#define CliExe         "livecap-cli.exe"
#define SourceDir      "..\dist\livecap"

[Setup]
AppId={{8F3C1A64-2B7E-4E1D-9C55-1A2B3C4D5E6F}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppNameEn}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename={#AppNameEn}-setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; per-user install: no UAC prompt, which makes the installer easy to pass around
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
MinVersion=10.0

[Languages]
Name: "chinese"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "downloadmodels"; Description: "安装后下载模型（约 4GB，不下就没有字幕）"; GroupDescription: "模型"; Flags: checkedonce

[Files]
Source: "{#SourceDir}\{#AppExe}";    DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\{#CliExe}";    DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\README.md";    DestDir: "{app}"; Flags: ignoreversion
Source: "使用说明.txt";               DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\_internal\*";  DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\tools\*";      DestDir: "{app}\tools";     Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}";                 Filename: "{app}\{#AppExe}"
Name: "{group}\使用说明";                    Filename: "{app}\使用说明.txt"
Name: "{group}\下载模型（首次必做）";        Filename: "{app}\{#CliExe}"; Parameters: "--download-models"
Name: "{group}\卸载 {#AppName}";            Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";           Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; a console window, so the user can actually watch the download progress
Filename: "{app}\{#CliExe}"; Parameters: "--download-models"; \
  Description: "下载模型（约 4GB，可中断，下次会继续）"; \
  Flags: postinstall nowait shellexec; Tasks: downloadmodels
Filename: "{app}\{#AppExe}"; Description: "启动 {#AppName}"; \
  Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\models"
Type: files;          Name: "{app}\settings.json"
Type: files;          Name: "{app}\settings.json.bak"
Type: files;          Name: "{app}\livecap.log"
Type: files;          Name: "{app}\livecap_crash.log"

[Code]
// Warn (once) when the user skipped the model download.
// WizardSilent matters: /SUPPRESSMSGBOXES does not cover MsgBox from [Code],
// so an unguarded call would hang an unattended install forever.
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall)
     and (not WizardIsTaskSelected('downloadmodels'))
     and (not WizardSilent) then
    MsgBox('没有下载模型，程序还不能出字幕。' + #13#10 +
           '随时可以从开始菜单点「下载模型（首次必做）」，' +
           '或者在程序面板里点「检查 / 下载模型」。', mbInformation, MB_OK);
end;
