; UPS Tray installer (Inno Setup 6).
;
; Built by build.ps1, which first creates build\app\UpsTray\ (the
; PyInstaller onedir app) and build\icons\upstray.ico, then runs:
;   ISCC.exe /DAppVersion=x.y.z /DSourceRoot=<repository> installer\UpsTray.iss
;
; install:   stop a running UPS Tray -> copy files -> optional logon entry
;            (HKCU Run) -> start UPS Tray as the original user.
; uninstall: stop UPS Tray -> remove program files, logon entry and shortcuts.
;            Settings and log in %LOCALAPPDATA%\UpsTray are kept.

#ifndef AppVersion
  #error AppVersion is not defined. Build with build.ps1 (or pass /DAppVersion=x.y.z).
#endif
#ifndef SourceRoot
  #define SourceRoot AddBackslash(SourcePath) + ".."
#endif
; Releases need a current Inno Setup: 6.5-6.7 hardened Setup and Uninstall
; against temp-folder, junction and symlink redirection. build.ps1 passes
; /DAllowOldInno=1 only for local test builds (-AllowOldInnoSetup).
#ifndef AllowOldInno
  #if Ver < EncodeVer(6,7,0)
    #error Inno Setup 6.7.0 or newer is required for a release build (build.ps1 -AllowOldInnoSetup makes a local test build).
  #endif
#endif

#define AppName "UpsTray"
#define AppDisplayName "UPS Tray"
#define AppExeName "UpsTray.exe"
#define AppPublisher "Usama Masood"
#define AppURL "https://github.com/n3rd-bugs/UpsTray"

[Setup]
; Never change AppId: it ties upgrades and the uninstaller together.
AppId={{3DE2B0BC-3B32-489F-BDE5-E854534916BD}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppDisplayName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
AppCopyright=Copyright (c) 2026 {#AppPublisher}
VersionInfoVersion={#AppVersion}
VersionInfoProductVersion={#AppVersion}
VersionInfoDescription={#AppDisplayName} Setup
DefaultDirName={autopf}\{#AppName}
DisableDirPage=auto
DisableProgramGroupPage=yes
DisableWelcomePage=no
LicenseFile={#SourceRoot}\LICENSE
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
#if Ver >= EncodeVer(6,3,0)
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
#else
; Inno Setup before 6.3 has no x64compatible; x64 is its equivalent here.
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
#endif
MinVersion=10.0
OutputDir={#SourceRoot}\dist
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
SetupIconFile={#SourceRoot}\build\icons\upstray.ico
UninstallDisplayIcon={app}\app\{#AppExeName}
UninstallDisplayName={#AppDisplayName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; Restart Manager may close an UpsTray.exe that still uses the files, but
; must never restart it: Setup may have been explicitly run elevated.
CloseApplications=yes
RestartApplications=no
; The logon entry and shortcuts belong to the installing account.
UsedUserAreasWarning=no
SetupLogging=yes

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nUPS Tray is set up for your Windows account and does not need administrator rights. You can choose whether it starts when you sign in.%n%nIt is recommended that you close all other applications before continuing.
UninstalledAll=%1 was successfully removed from your computer.%n%nYour UPS Tray settings and log in your profile (AppData\Local\UpsTray) were kept. Delete that folder if you no longer need them.
UninstalledMost=%1 uninstall complete.%n%nSome elements could not be removed. These can be removed manually.%n%nYour UPS Tray settings and log in your profile (AppData\Local\UpsTray) were kept.

[Tasks]
Name: "autostart"; Description: "Start UPS Tray when I sign in"
Name: "desktopicon"; Description: "Create a desktop icon"; Flags: unchecked

[InstallDelete]
; the PyInstaller bundle's file list changes between versions: start clean
Type: filesandordirs; Name: "{app}\app"

[Files]
Source: "{#SourceRoot}\build\app\UpsTray\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceRoot}\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "{#SourceRoot}\build\THIRD-PARTY-NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Registry]
; Always record removal, including when the app creates the value itself.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "{#AppName}"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\app\{#AppExeName}"""; Flags: uninsdeletevalue; Tasks: autostart

[Icons]
Name: "{userprograms}\{#AppDisplayName}"; Filename: "{app}\app\{#AppExeName}"; Comment: "Start UPS Tray, or show its details when it is already running"
Name: "{userdesktop}\{#AppDisplayName}"; Filename: "{app}\app\{#AppExeName}"; Comment: "Show APC UPS readings in the notification area"; Tasks: desktopicon

[Run]
Filename: "{app}\app\{#AppExeName}"; Description: "Start UPS Tray"; Flags: postinstall nowait skipifsilent runasoriginaluser

[UninstallDelete]
Type: filesandordirs; Name: "{app}\app"
Type: dirifempty; Name: "{app}"

[Code]
const
  { Identical in every UPS Tray version: the hidden window's class and
    the single-instance mutex also detect copies running from other paths. }
  InstanceWindowClass = 'UpsTray.Window';
  InstanceMutex = 'Local\UpsTray';
  StopTimeoutMs = 10000;

function InstanceRunning(): Boolean;
begin
  Result := (FindWindowByClassName(InstanceWindowClass) <> 0) or CheckForMutexes(InstanceMutex);
end;

{ The installed app implements the clean-exit protocol. Check both instance
  markers afterwards, including if the exe is absent or could not be run. }
function StopRunningInstance(TimeoutMs: Integer): Boolean;
var
  AppExe: String;
  ResultCode, Waited: Integer;
begin
  AppExe := ExpandConstant('{app}\app\{#AppExeName}');
  if FileExists(AppExe) then
  begin
    Log('Asking UPS Tray to exit (--quit).');
    ResultCode := -1;
    if Exec(AppExe, '--quit', ExtractFileDir(AppExe), SW_HIDE,
        ewWaitUntilTerminated, ResultCode) then
      Log(Format('UPS Tray --quit exit code %d.', [ResultCode]))
    else
      Log(Format('Could not run UPS Tray --quit (error %d).', [ResultCode]));
  end;
  Waited := 0;
  while InstanceRunning() and (Waited < TimeoutMs) do
  begin
    Sleep(250);
    Waited := Waited + 250;
  end;
  Result := not InstanceRunning();
  if Result then
    Log('UPS Tray is stopped.')
  else
    Log('UPS Tray is still running.');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not StopRunningInstance(StopTimeoutMs) then
    Result := 'UPS Tray is still running and did not exit. ' +
      'Exit it from its tray icon (right-click, Exit), then run Setup again.';
end;

function InitializeUninstall(): Boolean;
begin
  Result := StopRunningInstance(StopTimeoutMs);
  if not Result then
    SuppressibleMsgBox('UPS Tray is still running and did not exit. ' +
      'Exit it from its tray icon (right-click, Exit), then run Uninstall again.',
      mbError, MB_OK, IDOK);
end;
