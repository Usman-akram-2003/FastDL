# Runs inside Windows Sandbox (see scoop.wsb): installs FastDL the way a Scoop user would.
$ErrorActionPreference = "Continue"
$local = "C:\scoop-report.txt"
$out = "C:\fastdl-results"
New-Item -ItemType Directory -Force $out | Out-Null
"started $(Get-Date)" | Set-Content $local
function Log($m) {
  $line = "{0:HH:mm:ss}  {1}" -f (Get-Date), $m
  $line | Add-Content $local; Write-Host $line
  try { Copy-Item $local "$out\scoop-report.txt" -Force } catch {}
}
function Check($ok, $what) { Log ($(if ($ok) { "PASS  " } else { "FAIL  " }) + $what) }
function Api($path) { try { return (Invoke-WebRequest "http://127.0.0.1:9614$path" -UseBasicParsing -TimeoutSec 10 -Headers @{ "X-FastDL" = "1" }).Content | ConvertFrom-Json } catch { return @{ error = $_.Exception.Message } } }

try {
  Remove-Item "$out\scoop-done.txt" -ErrorAction SilentlyContinue
  Log "Windows: $((Get-CimInstance Win32_OperatingSystem).Caption)"
  # 1. Scoop itself (the sandbox user is an administrator, hence -RunAsAdmin; normal users don't need it)
  Invoke-Expression "& {$(Invoke-RestMethod https://get.scoop.sh)} -RunAsAdmin" 2>&1 | Out-Null
  $scoop = "$env:USERPROFILE\scoop\shims\scoop.ps1"
  Check (Test-Path $scoop) "Scoop installed"
  $env:PATH += ";$env:USERPROFILE\scoop\shims"
  # 2. git (Scoop needs it to add a bucket), then our bucket from the GitHub repo, then FastDL
  scoop install git 2>&1 | Select-Object -Last 2 | ForEach-Object { Log "scoop git: $_" }
  scoop bucket add fastdl https://github.com/Usman-akram-2003/FastDL 2>&1 | ForEach-Object { Log "bucket add: $_" }
  Check ((scoop bucket list | Out-String) -match "fastdl") "bucket added"
  $t = Get-Date
  scoop install fastdl 2>&1 | ForEach-Object { Log "install: $_" }
  Log ("scoop install took {0:n0} s" -f ((Get-Date) - $t).TotalSeconds)
  $app = "$env:LOCALAPPDATA\Programs\FastDL"
  Check (Test-Path "$app\FastDL.exe") "FastDL.exe installed in $app"
  $up = $false
  for ($i = 0; $i -lt 90 -and -not $up; $i++) { Start-Sleep 1; $r = Api "/api/settings"; $up = [bool]$r.version }
  Check $up "FastDL is running and answers (version $($r.version))"
  Check ([bool](Get-ItemProperty "HKCU:\Software\Mozilla\NativeMessagingHosts\com.fastdl.launcher" -ErrorAction SilentlyContinue)) "browser launchers registered"
  Check (-not ((scoop list | Out-String) -notmatch "fastdl")) "scoop lists fastdl"
  # 3. uninstall
  Get-Process FastDL -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Sleep 2
  scoop uninstall fastdl 2>&1 | ForEach-Object { Log "uninstall: $_" }
  Start-Sleep 3
  Check (-not (Test-Path "$app\FastDL.exe")) "scoop uninstall removed the program"
} catch {
  Log "SCRIPT ERROR: $($_.Exception.Message) at line $($_.InvocationInfo.ScriptLineNumber)"
}
Log "DONE"
"DONE" | Set-Content "$out\scoop-done.txt"
