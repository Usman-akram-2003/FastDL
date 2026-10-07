"""FastDL - fast multi-connection downloader.

python fastdl.py               -> starts the app at http://127.0.0.1:9614
python fastdl.py URL [folder]  -> download from the command line
"""
import base64, functools, glob, http.client, ipaddress, json, os, re, secrets, shutil, socket, subprocess, sys, threading, time, types
import urllib.error, urllib.parse, urllib.request, uuid, webbrowser
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FROZEN = getattr(sys, "frozen", False)  # running as FastDL.exe (PyInstaller)

# Check HTTPS certificates the way Windows (and so Chrome and IDM) does. Python's own check fails on servers
# that send an incomplete certificate chain ("unable to get local issuer certificate", seen on okcdn.ru);
# Windows fetches the missing piece. Expired or fake certificates are still refused.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass


def find_tool(name):
    """aria2c / ffmpeg / deno: PATH first, then straight in winget's folders, so it doesn't matter what
    PATH (or process protections) the program that started FastDL handed down."""
    found = shutil.which(name)
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    # the real files first: WinGet\Links holds symlinks, and a FastDL started by its installer inherited a
    # Windows 11 "untrusted mount point" guard that refuses to follow them (WinError 448)
    for pattern in (os.path.join(local, "Microsoft", "WinGet", "Packages", "*", "**", name + ".exe"),
                    os.path.join(local, "Microsoft", "WinGet", "Links", name + ".exe")):
        hits = glob.glob(pattern, recursive=True)
        if hits:
            return sorted(hits)[-1]
    return None

# aria2c, ffmpeg and deno come from winget, which puts them in WinGet\Links. A FastDL started by its
# installer (after an update) didn't always get that folder in PATH: torrents failed "aria2c not found".
_links_dir = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links")
if os.path.isdir(_links_dir) and _links_dir.lower() not in os.environ.get("PATH", "").lower():
    os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + _links_dir
# FASTDL_HOME / FASTDL_PORT: a separate test copy that can't touch the real list, settings or port
HOME = os.environ.get("FASTDL_HOME") or os.path.join(os.path.expanduser("~"), ".fastdl")
PYLIB = os.path.join(os.path.expanduser("~"), ".fastdl", "pylib")  # newer yt-dlp from update_deps.py
if FROZEN and os.path.isdir(os.path.join(PYLIB, "yt_dlp")):
    # YouTube breaks old yt-dlp versions, so the newer copy must win over the one frozen into the .exe.
    # A plain sys.path entry isn't enough: PyInstaller's importer still serves its frozen copies first,
    # mixing two versions (that broke every YouTube lookup: "Error -3 while decompressing data").
    # So every package that lives in pylib is loaded from pylib, whole.
    import importlib.machinery
    _tops = {n.split(".")[0] for n in os.listdir(PYLIB) if not n.endswith((".dist-info", "__pycache__")) and n not in ("bin", "share")}

    class _PylibFirst:
        @staticmethod
        def find_spec(name, path=None, target=None):
            if name.split(".")[0] in _tops:
                return importlib.machinery.PathFinder.find_spec(name, path if "." in name else [PYLIB])
            return None

    sys.meta_path.insert(0, _PylibFirst)
if FROZEN:
    if sys.stdout is None:  # windowed .exe has no console, but print() and yt-dlp need somewhere to write
        os.makedirs(HOME, exist_ok=True)
        sys.stdout = sys.stderr = open(os.path.join(HOME, "fastdl.log"), "a", encoding="utf-8", buffering=1)

PORT = int(os.environ.get("FASTDL_PORT") or 9614)
APP_VERSION = "1.4"  # bump for every release: build.py stamps it into the installer and latest.json
# where latest.json is published (Options can override): always the newest GitHub release
UPDATE_URL = "https://github.com/Usman-akram-2003/FastDL/releases/latest/download/latest.json"
CONNS = 8             # max connections per file (IDM's default; some servers ban more)
MULTILINK = True      # spread connections over every internet link (Wi-Fi + Ethernet + phone tethering)
MAX_ACTIVE = 3        # files downloading at the same time
MIN_SPLIT = 1 << 20   # never split a piece below 1 MB
CHUNK = 1 << 16       # 64 KB reads: finer progress and speed readings, still cheap
DEFAULT_FOLDER = os.path.join(os.path.expanduser("~"), "Downloads")
UA = {"User-Agent": "Mozilla/5.0 FastDL"}


def clean(name):
    """Safe Windows filename."""
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name or "").strip(". ")[:150]


def probe(url, headers):
    """Return (final_url, filename, size or None, supports_ranges)."""
    req = urllib.request.Request(url, headers={**headers, "Range": "bytes=0-0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        final = r.geturl()
        m = Message()
        m["content-disposition"] = r.headers.get("Content-Disposition", "")
        name = m.get_filename() or os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(final).path))
        name = clean(name) or "download"
        if r.status == 206:
            total = r.headers.get("Content-Range", "").split("/")[-1]
            if total.isdigit():
                return final, name, int(total), True
        size = r.headers.get("Content-Length")
        return final, name, int(size) if size and r.status == 200 else None, False


_links = (0.0, [])


def links():
    """Local IPs that really reach the internet: one per Wi-Fi / Ethernet / phone link. Cached 30 s.
    Virtual adapters (VMware, Hyper-V) fail the check because they have no route out."""
    global _links
    if time.time() - _links[0] < 30:
        return _links[1]
    try:
        ips = [ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if not ip.startswith(("127.", "169.254."))]
    except OSError:
        ips = []
    ok = []

    def check(ip):
        try:
            with socket.create_connection(("1.1.1.1", 443), timeout=2, source_address=(ip, 0)):
                ok.append(ip)
        except OSError:
            pass

    threads = [threading.Thread(target=check, args=(ip,)) for ip in ips]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    _links = (time.time(), sorted(ok, key=ips.index))
    return _links[1]


def lan(url):
    """Server on this PC or the local network (NAS, router): only one link can reach it."""
    try:
        host = urllib.parse.urlparse(url).hostname
        return ipaddress.ip_address(socket.getaddrinfo(host, None)[0][4][0].split("%")[0]).is_private
    except (OSError, ValueError, TypeError):
        return False


