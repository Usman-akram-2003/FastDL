<p align="center"><img src="assets/logo.png" width="120" alt="FastDL logo"></p>

<h1 align="center">FastDL</h1>
<p align="center">A fast, free download manager for Windows: files, videos and torrents, with a browser button like IDM.</p>
<p align="center"><a href="https://github.com/Usman-akram-2003/FastDL/releases/latest"><b>⬇ Download the latest version</b></a></p>

---

## What it does

- **Fast downloads**: up to 32 connections per file; a connection that finishes takes over half of the largest part still left, so no connection sits idle. Pause and resume any time, even after a restart.
- **Videos** from YouTube, Bilibili, Dailymotion, OK.ru and about 1,800 other sites: pick the quality, audio only or subtitles, or *Download all*.
- **Torrents**: magnet links and `.torrent` files; choose which files you want before anything downloads.
- **Browser button**: hover any video for *Download this video*; browser downloads go to FastDL with a *Download File Info* window first (folder, category, name). If FastDL is closed, the browser starts it.
- **Both internet connections at once**: Wi-Fi + Ethernet + phone tethering, for up to ~2× speed.
- **Mirrors**: download one file from several servers at the same time.
- **Scheduler**: start downloads at night, stop them in the morning.
- **Speed limits** per download and for everything together, live.
- **Expired links**: a video link that expires is renewed from its page automatically; for any other site, open the page and download again: FastDL recognises the file and continues where it stopped.
- **Self-tuning**: remembers how many connections each site accepts.
- **Tray app**: starts with Windows, keeps downloading in the background, and updates itself from this page.

## Install

1. Download **FastDL-Setup.exe** from [Releases](https://github.com/Usman-akram-2003/FastDL/releases/latest) and run it. No admin rights needed.
2. Keep *Install ffmpeg, aria2 and Deno* ticked: they're needed for videos and torrents (installed with winget).
3. Windows may say *"Windows protected your PC"* because FastDL isn't code-signed yet: click **More info → Run anyway**.

### Browser button (Chrome, Edge, Brave)

1. Open `chrome://extensions` (or `edge://extensions`) and turn on **Developer mode**.
2. Click **Load unpacked** and choose `%LOCALAPPDATA%\Programs\FastDL\extension`.
3. Refresh your open tabs.

## Good to know

- FastDL runs on your PC only: its window talks to it at `127.0.0.1`, nothing is sent anywhere else.
- It can't download DRM-protected streams (Netflix, Prime Video, Spotify, paid courses) and isn't meant to.
- Only download what you have the right to.

## Build from source

Needs Windows, Python 3.12 or newer (developed on 3.14) and [Inno Setup](https://jrsoftware.org/isinfo.php) (`winget install JRSoftware.InnoSetup`).

```
pip install -r requirements.txt
winget install Gyan.FFmpeg aria2.aria2 DenoLand.Deno
python fastdl.py            # run it from source
python test_fastdl.py       # tests (local servers; a few need the internet)
python build.py             # dist\FastDL\ (app), dist\FastDL-Setup.exe, dist\latest.json
```

**Releasing**: raise `APP_VERSION` in `fastdl.py`, run `python build.py`, then
`gh release create vX.Y dist\FastDL-Setup.exe dist\latest.json --title "FastDL X.Y" --notes "..."`.
Installed copies find the update within a day.

**Code signing**: set `FASTDL_SIGN_THUMBPRINT` (certificate in Windows) or `FASTDL_SIGN_PFX` + `FASTDL_SIGN_PASSWORD` before `build.py`: the app, the installer and the uninstaller get signed.

## Files

| File | What it is |
|---|---|
| `fastdl.py` | the engine, the local server, the windows and the tray |
| `ui.html` | the interface (list, progress windows, dialogs) |
| `extension/` | the browser extension |
| `build.py`, `installer.iss` | building the app and the installer |
| `update_deps.py` | keeps yt-dlp, ffmpeg, aria2 and Deno up to date |
| `test_fastdl.py` | the tests |
