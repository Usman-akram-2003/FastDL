# Runs inside Windows Sandbox (see fresh-pc.wsb): installs FastDL like a new user would and checks it works.
$ErrorActionPreference = "Continue"
$local = "C:\report.txt"      # written on the sandbox's own disk, then copied out: writes straight to the shared folder got lost
$out = "C:\fastdl-results"
New-Item -ItemType Directory -Force $out | Out-Null
"started $(Get-Date)" | Set-Content $local
function Log($m) {
  $line = "{0:HH:mm:ss}  {1}" -f (Get-Date), $m
  $line | Add-Content $local; Write-Host $line
  try { Copy-Item $local "$out\report.txt" -Force } catch {}
}
function Check($ok, $what) { Log ($(if ($ok) { "PASS  " } else { "FAIL  " }) + $what) }
function Api($path, $body) {
  $a = @{ Uri = "http://127.0.0.1:9614$path"; UseBasicParsing = $true; TimeoutSec = 20; Headers = @{ "X-FastDL" = "1" } }
  if ($body) { $a += @{ Method = "POST"; Body = ($body | ConvertTo-Json -Depth 5); ContentType = "application/json" } }
  try { return (Invoke-WebRequest @a).Content | ConvertFrom-Json } catch { return @{ error = $_.Exception.Message } }
}
function Has($n) { [bool](Get-Command $n -ErrorAction SilentlyContinue) }

try {
  $os = Get-CimInstance Win32_OperatingSystem
  Log "Windows: $($os.Caption) $($os.Version)"
  Log "winget present: $(Has winget)"
  Log "ffmpeg / aria2c / deno on PATH before install: $(Has ffmpeg) / $(Has aria2c) / $(Has deno)"
  Log "installer present: $(Test-Path C:\fastdl-dist\FastDL-Setup.exe)"

  # 1. install, as a user would (silent, helpers task on)
  $t = Get-Date
  $p = Start-Process C:\fastdl-dist\FastDL-Setup.exe -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/TASKS="helpers,desktopicon"' -PassThru -Wait
  Log ("installer exit code {0}, took {1:n0} s" -f $p.ExitCode, ((Get-Date) - $t).TotalSeconds)
  $app = "$env:LOCALAPPDATA\Programs\FastDL"
  Check (Test-Path "$app\FastDL.exe") "FastDL.exe installed in $app"
  Check (Test-Path "$app\extension\manifest.json") "browser extension folder installed"
  Check (Test-Path "$env:USERPROFILE\Desktop\FastDL.lnk") "desktop shortcut created"
  Check (Test-Path "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\FastDL.lnk") "Start menu entry created"
  foreach ($n in "aria2c", "ffmpeg", "deno") {
    $f = "$env:USERPROFILE\.fastdl\tools\$n.exe"
    Check ((Test-Path $f) -and (Get-Item $f).Length -gt 1MB) "helper downloaded by the installer: $n ($(if (Test-Path $f) { '{0:n1} MB' -f ((Get-Item $f).Length / 1MB) } else { 'missing' }))"
  }
  Log "after install: ffmpeg=$(Has ffmpeg) aria2c=$(Has aria2c) deno=$(Has deno) (this shell's PATH; FastDL also looks in winget's folders)"

  # 2. does it start by itself and answer
  $up = $false
  for ($i = 0; $i -lt 60 -and -not $up; $i++) { Start-Sleep 1; $r = Api "/api/settings"; $up = [bool]$r.version }
  Check $up "FastDL started by itself after the silent install (version $($r.version))"
  if (-not $up) {
    Log "starting it by hand..."
    Start-Process "$app\FastDL.exe"
    for ($i = 0; $i -lt 60 -and -not $up; $i++) { Start-Sleep 1; $r = Api "/api/settings"; $up = [bool]$r.version }
    Check $up "FastDL started when launched by hand"
  }
  Log "FastDL.exe running: $([bool](Get-Process FastDL -ErrorAction SilentlyContinue))"

  # 3. what FastDL registered for the browser extension and autostart
  $k = "HKCU:\Software"
  Check ((Get-ItemProperty "$k\Microsoft\Windows\CurrentVersion\Run" -ErrorAction SilentlyContinue).FastDL) "starts with Windows (Run key)"
  foreach ($b in "Google\Chrome", "Microsoft\Edge", "BraveSoftware\Brave-Browser", "Mozilla") {
    Check (Test-Path "$k\$b\NativeMessagingHosts\com.fastdl.launcher") "browser launcher registered: $b"
  }

  # 4. a real download
  New-Item -ItemType Directory -Force C:\Users\WDAGUtilityAccount\Downloads | Out-Null
  $f = Api "/api/downloads" @{ url = "https://proof.ovh.net/files/10Mb.dat"; folder = "C:\Users\WDAGUtilityAccount\Downloads"; quiet = $true }
  if ($f.id) {
    for ($i = 0; $i -lt 90; $i++) { Start-Sleep 1; $d = (Api "/api/downloads") | Where-Object id -eq $f.id; if ($d.status -in "done", "error") { break } }
    Check ($d.status -eq "done" -and $d.size -eq 10485760) "downloaded a 10 MB file from the internet (status $($d.status) $($d.error))"
  } else { Check $false "could not add a download: $($f.error)" }

  # 5. video and torrent: need ffmpeg / deno / aria2 (what the helpers task is for)
  $v = Api "/api/formats" @{ url = "https://www.youtube.com/watch?v=jNQXAC9IVRw" }
  Log ("video quality list: {0} choices; error: {1}" -f @($v.items).Count, $v.error)
  if ("$($v.error)" -match "not a bot|Sign in to confirm") {
    Log "SKIP  video qualities: YouTube's own bot check blocked this connection (not a FastDL problem; happens after many requests from one address)"
  } else {
    Check (@($v.items | Where-Object { $_.label -match "p" }).Count -gt 0) "a video page lists its qualities"
  }
  $m = Api "/api/downloads" @{ url = "magnet:?xt=urn:btih:dd8255ecdc7ca55fb0bbf81323d87062db1f6d1c&dn=Big+Buck+Bunny"; quiet = $true; folder = "C:\Users\WDAGUtilityAccount\Downloads" }
  Start-Sleep 12
  $t2 = (Api "/api/downloads") | Where-Object id -eq $m.id
  Check ($t2.status -ne "error") "a torrent starts (status $($t2.status) $($t2.error))"

  # 6. update check, then uninstall
  $u = Api "/api/update/check" @{}
  Log "update check: current $($u.current); error: $($u.error); newer: $($u.update.version)"
  Get-Process FastDL -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Sleep 2
  $un = Get-ChildItem $app -Filter "unins*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($un) {
    Start-Process $un.FullName -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES' -Wait; Start-Sleep 3
    Check (-not (Test-Path "$app\FastDL.exe")) "uninstall removed the program"
    Check (-not (Test-Path "$k\Mozilla\NativeMessagingHosts\com.fastdl.launcher")) "uninstall removed the Firefox launcher entry"
    Check (-not (Get-ItemProperty "$k\Microsoft\Windows\CurrentVersion\Run" -ErrorAction SilentlyContinue).FastDL) "uninstall removed autostart"
  } else { Check $false "uninstaller not found" }
} catch {
  Log "SCRIPT ERROR: $($_.Exception.Message) at line $($_.InvocationInfo.ScriptLineNumber)"
}
Log "DONE"
"DONE" | Set-Content "$out\done.txt"
