"""python test_fastdl.py - local server check: splitting, pause/resume, no-range fallback, name clash."""
import glob, os, tempfile, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import fastdl
from fastdl import Download

fastdl.MIN_SPLIT = 1 << 16  # small pieces so a 3 MB file exercises dynamic splitting
fastdl.ARIA_PORT = 6811     # own aria2, so the tests can run while the FastDL app is open
fastdl.SETTINGS_FILE = os.path.join(tempfile.mkdtemp(), "settings.json")  # never the user's real settings
DATA = os.urandom(3_000_017)


busy, busy_lock = [0], threading.Lock()
allowed = {"token": "old"}  # /exp/<token>/...: links work only while their token is the current one


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/limited") and self.headers.get("Range") != "bytes=0-0":
            with busy_lock:
                busy[0] += 1
            try:
                if busy[0] > 2:  # server that allows only 2 connections per client
                    self.send_response(503)
                    self.end_headers()
                    return
                self.serve()
            finally:
                with busy_lock:
                    busy[0] -= 1
        elif self.path.startswith("/missing"):  # dead mirror
            self.send_response(404)
            self.end_headers()
        elif self.path.startswith("/exp/") and self.path.split("/")[2] != allowed["token"]:
            self.send_response(403)  # an expired signed link
            self.end_headers()
        else:
            self.serve()

    def do_POST(self):
        """Form downloads: the right form gets the file (directly, or a redirect to it); a page, or 403 otherwise."""
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if body != b"id=42&ok=1":
            self.send_response(403); self.end_headers(); return
        if self.path == "/form/redirect":
            self.send_response(303); self.send_header("Location", "/file-from-form.bin"); self.end_headers(); return
        if self.path == "/form/page":
            page = b"<html>Please wait...</html>"
            self.send_response(200); self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(page))); self.end_headers(); self.wfile.write(page); return
        self.send_response(200)  # /form/direct: the file itself, named by the server
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", 'attachment; filename="game.zip"')
        self.send_header("Content-Length", str(len(DATA))); self.end_headers()
        try:
            self.wfile.write(DATA)
        except OSError:
            pass

    def serve(self):
        rng = self.headers.get("Range")
        data = DATA[:100_000] if self.path.startswith("/other") else DATA  # mirror with a different file
        if rng and not self.path.startswith("/norange"):
            a, b = rng[6:].split("-")
            a, b = int(a), min(int(b) if b else len(data) - 1, len(data) - 1)
            body = data[a:b + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {a}-{b}/{len(data)}")
        else:
            body = data
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            for i in range(0, len(body), 16384):
                self.wfile.write(body[i:i + 16384])
                if self.path.startswith(("/slow", "/limited")):
                    time.sleep(0.2 if self.path.startswith("/slow") else 0.01)
                if self.path.startswith("/exp/"):
                    if self.path.split("/")[2] != allowed["token"]:
                        break  # the link expired mid-download: the server drops the connection
                    time.sleep(0.05)
        except OSError:
            pass  # client hung up (pause, or piece got split)

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"
read = lambda p: open(p, "rb").read()

with tempfile.TemporaryDirectory() as d:
    a = Download(f"{base}/file.bin", d)
    a.run()
    assert a.status == "done", a.error
    assert read(a.dest) == DATA, "multi-part"

    b = Download(f"{base}/file.bin", d)
    b.run()
    assert b.name == "file (1).bin", b.name

    c = Download(f"{base}/slow.bin", d)
    t = threading.Thread(target=c.run)
    t.start()
    while c.done < 300_000:
        time.sleep(0.05)
    c.stop.set()
    t.join()
    assert c.status == "paused" and os.path.exists(c.dest + ".fdl"), c.status
    c2 = Download(f"{base}/slow.bin", d)  # fresh object = app restart
    c2.run()
    assert c2.dest == c.dest and c2.status == "done", (c2.dest, c2.error)
    assert read(c2.dest) == DATA, "resume"

    e = Download(f"{base}/norange.bin", d)
    e.run()
    assert read(e.dest) == DATA, "no-range fallback"

    w = Download(f"{base}/wide.bin", d, conns=32)
    w.run()
    assert w.status == "done" and read(w.dest) == DATA, ("32 connections", w.error)

    g = Download(f"{base}/limited.bin", d)
    g.run()
    assert g.status == "done" and read(g.dest) == DATA, ("connection-limited server", g.error)
    assert g.conns < fastdl.CONNS, g.conns  # backed off instead of hammering the server

    yt = Download(f"{base}/chunked.bin", d)
    yt.range_cap = 100_000  # YouTube-style: many small range requests per connection
    yt.run()
    assert yt.status == "done" and read(yt.dest) == DATA, ("range cap", yt.error)

    # two internet links: 127.0.0.1 and 127.0.0.2 stand in for Wi-Fi and phone tethering
    real_links, real_lan = fastdl.links, fastdl.lan
    fastdl.links = lambda: ["127.0.0.1", "127.0.0.2"]
    fastdl.lan = lambda url: False  # pretend the local test server is on the internet
    two = Download(f"{base}/twolinks.bin", d)
    two.run()
    assert two.status == "done" and read(two.dest) == DATA, ("two links", two.error)
    assert set(two.by_link) == {"127.0.0.1", "127.0.0.2"}, two.by_link

    # one link can't connect at all (192.0.2.1 isn't on this PC): dropped, the other link finishes the job
    fastdl.links = lambda: ["192.0.2.1", "127.0.0.1"]
    dead = Download(f"{base}/deadlink.bin", d)
    t0 = time.time()
    dead.run()
    assert dead.status == "done" and read(dead.dest) == DATA, ("dead link", dead.error)
    assert dead.bad_links == {"192.0.2.1"} and time.time() - t0 < 10, (dead.bad_links, time.time() - t0)
    fastdl.links, fastdl.lan = real_links, real_lan
    assert fastdl.lan("http://127.0.0.1:9614/x") and fastdl.lan("http://192.168.1.20/share.iso")
    assert not fastdl.lan("https://1.1.1.1/file.zip")

    # mirrors: connections spread over the main link + the good mirror; dead and wrong-size mirrors left out
    mir = Download(f"{base}/main.bin", d)
    mir.mirrors = [f"{base}/mirror2.bin", f"{base}/missing.bin", f"{base}/other.bin"]
    mir.run()
    assert mir.status == "done" and read(mir.dest) == DATA, ("mirrors", mir.error)
    assert set(mir.by_source) == {f"{base}/main.bin", f"{base}/mirror2.bin"}, mir.by_source
    assert all(b > 0 for b in mir.by_source.values())

    # speed limiter: 3 MB at 1 MB/s takes about 3 s, however many connections there are
    cap = Download(f"{base}/capped.bin", d)
    cap.limit = 1_000_000
    t0 = time.time()
    cap.run()
    took = time.time() - t0
    assert cap.status == "done" and read(cap.dest) == DATA, ("limiter", cap.error)
    assert 2.3 < took < 10, f"1 MB/s limit, 3 MB took {took:.1f}s"

    # a video saved again must not reuse the finished file's name, nor the parts of one in progress
    clip = os.path.join(d, "Clip [abc].webm")
    assert fastdl.free_name(clip) == clip
    open(clip, "w").close()
    assert fastdl.free_name(clip) == os.path.join(d, "Clip [abc] (1).webm")
    open(os.path.join(d, "Clip [abc] (1).webm.f399.mp4.fdpart"), "w").close()
    assert fastdl.free_name(clip) == os.path.join(d, "Clip [abc] (2).webm")
    for n in ("Clip [abc].webm", "Clip [abc] (1).webm.f399.mp4.fdpart"):
        os.remove(os.path.join(d, n))

    assert not [n for n in os.listdir(d) if n.endswith((".fdpart", ".fdl", ".tmp"))], "leftover files"

route = lambda u, k=None: type(fastdl.make(u, kind=k)).__name__
assert route("https://example.com/report.pdf") == "Download"
assert route("https://example.com/sheet.xlsx") == "Download"
assert route("magnet:?xt=urn:btih:abc") == "Torrent"
assert route("https://example.com/linux.iso.torrent") == "Torrent"
assert route("https://example.com/get.php?id=1", "torrent") == "Torrent"
assert route("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "Video"
assert route("https://www.bilibili.com/video/BV1xx411c7mD") == "Video"
assert route("https://www.dailymotion.com/video/x7tgad0") == "Video"
assert route("https://example.com/some-page", "video") == "Video"
assert fastdl.start_aria2()["version"], "aria2 rpc"

# video button: known site -> page via yt-dlp; unknown site -> sniffed manifest / file, page cookies dropped
hls = {"url": "https://cdn.example.net/master.m3u8", "manifest": True}
mp4 = {"url": "https://cdn.example.net/movie.mp4", "manifest": False}
ck = {"Cookie": "session=secret", "Referer": "https://news.example.com/a"}
v = fastdl.make("https://www.youtube.com/watch?v=dQw4w9WgXcQ", kind="page", streams=[hls])
assert type(v).__name__ == "Video" and "youtube" in v.url
v = fastdl.make("https://news.example.com/a", headers=ck, kind="page", streams=[hls, mp4], title="My: Clip")
assert type(v).__name__ == "Video" and v.url == hls["url"] and "Cookie" not in v.headers and v.title == "My: Clip"
v = fastdl.make("https://news.example.com/a", headers=ck, kind="page", streams=[mp4])
assert type(v).__name__ == "Download" and v.url == mp4["url"] and "Cookie" not in v.headers
v = fastdl.make("https://news.example.com/a", kind="page")
assert type(v).__name__ == "Video" and v.url == "https://news.example.com/a"
assert fastdl.clean('My: Clip?') == "My_ Clip_"

# download list survives a restart
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "downloads.json")
    m = fastdl.Manager(path)
    a = m.add(f"{base}/file.bin", d, {"Cookie": "k=v"}, later=True, conns=16,
              format="137+140", desc="for the trip", filename="My Clip.mp4")
    assert a.title == "My Clip.mp4", a.title  # a plain file keeps the Save As name as typed
    v = fastdl.make("https://www.youtube.com/watch?v=dQw4w9WgXcQ", d)
    v.status, v.done = "downloading", 123
    m.items[v.id] = v
    m.save()
    m2 = fastdl.Manager(path)
    m2.kick = lambda: None  # don't start anything, just check what came back
    m2.load()
    a2, v2 = m2.items[a.id], m2.items[v.id]
    assert type(a2) is Download and a2.status == "paused" and a2.headers["Cookie"] == "k=v" and a2.conns == 16
    assert (a2.format, a2.desc, a2.title) == ("137+140", "for the trip", "My Clip.mp4")
    assert type(v2).__name__ == "Video" and v2.status == "queued" and v2.done == 123

    with open(path, "w") as f:
        f.write("{broken")
    m3 = fastdl.Manager(path)
    m3.load()
    assert not m3.items and os.path.exists(path + ".bad"), "corrupt list must not crash"

# .torrent file from the Add dialog: kept under TORRENT_DIR, garbage refused, routed to aria2
import base64
with tempfile.TemporaryDirectory() as d:
    fastdl.TORRENT_DIR = d
    tor = b"d8:announce3:x:y4:infod4:name4:test6:lengthi1e12:piece lengthi16384e6:pieces20:" + b"\0" * 20 + b"ee"
    p = fastdl.save_torrent(base64.b64encode(tor).decode(), "My Show.torrent")
    assert p == os.path.join(d, "My Show.torrent") and open(p, "rb").read() == tor
    assert fastdl.save_torrent(base64.b64encode(tor).decode(), "My Show.torrent").endswith("My Show (1).torrent")
    for junk in (base64.b64encode(b"<html>not a torrent</html>").decode(), "%%%not base64%%%"):
        try:
            fastdl.save_torrent(junk, "x.torrent")
            raise AssertionError("junk accepted")
        except ValueError:
            pass
    assert type(fastdl.make(p, kind="torrent")).__name__ == "Torrent"

# settings file (tray: Start with Windows choice, one-time shortcuts) and the shared icon
with tempfile.TemporaryDirectory() as d:
    real_file, fastdl.SETTINGS_FILE = fastdl.SETTINGS_FILE, os.path.join(d, "settings.json")
    assert fastdl.settings() == {} and fastdl.settings().get("autostart", True)  # on by default
    fastdl.settings(autostart=False)
    assert fastdl.settings(shortcuts_made=True) == {"autostart": False, "shortcuts_made": True}
    with open(fastdl.SETTINGS_FILE, "w") as f:
        f.write("{oops")
    assert fastdl.settings() == {}, "broken settings file must not crash FastDL"
    fastdl.SETTINGS_FILE = real_file
assert fastdl.app_icon(64).size == (64, 64)
assert fastdl.launch_cmd().endswith('fastdl.py"')

# Download File Info: names, types and categories; "file" never goes to yt-dlp; Save As keeps its extension
assert type(fastdl.make("https://www.youtube.com/api/timedtext?v=x&lang=en", kind="file")).__name__ == "Download"
assert fastdl.category_of("Clip.mp4") == "Video" and fastdl.category_of("song.flac") == "Music"
assert fastdl.category_of("setup.exe") == "Programs" and fastdl.category_of("notes.xyz") == "Other"
s = fastdl.dialog_spec({"url": "https://www.youtube.com/watch?v=x", "kind": "page", "title": "My: Clip",
                        "item": {"format": "137+140", "ext": "mp4", "size": 5, "label": "MP4 file, quality 1080p HD"}}, {}, [])
assert (s["filename"], s["category"], s["format"], s["kind"]) == ("My_ Clip.mp4", "Video", "137+140", "page"), s
s = fastdl.dialog_spec({"url": "https://www.youtube.com/watch?v=x", "kind": "page", "title": "Clip",
                        "item": {"sub_url": "https://www.youtube.com/api/timedtext?v=x", "ext": "en.vtt", "label": "VTT file, EN subtitles"}}, {}, [])
assert (s["filename"], s["kind"], s["url"]) == ("Clip.en.vtt", "file", "https://www.youtube.com/api/timedtext?v=x"), s
s = fastdl.dialog_spec({"url": "https://www.youtube.com/watch?v=x", "kind": "page", "title": "Clip",
                        "item": {"format": "251", "ext": "webm", "label": "WEBM audio only, 141 kbps"}}, {}, [])
assert s["category"] == "Music", s
with tempfile.TemporaryDirectory() as d:  # dialog Save As: a plain file keeps the typed name and extension
    sub = Download(f"{base}/timedtext", d)
    sub.title = "Clip.en.vtt"
    sub.run()
    assert sub.status == "done" and os.path.basename(sub.dest) == "Clip.en.vtt", (sub.error, sub.dest)

# global speed limit (Options): two downloads at once share one 1 MB/s budget -> 2 x 3 MB takes ~6 s
with tempfile.TemporaryDirectory() as d:
    fastdl.GLOBAL.limit = 1_000_000
    g1, g2 = Download(f"{base}/g1.bin", d), Download(f"{base}/g2.bin", d)
    t0 = time.time()
    ts = [threading.Thread(target=x.run) for x in (g1, g2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    took = time.time() - t0
    fastdl.GLOBAL.limit = 0
    assert g1.status == g2.status == "done" and read(g1.dest) == read(g2.dest) == DATA
    assert 5.0 < took < 15, f"global 1 MB/s for 6 MB took {took:.1f}s"

    # Delete with file: the file and every part it left behind go; other files in the folder stay
    keep = os.path.join(d, "unrelated.txt"); open(keep, "w").close()
    victim = Download(f"{base}/slow.bin", d)
    tv = threading.Thread(target=victim.run); tv.start()
    while not os.path.exists(str(victim.dest) + ".fdpart"):
        time.sleep(0.05)
    open(victim.dest + ".f399.mp4", "w").close()  # a video stream part next to it
    m = fastdl.Manager(); m.items[victim.id] = victim
    m.remove(victim, files=True)
    tv.join()
    for _ in range(40):
        if not glob.glob(glob.escape(victim.dest) + "*"):
            break
        time.sleep(0.25)
    assert not glob.glob(glob.escape(victim.dest) + "*"), glob.glob(glob.escape(victim.dest) + "*")
    assert os.path.exists(keep) and os.path.exists(g1.dest), "only the deleted download's own files may go"

# self-tuning connections: a site that pushed back starts lower next time, then steps back up
with tempfile.TemporaryDirectory() as d:
    real_file, fastdl.SETTINGS_FILE = fastdl.SETTINGS_FILE, os.path.join(d, "settings.json")
    assert fastdl.site_of("https://rr3---sn-abc.googlevideo.com/videoplayback") == "googlevideo.com"
    assert fastdl.site_of("http://127.0.0.1:5000/x") == "127.0.0.1"
    fastdl.learn_conns("example.com", 8, 8)
    assert fastdl.tuned_conns("example.com") is None, "smooth download on an unknown site: nothing to remember"
    fastdl.learn_conns("example.com", 8, 2)
    assert fastdl.tuned_conns("example.com") == 2
    fastdl.learn_conns("example.com", 2, 2)
    assert fastdl.tuned_conns("example.com") == 4  # went fine: try more next time
    fastdl.learn_conns("example.com", 4, 4)
    assert fastdl.tuned_conns("example.com") is None  # back to the normal 8: forgotten
    # end to end: the 2-connection server teaches it; the next download there starts at what it accepted
    first = Download(f"{base}/limited-a.bin", d); first.run()
    learned = fastdl.tuned_conns("127.0.0.1")
    assert first.status == "done" and learned and learned < fastdl.CONNS, learned
    second = Download(f"{base}/limited-b.bin", d)
    second.run()
    assert second.status == "done" and read(second.dest) == DATA
    assert fastdl.tuned_conns("127.0.0.1") <= learned * 2
    # plain file links skip loading yt-dlp entirely
    assert fastdl.looks_like_file("https://x.org/a/setup.exe") and not fastdl.looks_like_file("https://www.youtube.com/watch?v=x")
    fastdl.SETTINGS_FILE = real_file

# plain-language errors instead of raw Windows/urllib text
import urllib.error, socket
assert "isn't responding" in fastdl.friendly_error(urllib.error.URLError(TimeoutError(10060, "A connection attempt failed")))
assert "isn't responding" in fastdl.friendly_error(OSError("[WinError 10060] A connection attempt failed"))
assert "refused the connection" in fastdl.friendly_error(urllib.error.URLError(ConnectionRefusedError(10061, "refused")))
assert "Site not found" in fastdl.friendly_error(urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed")))
assert "(403)" in fastdl.friendly_error(urllib.error.HTTPError("http://x", 403, "Forbidden", {}, None))
assert "(503)" in fastdl.friendly_error(urllib.error.HTTPError("http://x", 503, "Unavailable", {}, None))
assert fastdl.friendly_error(IOError("ERROR: [youtube] Video unavailable")) == "[youtube] Video unavailable"
assert "(403)" in fastdl.friendly_error(IOError("ERROR: [udemy:course] course: Unable to download webpage: HTTP Error 403: Forbidden"))
assert "own download button" in fastdl.friendly_error(IOError("ERROR: Unsupported URL: https://host.example/processing-your-download-please-wait/"))
with tempfile.TemporaryDirectory() as d:  # end to end: a dead link shows the plain message
    dead = Download("http://127.0.0.1:9/never.bin", d)  # port 9: nothing listens
    dead.run()
    assert dead.status == "error" and "refused" in dead.error, dead.error

# HTTPS checked the Windows way: an incomplete certificate chain loads (as in Chrome/IDM), an expired one doesn't
try:
    assert urllib.request.urlopen("https://incomplete-chain.badssl.com/", timeout=60).status == 200
    try:
        urllib.request.urlopen("https://expired.badssl.com/", timeout=30)
        raise AssertionError("expired certificate accepted")
    except urllib.error.URLError as e:
        assert "CERTIFICATE" in str(e).upper() or "certificate" in str(e), e
except urllib.error.URLError as e:
    if "CERTIFICATE" in str(e).upper():
        raise
    print("(skipped the certificate check: badssl.com not reachable)", e)

# expired link (IDM's "refresh address"): the download fails 403 halfway; the same file from a fresh link
# continues it, keeping what was already downloaded, and the result is byte-exact
with tempfile.TemporaryDirectory() as d:
    allowed["token"] = "old"
    m = fastdl.Manager()
    exp = m.add(f"{base}/exp/old/file.bin", d, {"Referer": "https://example.com/page"})
    while exp.done < 400_000:
        time.sleep(0.05)
    allowed["token"] = "new"  # the old link stops working
    while exp.status == "downloading":
        time.sleep(0.1)
    assert exp.status == "error" and exp.expired and "expired" in exp.error, (exp.status, exp.error)
    kept = exp.done
    assert m.match_expired(f"{base}/exp/new/other-name.zip", {}) is None, "different type must not match"
    same = m.match_expired(f"{base}/exp/new/file.bin", {})
    assert same is exp and exp.url.endswith("/exp/new/file.bin")
    while exp.status != "done":
        time.sleep(0.1)
    assert read(exp.dest) == DATA and kept > 0, "continued with the fresh link, byte-exact"

# form downloads (POST), like IDM: the same form the browser sent gets the file
import json as _json
form ={"body": base64.b64encode(b"id=42&ok=1").decode(), "type": "application/x-www-form-urlencoded"}
with tempfile.TemporaryDirectory() as d:
    direct = Download(f"{base}/form/direct", d); direct.post = form; direct.run()
    assert direct.status == "done" and read(direct.dest) == DATA and direct.name == "game.zip", (direct.error, direct.name)
    redir = Download(f"{base}/form/redirect", d); redir.post = form; redir.run()
    assert redir.status == "done" and read(redir.dest) == DATA, redir.error
    assert redir.post is None and redir.url.endswith("/file-from-form.bin") and redir.ranged, "redirect: fast path from there"
    wrong = Download(f"{base}/form/direct", d); wrong.post = {**form, "body": base64.b64encode(b"id=1").decode()}; wrong.run()
    assert wrong.status == "error" and "(403)" in wrong.error, wrong.error
    # through FastDL's API, as the extension sends it: a page answer is declined so the browser keeps its download
    real_port = fastdl.PORT
    api = fastdl.Server(("127.0.0.1", 0), fastdl.API); fastdl.PORT = api.server_port
    threading.Thread(target=api.serve_forever, daemon=True).start()
    def via_api(path):
        req = urllib.request.Request(f"http://127.0.0.1:{fastdl.PORT}/api/downloads", headers={"X-FastDL": "1", "Content-Type": "application/json"},
                                     data=_json.dumps({"url": f"{base}{path}", "kind": "file", "folder": d, "post": form, "quiet": True}).encode())
        try:
            return 200, _json.load(urllib.request.urlopen(req, timeout=30))
        except urllib.error.HTTPError as e:
            return e.code, _json.load(e)
    assert via_api("/form/page")[0] == 400, "a web page must be left to the browser"
    code, info = via_api("/form/direct")
    assert code == 200, info
    took = fastdl.M.items[info["id"]]
    for _ in range(100):
        if took.status == "done": break
        time.sleep(0.1)
    assert took.status == "done" and read(took.dest) == DATA, took.error
    api.shutdown(); fastdl.PORT = real_port

# self-update: only newer versions, only https, a proper SHA-256; a tampered download is refused
import io, json as _json
assert fastdl.version_tuple("1.10") > fastdl.version_tuple("1.9") > fastdl.version_tuple("1.1")
real_urlopen, real_ver = fastdl.urllib.request.urlopen, fastdl.APP_VERSION
def fake(manifest):
    fastdl.urllib.request.urlopen = lambda req, timeout=None: io.BytesIO(_json.dumps(manifest).encode())
good = {"version": "9.0", "url": "https://example.com/FastDL-Setup.exe", "sha256": "a" * 64}
fastdl.settings(update_url="https://example.com/latest.json")
fastdl.APP_VERSION = "1.1"
fake({**good, "version": "1.1"}); fastdl.UPDATE.clear()
assert fastdl.check_update() is None, "same version is not an update"
fake({**good, "url": "http://example.com/x.exe"}); assert fastdl.check_update() is None, "installer over plain http refused"
fake({**good, "sha256": "nope"}); assert fastdl.check_update() is None, "manifest without a real SHA-256 refused"
fake(good); assert fastdl.check_update()["version"] == "9.0"
fastdl.settings(update_url="http://example.com/latest.json"); fastdl.UPDATE.clear()
assert fastdl.check_update() is None, "update address over plain http is never read"
fastdl.urllib.request.urlopen, fastdl.APP_VERSION = real_urlopen, real_ver
with tempfile.TemporaryDirectory() as d:  # tampered installer: downloaded, hash differs, deleted, not run
    fake_setup = os.path.join(d, "setup.exe"); open(fake_setup, "wb").write(b"not the published file")
    real_home, fastdl.HOME = fastdl.HOME, d
    fastdl.UPDATE.clear(); fastdl.UPDATE.update(version="9.0", url="file:///" + fake_setup.replace("\\", "/"), sha256="b" * 64)
    fastdl.install_update()
    assert "didn't match" in fastdl.UPDATE["error"] and not fastdl.UPDATE["state"] and not os.listdir(os.path.join(d, "updates")), fastdl.UPDATE
    fastdl.HOME = real_home
fastdl.UPDATE.clear(); fastdl.settings(update_url="")

# scheduler: "Download later" joins the schedule; start runs scheduled ones only, stop pauses them
m = fastdl.Manager()
m.kick = lambda: None  # don't actually download
later = m.add("http://x/later.bin", ".", {}, later=True)
now = m.add("http://x/now.bin", ".", {})
now.status = "paused"  # paused by hand, not scheduled: the scheduler must leave it alone
assert later.scheduled and later.status == "paused" and not now.scheduled
m.run_schedule(True)
assert later.status == "queued" and now.status == "paused", (later.status, now.status)
later.status = "downloading"
m.run_schedule(False)
assert later.stop.is_set(), "stop time must pause scheduled downloads"
assert fastdl.HHMM.match("02:30") and not fastdl.HHMM.match("24:00") and not fastdl.HHMM.match("2:30")

# options on completion: shutdown waits for the other downloads (power() swapped out, nothing really shuts down)
calls, real_power = [], fastdl.power
fastdl.power = calls.append
m = fastdl.Manager()
x, busy = Download("http://x/a"), Download("http://x/b")
x.status, x.on_done, busy.status = "done", "shutdown", "downloading"
m.items = {x.id: x, busy.id: busy}
m.finished(x)
m.power_check()
assert calls == [], "must not shut down while another download runs"
busy.status = "done"
m.power_check()
assert calls == ["shutdown"] and m.power_note and m.power_pending is None, (calls, m.power_note)
m.power_note, m.power_pending = "", "sleep"
m.cancel_power()
assert m.power_pending is None
fastdl.power = real_power

# ---- checksum: pasted text -> hash, finished file checked, mismatch reported ----
import hashlib
good = hashlib.sha256(DATA).hexdigest()
assert fastdl.parse_checksum("") == "" and fastdl.parse_checksum("nope") is None
assert fastdl.parse_checksum(f"SHA256: {good.upper()}  file.bin") == good
assert fastdl.parse_checksum("abc123") is None, "wrong length is not a hash"
with tempfile.TemporaryDirectory() as d:
    c = Download(f"{base}/file.bin", d)
    c.checksum = good
    c.run()
    fastdl.verify(c)
    assert c.status == "done" and c.verified is True and not c.error, (c.verified, c.error)
    c.checksum = hashlib.md5(b"other").hexdigest()
    fastdl.verify(c)
    assert c.verified is False and "does not match" in c.error
    assert fastdl.Download.info(c)["verified"] is False

# ---- site grabber: files a page links to, relative links resolved, pages and duplicates left out ----
PAGE = (b'<base href="/docs/"><a href="a.pdf">A</a><a href="/x/b.ZIP">B</a><a href="page.html">p</a><a href="a.pdf">dup</a>'
        b'<img src="pic.png" srcset="pic.png 1x, big.jpg 2x"><a href="mailto:x@y.z">m</a><a href="#top">t</a>'
        b'<video src="https://cdn.example.com/v.mp4"></video><a href="https://cdn.example.com/dir/">d</a>')


class G(BaseHTTPRequestHandler):
    def do_GET(self):
        body, kind = (PAGE, "text/html") if self.path == "/page" else (b"x" * 10, "application/zip")
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


gsrv = ThreadingHTTPServer(("127.0.0.1", 0), G)
threading.Thread(target=gsrv.serve_forever, daemon=True).start()
gbase = f"http://127.0.0.1:{gsrv.server_port}"
found = {l["url"].replace(gbase, ""): l for l in fastdl.grab_links(gbase + "/page", {})}
assert set(found) == {"/docs/a.pdf", "/x/b.ZIP", "/docs/pic.png", "/docs/big.jpg", "https://cdn.example.com/v.mp4"}, sorted(found)
assert found["/docs/a.pdf"]["cat"] == "Documents" and found["/x/b.ZIP"]["cat"] == "Compressed"
assert found["/docs/pic.png"]["cat"] == "Images" and found["https://cdn.example.com/v.mp4"]["cat"] == "Video"
try:
    fastdl.grab_links(gbase + "/file.zip", {})
    raise AssertionError("a file is not a page")
except ValueError:
    pass

# ---- proxy: Options value checked; requests really go through it; "none" skips Windows' proxy ----
assert all(fastdl.proxy_ok(x) for x in ("", "none", "http://127.0.0.1:8080", "http://u:p@proxy.local:3128", "https://p.example.com:443"))
assert not any(fastdl.proxy_ok(x) for x in ("proxy:8080", "http://nohost", "socks5://h:1", "http://h:8080/x y", "javascript:alert(1)"))
seen_by_proxy = []


class P(BaseHTTPRequestHandler):  # a proxy gets the full URL in the request line
    def do_GET(self):
        seen_by_proxy.append(self.path)
        self.send_response(200)
        self.send_header("Content-Length", "5")
        self.end_headers()
        self.wfile.write(b"hello")

    def log_message(self, *a):
        pass


psrv = ThreadingHTTPServer(("127.0.0.1", 0), P)
threading.Thread(target=psrv.serve_forever, daemon=True).start()
try:
    fastdl.set_proxy(f"http://127.0.0.1:{psrv.server_port}")
    with fastdl.opener(None).open("http://files.invalid/a.bin", timeout=10) as r:
        assert r.read() == b"hello"
    assert seen_by_proxy == ["http://files.invalid/a.bin"], seen_by_proxy
    with fastdl.opener(None).open(f"{gbase}/file.zip", timeout=10) as r:  # this PC never goes through the proxy
        assert r.read() == b"x" * 10
    assert len(seen_by_proxy) == 1, "localhost must bypass the proxy"
    with urllib.request.urlopen("http://files.invalid/b.bin", timeout=10) as r:  # urlopen (probe, updates), not only opener()
        assert r.read() == b"hello"
    assert seen_by_proxy[-1] == "http://files.invalid/b.bin", "a proxy set in Options must apply to urlopen at once"
    fastdl.set_proxy("none")
    assert os.environ["NO_PROXY"] == "*"
    assert urllib.request.getproxies().get("http") is None
finally:
    fastdl.set_proxy("")
assert os.environ.get("HTTP_PROXY") == fastdl._proxy_env0["HTTP_PROXY"], "empty must restore what was there"

# ---- helper programs: fetched from a release zip only if it matches its published checksum ----
import io, zipfile
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("tool-1.0/README.txt", "docs")
    z.writestr("tool-1.0/bin/faketool.exe", b"MZ fake program")
ZIP = buf.getvalue()


class T(BaseHTTPRequestHandler):
    def do_GET(self):
        body = {"/t.zip": ZIP, "/t.zip.sha256": f"Algorithm : SHA256\nHash      : {hashlib.sha256(ZIP).hexdigest().upper()}\nPath : C:\a\t.zip\n".encode(),
                "/bad.sha256": b"0" * 64}.get(self.path)
        self.send_response(200 if body else 404)
        self.send_header("Content-Length", str(len(body or b"")))
        self.end_headers()
        self.wfile.write(body or b"")

    def log_message(self, *a):
        pass


tsrv = ThreadingHTTPServer(("127.0.0.1", 0), T)
threading.Thread(target=tsrv.serve_forever, daemon=True).start()
tb = f"http://127.0.0.1:{tsrv.server_port}"
real_tools, real_dir = dict(fastdl.TOOLS), fastdl.TOOLS_DIR
with tempfile.TemporaryDirectory() as tools:
    fastdl.TOOLS_DIR = tools
    fastdl.TOOLS["faketool"] = (tb + "/t.zip", tb + "/t.zip.sha256")  # checksum in a Deno-style file
    exe = fastdl.fetch_tool("faketool")
    assert open(exe, "rb").read() == b"MZ fake program" and exe == os.path.join(tools, "faketool.exe"), exe
    assert fastdl.find_tool("faketool") == exe, "find_tool must look in FastDL's own tools folder"
    assert sorted(os.listdir(tools)) == ["faketool.exe"], "no leftovers: " + str(os.listdir(tools))
    os.remove(exe)
    fastdl.TOOLS["faketool"] = (tb + "/t.zip", hashlib.sha256(ZIP).hexdigest())  # pinned in the code
    assert open(fastdl.fetch_tool("faketool"), "rb").read() == b"MZ fake program"
    os.remove(exe)
    for bad in (tb + "/bad.sha256", "0" * 64):  # a zip that doesn't match is never unpacked or used
        fastdl.TOOLS["faketool"] = (tb + "/t.zip", bad)
        try:
            fastdl.fetch_tool("faketool")
            raise AssertionError("a wrong checksum must be refused")
        except IOError as e:
            assert "checksum" in str(e), e
        assert os.listdir(tools) == [], "refused download must leave nothing behind: " + str(os.listdir(tools))
# another process is already fetching the same helper: wait for it, never download it twice
with tempfile.TemporaryDirectory() as tools:
    fastdl.TOOLS_DIR = tools
    fastdl.TOOLS["faketool"] = ("http://127.0.0.1:9/never.zip", "0" * 64)  # unreachable: a download attempt would fail
    lock, exe = os.path.join(tools, "faketool.lock"), os.path.join(tools, "faketool.exe")
    open(lock, "w").close()

    def other_process_finishes():
        time.sleep(1.5)
        open(exe, "wb").write(b"MZ")
        os.remove(lock)
    threading.Thread(target=other_process_finishes).start()
    t0 = time.time()
    assert fastdl.fetch_tool("faketool") == exe and time.time() - t0 >= 1.4, "must wait for the other fetch"
    assert not os.path.exists(lock), "no lock left behind"
fastdl.TOOLS.clear(); fastdl.TOOLS.update(real_tools); fastdl.TOOLS_DIR = real_dir
assert all(len(v[1]) == 64 or v[1].startswith("https://") for v in fastdl.TOOLS.values())

# WebView2 check: same answer pywebview gets from the registry (it silently picks an engine that can't draw the UI otherwise)
import winreg
def _pywebview_says():
    from webview.platforms import winforms
    return winforms._is_chromium()
assert fastdl.webview2_installed() == _pywebview_says(), "FastDL's check must agree with pywebview's"
real_open = winreg.OpenKey
winreg.OpenKey = lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError())  # a clean Windows: no key anywhere
try:
    assert fastdl.webview2_installed() is False, "no registry key must mean not installed"
finally:
    winreg.OpenKey = real_open

# ---- fixes from the code review ----
# Windows device names: "nul.zip" would be written to the NUL device and vanish
assert fastdl.clean("nul.zip") == "_nul.zip" and fastdl.clean("Con") == "_Con" and fastdl.clean("com1.tar.gz") == "_com1.tar.gz"
assert fastdl.clean("console.txt") == "console.txt" and fastdl.clean("nulled.zip") == "nulled.zip"


# an empty file: servers answer Range: bytes=0-0 with 416 "range not satisfiable"
class E(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(416 if self.headers.get("Range") else 200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


esrv = ThreadingHTTPServer(("127.0.0.1", 0), E)
threading.Thread(target=esrv.serve_forever, daemon=True).start()
with tempfile.TemporaryDirectory() as d:
    e = Download(f"http://127.0.0.1:{esrv.server_port}/empty.txt", d)
    e.run()
    assert e.status == "done" and os.path.getsize(e.dest) == 0, (e.status, e.error)

# cookies stay with the site they were for: a redirect to another site (a CDN) must not carry them along
seen_cookie = {}


class R(BaseHTTPRequestHandler):
    def do_GET(self):
        seen_cookie[self.server.server_port] = self.headers.get("Cookie")
        loc = {"/to-same": f"http://localhost:{same.server_port}/f", "/to-other": f"http://127.0.0.1:{other.server_port}/f"}.get(self.path)
        self.send_response(302 if loc else 200)
        if loc:
            self.send_header("Location", loc)
        self.send_header("Content-Length", "3" if not loc else "0")
        self.end_headers()
        if not loc:
            self.wfile.write(b"abc")

    def log_message(self, *a):
        pass


origin, same, other = (ThreadingHTTPServer(("127.0.0.1", 0), R) for _ in range(3))
for srv_ in (origin, same, other):
    threading.Thread(target=srv_.serve_forever, daemon=True).start()
fastdl.probe(f"http://localhost:{origin.server_port}/to-same", {"Cookie": "session=secret"})
assert seen_cookie[same.server_port] == "session=secret", "same site: the cookie goes along"
fastdl.probe(f"http://localhost:{origin.server_port}/to-other", {"Cookie": "session=secret"})
assert seen_cookie[other.server_port] is None, "another site must never get the browser's cookie"
fastdl.opener(None).open(urllib.request.Request(f"http://localhost:{origin.server_port}/to-other", headers={"Cookie": "x=1"}), timeout=10).read()
assert seen_cookie[other.server_port] is None, "same rule for the connection workers"

# scheduler: a start time that went by while the PC slept still fires on wake-up, and never fires just because FastDL started
import datetime as _dt


def at(h, m, day=0):
    d = _dt.date.today() + _dt.timedelta(days=day)
    return time.mktime((d.year, d.month, d.day, h, m, 0, 0, 0, -1))


alldays = range(7)
assert fastdl.crossed("02:00", alldays, at(1, 0), at(8, 0)) is True, "slept through the start time"
assert fastdl.crossed("02:00", alldays, at(3, 0), at(8, 0)) is False, "started after it: nothing to catch up"
assert fastdl.crossed("02:00", alldays, at(1, 59), at(2, 0)) is True, "the ordinary tick"
assert fastdl.crossed("02:00", alldays, at(23, 0, -1), at(1, 0)) is False
assert fastdl.crossed("02:00", alldays, at(23, 0, -1), at(3, 0)) is True, "across midnight"
assert fastdl.crossed("02:00", [(_dt.date.today().weekday() + 1) % 7], at(1, 0), at(8, 0)) is False, "not a chosen day"
assert not fastdl.crossed("", alldays, at(1, 0), at(8, 0)) and not fastdl.crossed("25:99", alldays, at(1, 0), at(8, 0))

# a step after a download failing (a checksum read refused by antivirus) must not stall the queue behind it
real_verify, real_max = fastdl.verify, fastdl.MAX_ACTIVE


def broken(d):
    raise PermissionError("antivirus has the file")


fastdl.verify, fastdl.MAX_ACTIVE = broken, 1
with tempfile.TemporaryDirectory() as d:
    qm = fastdl.Manager()
    q1, q2 = qm.add(f"{base}/stall-a.bin", d, {}), qm.add(f"{base}/stall-b.bin", d, {})
    for _ in range(150):
        if q2.status == "done":
            break
        time.sleep(0.1)
    assert q1.status == "done" and q2.status == "done", (q1.status, q2.status)
fastdl.verify, fastdl.MAX_ACTIVE = real_verify, real_max

# a failed update download leaves an error beside the Update button, not a status that hides it
real_home = fastdl.HOME
with tempfile.TemporaryDirectory() as h:
    fastdl.HOME = h
    fastdl.UPDATE.clear()
    fastdl.UPDATE.update(version="9.9", url="http://127.0.0.1:9/FastDL-Setup.exe", sha256="0" * 64)
    fastdl.install_update()
    assert fastdl.UPDATE["state"] == "" and "Couldn't download" in fastdl.UPDATE["error"], fastdl.UPDATE
    fastdl.UPDATE.clear()
    fastdl.HOME = real_home

# ---- speed: finding the server, keeping the connections, the last percent, torrents ----
import socket as _s
real_gai = _s.getaddrinfo


def two_addresses(host, port, *a, **k):  # "two.test": one address nobody answers on, then a live one
    if host in ("two.test", "dead.test"):
        dead = [(_s.AF_INET, _s.SOCK_STREAM, 6, "", ("10.255.255.1", port))]
        return dead + ([(_s.AF_INET, _s.SOCK_STREAM, 6, "", ("127.0.0.1", port))] if host == "two.test"
                       else [(_s.AF_INET, _s.SOCK_STREAM, 6, "", ("10.255.255.2", port))])
    return real_gai(host, port, *a, **k)


_s.getaddrinfo = two_addresses
try:
    t0 = time.time()
    c = fastdl.fast_connect(("two.test", esrv.server_port), 20)
    assert c.getpeername()[0] == "127.0.0.1" and time.time() - t0 < 2.5, "a dead first address must not hold up the live one"
    c.close()
    t0 = time.time()
    try:
        fastdl.fast_connect(("dead.test", esrv.server_port), 2)
        raise AssertionError("nothing is reachable")
    except OSError:
        assert time.time() - t0 < 5, "tried together, so about one timeout, not one per address"
    c = fastdl.fast_connect(("127.0.0.1", esrv.server_port), 5)  # a plain single address still works
    c.close()
finally:
    _s.getaddrinfo = real_gai

# probe: one retry for a hiccup (5xx), none for an answer that won't change (404)
hits = {"flaky": 0, "gone": 0}


class F(BaseHTTPRequestHandler):
    def do_GET(self):
        key = "flaky" if self.path == "/flaky" else "gone"
        hits[key] += 1
        code = 404 if key == "gone" else 503 if hits[key] == 1 else 200
        self.send_response(code)
        self.send_header("Content-Length", "5" if code == 200 else "0")
        self.end_headers()
        if code == 200:
            self.wfile.write(b"hello")

    def log_message(self, *a):
        pass


fsrv = ThreadingHTTPServer(("127.0.0.1", 0), F)
threading.Thread(target=fsrv.serve_forever, daemon=True).start()
fb = f"http://127.0.0.1:{fsrv.server_port}"
assert fastdl.probe(f"{fb}/flaky", {})[2] == 5 and hits["flaky"] == 2, hits
try:
    fastdl.probe(f"{fb}/gone", {})
    raise AssertionError("404 must be raised")
except urllib.error.HTTPError as e:
    assert e.code == 404 and hits["gone"] == 1, hits

# a refusal sets the cap to what the server is really giving (the connections receiving data), not to half
pb = Download("http://x/y")
pb.conns = pb.max_conns = 8
pb.workers = [{"info": "Receiving data..."}] * 3 + [{"info": "Retrying in 2 sec..."}] * 4
pb.active = 7
assert pb._push_back() is True and pb.conns == 3, pb.conns
pb.workers = [{"info": "Receiving data..."}]
pb._push_back()
assert pb.conns == 3, "one reaction per 3 s"
pb2 = Download("http://x/y")
pb2.conns = 8
pb2.workers, pb2.active = [{"info": "Connecting..."}] * 4, 4
pb2._push_back()
assert pb2.conns == 4, "nobody receiving yet: halve"

# ... and then it climbs back: +1, +2, +4 while probes aren't refused
cl = Download("http://x/z")
cl.max_conns, cl.conns, cl._throttled, cl._grew = 8, 1, 100.0, 0
seq = []
for now in (101, 103, 105.5, 108, 110.5, 113):
    cl._climb(now, 0)
    seq.append(cl.conns)
assert seq == [1, 1, 2, 4, 8, 8], seq
cl.conns, cl._throttled, cl._step = 3, 113.0, 1  # a refusal: wait the longer interval before the next probe
cl._climb(114, 0)
assert cl.conns == 3
assert cl._stable is None or cl._stable <= 8

# what is remembered for a site: where it held, including a level reached by climbing
fastdl.settings(host_conns={"site.test": 3})
fastdl.learn_conns("site.test", 3, 5)
assert fastdl.settings()["host_conns"]["site.test"] == 5, fastdl.settings()
fastdl.learn_conns("site.test", 5, 2)
assert fastdl.settings()["host_conns"]["site.test"] == 2
fastdl.learn_conns("site.test", 2, 2)
assert fastdl.settings()["host_conns"]["site.test"] == 4, "a smooth download doubles it"
fastdl.settings(host_conns={})

# torrents: public trackers for magnets (a cached list, else the built-in one); never for a private .torrent
with tempfile.TemporaryDirectory() as tdir:
    real_tf = fastdl.TRACKERS_FILE
    fastdl.TRACKERS_FILE = os.path.join(tdir, "t.txt")
    assert fastdl.trackers() == list(fastdl.DEFAULT_TRACKERS)
    open(fastdl.TRACKERS_FILE, "w").write("\n".join(f"udp://t{i}.example:80/announce" for i in range(8)) + "\n\nnot a tracker\n")
    got = fastdl.trackers()
    assert len(got) == 8 and got[0] == "udp://t0.example:80/announce", got
    fastdl.TRACKERS_FILE = real_tf
    pub, prv = os.path.join(tdir, "a.torrent"), os.path.join(tdir, "b.torrent")
    open(pub, "wb").write(b"d4:infod6:lengthi1e4:name1:aee")
    open(prv, "wb").write(b"d4:infod6:lengthi1e4:name1:a7:privatei1eee")
    assert not fastdl.is_private(pub) and fastdl.is_private(prv) and fastdl.is_private(os.path.join(tdir, "missing"))

print("all good")
