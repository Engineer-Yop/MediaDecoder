; Inno Setup 6 script - compiled by build.ps1 into installer\MediaDecoder-Setup-1.0.0.exe
#ifndef SourceDir
  #define SourceDir "dist\MediaDecoder"
#endif
#define AppVersion "1.1.0"

[Setup]
AppId={{6B0E5C1A-3E77-4F7B-9D3B-0C9C2E8B7A11}
AppName=MediaDecoder
AppVersion={#AppVersion}
AppPublisher=MediaDecoder
DefaultDirName={autopf}\MediaDecoder
DefaultGroupName=MediaDecoder
UninstallDisplayIcon={app}\MediaDecoder.exe
OutputDir=installer
OutputBaseFilename=MediaDecoder-Setup-{#AppVersion}
SetupIconFile=assets\icon.ico
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\MediaDecoder"; Filename: "{app}\MediaDecoder.exe"
Name: "{group}\Uninstall MediaDecoder"; Filename: "{uninstallexe}"
Name: "{autodesktop}\MediaDecoder"; Filename: "{app}\MediaDecoder.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MediaDecoder.exe"; Description: "Launch MediaDecoder"; Flags: nowait postinstall skipifsilent
