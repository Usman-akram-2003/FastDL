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
Name: helpers; Description: "Download ffmpeg, aria2 and Deno in the background (needed for YouTube and torrents; about 150 MB)"

[Files]
Source: "dist\FastDL\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "extension\*"; DestDir: "{app}\extension"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\FastDL"; Filename: "{app}\FastDL.exe"
Name: "{autodesktop}\FastDL"; Filename: "{app}\FastDL.exe"; Tasks: desktopicon

[Run]
; the app window needs Microsoft's WebView2 runtime (Windows 11 and most Windows 10 have it; a clean one may not)
Filename: "{app}\FastDL.exe"; Parameters: "--install-webview2"; StatusMsg: "Installing Microsoft Edge WebView2 (the app window needs it)..."; Flags: runhidden waituntilterminated; Check: WebView2Missing
; FastDL fetches whichever helper is missing: with winget when the PC has it, else from the official releases
; nowait: the installer finishes at once (a slow connection made the wizard look frozen for minutes); the download goes on in
; the background, and the app fetches any helper still missing the first time it's needed (torrent, video)
Filename: "{app}\FastDL.exe"; Parameters: "--install-tools"; Flags: runhidden nowait; Tasks: helpers
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
Root: HKCU; Subkey: "Software\Mozilla\NativeMessagingHosts\com.fastdl.launcher"; Flags: uninsdeletekey

[Code]
// The same registry check FastDL (and its window library) make: is a WebView2 runtime installed, for this user or all?
function WebView2Has(Root: Integer; Subkey: String): Boolean;
var Version: String;
begin
  Result := RegQueryStringValue(Root, Subkey, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0');
end;

function WebView2Missing: Boolean;
var Key: String;
begin
  Key := 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result := not (WebView2Has(HKLM32, Key) or WebView2Has(HKCU, Key));
end;
