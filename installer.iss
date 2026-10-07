; FastDL installer (Inno Setup). Built by build.py -> dist\FastDL-Setup.exe
; Per user, no admin: installs to %LOCALAPPDATA%\Programs\FastDL.

#ifndef AppVersion
  #define AppVersion "1.0"
#endif

[Setup]
AppId={{E73448EF-A59C-449F-BDEA-8CDE4711F725}
AppName=FastDL
AppVersion={#AppVersion}
AppPublisher=FastDL
DefaultDirName={localappdata}\Programs\FastDL
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=FastDL-Setup
SetupIconFile=build\fastdl.ico
UninstallDisplayIcon={app}\FastDL.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; a running FastDL holds its files: close it before replacing them
CloseApplications=force
RestartApplications=no
#ifdef SIGN
; build.py signs the setup and the uninstaller with your certificate (see build.py)
SignTool=signtool
SignedUninstaller=yes
#endif

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"
Name: helpers; Description: "Install ffmpeg, aria2 and Deno with winget (needed for YouTube and torrents)"

[Files]
Source: "dist\FastDL\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "extension\*"; DestDir: "{app}\extension"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\FastDL"; Filename: "{app}\FastDL.exe"
Name: "{autodesktop}\FastDL"; Filename: "{app}\FastDL.exe"; Tasks: desktopicon

[Run]
Filename: "{cmd}"; Parameters: "/c winget install --id Gyan.FFmpeg -e --silent --accept-source-agreements --accept-package-agreements"; StatusMsg: "Installing ffmpeg..."; Flags: runhidden waituntilterminated; Tasks: helpers
Filename: "{cmd}"; Parameters: "/c winget install --id aria2.aria2 -e --silent --accept-source-agreements --accept-package-agreements"; StatusMsg: "Installing aria2..."; Flags: runhidden waituntilterminated; Tasks: helpers
Filename: "{cmd}"; Parameters: "/c winget install --id DenoLand.Deno -e --silent --accept-source-agreements --accept-package-agreements"; StatusMsg: "Installing Deno..."; Flags: runhidden waituntilterminated; Tasks: helpers
Filename: "{win}\explorer.exe"; Parameters: """{app}\FastDL.exe"""; Description: "Start FastDL"; Flags: nowait postinstall skipifsilent
; a silent install is FastDL updating itself: start the new version when done.
; Through Explorer: FastDL must not inherit the installer's redirection guard (it blocks winget's tool links)
Filename: "{win}\explorer.exe"; Parameters: """{app}\FastDL.exe"""; Flags: nowait; Check: WizardSilent

[UninstallRun]
Filename: "taskkill"; Parameters: "/im FastDL.exe /f"; Flags: runhidden; RunOnceId: "StopFastDL"

[Registry]
; FastDL writes these itself when it starts; the uninstaller removes them again
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "FastDL"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Google\Chrome\NativeMessagingHosts\com.fastdl.launcher"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Microsoft\Edge\NativeMessagingHosts\com.fastdl.launcher"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\com.fastdl.launcher"; Flags: uninsdeletekey
