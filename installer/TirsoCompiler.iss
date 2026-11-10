; Tirso Compiler - Windows installer (Inno Setup 6).
; Made by "Make Installer.bat"; you don't need to open this file.

#define AppName "Tirso Compiler"
#define AppVersion "1.0.0"
#define AppExe "Tirso Compiler.exe"

[Setup]
AppId={{6E1B2C55-4F6A-4D7E-9B39-7A1C0F3E5D21}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Tirso
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\Installer
OutputBaseFilename=Tirso Compiler Setup {#AppVersion}
SetupIconFile=tirso.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
LicenseFile=..\build\dist\Tirso Compiler\LICENSES.txt

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\build\dist\Tirso Compiler\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

; Your games, BIOS copies, signing key and settings are kept in %LOCALAPPDATA%\TirsoCompiler.
; Uninstalling does not delete them, so a reinstall keeps your library and your signing key.
