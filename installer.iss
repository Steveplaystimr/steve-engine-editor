#define AppName "Steve Engine Editor"
#define AppVersion "1.2.2.1"
#define AppPublisher "StevePlaysTimr"
#define AppId "{{B395A2AB-762B-44E8-A70B-9F4793B3D0FD}"
#define AppExeName "engine-editor.exe"
#define PayloadDir AddBackslash(SourcePath) + "dist\engine-editor"
#define PlayerPayloadDir AddBackslash(SourcePath) + "dist\steve-game-player"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\Steve Engine Editor
DisableDirPage=no
PrivilegesRequired=admin
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=Steve-Engine-Editor-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
; Removing "Flags: unchecked" makes this shortcut checkbox turned ON by default
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Excludes: "_internal\engine_editor\engine_native.dll"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PlayerPayloadDir}\*"; DestDir: "{app}\player"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "engine_editor\engine_native.dll"; DestDir: "{app}\_internal\engine_editor"; Flags: ignoreversion
Source: "engine_editor\engine_physics_native.dll"; DestDir: "{app}\_internal\engine_editor"; Flags: ignoreversion
Source: "mods\*"; DestDir: "{app}\mods"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "MODDING.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.txt"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}\mods"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autoprograms}\Steve Game Player"; Filename: "{app}\player\steve-game-player.exe"; WorkingDir: "{app}\player"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
