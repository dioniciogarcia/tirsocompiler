; Tirso Compiler - Windows installer (Inno Setup 6).
; Made by "Make Installer.bat" (on your PC) or by the GitHub build; you don't need to open this file.
;
; Two kinds of installer come out of this one script:
;   TirsoCompilerSetup.exe   everything (first install, or a new PC)
;   TirsoCompilerUpdate.exe  the program and the Android templates, without the big tools
;                            (Java, Apktool, signer), which an update leaves exactly as they are.
; Both use the same AppId, so running either one over an existing install updates it in place.

#define AppName "Tirso Compiler"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef OutputName
  #define OutputName "Tirso Compiler Setup " + AppVersion
#endif
#ifndef SrcDir
  #define SrcDir "..\build\dist\Tirso Compiler"
#endif
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
OutputBaseFilename={#OutputName}
SetupIconFile=tirso.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
LicenseFile={#SrcDir}\LICENSES.txt
; When Tirso updates itself it closes first and starts this installer with /relaunch=1, which opens it again afterwards.
; Windows must not also restart it (that would open two copies).
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: RelaunchRequested

; Your games, BIOS copies, signing key and settings are kept in %LOCALAPPDATA%\TirsoCompiler.
; Installing, updating or uninstalling does not touch them, so your library and your signing key survive.

[Code]
function RelaunchRequested: Boolean;
begin
  Result := ExpandConstant('{param:relaunch|0}') = '1';
end;

