; Inno Setup script - builds dist\KhmerPDF-Setup-<version>.exe
; Build:  iscc /DAppVersion=0.1.0 installer\khmerpdf.iss

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define AppName "KhmerPDF Editor"
#define AppExe "KhmerPDF.exe"

[Setup]
AppId={{6B3E1C52-7A0F-4E8B-9C1D-2F5A8E4B7C31}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Footprints International School
DefaultDirName={autopf}\KhmerPDF Editor
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=KhmerPDF-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Per-user install by default (no admin needed); the user can choose all users.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ChangesAssociations=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "openwith"; Description: "Add KhmerPDF Editor to ""Open with"" for PDF files"; GroupDescription: "Integration:"

[Files]
Source: "..\dist\KhmerPDF\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Registers in "Open with" without taking over the user's default PDF app.
Root: HKA; Subkey: "Software\Classes\KhmerPDF.Document"; ValueType: string; ValueData: "PDF Document"; Flags: uninsdeletekey; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\KhmerPDF.Document\DefaultIcon"; ValueType: string; ValueData: "{app}\{#AppExe},0"; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\KhmerPDF.Document\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: openwith
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "KhmerPDF.Document"; ValueData: ""; Flags: uninsdeletevalue; Tasks: openwith

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