@functools.lru_cache(maxsize=None)
def opener(ip):
    """urllib opener whose sockets leave through the link that owns `ip` (None = Windows' default route)."""
    if not ip:
        return urllib.request.build_opener()
    bind = {"source_address": (ip, 0)}

    class Http(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(functools.partial(http.client.HTTPConnection, **bind), req)

    class Https(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(functools.partial(http.client.HTTPSConnection, **bind), req, context=self._context)

    return urllib.request.build_opener(Http, Https)


def pick_dest(folder, name, url):
    """A free filename in folder, or the half-finished one for this same url (resume)."""
    base, ext = os.path.splitext(name)
    for n in range(10000):
        dest = os.path.join(folder, f"{base} ({n}){ext}" if n else name)
        meta = dest + ".fdl"
        if os.path.exists(meta):
            try:
                with open(meta) as f:
                    if json.load(f)["url"] == url:
                        return dest
            except (OSError, ValueError, KeyError):
                pass
        elif not os.path.exists(dest) and not os.path.exists(dest + ".fdpart"):
            return dest
    raise IOError("no free filename")


HTTP_ERRORS = {
    401: "The site wants you to sign in (401).",
    403: "The server refused access (403). The link may have expired: get a fresh one from the page.",
    404: "Not found on the server (404): the link is broken or the file was removed.",
    410: "The file was removed from the server (410).",
    416: "The server can't continue this file (416): delete it and download again.",
    429: "The site says too many requests (429): wait a while, then press Start.",
}


def link_expired(e):
    """403/410: what signed, time-limited download links answer once they run out."""
    code = getattr(e, "code", None)
    if not isinstance(code, int):
        m = re.search(r"HTTP Error (\d{3})", str(e))
        code = int(m.group(1)) if m else None
    return code in (403, 410)


def friendly_error(e):
    """Plain words instead of '<urlopen error [WinError 10060] A connection attempt failed because ...'."""
    reason = getattr(e, "reason", e)
    text = str(e) or type(e).__name__
    code = getattr(e, "code", None)
    if not isinstance(code, int):  # yt-dlp reports it as text: "Unable to download webpage: HTTP Error 403: Forbidden"
        m = re.search(r"HTTP Error (\d{3})", text)
        code = int(m.group(1)) if m else None
    if isinstance(code, int):
        if code in HTTP_ERRORS:
            return HTTP_ERRORS[code]
        if code >= 500:
            return f"The site's server has a problem ({code}). Try again later."
    if isinstance(reason, (TimeoutError, socket.timeout)) or "10060" in text or "timed out" in text:
        return "The server isn't responding. The site may be down, or blocked by your internet provider."
    if isinstance(reason, ConnectionRefusedError) or "10061" in text:
        return "The server refused the connection. The site may be down."
    if isinstance(reason, socket.gaierror) or "11001" in text or "getaddrinfo" in text:
        return "Site not found. Check the link and your internet connection."
    if isinstance(reason, ConnectionResetError) or "10054" in text:
        return "The connection was cut by the server or the network. Press Start to continue."
    if "Unsupported URL" in text:  # yt-dlp got a web page with no video it knows: often a file host's wait page
        return ("This is a web page, not a file or a video. If the site has its own download button "
                "(often after a countdown), click it: FastDL catches the real download.")
    return text.replace("ERROR: ", "")  # yt-dlp messages are already readable


def site_of(url):
    """Site a server belongs to: rr3---sn-x.googlevideo.com and rr5---sn-y.googlevideo.com are one site."""
    host = urllib.parse.urlparse(url).hostname or ""
    return host if host.replace(".", "").isdigit() else ".".join(host.split(".")[-2:])


def tuned_conns(site):
    return (settings().get("host_conns") or {}).get(site)


def learn_conns(site, started, ended):
    """Self-tuning connections. The server made us back off -> next time start at what it accepted
    (no ban, no slow start against a wall). A smooth download -> try twice as many next time,
    until the normal setting is reached and the site is forgotten. Survives restarts."""
    tuned = dict(settings().get("host_conns") or {})
    if ended < started:
        tuned[site] = ended
    elif site in tuned:
        up = tuned[site] * 2
        if up >= CONNS:
            del tuned[site]
        else:
            tuned[site] = up
    else:
        return
    settings(host_conns=dict(list(tuned.items())[-200:]))  # keep the 200 most recent sites


def book(L, n):
    """Book n bytes in speed budget L (a download, or GLOBAL); returns how long to wait. 0 = no limit."""
    if not L.limit:
        return 0
    with L._slot_lock:
        now = time.time()
        if L._booked_for != L.limit:  # limit changed: drop bookings made at the old speed, apply now
            L._slot, L._booked_for = now, L.limit
        L._slot = max(L._slot, now) + n / L.limit
        return L._slot - now


# Options > Global speed limit: one budget every connection of every download books in
GLOBAL = types.SimpleNamespace(limit=0, _slot=0.0, _slot_lock=threading.Lock(), _booked_for=0)


def free_name(path):
    """`path`, or `name (1).ext`, `name (2).ext` ... when a file or another download's parts already use it."""
    base, ext = os.path.splitext(path)
    for n in range(10000):
        p = f"{base} ({n}){ext}" if n else path
        if not os.path.exists(p) and not glob.glob(glob.escape(p) + ".f*"):
            return p
    raise IOError("no free filename")


class Download:
    measure = True  # speed from byte counts; Video/Torrent get it from their engine
    selected, files = None, ()  # torrents only: chosen file numbers, file list (saved with every download)
    expired = False  # link stopped working (403/410): the same file from a fresh link continues this download

    @property
    def page(self):
        """The web page the download came from (the browser sends it as Referer)."""
        return self.given.get("Referer")

    def __init__(self, url, folder=None, headers=None, conns=None, title=None):
        self.id = uuid.uuid4().hex[:8]
        # looked up now, not when Python read this line: Options can change them while FastDL runs
        self.url, self.folder, self.conns, self.title = url, folder or DEFAULT_FOLDER, conns or CONNS, title
        self.given = headers or {}  # headers from the browser (cookie, referer, ua)
        self.headers = {**UA, **self.given}
        self.dest = self.name = self.size = self.error = None
        self.status, self.done, self.active, self.speed = "queued", 0, 0, 0
        self.segs, self.errors = [], []  # segs: [next_byte, last_byte], shared with workers
        self.lock, self.stop = threading.Lock(), threading.Event()
        self._samples = []  # (time, bytes done) over the last 3 s, for the transfer rate
        self.added = time.time()
        self._throttled = 0
        self.range_cap = None  # max bytes per request (YouTube throttles huge ranges)
        self.multilink = True  # False for links locked to one IP (YouTube stream URLs)
        self.by_link = {}      # bytes received per local IP, to show how the links shared the work
        self.bad_links = set() # links that can't reach this server (blocked / down)
        self.format = None     # yt-dlp format picked in the video button's quality menu
        self.desc = ""         # user's note from the download dialog
        self.workers = []      # one row per connection for the progress window: n, got, info, via
        self.phase = ""        # what happens right now, when it isn't plain downloading
        self.ranged = None     # can it resume? (server accepts ranges)
        self.mirrors = []      # other links to the very same file; connections are spread over all of them
        self.by_source = {}    # bytes received per server link (only shown with mirrors)
        self.bad_sources = set()  # mirrors that failed for this download
        self.scheduled = False # waits for the scheduler's start time ("Download later")
        self.category = None   # picked in the Download File Info window; None = from the file type
        self.limit = 0         # max bytes/sec for this download, 0 = no limit; can change while running
        self.on_done = "nothing"  # Options on completion: one of ON_DONE
        self.limiter = self    # whose speed budget the connections spend (a video's streams share the video's)
        self._slot, self._slot_lock, self._booked_for = 0.0, threading.Lock(), 0

    def _limit(self, n):
        """Speed limiter: all connections book time slots in a shared budget, so the total stays under the
        limit however many connections there are. Two budgets: this download's and the global one (Options)."""
        wait = max(book(self.limiter, n), book(GLOBAL, n))
        if wait > 0:
            self.stop.wait(min(wait, 5))  # wakes on pause; ponytail: 5 s cap, a limit lowered mid-wait takes effect on the next chunk

    def _read_size(self):
        """Chunk to read next. Under a limit: small chunks, booked *before* reading, so 8 connections
        don't burst 2 MB at the start (the limiter showed 900 KB/s at a 500 KB/s limit)."""
        L = min([x for x in (self.limiter.limit, GLOBAL.limit) if x] or [0])
        n = CHUNK if not L else max(8192, min(CHUNK, L // 4))
        self._limit(n)
        return n

    def _rows(self):
        return [dict(w) for w in self.workers[-64:]]

    def info(self):
        now = time.time()
        if self.status != "downloading":
            self.speed, self._samples = 0, []
        elif self.measure:
            # 3-second sliding window. Data lands a chunk at a time, so a half-second window could only
            # show whole chunks per half second: speed jumped 1, 1.5, 2, 2.5 MB/s instead of the real value.
            self._samples = [(t, b) for t, b in self._samples if now - t <= 3] + [(now, self.done)]
            t0, b0 = self._samples[0]
            if now - t0 >= 0.5:
                self.speed = (self.done - b0) / (now - t0)
        info = {k: getattr(self, k) for k in ("id", "url", "name", "dest", "size", "done", "speed", "status", "error", "active", "added")}
        info["kind"] = type(self).__name__.lower()  # download / video / torrent
        # byte ranges still missing, so the UI can draw IDM's per-connection bar
        info["gaps"] = [[p, e] for p, e in list(self.segs) if p <= e][:64] if self.status != "done" else []
        info["links"] = dict(self.by_link) if len(self.by_link) > 1 else {}
        info["desc"] = self.desc
        info["workers"], info["phase"], info["resumable"] = self._rows(), self.phase, self.ranged
        info["limit"], info["on_done"] = self.limit, self.on_done
        info["sources"] = dict(self.by_source) if len(self.by_source) > 1 else {}
        info["scheduled"], info["mirrors"], info["category"] = self.scheduled, len(self.mirrors), self.category
        info["files"], info["selected"] = list(getattr(self, "files", ())), getattr(self, "selected", None)  # torrents
        return info

    def run(self):
        self.status, self.error, self.errors, self.expired = "downloading", None, [], False
        for attempt in range(2):
            try:
                self._run()
                self.status = "paused" if self.stop.is_set() else "done"
                break
            except Exception as e:
                if attempt == 0 and link_expired(e) and not self.stop.is_set() and self._refresh_link():
                    continue  # fresh link for the same file: carry on where it stopped
                self.status, self.error = "error", friendly_error(e)
                if link_expired(e) and type(self) is Download and not self.post:  # a refused form isn't an old link
                    self.expired = True
                    self.error = ("The link expired. Open its page in your browser and download it again: "
                                  "FastDL recognises the file and continues where it stopped.")
                break
        self.phase, self.active = "", 0

    def _refresh_link(self):
        """Link expired: ask the page (yt-dlp knows ~1800 video sites, e.g. OK.ru) for a fresh link to the
        *same* file, recognised by its size. Videos (Video class) already re-read their page by themselves."""
        if type(self) is not Download or self.post or not self.page or not self.size or not is_video_site(self.page):
            return False
        self.phase = "Link expired: getting a fresh one from the page..."
        try:
            import yt_dlp
            with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True, "http_headers": self.given}) as ydl:
                info = ydl.extract_info(self.page, download=False)
            for f in sorted(info.get("formats") or [info], key=lambda f: -(f.get("height") or 0)):
                if f.get("protocol") not in ("http", "https") or not f.get("url"):
                    continue
                headers = {**self.given, **(f.get("http_headers") or {})}
                size = f.get("filesize") or probe(f["url"], {**UA, **headers})[2]
                if size == self.size:
                    self.url, self.given, self.headers = f["url"], headers, {**UA, **headers}
                    return True
        except Exception as e:
            print("refresh link:", e)
        finally:
            self.phase = ""
        return False

    post = None       # form download: {"body": base64, "type": content type} the browser sent with the click
    _response = None  # the server's answer to that form, already open (FastDL sent it while the browser waited)

    def _open_post(self):
        req = urllib.request.Request(self.url, data=base64.b64decode(self.post["body"]), method="POST",
                                     headers={**self.headers, "Content-Type": self.post.get("type") or
                                              "application/x-www-form-urlencoded"})
        return urllib.request.urlopen(req, timeout=30)

    def _set_dest(self, name):
        if self.title and clean(self.title):  # Save As name, or a sniffed stream named after its page
            t = clean(self.title)
            # keep its own extension (subtitles.en.vtt, setup.exe); else take the server's ("index" -> "page.mp4")
            name = t if re.fullmatch(r"\.[A-Za-z0-9]{1,5}", os.path.splitext(t)[1]) else t + os.path.splitext(name)[1]
        if not self.dest:
            os.makedirs(self.folder, exist_ok=True)
            self.dest = pick_dest(self.folder, name, self.url)
            self.name = os.path.basename(self.dest)

    def _run(self):
        self.phase = "Connecting..."
        if self.post:  # a form download (POST), like IDM: send the same form the browser sent
            r, self._response = self._response or self._open_post(), None
            if r.geturl() != self.url:  # the form led to the real file's address: an ordinary link from here on
                r.close()
                self.url, self.post = r.geturl(), None
            else:  # the server answered the form with the file itself: one connection, from the start
                m = Message()
                m["content-disposition"] = r.headers.get("Content-Disposition", "")
                name = clean(m.get_filename() or os.path.basename(urllib.parse.urlparse(self.url).path)) or "download"
                self.size, self.ranged, self.phase = int(r.headers.get("Content-Length") or 0) or None, False, ""
                self._set_dest(name)
                part = self.dest + ".fdpart"
                self._single(self.url, part, r)
                if not self.stop.is_set():
                    os.replace(part, self.dest)
                return
        url, name, self.size, ranged = probe(self.url, self.headers)
        self.ranged, self.phase = ranged, ""
        self._set_dest(name)
        part, meta = self.dest + ".fdpart", self.dest + ".fdl"
        if ranged:
            sources = [url]
            if self.mirrors:
                self.phase = "Checking mirrors..."
                sources += self._check_mirrors()
                self.phase = ""
            site = site_of(url)
            learned = tuned_conns(site)
            if learned:  # this site pushed back before: start where it was happy
                self.conns = min(self.conns, learned)
            started = self.conns
            self._multi(sources, part, meta)
            if not self.stop.is_set():
                learn_conns(site, started, self.conns)
        else:
            self._single(url, part)
        if not self.stop.is_set():
            os.replace(part, self.dest)
            if os.path.exists(meta):
                os.remove(meta)

    def _single(self, url, part, response=None):
        # server has no range support (or answered a form with the file): one connection, restarts from zero
        self.done = 0
        self.active = 1
        row = {"n": 1, "got": 0, "info": "Connecting...", "via": None}
        self.workers = [row]
        opened = response or urllib.request.urlopen(urllib.request.Request(url, headers=self.headers), timeout=30)
        with opened as r, open(part, "wb") as f:
            row["info"] = "Receiving data..."
            while chunk := r.read(self._read_size()):
                if self.stop.is_set():
                    row["info"] = "Paused"
                    return
                f.write(chunk)
                self.done += len(chunk)
                row["got"] = self.done
        row["info"] = "Finished"
        if self.size and self.done != self.size:
            raise IOError(f"incomplete: {self.done} of {self.size} bytes")

    def _check_mirrors(self):
        """Mirrors that serve the very same file: same size and split downloads allowed. Wrong ones
        (a different version, an error page) are left out instead of corrupting the file."""
        good = {}

        def check(m):
            try:
                final, _, size, ranged = probe(m, self.headers)
                if ranged and size == self.size:
                    good[m] = final
            except Exception:
                pass  # mirror down or refusing: just not used

        threads = [threading.Thread(target=check, args=(m,)) for m in self.mirrors]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return [good[m] for m in self.mirrors if m in good]  # keep the user's order

    def _multi(self, sources, part, meta):
        url = sources[0]
        size = self.size
        self.segs = []
        if os.path.exists(part) and os.path.exists(meta):
            with open(meta) as f:
                m = json.load(f)
            if m.get("size") == size:
                self.segs = m["segs"]
        if not self.segs:
            n = max(1, min(self.conns, size // MIN_SPLIT))
            self.segs = [[i * size // n, (i + 1) * size // n - 1] for i in range(n)]
            with open(part, "wb") as f:
                f.truncate(size)

        owned = {}  # id(seg) -> the worker thread on it
        pool = links() if MULTILINK and self.multilink and not lan(url) else []  # url = the main link
        pool = pool if len(pool) > 1 else [None]  # one link: let Windows route as usual
        started = [0]
        self.workers = []

        def spawn(seg):
            started[0] += 1  # round-robin: connection 1 on link A, 2 on link B, 3 on A ...
            row = {"n": started[0], "got": 0, "info": "Connecting...", "via": None}
            self.workers.append(row)
            t = threading.Thread(target=self._worker, args=(sources, part, seg, pool, started[0], row), daemon=True)
            owned[id(seg)] = t
            t.start()

        self.done = size - self._left()
        self._samples = []  # resumed bytes arrived "instantly": counting them showed 1554 MB/s after a restart
        saved, allowed, grown = 0, 2, time.time()
        while True:
            for k in [k for k, t in owned.items() if not t.is_alive()]:
                del owned[k]
            # slow start: one more connection every 0.5 s; _push_back halves the cap when the server refuses
            if allowed < self.conns and time.time() - grown > 0.5:
                allowed, grown = allowed + 1, time.time()
            allowed = min(allowed, self.conns)
            while len(owned) < allowed and not self.stop.is_set() and not self.errors:
                with self.lock:
                    free = [s for s in self.segs if s[0] <= s[1] and id(s) not in owned]
                    if free:  # a piece nobody works on (start, resume, or handed back)
                        seg = free[0]
                    else:  # dynamic segmentation: steal half of the biggest piece left
                        big = max(self.segs, key=lambda s: s[1] - s[0])
                        left = big[1] - big[0] + 1
                        if left < 2 * MIN_SPLIT:
                            break
                        seg = [big[0] + left // 2, big[1]]
                        big[1] = seg[0] - 1
                        self.segs.append(seg)
                spawn(seg)
            self.active = len(owned)
            with self.lock:
                self.done = size - self._left()
            if not owned or time.time() - saved > 1:
                self._save(meta)
                saved = time.time()
            if not owned:
                break
            time.sleep(0.25)
        if self.stop.is_set():
            return
        if self.errors:
            raise self.errors[0]
        if self.done != size:
            raise IOError(f"incomplete: {self.done} of {size} bytes")

    def _left(self):
        return sum(max(0, e - p + 1) for p, e in self.segs)

    def _save(self, meta):
        with self.lock:
            data = json.dumps({"url": self.url, "size": self.size, "segs": self.segs})
        with open(meta + ".tmp", "w") as f:
            f.write(data)
        os.replace(meta + ".tmp", meta)  # atomic, a crash never leaves half a resume file

    def _push_back(self):
        """A connection got nothing: the server may limit connections, so halve the cap (at most every 3 s).
        True = too many are open, this worker should hand its piece back and quit."""
        with self.lock:
            if self.conns > 1 and time.time() - self._throttled > 3:
                self.conns = max(1, self.conns // 2)
                self._throttled = time.time()
            return self.active > self.conns

    def _worker(self, sources, part, seg, pool=(None,), k=0, row=None):
        if isinstance(sources, str):
            sources = [sources]
        row = row if row is not None else {}  # this connection's line in the progress window
        fails, err = 0, None
        while fails < 6 and not self.stop.is_set() and seg[0] <= seg[1]:
            before = seg[0]
            end = seg[1] if not self.range_cap else min(seg[1], seg[0] + self.range_cap - 1)
            try:
                live = [p for p in pool if p not in self.bad_links] or [None]  # all links failed: let Windows route
                ip = live[(k + fails) % len(live)]  # a retry moves to the next link
                srcs = [s for s in sources if s not in self.bad_sources] or sources[:1]
                url = srcs[(k + fails) % len(srcs)]  # mirrors: connection 1 on server A, 2 on server B ...
                host = urllib.parse.urlparse(url).hostname if len(sources) > 1 else None
                row["info"], row["via"] = "Connecting...", " · ".join(x for x in (host, ip) if x) or None
                req = urllib.request.Request(url, headers={**self.headers, "Range": f"bytes={seg[0]}-{end}"})
                with opener(ip).open(req, timeout=30) as r, open(part, "r+b") as f:
                    if r.status != 206:
                        raise IOError("server stopped accepting ranges")
                    row["info"] = "Receiving data..."
                    f.seek(seg[0])
                    while not self.stop.is_set() and seg[0] <= min(seg[1], end):
                        chunk = r.read(self._read_size())
                        if not chunk:
                            raise IOError("connection closed early")
                        with self.lock:  # seg[1] can shrink when another connection steals half
                            take = max(0, min(len(chunk), seg[1] - seg[0] + 1))
                            f.write(chunk[:take])
                            seg[0] += take
                            row["got"] = row.get("got", 0) + take
                            if ip:
                                self.by_link[ip] = self.by_link.get(ip, 0) + take
                            if len(sources) > 1:
                                self.by_source[url] = self.by_source.get(url, 0) + take
                fails = 0  # finished this request; range_cap means the loop asks for the next one
            except Exception as e:
                err = e
                code = getattr(e, "code", None)
                others = [s for s in sources if s not in self.bad_sources and s != url]
                if others and seg[0] == before and (code is not None or len(pool) == 1):
                    # this mirror failed (HTTP error, or no answer while the link itself is fine): drop it,
                    # the other servers carry on. Never drops the last one.
                    self.bad_sources.add(url)
                    row["info"] = "Mirror failed, switching server"
                    continue
                if code and 400 <= code < 500 and code not in (408, 429):
                    fails = 6  # 403/404/410: link expired or gone, retrying won't help
                elif seg[0] > before:
                    fails = 0  # it was working, the connection just dropped
                else:
                    fails += 1
                    if ip and code is None and ip not in self.bad_links and len(pool) > 1:
                        # no HTTP answer at all over this link: the link is the problem, not the server.
                        # ponytail: dropped for the rest of this download, even if the outage was brief
                        self.bad_links.add(ip)
                        row["info"] = "Network can't reach server, switching"
                        continue  # retry at once on another link, no back-off
                    if self._push_back():
                        row["info"] = "Server busy, piece handed to others"
                        return  # the piece goes back to the pool for the connections that still work
                    row["info"] = f"Retrying in {min(2 ** fails, 15)} sec..."
                    self.stop.wait(min(2 ** fails, 15))
        if fails >= 6:
            row["info"] = f"Error: {err}"[:120]
            self.errors.append(err)
        else:
            row["info"] = "Paused" if self.stop.is_set() and seg[0] <= seg[1] else "Finished"


class Video(Download):
    """YouTube, Bilibili, Dailymotion and ~1800 other sites via yt-dlp."""
    measure = False
    parts = ()

    def _rows(self):
        # both streams' connections in one table, labelled so video and audio can be told apart
        rows = [{**w, "info": f"{label}: {w['info']}"} for p, label in zip(self.parts, ("Video", "Audio"))
                for w in p._rows()]
        for n, w in enumerate(rows, 1):
            w["n"] = n
        return rows

    def _run(self):
        import yt_dlp

        def progress(d):
            if self.stop.is_set():
                raise yt_dlp.utils.DownloadCancelled()
            if d["status"] == "downloading":
                self.name = os.path.basename(d.get("filename") or "")
                self.size = d.get("total_bytes") or d.get("total_bytes_estimate")
                self.done, self.speed = d.get("downloaded_bytes") or 0, d.get("speed") or 0

        def finished(path):
            self.dest, self.name = path, os.path.basename(path)

        opts = {
            "paths": {"home": self.folder},
            "outtmpl": clean(self.title).replace("%", "%%") + ".%(ext)s" if self.title and clean(self.title) else "%(title).150B [%(id)s].%(ext)s",
            # best video + best audio merged needs ffmpeg; without it take the best single file
            # direct (http) streams first: they split over 8 connections. YouTube's "best" is often an
            # HLS stream (many small pieces, one at a time), which made it 3x slower than IDM.
            "format": self.format or ("bv*[protocol^=http]+ba[protocol^=http]/b[protocol^=http]/bv*+ba/b"
                                      if find_tool("ffmpeg") else "b[protocol^=http]/b"),
            "ffmpeg_location": find_tool("ffmpeg"),  # winget's ffmpeg even when PATH lacks it
            # watch?v=X&list=Y means "this video" (IDM does the same); without it a YouTube Mix (list=RD...)
            # is an endless playlist and the download sat at 0 B. Pure playlist links still download all.
            "noplaylist": True,
            "concurrent_fragment_downloads": 8,
            "ratelimit": self.limit or None,  # HLS/playlist path only; ponytail: fixed at start, the fast path follows changes live
            "http_headers": self.given,
            "progress_hooks": [progress],
            "post_hooks": [finished],
            "quiet": True, "noprogress": True,
        }
        self.active, self.ranged = 1, True
        try:
            for attempt in range(2):
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        self.phase = "Finding video streams..."
                        info = ydl.extract_info(self.url, download=False)
                        self.phase = ""
                        fmts = info.get("requested_formats") or [info]
                        if info.get("_type") in ("playlist", "multi_video") or any(f.get("protocol") not in ("http", "https") for f in fmts):
                            self.phase = "Downloading with yt-dlp (stream in pieces)"
                            ydl.process_ie_result(info, download=True)  # playlists, HLS/DASH fragments: yt-dlp, 8 fragments at once
                        else:
                            if self.dest and not os.path.isfile(self.dest):
                                out = self.dest  # resuming: keep the name its part files were saved under
                            else:  # new download: never reuse a finished file's name (two copies mixed their parts)
                                out = free_name(ydl.prepare_filename(info))
                            self.dest, self.name = out, os.path.basename(out)
                            self._fast(fmts, out)
                    break
                except IOError as e:
                    # YouTube now and then refuses a fresh stream link (403); new links usually work.
                    # The parts already downloaded are kept and resumed.
                    if attempt or "403" not in str(e) or self.stop.is_set():
                        raise
            if not self.stop.is_set() and self.dest and os.path.isfile(self.dest):
                self.size = self.done = os.path.getsize(self.dest)  # the last progress tick can lag the finish
        except Exception:
            if not self.stop.is_set():
                raise
        finally:
            self.phase = ""

    def _fast(self, fmts, out):
        """IDM's trick: each stream (video, audio) over many connections with our engine, then ffmpeg joins them.
        One YouTube connection is throttled to about playback speed; 8 of them are not."""
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        parts = []
        for f in fmts:
            d = Download(f["url"], self.folder, f.get("http_headers"), conns=self.conns)
            d.dest = out if len(fmts) == 1 else f"{out}.f{f['format_id']}.{f['ext']}"
            d.stop = self.stop  # one pause button for both streams
            d.range_cap = (f.get("downloader_options") or {}).get("http_chunk_size")
            d.multilink = False  # YouTube stream URLs carry the requesting IP; another link gets 403
            d.limiter = self     # video + audio share this download's speed limit
            parts.append(d)
        self.parts = parts
        threads = [threading.Thread(target=d.run, daemon=True) for d in parts]
        for t in threads:
            t.start()
        while any(t.is_alive() for t in threads):
            infos = [d.info() for d in parts]
            self.done = sum(i["done"] for i in infos)
            self.size = sum(i["size"] or 0 for i in infos) or None
            self.speed = sum(i["speed"] for i in infos)
            self.active = sum(i["active"] for i in infos)
            time.sleep(0.5)
        if self.stop.is_set():
            return
        bad = [d for d in parts if d.status != "done"]
        if bad:
            raise IOError(bad[0].error or "stream download failed")
        if len(parts) > 1:
            self.phase = "Joining video and audio..."  # takes a few seconds; no bytes move, speed shows 0
            inputs = [a for d in parts for a in ("-i", d.dest)]
            maps = [a for i in range(len(parts)) for a in ("-map", str(i))]
            r = subprocess.run([find_tool("ffmpeg"), "-y", "-loglevel", "error", *inputs, *maps, "-c", "copy", out],
                               capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode:
                raise IOError("ffmpeg could not join video and audio: " + r.stderr.strip()[-300:])
            for d in parts:
                os.remove(d.dest)


ARIA_PORT, ARIA_SECRET, aria_lock, aria_proc = 6801, secrets.token_hex(16), threading.Lock(), None


def aria2(method, *params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "aria2." + method, "params": [f"token:{ARIA_SECRET}", *params]})
    req = urllib.request.Request(f"http://127.0.0.1:{ARIA_PORT}/jsonrpc", body.encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)["result"]
    except urllib.error.HTTPError as e:
        raise IOError(json.load(e).get("error", {}).get("message", str(e)))


def start_aria2():
    global aria_proc
    with aria_lock:
        if aria_proc and aria_proc.poll() is None:
            return
        exe = find_tool("aria2c")
        if not exe:
            raise IOError(f"aria2c not found (looked in PATH and {_links_dir}), install it: winget install aria2.aria2")
        aria_proc = subprocess.Popen(
            [exe, "--enable-rpc", f"--rpc-listen-port={ARIA_PORT}", f"--rpc-secret={ARIA_SECRET}",
             "--seed-time=0", "--continue=true", f"--stop-with-process={os.getpid()}", "--quiet",
             f"--max-overall-download-limit={GLOBAL.limit}"],  # Options > global speed limit
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for _ in range(50):
            try:
                return aria2("getVersion")
            except OSError:
                time.sleep(0.1)
        raise IOError("aria2c did not start")


class Torrent(Download):
    """Magnet links and .torrent files via aria2. A torrent with several files waits until you pick
    which ones you want (Files tab in its window); the choice can be changed later."""
    measure = False
    gid = None
    selected = None  # aria2's file numbers (1-based) to download; None = not chosen yet
    files = ()       # [{index, path, size, done}] once the torrent's info is known
    reselect = False # choice changed while downloading: apply it

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.chosen = threading.Event()

    def choose(self, indices):
        """From the window's Files tab."""
        self.selected = sorted(set(indices))
        if self.chosen.is_set():
            self.reselect = True  # already downloading: the loop pauses, applies, continues
        self.chosen.set()

    def _read_files(self):
        self.files = [{"index": int(f["index"]), "size": int(f["length"]), "done": int(f["completedLength"]),
                       "path": os.path.relpath(f["path"], self.folder) if f.get("path") else f"file {f['index']}"}
                      for f in aria2("getFiles", self.gid)]

    def _start_files(self):
        """The torrent's info is in, its download is paused: ask which files (if several), then go."""
        self._read_files()
        if self.selected is None and len(self.files) > 1:
            self.phase = "Choose the files you want (Files tab), then press Start Download"
            open_progress(self.id)
            while not self.chosen.wait(0.5):
                if self.stop.is_set():
                    return False
            self.phase = ""
        if self.selected is None:
            self.selected = [f["index"] for f in self.files]
        self.chosen.set()
        aria2("changeOption", self.gid, {"select-file": ",".join(map(str, self.selected))})
        aria2("unpause", self.gid)
        return True

    def _apply_selection(self):
        """A paused download is the only kind aria2 lets you change the files of: pause, change, carry on."""
        self.reselect = False
        aria2("forcePause", self.gid)
        for _ in range(40):
            if aria2("tellStatus", self.gid, ["status"])["status"] == "paused":
                break
            time.sleep(0.25)
        aria2("changeOption", self.gid, {"select-file": ",".join(map(str, self.selected))})
        aria2("unpause", self.gid)

    def _run(self):
        start_aria2()
        sent_limit = None  # what aria2 was told; a re-added torrent (new gid) needs it again
        if self.gid:
            try:
                aria2("unpause", self.gid)
            except IOError:
                self.gid = None  # aria2 restarted, add again (it resumes from the files on disk)
        if not self.gid:
            # pause-metadata: a magnet's (or .torrent link's) real download waits paused for the file choice
            # bt-remove-unselected-file: files you didn't pick don't stay behind as full-size placeholders
            opts = {"dir": self.folder, "pause-metadata": "true", "bt-remove-unselected-file": "true"}
            if self.selected:
                opts["select-file"] = ",".join(map(str, self.selected))
            if os.path.isfile(self.url):  # a .torrent file picked from disk (saved under TORRENT_DIR)
                with open(self.url, "rb") as f:
                    self.gid = aria2("addTorrent", base64.b64encode(f.read()).decode(), [], {**opts, "pause": "true"})
            else:
                hdr = [f"{k}: {v}" for k, v in self.given.items() if k in ("Cookie", "Referer")]
                self.gid = aria2("addUri", [self.url], {**opts, "header": hdr})
        while True:
            s = aria2("tellStatus", self.gid)
            if s.get("followedBy"):  # magnet finished fetching metadata, the real download is a new gid
                self.gid, sent_limit = s["followedBy"][0], None
                continue
            if s["status"] == "paused" and (s.get("bittorrent") or {}).get("info") and not self.stop.is_set():
                if not self._start_files():
                    return  # paused by the user while choosing
                continue
            if self.reselect:
                self._apply_selection()
                continue
            if (s.get("bittorrent") or {}).get("info"):
                self._read_files()  # per-file progress for the Files tab
            self.size = int(s["totalLength"]) or None
            self.done, self.speed = int(s["completedLength"]), int(s["downloadSpeed"])
            self.active, self.ranged = int(s["connections"]), True
            if sent_limit != self.limit:  # speed limiter, live: aria2 takes it per download
                aria2("changeOption", self.gid, {"max-download-limit": str(self.limit or 0)})
                sent_limit = self.limit
            self.phase = "" if self.size else "Fetching torrent info from peers..."
            self.workers = [{"n": 1, "got": self.done, "via": None,
                             "info": f"BitTorrent: {self.active} peers, {s.get('numSeeders', 0)} seeders"}]
            name = s.get("bittorrent", {}).get("info", {}).get("name")
            if name:
                self.name, self.dest = name, os.path.join(self.folder, name)
            if s["status"] == "complete":
                # aria2 reports 0 bytes per file once a torrent has stopped: it said complete, so the chosen ones are
                self.files = [{**f, "done": f["size"] if f["index"] in (self.selected or ()) else f["done"]} for f in self.files]
                return
            if s["status"] in ("error", "removed"):
                raise IOError(s.get("errorMessage") or s["status"])
            if self.stop.is_set():
                aria2("forcePause", self.gid)
                return
            time.sleep(0.5)


_extractors = None


def is_video_site(url):
    global _extractors
    try:
        import yt_dlp
    except ImportError:
        return False
    if _extractors is None:
        _extractors = [ie for ie in yt_dlp.extractor.gen_extractor_classes() if ie.ie_key() != "Generic"]
    return any(ie.suitable(url) for ie in _extractors)


def make(url, folder=None, headers=None, kind=None, streams=(), title=None):
    """kind: 'torrent' / 'video' / 'page' from the extension, otherwise guessed from the link.
    'page' = video button: url is the page, streams are what the extension sniffed while it played."""
    if kind == "page":
        if is_video_site(url) or not streams:
            return Video(url, folder, headers)
        s = streams[0]  # extension sends manifests first, then the playing/biggest file
        headers = {k: v for k, v in (headers or {}).items() if k != "Cookie"}  # page cookies never go to another host
        return (Video if s["manifest"] else Download)(s["url"], folder, headers, title=title)
    if kind == "torrent" or url.startswith("magnet:") or urllib.parse.urlparse(url).path.lower().endswith(".torrent"):
        return Torrent(url, folder, headers)
    if kind == "file" or (kind != "video" and looks_like_file(url)):
        return Download(url, folder, headers)  # direct link: no need to load yt-dlp's ~1800 site modules
    if kind == "video" or is_video_site(url):
        return Video(url, folder, headers)
    return Download(url, folder, headers)


def looks_like_file(url):
    """A link that ends in a known file type (.zip .exe .iso .pdf .mp4 ...): a file, never a video page."""
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower().lstrip(".")
    return any(ext in exts for exts in CAT_EXT.values()) or ext in ("bin", "dat", "img", "7z", "jar")


def formats(url, headers=None, streams=(), title=None):
    """Quality menu for the video button, IDM style: one entry per resolution, best stream for each,
    plus audio only. Each entry's 'format' is a yt-dlp format selector the download then uses."""
    result = {"title": title or "", "folder": DEFAULT_FOLDER, "items": []}
    if not is_video_site(url) and streams:  # a page yt-dlp doesn't know, but the extension saw it play
        result["items"] = [{"format": None, "label": "Detected video stream", "size": None, "ext": "mp4"}]
        return result
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True, "http_headers": headers or {}}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        result["error"] = str(e).replace("ERROR: ", "")[:300]
        result["items"] = [{"format": None, "label": "Best quality", "size": None, "ext": "mp4"}]
        return result
    result["title"] = info.get("title") or result["title"]
    fs = info.get("formats") or [info]
    size = lambda f: f.get("filesize") or f.get("filesize_approx") or 0
    audio = [f for f in fs if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    best = {}  # height -> best video format: direct file first (our fast engine, known size), mp4 (plays everywhere), bitrate
    for f in fs:
        h = f.get("height")
        if h and f.get("vcodec") not in (None, "none"):
            rank = (f.get("protocol") in ("http", "https"), f.get("ext") == "mp4", f.get("tbr") or 0)
            if h not in best or rank > best[h][0]:
                best[h] = (rank, f)
    for h in sorted(best, reverse=True):
        v = best[h][1]
        if v.get("acodec") in (None, "none") and audio:  # video-only stream: add the matching audio
            want = "m4a" if v["ext"] == "mp4" else "webm"
            a = max(audio, key=lambda f: (f.get("ext") == want, f.get("abr") or 0))
            sel, total = f"{v['format_id']}+{a['format_id']}", size(v) + size(a)
            ext = "mp4" if (v["ext"], a["ext"]) == ("mp4", "m4a") else "webm" if v["ext"] == a["ext"] == "webm" else "mkv"
        else:
            sel, total, ext = v["format_id"], size(v), v["ext"]
        result["items"].append({"format": sel, "size": total or None, "ext": ext,
                                "label": f"{ext.upper()} file, quality {h}p" + (" HD" if h >= 720 else "")})
    if audio:
        a = max(audio, key=lambda f: f.get("abr") or 0)
        result["items"].append({"format": a["format_id"], "size": size(a) or None, "ext": a["ext"],
                                "label": f"{a['ext'].upper()} audio only, {round(a.get('abr') or 0)} kbps"})
    if not result["items"]:
        result["items"] = [{"format": None, "label": "Best quality", "size": None, "ext": info.get("ext") or "mp4"}]
    result["items"] += subtitle_items(info)
    return result


def subtitle_items(info):
    """Subtitles for the quality menu (IDM lists them too). Real subtitles per language; if there are none,
    the auto-generated ones in the video's own language. One file each, saved like any other download."""
    subs = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    auto = False
    if not subs:
        lang = info.get("language") or "en"
        captions = info.get("automatic_captions") or {}
        key = f"{lang}-orig" if f"{lang}-orig" in captions else lang  # the original transcript, listed once
        subs = {lang: captions[key]} if key in captions else {}
        auto = True
    items = []
    for lang, files in list(subs.items())[:6]:
        best = sorted(files, key=lambda f: ("srt", "vtt", "ttml").index(f.get("ext")) if f.get("ext") in ("srt", "vtt", "ttml") else 9)
        if best and str(best[0].get("url", "")).startswith("http"):
            f = best[0]
            items.append({"format": None, "sub_url": f["url"], "size": None, "ext": f"{lang}.{f['ext']}",
                          "label": f"{f['ext'].upper()} file, {lang.upper()} subtitles" + (" (auto)" if auto else "")})
    return items


def browse(start):
    """Windows' own "choose folder" window for the dialog's ... button. Via PowerShell, because inside
    FastDL.exe there is no separate Python to run a Tk dialog in. The start folder goes in through the
    environment, so no path can break the command."""
    ps = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; Add-Type -AssemblyName System.Windows.Forms;"
          "$top = New-Object System.Windows.Forms.Form -Property @{TopMost = $true};"
          "$f = New-Object System.Windows.Forms.FolderBrowserDialog;"
          "$f.Description = 'Save to'; $f.SelectedPath = $env:FASTDL_START;"
          "if ($f.ShowDialog($top) -eq 'OK') { [Console]::Out.Write($f.SelectedPath) }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, text=True,
                           encoding="utf-8", timeout=600, env={**os.environ, "FASTDL_START": start},
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    return os.path.normpath(r.stdout.strip()) if r.stdout.strip() else None


STATE_FILE = os.path.join(HOME, "downloads.json")
TORRENT_DIR = os.path.join(HOME, "torrents")  # copies of uploaded .torrent files


def save_torrent(data_b64, name):
    """Keep an uploaded .torrent file, so the download can resume after a restart. Returns its path."""
    data = base64.b64decode(data_b64, validate=True)
    if not data.startswith(b"d") or len(data) > 20_000_000:  # a .torrent is a bencoded dictionary
        raise ValueError("not a .torrent file")
    os.makedirs(TORRENT_DIR, exist_ok=True)
    stem = clean(os.path.splitext(name or "")[0]) or "download"
    path = free_name(os.path.join(TORRENT_DIR, stem + ".torrent"))
    with open(path, "wb") as f:
        f.write(data)
    return path
SAVED = ("id", "url", "folder", "given", "title", "dest", "name", "size", "done", "status", "error", "added", "conns", "format", "desc",
         "limit", "on_done", "mirrors", "scheduled", "category", "selected", "expired", "post")
BUILTIN_CATS = ("Video", "Music", "Documents", "Compressed", "Programs", "Other")
CAT_EXT = {
    "Video": ("mp4", "mkv", "webm", "avi", "mov", "flv", "m4v", "wmv", "3gp", "ts", "vtt", "srt", "ttml"),
    "Music": ("mp3", "m4a", "aac", "flac", "wav", "ogg", "opus", "wma"),
    "Documents": ("pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv", "odt", "ods", "rtf", "epub"),
    "Compressed": ("zip", "rar", "7z", "tar", "gz", "bz2", "xz", "tgz"),
    "Programs": ("exe", "msi", "apk", "dmg", "iso", "deb", "rpm", "appx", "msix"),
}


def category_of(filename):
    ext = os.path.splitext(filename or "")[1].lower().lstrip(".")
    return next((c for c, exts in CAT_EXT.items() if ext in exts), "Other")


def cat_folder(cat):
    """Where a category saves: the remembered path, else Downloads\\<category> like IDM (Other: Downloads)."""
    remembered = (settings().get("cat_paths") or {}).get(cat)
    return remembered or (DEFAULT_FOLDER if cat == "Other" else os.path.join(DEFAULT_FOLDER, clean(cat) or "Other"))


def categories():
    return list(BUILTIN_CATS) + [c for c in settings().get("categories", []) if c not in BUILTIN_CATS]
ON_DONE = ("nothing", "open", "folder", "sleep", "shutdown")
HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d$")
SCHEDULE_DEFAULT = {"enabled": False, "start": "02:00", "stop": "07:00", "days": list(range(7))}


def scheduler():
    """IDM-style scheduler: at the start time every scheduled download starts; at the stop time
    (optional) they pause, and carry on at the next start. Checked every 15 s."""
    fired = set()  # (event, date) already done, so each start/stop happens once a day
    while True:
        s = settings().get("schedule") or {}
        now = time.localtime()
        today, hm = time.strftime("%Y-%m-%d", now), time.strftime("%H:%M", now)
        if s.get("enabled") and now.tm_wday in s.get("days", range(7)):
            for event, start in (("start", True), ("stop", False)):
                if s.get(event) == hm and (event, today) not in fired:
                    fired.add((event, today))
                    M.run_schedule(start)
        # ponytail: a start time that passes while the PC sleeps is skipped until the next day
        time.sleep(15)


def power(action):
    """Put Windows to sleep or shut it down. Shutdown waits 60 s: Windows shows a warning and FastDL a Cancel button."""
    if action == "shutdown":
        subprocess.run(["shutdown", "/s", "/t", "60", "/c", "FastDL: downloads finished, shutting down in 60 seconds."])
    elif action == "sleep":
        # ponytail: Windows hibernates instead when hibernation is switched on; fine for "PC off when done"
        subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])


class Manager:
    def __init__(self, path=None):
        self.items, self.lock, self.path, self._saved = {}, threading.Lock(), path, None
        self.power_pending, self.power_note = None, ""  # sleep/shutdown waiting for the other downloads

    def save(self):
        """Write the list (only if it changed). Cookies are kept too, so logged-in downloads can resume."""
        if not self.path:
            return
        data = json.dumps([{"kind": type(d).__name__.lower(), **{k: getattr(d, k) for k in SAVED}}
                           for d in list(self.items.values())], ensure_ascii=False)
        if data == self._saved:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path + ".tmp", "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(self.path + ".tmp", self.path)  # atomic: a crash never leaves half a list
        self._saved = data

    def autosave(self):
        while True:
            time.sleep(2)
            try:
                self.save()
            except OSError:
                pass  # disk hiccup: try again next round

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                rows = json.load(f)
        except FileNotFoundError:
            return
        except ValueError:  # corrupt list: keep it for inspection, start empty rather than crash
            os.replace(self.path, self.path + ".bad")
            return
        kinds = {"download": Download, "video": Video, "torrent": Torrent}
        for r in rows:
            d = kinds.get(r.get("kind"), Download)(r["url"], r.get("folder") or DEFAULT_FOLDER, r.get("given"), title=r.get("title"))
            for k in SAVED:
                if k not in ("given", "title") and k in r:
                    setattr(d, k, r[k])
            if d.error:
                d.error = friendly_error(IOError(d.error))  # lists saved before plain-language errors
            if d.status in ("downloading", "queued"):
                d.status = "queued"  # was running when the app closed: carry on (resume files are on disk)
            elif d.status == "done" and d.dest and os.path.isfile(d.dest):
                d.size = d.done = os.path.getsize(d.dest)  # trust the finished file over the last progress tick
            self.items[d.id] = d
        self.kick()

    def add(self, url, folder, headers, kind=None, streams=(), title=None, later=False, conns=None,
            format=None, desc="", filename=None, mirrors=(), post=None, response=None):
        d = make(url, folder, headers, kind, streams, title)
        d.conns, d.format, d.desc = conns or CONNS, format, desc
        d.post, d._response = post, response  # form download: set before it can start
        d.mirrors = [m for m in mirrors if m != d.url] if type(d) is Download else []  # mirrors: plain files only
        if filename:  # "Save As" from the dialog wins over the site's title
            # a video's extension comes from the streams yt-dlp joins; a plain file keeps the name as typed
            d.title = filename if type(d) is Download else os.path.splitext(filename)[0]
        for old in list(self.items.values()):
            if old.url == d.url and old.status in ("queued", "downloading"):
                return old
        if later:
            d.status, d.scheduled = "paused", True  # "Download later": waits for the scheduler (or Resume)
        self.items[d.id] = d
        self.kick()
        return d

    def kick(self):
        with self.lock:
            free = MAX_ACTIVE - sum(d.status == "downloading" for d in list(self.items.values()))
            for d in list(self.items.values()):
                if free <= 0:
                    break
                if d.status == "queued":
                    d.status = "downloading"
                    d.stop.clear()
                    free -= 1
                    threading.Thread(target=self._go, args=(d,), daemon=True).start()

    def _go(self, d):
        d.run()
        if d.status == "done":
            self.finished(d)
        self.kick()
        self.power_check()

    def finished(self, d):
        """Options on completion."""
        notify("Download complete", d.name or d.url)
        try:
            if d.on_done == "open" and d.dest and hasattr(os, "startfile"):
                os.startfile(d.dest)
            elif d.on_done == "folder":
                self.folder(d)
        except OSError:
            pass  # file moved or no app for this type: nothing to open
        if d.on_done in ("sleep", "shutdown"):
            self.power_pending = d.on_done

    def power_check(self):
        """Sleep / shut down only once nothing is downloading or waiting, so no other download gets cut off."""
        if self.power_pending and not any(x.status in ("downloading", "queued") for x in list(self.items.values())):
            action, self.power_pending = self.power_pending, None
            self.power_note = "Shutting down in 60 seconds" if action == "shutdown" else ""
            power(action)

    def cancel_power(self):
        self.power_pending = None
        if self.power_note:
            subprocess.run(["shutdown", "/a"])  # abort the 60-second shutdown
            self.power_note = ""

    def options(self, d, limit=None, on_done=None, scheduled=None):
        if limit is not None:
            d.limit = limit
        if on_done is not None:
            d.on_done = on_done
        if scheduled is not None:
            d.scheduled = scheduled

    def match_expired(self, url, headers):
        """IDM's "refresh download address": a download whose link expired, and the browser now hands over
        the same file again (same size and type) with a fresh link: continue that one instead of starting over."""
        waiting = [d for d in list(self.items.values()) if d.expired and type(d) is Download and d.status != "done"]
        if not waiting:
            return None
        try:
            _, name, size, _ = probe(url, {**UA, **headers})
        except Exception:
            return None
        ext = os.path.splitext(name)[1].lower()
        for d in waiting:
            if size and d.size == size and os.path.splitext(d.name or "")[1].lower() == ext:
                d.url, d.given = url, {**d.given, **headers}
                d.headers, d.expired, d.error = {**UA, **d.given}, False, None
                self.resume(d)
                return d
        return None

    def run_schedule(self, start):
        """Scheduler fired (or Start now / Stop now): start, or pause, every scheduled download."""
        for d in list(self.items.values()):
            if not d.scheduled:
                continue
            if start and d.status in ("paused", "error"):
                self.resume(d)
            elif not start and d.status in ("queued", "downloading"):
                self.pause(d)

    def pause(self, d):
        if d.status == "queued":
            d.status = "paused"
        d.stop.set()

    def resume(self, d):
        if d.status in ("paused", "error"):
            d.status = "queued"
            self.kick()

    def remove(self, d, files=False):
        """Take it off the list. files=False keeps .fdpart/.fdl, so re-adding the link resumes;
        files=True ("also delete the file") removes the file and every part it left behind."""
        d.stop.set()
        self.items.pop(d.id, None)
        if files:
            threading.Thread(target=self._delete_files, args=(d,), daemon=True).start()

    @staticmethod
    def _delete_files(d):
        for _ in range(60):  # Windows can't delete a file a connection still holds open: let them stop first
            if d.status not in ("downloading", "queued"):
                break
            time.sleep(0.25)
        if isinstance(d, Torrent) and d.gid:
            try:
                aria2("forceRemove", d.gid)
                time.sleep(1)  # aria2 closes the torrent's files
            except Exception:
                pass
        if not d.dest:
            return  # never got far enough to have a file
        # the file, its resume data (.fdpart .fdl), a video's stream parts (.f399.mp4 ...) and aria2's .aria2;
        # only names built from this download's own file name, nothing else in the folder
        paths = [d.dest, d.dest + ".aria2", *glob.glob(glob.escape(d.dest) + ".f*")]
        for p in paths:
            try:
                if os.path.isdir(p):  # a multi-file torrent is a folder
                    shutil.rmtree(p)
                elif os.path.exists(p):
                    os.remove(p)
            except OSError as e:
                print("could not delete", p, e)

    def folder(self, d):
        if d.dest and hasattr(os, "startfile"):
            os.startfile(os.path.dirname(d.dest))


M = Manager()

OPTIONS_DEFAULT = {"conns": 8, "max_active": 3, "folder": "", "global_limit": 0, "multilink": True,
                   "ask_browser": True, "skip_smaller_mb": 0, "skip_types": "jpg jpeg png gif webp svg ico bmp",
                   "close_done": True}


def options():
    s = settings()
    return {**OPTIONS_DEFAULT, **{k: s[k] for k in OPTIONS_DEFAULT if k in s}, "autostart": s.get("autostart", True),
            "default_folder": os.path.join(os.path.expanduser("~"), "Downloads"), "version": APP_VERSION,
            "update_url": s.get("update_url") or UPDATE_URL}


def apply_options():
    """Options take effect at once: new downloads, the queue, the global speed limit, the links switch."""
    global CONNS, MAX_ACTIVE, DEFAULT_FOLDER, MULTILINK
    o = options()
    CONNS, MAX_ACTIVE, MULTILINK, GLOBAL.limit = o["conns"], o["max_active"], o["multilink"], o["global_limit"]
    DEFAULT_FOLDER = o["folder"] or o["default_folder"]
    if aria_proc and aria_proc.poll() is None:  # torrents: aria2 keeps its own overall limit
        try:
            aria2("changeGlobalOption", {"max-overall-download-limit": str(GLOBAL.limit)})
        except Exception:
            pass
    M.kick()  # more downloads at once allowed: start waiting ones now


# inside FastDL.exe the bundled files are unpacked to sys._MEIPASS, not next to the program
UI_FILE = os.path.join(sys._MEIPASS if FROZEN else os.path.dirname(os.path.abspath(__file__)), "ui.html")


NATIVE_HOST = "com.fastdl.launcher"
EXTENSION_ID = "ncandpkokkmoafpomjemanhficndppjf"  # pinned by the "key" in extension/manifest.json
BROWSERS = (r"Software\Google\Chrome", r"Software\Microsoft\Edge", r"Software\BraveSoftware\Brave-Browser")


def launch_cmd():
    """Command line that starts FastDL: the .exe, or pythonw + this script (no console window)."""
    if FROZEN:
        return f'"{sys.executable}"'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return f'"{pyw if os.path.exists(pyw) else sys.executable}" "{os.path.abspath(__file__)}"'


SETTINGS_FILE = os.path.join(HOME, "settings.json")


SETTINGS_LOCK = threading.Lock()  # download threads write it too (learned connections per site)


def settings(**changes):
    """Read the small settings file; with keyword args, update and save it first."""
    with SETTINGS_LOCK:
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                s = json.load(f)
        except (OSError, ValueError):
            s = {}
        if changes:
            s.update(changes)
            os.makedirs(HOME, exist_ok=True)
            with open(SETTINGS_FILE + ".tmp", "w", encoding="utf-8") as f:
                json.dump(s, f, indent=2)
            os.replace(SETTINGS_FILE + ".tmp", SETTINGS_FILE)  # all or nothing, never half a file
        return s


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def set_autostart(on):
    """Start with Windows: quietly into the tray (--tray), per user, no admin."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "FastDL", 0, winreg.REG_SZ, launch_cmd() + " --tray")
        else:
            try:
                winreg.DeleteValue(k, "FastDL")
            except FileNotFoundError:
                pass


def make_shortcuts():
    """Desktop + Start menu shortcuts, made once: if the user deletes them, they stay deleted."""
    ps = ("$s = New-Object -ComObject WScript.Shell;"
          "foreach ($dir in [Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs')) {"
          " $l = $s.CreateShortcut((Join-Path $dir 'FastDL.lnk')); $l.TargetPath = $env:FASTDL_EXE;"
          " $l.WorkingDirectory = Split-Path $env:FASTDL_EXE; $l.Description = 'FastDL download manager'; $l.Save() }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], env={**os.environ, "FASTDL_EXE": sys.executable},
                   capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


LOGO = os.path.join(os.path.dirname(UI_FILE), "assets", "logo.png")  # the FastDL logo (bundled in the .exe)


def app_icon(size=256):
    """The FastDL logo at any size: tray icon here, app/installer icon in build.py."""
    from PIL import Image
    return Image.open(LOGO).convert("RGBA").resize((size, size), Image.LANCZOS)


def register_launcher():
    """Let the browser extension start FastDL when it's closed (Chrome/Edge/Brave native messaging).
    Per user (HKCU), no admin. Re-written on every start, so moving FastDL.exe keeps it working."""
    if os.name != "nt":
        return
    import winreg
    cmd = launch_cmd()
    # WMI starts FastDL as its own process: started directly, it would sit in the browser's job object
    # and be killed when the short-lived host script exits.
    ps = f"Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{{CommandLine='{cmd.replace(chr(39), chr(39) * 2)}'}}"
    bat = os.path.join(HOME, "launch-fastdl.bat")
    manifest = os.path.join(HOME, "native-host.json")
    os.makedirs(HOME, exist_ok=True)
    encoded = base64.b64encode(ps.encode("utf-16-le")).decode()  # no quoting can break, spaces in paths are fine
    with open(bat, "w") as f:
        f.write(f"@echo off\npowershell -NoProfile -WindowStyle Hidden -EncodedCommand {encoded} >nul 2>&1\n")
    with open(manifest, "w") as f:
        json.dump({"name": NATIVE_HOST, "description": "Starts FastDL", "path": bat, "type": "stdio",
                   "allowed_origins": [f"chrome-extension://{EXTENSION_ID}/"]}, f, indent=2)
    for browser in BROWSERS:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{browser}\NativeMessagingHosts\{NATIVE_HOST}") as k:
            winreg.SetValue(k, "", winreg.REG_SZ, manifest)


UPDATE = {}  # newer version found: {"version", "url", "sha256", "notes"}; "state" while installing


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4])


def check_update():
    """Read latest.json from the update address; remember it if it's newer than this FastDL."""
    url = settings().get("update_url") or UPDATE_URL
    if not url.startswith("https://"):  # only over https: what we'd install must not be swappable on the way
        return None
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        info = json.load(r)
    if version_tuple(info.get("version")) > version_tuple(APP_VERSION) and str(info.get("url", "")).startswith("https://") \
            and re.fullmatch(r"[0-9a-f]{64}", str(info.get("sha256", "")).lower()):
        if not UPDATE or version_tuple(info["version"]) > version_tuple(UPDATE.get("version")):
            UPDATE.clear()
            UPDATE.update(version=str(info["version"]), url=info["url"], sha256=info["sha256"].lower(),
                          notes=str(info.get("notes", ""))[:500])
            notify("FastDL update available", f"Version {info['version']} is ready to install.")
        return UPDATE
    return None


def update_checker():
    """Once a minute after start, then daily."""
    time.sleep(60)
    while True:
        try:
            check_update()
        except Exception as e:
            print("update check:", e)
        time.sleep(24 * 3600)


def install_update():
    """Download the new installer, check it's exactly the published file (SHA-256; and, if FastDL itself is
    signed, the same signature), run it silently and step aside. The installer closes FastDL and restarts it."""
    import hashlib
    UPDATE["state"] = "Downloading the update..."
    folder = os.path.join(HOME, "updates")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"FastDL-Setup-{UPDATE['version']}.exe")
    # FastDL's own engine: several connections (a plain single download took 107 s for 37 MB)
    dl = Download(UPDATE["url"], folder)
    dl.dest = path
    t = threading.Thread(target=dl.run, daemon=True)
    t.start()
    while t.is_alive():
        if dl.size:
            UPDATE["state"] = f"Downloading the update... {100 * dl.done // dl.size}%"
        t.join(0.5)
    if dl.status != "done":
        UPDATE["state"] = f"Couldn't download the update: {dl.error}"
        return
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    if h.hexdigest() != UPDATE["sha256"]:
        os.remove(path)
        UPDATE["state"] = "The download didn't match the published file, so it was not installed."
        return
    if signature_status(sys.executable) == "Valid" and signature_status(path) != "Valid":
        os.remove(path)  # a signed FastDL only accepts a signed update
        UPDATE["state"] = "The update isn't signed, so it was not installed."
        return
    UPDATE["state"] = "Installing... FastDL will restart."
    M.save()
    subprocess.Popen([path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    time.sleep(1)
    quit_app()  # free FastDL's files; the installer starts the new version when it's done


def signature_status(path):
    """Windows' verdict on a file's code signature: Valid, NotSigned, UnknownError ..."""
    if os.name != "nt" or not FROZEN:
        return "NotSigned"
    r = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-AuthenticodeSignature -LiteralPath $env:F).Status"],
                       capture_output=True, text=True, env={**os.environ, "F": path}, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.stdout.strip() or "UnknownError"


WINDOW = None   # the app window, once open
TRAY = None     # the tray icon, once running
HIDDEN = False  # window hidden in the tray (closed with X, or started with Windows)


def show_window():
    """Bring the app window to the front (tray, second launch, the video button's ? icon)."""
    global HIDDEN
    if WINDOW is None:
        webbrowser.open(f"http://127.0.0.1:{PORT}")
        return
    HIDDEN = False
    WINDOW.show()     # pywebview also activates it
    WINDOW.restore()  # un-minimize
    # (no "on_top" trick: pywebview sets that from the calling thread, and WinForms hangs on it)


PROGRESS = {}  # download id -> its own progress window (like IDM's)


def progress_title(d):
    pct = f"{100 * d.done / d.size:.0f}% " if d.size and d.status != "done" else ""
    name = d.name or os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(d.url).path)) or d.url  # name not known yet
    return (pct if d.status != "done" else "Complete · ") + name


def progress_titles():
    """'79% ubuntu.iso' in each progress window's title bar, like IDM. Own thread: works without the tray icon.
    Options > close finished windows: a finished download's window closes 10 s later; each window is
    a whole browser engine (WebView2, ~50-100 MB), so a pile of finished ones adds up."""
    done_at = {}
    while True:
        close = options()["close_done"]
        for did, w in list(PROGRESS.items()):
            d = M.items.get(did)
            if not d:
                continue
            try:
                w.title = progress_title(d)
            except Exception:  # the window was just closed (WinForms: "disposed object"): forget it, keep going
                PROGRESS.pop(did, None)
                continue
            if d.status == "done" and close:
                if time.time() - done_at.setdefault(did, time.time()) >= 10:
                    close_progress(did)
                    done_at.pop(did, None)
            else:
                done_at.pop(did, None)
        time.sleep(1)


def open_progress(did):
    """IDM's per-download window: a separate Windows window showing ui.html in progress mode.
    Already open: bring it forward. No app window (browser fallback): False, the page shows it inline."""
    if WINDOW is None or did not in M.items:
        return False
    w = PROGRESS.get(did)
    if w:
        w.show()
        w.restore()
        return True
    import webview
    w = webview.create_window(progress_title(M.items[did]), f"http://127.0.0.1:{PORT}/?progress={did}",
                              width=680, height=580, min_size=(520, 420))
    w.events.closed += lambda: PROGRESS.pop(did, None)
    PROGRESS[did] = w
    return True


DIALOGS = {}  # dialog id -> what the browser asked to download, waiting in a Download File Info window


def open_dialog(spec):
    """IDM's "Download File Info": its own window, on top of the browser, before anything downloads.
    Returns its id, or None without an app window (the extension then shows its in-page dialog)."""
    if WINDOW is None:
        return None
    import webview
    did = uuid.uuid4().hex[:8]
    DIALOGS[did] = spec
    w = webview.create_window("Download File Info", f"http://127.0.0.1:{PORT}/?dialog={did}",
                              width=800, height=360, resizable=False, on_top=True)
    spec["window"] = w
    w.events.closed += lambda: DIALOGS.pop(did, None)
    return did


def dialog_spec(body, headers, streams):
    """Name, size, type and category for the dialog, from the browser's request."""
    url, item, kind = str(body.get("url")), body.get("item") or {}, body.get("kind")
    spec = {"headers": headers, "streams": streams, "kind": kind, "format": None, "size": None, "page": url}
    if str(item.get("sub_url", "")).startswith(("http://", "https://")):  # subtitles: a plain little file
        spec.update(url=item["sub_url"], kind="file", ext=str(item.get("ext") or "vtt"))
        name = f"{clean(body.get('title') or 'subtitles')}.{spec['ext']}"
    elif kind == "page":  # a video picked in the quality menu
        spec.update(url=url, format=item.get("format"), size=item.get("size"), ext=str(item.get("ext") or "mp4"))
        name = f"{clean(body.get('title') or 'video')}.{spec['ext']}"
    else:  # a file the browser was about to download: ask its server for name and size, like IDM does
        spec.update(url=url, kind="file")
        try:
            _, name, spec["size"], _ = probe(url, {**UA, **headers})
        except Exception:
            name = clean(os.path.basename(urllib.parse.urlparse(url).path)) or "download"
        spec["ext"] = os.path.splitext(name)[1].lstrip(".") or "file"
    spec["filename"] = name
    spec["category"] = "Music" if "audio only" in str(item.get("label", "")) else category_of(name)
    return spec


def close_progress(did):
    w = PROGRESS.pop(did, None)
    if w:
        w.destroy()


def notify(title, text):
    """Windows notification from the tray icon, only while the window is hidden (else the window shows it)."""
    if TRAY and HIDDEN:
        try:
            TRAY.notify(text[:200], title)
        except Exception:
            pass  # notifications off in Windows settings: nothing to do


def on_close():
    """X on the window: hide to the tray, like IDM. Downloads keep going; Exit in the tray menu really quits."""
    global HIDDEN
    if TRAY is None:
        return True  # no tray icon (pystray missing): closing exits as before
    HIDDEN = True
    WINDOW.hide()
    if not settings().get("told_tray"):
        settings(told_tray=True)
        notify("FastDL is still running", "Downloads continue in the background. Open or exit FastDL from this icon.")
    return False  # cancel the close


def start_tray():
    """Tray icon: Open (double-click), Start with Windows, Exit; tooltip shows live speed."""
    global TRAY
    try:
        import pystray
    except ImportError:
        return

    def toggle_autostart(icon, item):
        on = not settings().get("autostart", True)
        settings(autostart=on)
        set_autostart(on)

    TRAY = pystray.Icon("FastDL", app_icon(64), "FastDL", pystray.Menu(
        pystray.MenuItem("Open FastDL", lambda: show_window(), default=True),
        pystray.MenuItem("Start with Windows", toggle_autostart, checked=lambda item: settings().get("autostart", True)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", lambda: quit_app())))
    threading.Thread(target=TRAY.run, daemon=True).start()

    def tooltip():
        while True:
            busy = [d for d in list(M.items.values()) if d.status == "downloading"]
            speed = sum(d.info()["speed"] for d in busy)
            TRAY.title = f"FastDL - {len(busy)} downloading · {human(speed)}/s" if busy else "FastDL"
            time.sleep(2)
    threading.Thread(target=tooltip, daemon=True).start()


def quit_app():
    """Exit (tray menu): save the list (running downloads resume next start) and stop; aria2 follows us out."""
    M.save()
    if TRAY:
        TRAY.visible = False  # don't leave a ghost icon in the tray
    os._exit(0)


class API(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def trusted(self, post=False):
        # Host check blocks DNS rebinding; the custom header forces a CORS preflight
        # we never answer, so random websites can't add downloads.
        host_ok = self.headers.get("Host") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")
        return host_ok and (not post or self.headers.get("X-FastDL") == "1")

    def do_GET(self):
        if not self.trusted():
            return self.send(403, {"error": "forbidden"})
        if urllib.parse.urlparse(self.path).path == "/":  # also "/?progress=<id>": a download's own window
            with open(UI_FILE, "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if self.path == "/logo.png":  # header logo and page icon
            with open(LOGO, "rb") as f:
                return self.send(200, f.read(), "image/png")
        q = urllib.parse.urlparse(self.path).path.strip("/").split("/")
        if q[:2] == ["api", "dialog"] and len(q) == 3 and q[2] in DIALOGS:  # filling a Download File Info window
            s = settings()
            spec = DIALOGS[q[2]]
            return self.send(200, {
                "url": spec["page"], "filename": spec["filename"], "size": spec["size"], "ext": spec["ext"],
                "category": spec["category"], "categories": categories(),
                "folders": {c: cat_folder(c) for c in categories()}, "remembered": sorted((s.get("cat_paths") or {})),
                "recent": s.get("recent", [])})
        if self.path == "/api/settings":
            return self.send(200, options())
        if self.path == "/api/categories":
            return self.send(200, {"categories": categories(), "custom": categories()[len(BUILTIN_CATS):]})
        if self.path == "/api/schedule":
            waiting = sum(d.scheduled and d.status in ("paused", "error") for d in list(M.items.values()))
            return self.send(200, {**SCHEDULE_DEFAULT, **(settings().get("schedule") or {}), "waiting": waiting})
        if self.path == "/api/update":
            return self.send(200, {"current": APP_VERSION, "update": {k: v for k, v in UPDATE.items()},
                                   "source": settings().get("update_url") or UPDATE_URL})
        if self.path == "/api/power":
            return self.send(200, {"pending": M.power_pending, "note": M.power_note})
        if self.path == "/api/links":
            return self.send(200, {"enabled": MULTILINK, "links": links()})
        if self.path == "/api/downloads":
            return self.send(200, [d.info() for d in list(M.items.values())])
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.trusted(post=True):
            return self.send(403, {"error": "forbidden"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self.send(400, {"error": "bad json"})
        p = self.path.strip("/").split("/")
        if p == ["api", "links"]:
            global MULTILINK
            MULTILINK = bool(body.get("enabled"))  # applies to downloads that start from now on
            settings(multilink=MULTILINK)  # kept after a restart (it used to turn back ON every time)
            return self.send(200, {"enabled": MULTILINK, "links": links()})
        if p == ["api", "settings"]:  # Options
            o, new = options(), {}
            try:
                new["conns"] = min(32, max(1, int(body.get("conns", o["conns"]))))
                new["max_active"] = min(10, max(1, int(body.get("max_active", o["max_active"]))))
                new["global_limit"] = max(0, int(body.get("global_limit", o["global_limit"])))
                new["skip_smaller_mb"] = max(0.0, float(body.get("skip_smaller_mb", o["skip_smaller_mb"])))
            except (TypeError, ValueError):
                return self.send(400, {"error": "Numbers only, please"})
            folder = str(body.get("folder", o["folder"]) or "").strip()
            if folder and not os.path.isabs(folder):
                return self.send(400, {"error": "The download folder must be a full path, like C:\\Downloads"})
            new["folder"] = folder
            new["skip_types"] = " ".join(re.findall(r"[a-z0-9]{1,8}", str(body.get("skip_types", o["skip_types"])).lower()))
            for k in ("multilink", "ask_browser", "close_done"):
                new[k] = bool(body.get(k, o[k]))
            settings(**new)
            if "update_url" in body:
                u = str(body["update_url"] or "").strip()
                if u and not u.startswith("https://"):
                    return self.send(400, {"error": "The update address must start with https://"})
                settings(update_url=u)
            if "autostart" in body and os.name == "nt":
                settings(autostart=bool(body["autostart"]))
                set_autostart(bool(body["autostart"]))
            apply_options()
            return self.send(200, options())
        if p == ["api", "schedule"]:
            start, stop = str(body.get("start") or ""), str(body.get("stop") or "")
            days = body.get("days")
            if not HHMM.match(start) or (stop and not HHMM.match(stop)):
                return self.send(400, {"error": "Times must look like 02:30"})
            if not isinstance(days, list) or not days or not all(isinstance(x, int) and 0 <= x <= 6 for x in days):
                return self.send(400, {"error": "Pick at least one day"})
            sched = {"enabled": bool(body.get("enabled")), "start": start, "stop": stop, "days": sorted(set(days))}
            settings(schedule=sched)
            return self.send(200, sched)
        if p in (["api", "schedule", "start"], ["api", "schedule", "stop"]):
            M.run_schedule(p[2] == "start")
            return self.send(200, {"ok": True})
        if len(p) == 4 and p[:2] == ["api", "progress"] and p[3] in ("open", "close"):
            if p[3] == "close":
                close_progress(p[2])
                return self.send(200, {"ok": True})
            return self.send(200, {"window": open_progress(p[2])})  # False: no app window, page shows it inline
        if p == ["api", "update", "check"]:
            try:
                found = check_update()
            except Exception as e:
                return self.send(200, {"current": APP_VERSION, "error": friendly_error(e)})
            return self.send(200, {"current": APP_VERSION, "update": found or {}})
        if p == ["api", "update", "install"]:
            if not UPDATE.get("url"):
                return self.send(400, {"error": "No update to install"})
            threading.Thread(target=install_update, daemon=True).start()
            return self.send(200, {"ok": True})
        if p == ["api", "show"]:
            show_window()
            return self.send(200, {"ok": True})
        if p == ["api", "categories"]:  # the dialog's + button: a category of your own
            name = clean(str(body.get("name") or "")).strip()
            if not name:
                return self.send(400, {"error": "Type a name for the category"})
            if name not in categories():
                settings(categories=settings().get("categories", []) + [name])
            return self.send(200, {"name": name, "categories": categories(), "folder": cat_folder(name)})
        if len(p) == 4 and p[:2] == ["api", "dialog"] and p[3] in ("submit", "cancel") and p[2] in DIALOGS:
            spec = DIALOGS.pop(p[2])
            threading.Timer(0.2, spec["window"].destroy).start()  # after this reply reaches the window
            if p[3] == "cancel":
                return self.send(200, {"ok": True})
            folder, filename = str(body.get("folder") or "").strip(), clean(str(body.get("filename") or ""))
            if not folder or not filename:
                return self.send(400, {"error": "Choose where to save it"})
            cat = str(body.get("category") or spec["category"])
            s = settings()
            if body.get("remember"):  # "Remember this path for <category>"
                settings(cat_paths={**(s.get("cat_paths") or {}), cat: folder})
            settings(recent=[folder] + [r for r in s.get("recent", []) if r != folder][:7])  # Save As history
            later = bool(body.get("later"))
            d = M.add(spec["url"], folder, spec["headers"], spec["kind"], spec["streams"], None, later, CONNS,
                      spec["format"], str(body.get("desc") or ""), filename)
            d.category = cat
            if not later:
                open_progress(d.id)
            return self.send(200, d.info())
        if p == ["api", "browse"]:
            return self.send(200, {"folder": browse(str(body.get("folder") or DEFAULT_FOLDER))})
        if p == ["api", "downloads"] and body.get("torrent"):  # .torrent file from the Add dialog
            try:
                path = save_torrent(str(body["torrent"]), str(body.get("torrent_name") or ""))
            except (ValueError, TypeError):
                return self.send(400, {"error": "That is not a valid .torrent file."})
            d = M.add(path, str(body.get("folder") or "").strip() or DEFAULT_FOLDER, {}, "torrent", later=bool(body.get("later")))
            if not body.get("later"):
                open_progress(d.id)
            return self.send(200, d.info())
        if p in (["api", "downloads"], ["api", "formats"], ["api", "dialog"]):
            url = str(body.get("url", "")).strip()
            if not url.startswith(("http://", "https://", "magnet:")):
                return self.send(400, {"error": "need an http(s) or magnet link"})
            headers = {h: str(body[k]) for h, k in (("Referer", "referer"), ("Cookie", "cookie"), ("User-Agent", "ua")) if body.get(k)}
            streams = [{"url": s["url"], "manifest": bool(s.get("manifest"))} for s in body.get("streams") or []
                       if isinstance(s, dict) and str(s.get("url", "")).startswith(("http://", "https://"))]
            if p[1] == "formats":
                return self.send(200, formats(url, headers, streams, body.get("title")))
            if body.get("kind") in (None, "file") and url.startswith("http"):
                old = M.match_expired(url, headers)  # the same file again, from a fresh link: continue it
                if old:
                    open_progress(old.id)
                    return self.send(200, {**old.info(), "window": "resumed", "resumed": True})
            if p[1] == "dialog":  # quality picked / browser download caught: ask the user first, like IDM
                return self.send(200, {"window": open_dialog(dialog_spec(body, headers, streams))})
            try:
                conns = min(32, max(1, int(body.get("conns") or CONNS)))  # past 32 servers ban you, not speed you up
            except (TypeError, ValueError):
                return self.send(400, {"error": "conns must be a number"})
            raw = body.get("mirrors") or []
            raw = raw.split() if isinstance(raw, str) else raw  # the dialog's box: one link per line
            mirrors = [m.strip() for m in raw if isinstance(m, str) and m.strip().startswith(("http://", "https://"))][:10]
            cat = body.get("category") if body.get("category") in categories() else None
            folder = str(body.get("folder") or "").strip() or (cat_folder(cat) if cat else DEFAULT_FOLDER)
            post, response = body.get("post"), None
            if post:  # form download: send the form now, while the browser's own download waits paused
                try:
                    post = {"body": base64.b64encode(base64.b64decode(str(post.get("body", "")), validate=True)).decode(),
                            "type": str(post.get("type") or "application/x-www-form-urlencoded")[:200]}
                    probe_dl = Download(url, folder, headers)
                    probe_dl.post = post
                    response = probe_dl._open_post()
                except Exception as e:  # e.g. a one-time form: the browser keeps its download
                    return self.send(400, {"error": friendly_error(e)})
                if response.geturl() == url and response.headers.get_content_type() == "text/html":
                    response.close()  # the server answered with a page, not the file: leave it to the browser
                    return self.send(400, {"error": "The site answered with a web page, not the file"})
            d = M.add(url, folder, headers, body.get("kind"), streams,
                      body.get("title"), bool(body.get("later")), conns,
                      body.get("format") or None, str(body.get("desc") or ""), body.get("filename") or None, mirrors,
                      post, response)
            d.category = cat
            notify("Download started", body.get("filename") or d.name or urllib.parse.urlparse(url).hostname or url)
            if not body.get("later") and not body.get("quiet"):  # quiet: "Download all" adds many at once
                open_progress(d.id)  # its own window pops up, like IDM's (also while FastDL sits in the tray)
            return self.send(200, d.info())
        if p == ["api", "power", "cancel"]:
            M.cancel_power()
            return self.send(200, {"ok": True})
        if len(p) == 4 and p[:2] == ["api", "downloads"] and p[3] == "options" and p[2] in M.items:
            try:
                limit = None if body.get("limit") is None else max(0, int(body["limit"]))
            except (TypeError, ValueError):
                return self.send(400, {"error": "limit must be bytes per second"})
            on_done = body.get("on_done")
            if on_done is not None and on_done not in ON_DONE:
                return self.send(400, {"error": f"on_done must be one of {', '.join(ON_DONE)}"})
            sched = body.get("scheduled")
            M.options(M.items[p[2]], limit, on_done, None if sched is None else bool(sched))
            return self.send(200, M.items[p[2]].info())
        if len(p) == 4 and p[:2] == ["api", "downloads"] and p[3] == "files" and p[2] in M.items:
            d = M.items[p[2]]  # torrent: which files to download
            known = {f["index"] for f in getattr(d, "files", ())}
            sel = body.get("selected")
            if not isinstance(d, Torrent) or not known:
                return self.send(400, {"error": "This download has no file list"})
            if not isinstance(sel, list) or not sel or not all(isinstance(i, int) and i in known for i in sel):
                return self.send(400, {"error": "Pick at least one file"})
            d.choose(sel)
            return self.send(200, d.info())
        if len(p) == 4 and p[:2] == ["api", "downloads"] and p[3] == "remove" and p[2] in M.items:
            M.remove(M.items[p[2]], files=bool(body.get("files")))  # files: "also delete the file from disk"
            close_progress(p[2])
            return self.send(200, {"ok": True})
        if len(p) == 4 and p[:2] == ["api", "downloads"] and p[3] in ("pause", "resume", "folder") and p[2] in M.items:
            getattr(M, p[3])(M.items[p[2]])
            return self.send(200, {"ok": True})
        self.send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


class Server(ThreadingHTTPServer):
    # Python's default (SO_REUSEADDR) lets a 2nd program on Windows bind the same port and steal requests;
    # off, the port is ours alone, and "port taken" reliably means "FastDL is already running"
    allow_reuse_address = False
    daemon_threads = True


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def cli(url, folder):
    d = make(url, folder)
    t = threading.Thread(target=d.run, daemon=True)
    t.start()
    while t.is_alive():
        i = d.info()
        total = human(i["size"]) if i["size"] else "?"
        print(f"\r{human(i['done'])} / {total}   {human(i['speed'])}/s   {i['active']} conns   ", end="", flush=True)
        t.join(0.5)
    print(f"\n{d.status}: {d.error or d.dest}")


def windows_setup():
    """Browser launcher, start with Windows, shortcuts. Rewritten each start, so a moved FastDL.exe keeps working."""
    if os.name != "nt" or os.environ.get("FASTDL_HOME"):  # a test copy must not take over the real registration
        return
    try:
        register_launcher()
        s = settings()
        set_autostart(s.get("autostart", True))
        installed = os.path.exists(os.path.join(os.path.dirname(sys.executable), "unins000.exe"))
        if FROZEN and not installed and not s.get("shortcuts_made"):  # the installer makes its own shortcuts
            make_shortcuts()
            settings(shortcuts_made=True)
    except (OSError, subprocess.SubprocessError) as e:
        print("Windows setup:", e)  # FastDL itself still works


if __name__ == "__main__":
    tray_start = "--tray" in sys.argv[1:]  # started with Windows: straight to the tray, no window
    args = [a for a in sys.argv[1:] if a != "--tray"]
    if args:
        cli(args[0], args[1] if len(args) > 1 else ".")
    else:
        url = f"http://127.0.0.1:{PORT}"
        try:  # take the port: it's ours, or FastDL is already running. Never a 2nd copy.
            # (it used to *ask* the port first; on Windows a refused connection takes ~2 s, every single start)
            srv = Server(("127.0.0.1", PORT), API)
        except OSError:  # already running: show its window (starting with Windows: just leave it be)
            if not tray_start:
                if os.name == "nt":  # we were just launched by the user: let the running copy take the foreground
                    import ctypes
                    ctypes.windll.user32.AllowSetForegroundWindow(-1)
                try:
                    urllib.request.urlopen(urllib.request.Request(url + "/api/show", b"{}", {"X-FastDL": "1"}), timeout=5)
                except urllib.error.HTTPError:  # an older FastDL without /api/show
                    webbrowser.open(url)
                except OSError:
                    pass
            sys.exit()
        threading.Thread(target=windows_setup, daemon=True).start()  # shortcuts take a few seconds: don't wait
        M.path = STATE_FILE
        apply_options()  # saved Options first, so resumed downloads already follow them
        M.load()
        threading.Thread(target=M.autosave, daemon=True).start()
        threading.Thread(target=scheduler, daemon=True).start()
        threading.Thread(target=update_checker, daemon=True).start()
        try:
            import webview  # the app's own window (WebView2, built into Windows), not a browser tab
        except ImportError:
            webview = None
        if webview:
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            start_tray()
            threading.Thread(target=progress_titles, daemon=True).start()
            HIDDEN = tray_start
            WINDOW = webview.create_window("FastDL", url, width=1280, height=820, min_size=(760, 520), hidden=tray_start)
            WINDOW.events.closing += on_close  # X hides to the tray instead of quitting
            webview.start()  # returns only if the window really closed (no tray icon available)
            quit_app()
        else:  # pywebview missing: fall back to the browser
            print(f"FastDL running at {url}  (Ctrl+C to quit)")
            webbrowser.open(url)
            try:
                srv.serve_forever()
            finally:
                M.save()
