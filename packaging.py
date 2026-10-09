"""Package manifests for winget and Scoop, from the build's dist/latest.json:  python packaging.py

  bucket/fastdl.json                         Scoop: `scoop bucket add fastdl https://github.com/Usman-akram-2003/FastDL`,
                                             then `scoop install fastdl` (checkver/autoupdate keep it current)
  packaging/winget/<version>/*.yaml          winget: copy to manifests/u/UsmanAkram/FastDL/<version>/ in a fork of
                                             github.com/microsoft/winget-pkgs and open a pull request

Run it after `build.py` and the GitHub release, so the hash and address are the published installer's.
"""
import datetime
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "https://github.com/Usman-akram-2003/FastDL"
SITE = "https://usman-akram-2003.github.io/FastDL/"
ID = "UsmanAkram.FastDL"
DESC = "A fast, free download manager for Windows: files, videos and torrents, with a browser button like IDM."
LICENSE = "MIT"


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("wrote", os.path.relpath(path, HERE))


def main():
    latest = json.load(open(os.path.join(HERE, "dist", "latest.json"), encoding="utf-8"))
    v, sha, url = latest["version"], latest["sha256"].upper(), latest["url"]

    write(os.path.join(HERE, "bucket", "fastdl.json"), json.dumps({
        "version": v,
        "description": DESC,
        "homepage": SITE,
        "license": LICENSE,
        "url": url,
        "hash": sha.lower(),
        # the real installer (per user, no admin): it installs WebView2 if missing and registers the browser launchers
        "installer": {"script": "Start-Process -FilePath \"$dir\\FastDL-Setup.exe\" -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -Wait"},
        "uninstaller": {"script": "$u = \"$env:LOCALAPPDATA\\Programs\\FastDL\\unins000.exe\"; if (Test-Path $u) { Start-Process $u -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES' -Wait }"},
        "notes": "FastDL is installed in %LOCALAPPDATA%\\Programs\\FastDL. Browser extension: load that folder's 'extension' directory (see the website).",
        "checkver": {"github": REPO},
        "autoupdate": {"url": REPO + "/releases/download/v$version/FastDL-Setup.exe",
                       "hash": {"url": REPO + "/releases/download/v$version/latest.json", "jsonpath": "$.sha256"}},
    }, indent=2) + "\n")

    d = os.path.join(HERE, "packaging", "winget", v)
    head = f"PackageIdentifier: {ID}\nPackageVersion: {v}\n"
    write(os.path.join(d, f"{ID}.yaml"), head + "DefaultLocale: en-US\nManifestType: version\nManifestVersion: 1.6.0\n")
    write(os.path.join(d, f"{ID}.installer.yaml"), head + f"""InstallerType: inno
Scope: user
InstallModes:
- interactive
- silent
- silentWithProgress
UpgradeBehavior: install
ReleaseDate: {datetime.date.today().isoformat()}
Installers:
- Architecture: x64
  InstallerUrl: {url}
  InstallerSha256: {sha}
ManifestType: installer
ManifestVersion: 1.6.0
""")
    write(os.path.join(d, f"{ID}.locale.en-US.yaml"), head + f"""PackageLocale: en-US
Publisher: Usman Akram
PublisherUrl: https://github.com/Usman-akram-2003
PackageName: FastDL
PackageUrl: {SITE}
License: {LICENSE}
LicenseUrl: {REPO}/blob/main/LICENSE
ShortDescription: "{DESC}"
Description: "FastDL splits every download over many connections at once. Files, videos from about 1,800 sites, and torrents, with a button in the browser, a scheduler, speed limits, mirrors, a site grabber and a light/dark theme that follows Windows."
Moniker: fastdl
Tags:
- download-manager
- downloader
- torrent
- video-downloader
- idm
ReleaseNotesUrl: {REPO}/releases/tag/v{v}
ManifestType: defaultLocale
ManifestVersion: 1.6.0
""")


if __name__ == "__main__":
    main()
