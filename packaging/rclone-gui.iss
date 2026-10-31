; Inno Setup script. Run through packaging/build.ps1, which supplies AppVersion.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{7C1E2B74-5D0A-4E8B-9C36-2F4B8A6D1E90}
AppName=rClone GUI
AppVersion={#AppVersion}
AppPublisher=Joe Oghali
DefaultDirName={localappdata}\Programs\rClone GUI
DefaultGroupName=rClone GUI
DisableProgramGroupPage=yes
; Per-user: no UAC prompt, matching the HKCU autostart and per-user rclone config.
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=rClone-GUI-Setup
SetupIconFile=..\desktop\assets\icon.ico
UninstallDisplayIcon={app}\rClone GUI.exe
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
; Held by the tray and server (desktop/tray.py, desktop/server.py).
AppMutex=Local\rCloneGUI-tray,Local\rCloneGUI-server
CloseApplications=yes

[Tasks]
Name: "startup"; Description: "Start rClone GUI when I log in, so scheduled syncs keep running"
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\rClone GUI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\rClone GUI"; Filename: "{app}\rClone GUI.exe"
Name: "{autodesktop}\rClone GUI"; Filename: "{app}\rClone GUI.exe"; Tasks: desktopicon

[Registry]
; Keep the value name in sync with paths.APP_NAME (desktop/autostart.py).
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "rClone GUI"; ValueData: """{app}\rClone GUI.exe"" --background"; Flags: uninsdeletevalue; Tasks: startup
; Removes the value on uninstall even if the tray menu was what enabled it.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "rClone GUI"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\rClone GUI.exe"; Description: "Launch rClone GUI"; Flags: nowait postinstall skipifsilent
