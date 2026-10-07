"""Build FastDL:  python build.py
  dist\\FastDL\\FastDL.exe   the app (a folder app: starts fast)
  dist\\FastDL-Setup.exe     the installer
  dist\\latest.json          what FastDL's update check reads: publish it next to the installer

Releasing a new version: raise APP_VERSION in fastdl.py, build, then
  gh release create vX.Y dist\\FastDL-Setup.exe dist\\latest.json --title "FastDL X.Y" --notes "..."
Installed copies find it within a day (Options > Updates > Check now: at once).
  FASTDL_RELEASE_URL   overrides where FastDL-Setup.exe is downloaded from (goes into latest.json)

Code signing (stops Windows' "unknown publisher" warnings), with a certificate either
  FASTDL_SIGN_PFX + FASTDL_SIGN_PASSWORD   a .pfx file and its password, or
  FASTDL_SIGN_THUMBPRINT                   a certificate installed in Windows (also hardware tokens)
signs FastDL.exe, the installer and the uninstaller, with a timestamp so signatures outlive the certificate.

ffmpeg, aria2 and deno are not bundled: the installer can install them with winget, so they keep updating.
"""
import hashlib
import json
import os
import shutil
import subprocess
import PyInstaller.__main__
from fastdl import APP_VERSION, app_icon

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
DIST = os.path.join(HERE, "dist")
ICON = os.path.join(BUILD, "fastdl.ico")
TIMESTAMP = "http://timestamp.digicert.com"

os.makedirs(BUILD, exist_ok=True)
app_icon(256).save(ICON, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

PyInstaller.__main__.run([
    os.path.join(HERE, "fastdl.py"),
    "--name", "FastDL", "--onedir", "--noconsole", "--noconfirm",
    "--icon", ICON,
    "--add-data", os.path.join(HERE, "ui.html") + os.pathsep + ".",
    "--add-data", os.path.join(HERE, "assets", "logo.png") + os.pathsep + "assets",
    "--collect-submodules", "yt_dlp",  # ~1800 site extractors are imported by name at runtime
    "--collect-all", "yt_dlp_ejs",     # YouTube challenge solver scripts (run with deno)
    "--hidden-import", "pystray._win32",  # tray backend, picked at runtime
    "--distpath", DIST,
    "--workpath", BUILD,
    "--specpath", BUILD,
])


def signtool():
    kits = os.path.expandvars(r"%ProgramFiles(x86)%\Windows Kits\10\bin")
    found = sorted((os.path.join(r, "signtool.exe") for r, _, fs in os.walk(kits) if "signtool.exe" in fs and r.endswith("x64")))
    return found[-1] if found else shutil.which("signtool")


def sign_args():
    """signtool arguments for the configured certificate, or None (unsigned build)."""
    if os.environ.get("FASTDL_SIGN_THUMBPRINT"):
        who = ["/sha1", os.environ["FASTDL_SIGN_THUMBPRINT"]]
    elif os.environ.get("FASTDL_SIGN_PFX"):
        who = ["/f", os.environ["FASTDL_SIGN_PFX"], "/p", os.environ.get("FASTDL_SIGN_PASSWORD", "")]
    else:
        return None
    return ["sign", "/fd", "sha256", "/tr", TIMESTAMP, "/td", "sha256", "/d", "FastDL", *who]


tool, args = signtool(), sign_args()
if args and not tool:
    raise SystemExit("A certificate is configured but signtool.exe was not found (install the Windows SDK)")
if args:
    subprocess.run([tool, *args, os.path.join(DIST, "FastDL", "FastDL.exe")], check=True)
    print("signed FastDL.exe")

# the installer (Inno Setup: winget install JRSoftware.InnoSetup)
iscc = next((p for p in (os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
                         r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", shutil.which("iscc") or "")
             if p and os.path.exists(p)), None)
if not iscc:
    raise SystemExit("Inno Setup not found: only the folder app was built (winget install JRSoftware.InnoSetup)")
cmd = [iscc, "/Q", f"/DAppVersion={APP_VERSION}"]
if args:  # Inno signs the setup and its uninstaller with the same certificate
    # $q is Inno's quote mark: real quotes inside this one argument break ISCC's command line
    quoted = " ".join(f"$q{a}$q" if " " in a else a for a in args)
    cmd += ["/DSIGN", f"/Ssigntool=$q{tool}$q {quoted} $f"]
subprocess.run([*cmd, os.path.join(HERE, "installer.iss")], check=True)
setup = os.path.join(DIST, "FastDL-Setup.exe")

# latest.json: FastDL compares versions and checks the installer's SHA-256 before installing it
h = hashlib.sha256()
with open(setup, "rb") as f:
    for block in iter(lambda: f.read(1 << 20), b""):
        h.update(block)
# this exact version's installer on GitHub Releases (the SHA-256 above belongs to this file only)
release = os.environ.get("FASTDL_RELEASE_URL",
                         f"https://github.com/Usman-akram-2003/FastDL/releases/download/v{APP_VERSION}/FastDL-Setup.exe")
with open(os.path.join(DIST, "latest.json"), "w") as f:
    json.dump({"version": APP_VERSION, "url": release, "sha256": h.hexdigest(), "notes": ""}, f, indent=2)
print(f"FastDL {APP_VERSION}: {setup} ({'signed' if args else 'NOT signed'}) + dist\\latest.json")
