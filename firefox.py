"""Firefox package of the extension:  python firefox.py  ->  dist/FastDL-Firefox.zip

Same scripts as Chrome's extension; only the manifest differs. Firefox's stable add-ons are Manifest V2
(V3 there makes the user grant site access by hand, which would break the video button and downloads).
Mozilla must sign it before Firefox release builds will keep it: upload the zip at
https://addons.mozilla.org/developers/ ("On your own", i.e. unlisted: signed in minutes, free) and
hand out the .xpi it gives back. Without signing: about:debugging > This Firefox > Load Temporary Add-on.
"""
import json
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "extension")
FIREFOX_ID = "fastdl@usman-akram-2003.github.io"  # must match fastdl.FIREFOX_ID (the native host allows this id)


def manifest():
    m = json.load(open(os.path.join(SRC, "manifest.json"), encoding="utf-8"))
    perms = [p for p in m["permissions"]]
    return {
        "manifest_version": 2,
        "name": m["name"], "version": m["version"], "description": m["description"],
        "icons": m["icons"],
        "permissions": perms + m["host_permissions"],
        "browser_action": {"default_title": m["action"]["default_title"], "default_icon": m["action"]["default_icon"]},
        "background": {"scripts": ["background.js"]},
        "content_scripts": m["content_scripts"],
        "web_accessible_resources": m["web_accessible_resources"][0]["resources"],
        "browser_specific_settings": {"gecko": {
            "id": FIREFOX_ID, "strict_min_version": "140.0",
            "data_collection_permissions": {"required": ["none"]},  # FastDL sends nothing anywhere but this PC
        }},
    }


def build(out):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest(), indent=2))
        for root, _, files in os.walk(SRC):
            for name in files:
                path = os.path.join(root, name)
                rel = os.path.relpath(path, SRC).replace(os.sep, "/")
                if rel != "manifest.json":
                    z.write(path, rel)
    return out


if __name__ == "__main__":
    print("built", build(os.path.join(HERE, "dist", "FastDL-Firefox.zip")))
