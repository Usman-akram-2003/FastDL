# Store listings: Chrome Web Store and Microsoft Edge Add-ons

The package for both is `dist/FastDL-Chrome-Edge.zip` (`python store.py`). Everything below is meant to be pasted into the
dashboards. Privacy policy address: **https://usman-akram-2003.github.io/FastDL/privacy.html**

## Order of work

1. Create the accounts: Chrome Web Store developer account (one-time US$5, https://chrome.google.com/webstore/devconsole),
   Microsoft Partner Center / Edge Add-ons (free, https://partner.microsoft.com/dashboard/microsoftedge).
2. In each, **create a new item and upload the zip as a draft**. Each store shows the item's **ID** (32 letters) right away.
3. Send me both IDs. I add them to `EXTENSION_IDS` in `fastdl.py` and release a new FastDL, because the app only lets
   the extensions it knows start it from the browser. (The extension still works without this when FastDL is already running.)
4. Fill in the listing from this file, upload the screenshots from `store/img/`, and submit for review.
   Publish only after the FastDL release from step 3 is out.

## Important: no YouTube in this edition

Both stores forbid extensions that help download YouTube videos. The store zip is built that way (`python store.py`): the
video button never appears on YouTube pages and the extension ignores them. Do not mention YouTube in the listing, and do not
use YouTube in screenshots. The extension you load from FastDL's install folder keeps working everywhere.

## Name
FastDL Download Manager

## Summary (132 characters max)
Send downloads and page videos to the FastDL desktop app for faster multi-connection downloads. Requires FastDL for Windows.

## Description
FastDL is a free download manager for Windows that splits every file over many connections at once, like IDM. This extension connects your browser to it.

What you get:
• Browser downloads (zip, exe, pdf, iso, anything) are handed to FastDL, with a "Download File Info" window first: folder, category, file name. Pause and resume at any time, even after a restart. Schedule downloads, limit speed, use several connections.
• Hover a video and click "Download this video": pick the stream to download, and FastDL fetches it over many connections. Works on many sites (not on YouTube).
• Right-click a link, image or video and choose "Download with FastDL".
• Torrents and magnet links go straight to FastDL.
• Files that need you to be signed in download with your browser's session. Download links that expire are renewed from their page.

IMPORTANT: this extension needs the FastDL desktop app, a free download for Windows 10 and 11: https://usman-akram-2003.github.io/FastDL/ . Without it the extension does nothing. FastDL starts itself when the browser needs it.

Privacy: everything stays on your computer. The extension talks only to the FastDL app on your own PC (127.0.0.1) and sends nothing anywhere else. No ads, no accounts, no tracking. FastDL is open source (MIT): https://github.com/Usman-akram-2003/FastDL

## Category
Chrome Web Store: Productivity (or "Tools"). Edge Add-ons: Productivity.

## Language
English

## Single purpose (Chrome: "Single purpose description")
Send the files, videos and torrents that the user chooses to download in the browser to the FastDL desktop download manager on the same computer.

## Permission justifications (Chrome: Privacy practices tab)

| Permission | Why |
|---|---|
| `downloads` | To notice when the browser starts a download and, when the user has FastDL set to take over, pause it, hand it to the FastDL app, then cancel the browser's copy. If FastDL isn't running the download is resumed in the browser. |
| `cookies` | To read the cookies the browser would send to the site being downloaded from, and pass them to the FastDL app, so files that need the user to be signed in (private files, member downloads) can be fetched. Sent only to the app on this computer. |
| `webRequest` | To see which video streams (HLS/DASH manifests, large video files) a page loads, so "Download this video" works on sites with no known extractor; and to remember the form data of a form-button download, so the app can repeat it. Read-only: it never blocks or changes requests. |
| `contextMenus` | Adds "Download with FastDL" to the right-click menu on links, images, video, and "Download video on this page with FastDL" on pages. |
| `storage` | Temporary session storage of the video stream addresses seen in each tab (cleared on navigation or browser close). Nothing is stored permanently. |
| `nativeMessaging` | To start the FastDL app when it is not running (the app registers a small launcher with the browser), so the user's first download or video click works without opening it by hand. No data is exchanged through it. |
| Host permissions `<all_urls>` and the content script on all pages | The "Download this video" button must appear over a video on any website the user visits, and downloads can start from any site. Page content is not read or sent: the script only finds `<video>` elements and draws the button. The background part contacts only `127.0.0.1`. |

Remote code: none. All code is in the package.

## Privacy practices tab (Chrome)

Data the extension handles (tick these; all of it goes only to the app on the user's own computer, never to the developer):
- **Authentication information** (cookies for the download, local app only)
- **Web history** (address and title of the page or file being downloaded)
- **Website content** (video stream addresses seen on the page)

Certifications (tick all three): not sold to third parties; not used or transferred for purposes unrelated to the extension's single purpose; not used for creditworthiness or lending.

Privacy policy URL: https://usman-akram-2003.github.io/FastDL/privacy.html

## Notes for the reviewers (Chrome: "Test instructions"; Edge: "Notes for certification")

The extension works together with a free Windows desktop app, FastDL, which must be installed to test it.

1. Install FastDL: https://github.com/Usman-akram-2003/FastDL/releases/latest/download/FastDL-Setup.exe (Windows 10/11, no admin rights needed; if Windows shows "Windows protected your PC", choose More info > Run anyway; the installer is not code-signed yet). Source code: https://github.com/Usman-akram-2003/FastDL
2. Open the test page https://usman-akram-2003.github.io/FastDL/demo.html with the extension enabled. Hover the video: a "Download this video" bar appears at its top right; click it and pick the stream.
3. On the same page, click the sample.zip link. The "Download File Info" window of the FastDL app opens; choose Start Download and the file downloads in the FastDL window.
YouTube pages are deliberately ignored by this edition.
4. The only address the extension contacts is http://127.0.0.1:9614 (the app, on the same PC). It has no other network access.
No account or login is needed.

## Edge Add-ons extras
- Search terms (7 max): download manager, downloader, video downloader, IDM, torrent, fast download, FastDL
- Website: https://usman-akram-2003.github.io/FastDL/   Support: https://github.com/Usman-akram-2003/FastDL/issues
- Edge accepts the same zip. Its logo is the 128-pixel icon already in the package; upload `store/img/logo-300.png` if it asks for 300x300.

## Images (in `store/img/`)
- Screenshots 1280x800: `shot-1-*.png` to `shot-4-*.png`
- Small promo tile 440x280: `promo-small.png`
- Marquee 1400x560 (optional): `promo-marquee.png`
