"""Chrome Web Store / Edge Add-ons package:  python store.py  ->  dist/FastDL-Chrome-Edge.zip

The same zip goes to both stores. Differences from the extension you load from the install folder: no pinned "key"
(the stores assign their own ID; add it to EXTENSION_IDS in fastdl.py), the version follows the app's, and YouTube is left
out: the stores don't allow extensions that help download YouTube videos (STORE in background.js, exclude_matches below).
Texts, permission reasons and screenshots for the listings: store/listing.md.
"""
import json
import os
import zipfile

from fastdl import APP_VERSION

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "extension")


YOUTUBE = ["*://*.youtube.com/*", "*://youtu.be/*", "*://*.youtube-nocookie.com/*"]


def build(out):
    m = json.load(open(os.path.join(SRC, "manifest.json"), encoding="utf-8"))
    m.pop("key", None)
    m["version"] = APP_VERSION
    for cs in m["content_scripts"]:  # no video button on YouTube
        cs["exclude_matches"] = YOUTUBE
    bg = open(os.path.join(SRC, "background.js"), encoding="utf-8").read()
    assert bg.count("const STORE = false;") == 1
    bg = bg.replace("const STORE = false;", "const STORE = true;")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(m, indent=2))
        z.writestr("background.js", bg)
        for root, _, files in os.walk(SRC):
            for name in files:
                rel = os.path.relpath(os.path.join(root, name), SRC).replace(os.sep, "/")
                if rel not in ("manifest.json", "background.js"):
                    z.write(os.path.join(root, name), rel)
    return out


if __name__ == "__main__":
    print("built", build(os.path.join(HERE, "dist", "FastDL-Chrome-Edge.zip")))
