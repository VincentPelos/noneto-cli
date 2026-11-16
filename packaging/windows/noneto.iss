; Compile via build_windows.py, which supplies the stable version and pinned mpv manifest.
#include "build-config.iss"
#ifndef TestBuild
  #define InstallId "Noneto.CLI.Windows"
  #define EnvironmentKey "Environment"
  #define ShortcutName "Noneto CLI"
#else
  #define InstallId "Noneto.CLI.PackagingTest"
  #define EnvironmentKey "Software\Noneto.CLI.PackagingTest\Environment"
  #define ShortcutName "Noneto CLI Packaging QA"
#endif

[Setup]
AppId={#InstallId}
AppName=Noneto CLI
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}.0
VersionInfoProductName=Noneto CLI
VersionInfoProductVersion={#AppVersion}
AppPublisher=VincentPelos
AppPublisherURL=https://github.com/VincentPelos/noneto-cli
AppSupportURL=https://github.com/VincentPelos/noneto-cli/issues
DefaultDirName={localappdata}\Programs\Noneto CLI
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
DisableProgramGroupPage=yes
WizardStyle=modern
LicenseFile={#SourceDir}\LICENSE
SetupIconFile={#SourceDir}\noneto\assets\noneto.ico
UninstallDisplayIcon={app}\noneto.exe
OutputDir={#OutputDir}
OutputBaseFilename=Noneto-CLI-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
ChangesEnvironment=yes
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "addtopath"; Description: "Make the noneto command available in new terminals"; Flags: checkedonce
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{tmp}\noneto-mpv\*"; DestDir: "{app}\mpv"; Flags: external ignoreversion recursesubdirs createallsubdirs; Check: InstallMpvFiles

[Icons]
Name: "{userprograms}\{#ShortcutName}"; Filename: "{app}\noneto.exe"; WorkingDir: "{app}"; AppUserModelID: "Noneto.CLI"; Check: not WizardNoIcons
Name: "{userdesktop}\{#ShortcutName}"; Filename: "{app}\noneto.exe"; WorkingDir: "{app}"; Tasks: desktopicon; AppUserModelID: "Noneto.CLI"; Check: not WizardNoIcons

[Run]
Filename: "{app}\noneto.exe"; Description: "Launch Noneto CLI"; Flags: postinstall nowait skipifsilent unchecked

[Code]
var
  DownloadPage: TDownloadWizardPage;
  NeedsMpv: Boolean;

function InstallMpvFiles: Boolean;
begin
  Result := NeedsMpv;
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage('Preparing audio playback',
    'Downloading mpv and checking its SHA-256. No TIDAL account is needed.', nil);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
  Stage: String;
begin
  Result := '';
  NeedsMpv := not FileExists(ExpandConstant('{app}\mpv\mpv.exe'));
  if not NeedsMpv then exit;
  DownloadPage.Clear;
  DownloadPage.Add('{#MpvUrl}', 'noneto-mpv.7z', '{#MpvSha256}');
  DownloadPage.Show;
  try
    try
      DownloadPage.Download;
      Stage := ExpandConstant('{tmp}\noneto-mpv');
      ForceDirectories(Stage);
      if not Exec(ExpandConstant('{sys}\tar.exe'),
          '-xf "' + ExpandConstant('{tmp}\noneto-mpv.7z') + '" -C "' + Stage + '"',
          '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then
        RaiseException('Could not start Windows tar.exe.');
      if (ExitCode <> 0) or not FileExists(Stage + '\mpv.exe') then
        RaiseException('The verified mpv archive could not be extracted.');
    except
      Result := 'Could not prepare mpv. Check your connection and retry. ' + GetExceptionMessage;
    end;
  finally
    DownloadPage.Hide;
  end;
end;

function HasPathEntry(Value, Entry: String): Boolean;
begin
  Result := Pos(';' + Lowercase(Entry) + ';', ';' + Lowercase(Value) + ';') > 0;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Value, Entry: String;
begin
  if (CurStep <> ssPostInstall) or not WizardIsTaskSelected('addtopath') then exit;
  Entry := ExpandConstant('{app}');
  RegQueryStringValue(HKCU, '{#EnvironmentKey}', 'Path', Value);
  if not HasPathEntry(Value, Entry) then begin
    RegWriteStringValue(HKCU, 'Software\{#InstallId}', 'PathBeforeInstall', Value);
    if (Value <> '') and (Copy(Value, Length(Value), 1) <> ';') then Value := Value + ';';
    if not RegWriteStringValue(HKCU, '{#EnvironmentKey}', 'Path', Value + Entry) then
      RaiseException('Could not update your user PATH.');
    RegWriteStringValue(HKCU, 'Software\{#InstallId}', 'PathAfterInstall', Value + Entry);
    RegWriteDWordValue(HKCU, 'Software\{#InstallId}', 'OwnsPathEntry', 1);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Value, Entry, Updated, Item, BeforeValue, AfterValue: String;
  Owned: Cardinal;
  Split: Integer;
begin
  if CurUninstallStep <> usPostUninstall then exit;
  if RegQueryDWordValue(HKCU, 'Software\{#InstallId}', 'OwnsPathEntry', Owned) and (Owned = 1) then begin
    Entry := ExpandConstant('{app}');
    RegQueryStringValue(HKCU, '{#EnvironmentKey}', 'Path', Value);
    RegQueryStringValue(HKCU, 'Software\{#InstallId}', 'PathBeforeInstall', BeforeValue);
    RegQueryStringValue(HKCU, 'Software\{#InstallId}', 'PathAfterInstall', AfterValue);
    Updated := '';
    while Value <> '' do begin
      Split := Pos(';', Value);
      if Split = 0 then Split := Length(Value) + 1;
      Item := Copy(Value, 1, Split - 1);
      Delete(Value, 1, Split);
      if CompareText(Item, Entry) <> 0 then begin
        if Updated <> '' then Updated := Updated + ';';
        Updated := Updated + Item;
      end;
    end;
    RegQueryStringValue(HKCU, '{#EnvironmentKey}', 'Path', Value);
    if Value = AfterValue then Updated := BeforeValue;
    RegWriteStringValue(HKCU, '{#EnvironmentKey}', 'Path', Updated);
  end;
  RegDeleteValue(HKCU, 'Software\{#InstallId}', 'OwnsPathEntry');
  RegDeleteValue(HKCU, 'Software\{#InstallId}', 'PathBeforeInstall');
  RegDeleteValue(HKCU, 'Software\{#InstallId}', 'PathAfterInstall');
  RegDeleteKeyIfEmpty(HKCU, 'Software\{#InstallId}');
end;
