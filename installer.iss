#define AppName "Steve Scratch Engine"
#define AppVersion "1.0.0"
#define AppPublisher "Steve Scratch Engine"
#define AppId "{{B395A2AB-762B-44E8-A70B-9F4793B3D0FD}"
#define AppExeName "steve-scratch-engine.exe"
#define PayloadDir AddBackslash(SourcePath) + "dist\steve-scratch-engine"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\Steve Scratch Engine
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=Steve-Scratch-Engine-Setup
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
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "mods\*"; DestDir: "{app}\mods"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "MODDING.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.txt"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}\mods"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
