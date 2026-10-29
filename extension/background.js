// Chrome, Edge, Brave: `chrome`. Firefox: `browser` (promises); its `chrome` works with callbacks only.
const ext = globalThis.browser ?? chrome;
const action = ext.action ?? ext.browserAction; // Firefox's extension is Manifest V2
const mem = {}; // Firefox keeps this page alive and may lack storage.session
const session = ext.storage.session ?? { get: async k => ({ [k]: mem[k] }), set: async o => void Object.assign(mem, o), remove: async k => void delete mem[k] };

// The Chrome Web Store / Edge Add-ons edition (store.py sets STORE to true): the stores don't allow extensions that help
// download YouTube videos, so that edition ignores YouTube pages entirely (its manifest also keeps the video button off them).
// The extension you load from FastDL's install folder has no such limit.
const STORE = false;
const NO_VIDEO_SITES = /(^|\.)(youtube\.com|youtu\.be|youtube-nocookie\.com|googlevideo\.com)$/i;
const blocked = url => { try { return STORE && NO_VIDEO_SITES.test(new URL(url).hostname); } catch { return false; } };

const API = "http://127.0.0.1:9614/api";
const MANIFEST_URL = /\.(m3u8|mpd)($|[?#])/i;
const MANIFEST_TYPE = /mpegurl|dash\+xml|vnd\.apple\.mpegurl|vnd\.ms-sstr\+xml/i;
const MEDIA_URL = /\.(mp4|m4v|webm|mkv|flv|mov|avi|ts|m4s|m4a|aac|mp3|ogg|opus)($|[?#])/i;

async function call(path, body) {
  const r = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-FastDL": "1" },
    body: JSON.stringify(body),
    ...(path === "/formats" ? { signal: AbortSignal.timeout(25000) } : {}),
  });
  return { ok: r.ok, ...(await r.json()) };
}

// FastDL closed? Start it, like IDM: the browser runs FastDL's registered launcher (native messaging;
// FastDL registers it each time it starts), then we wait until it answers. One start for many callers.
let starting = null;
const alive = () => fetch(API + "/power").then(r => r.ok, () => false);
function startFastDL() {
  starting ??= (async () => {
    try { await ext.runtime.sendNativeMessage("com.fastdl.launcher", {}); } catch {} // launcher exits at once: "error" is normal
    for (let i = 0; i < 50; i++) { // up to 25 s: FastDL.exe unpacks itself first
      if (await alive()) return true;
      await new Promise(r => setTimeout(r, 500));
    }
    return false;
  })().finally(() => { starting = null; });
  return starting;
}

async function api(path, body) {
  try {
    return await call(path, body);
  } catch (e) { // nothing listening
    if (e.name === "TimeoutError" || e.name === "AbortError") throw e;
    if (!(await startFastDL())) throw new Error("FastDL could not start");
    return call(path, body);
  }
}

// Cookies + referer let FastDL download files that need you to be logged in.
async function browserHeaders(url, referer) {
  const cookie = url.startsWith("http") ? (await ext.cookies.getAll({ url })).map(c => `${c.name}=${c.value}`).join("; ") : "";
  return { url, referer, cookie, ua: navigator.userAgent };
}

// kind: "video" forces yt-dlp, "torrent" forces aria2, "page" = video button, empty = FastDL guesses.
async function send(url, referer, kind, extra = {}) {
  return (await api("/downloads", { ...(await browserHeaders(url, referer)), kind, ...extra })).ok;
}

function badge(ok) {
  action.setBadgeText({ text: ok ? "OK" : "!" });
  action.setBadgeBackgroundColor({ color: ok ? "#16a34a" : "#dc2626" });
  setTimeout(() => action.setBadgeText({ text: "" }), 2500);
}

// ---- stream sniffing: remember video streams each tab loads (session storage survives worker sleep) ----
ext.webRequest.onHeadersReceived.addListener(d => {
  if (d.tabId < 0) return;
  const h = Object.fromEntries((d.responseHeaders || []).map(x => [x.name.toLowerCase(), x.value || ""]));
  const type = (h["content-type"] || "").toLowerCase();
  let size = 0;
  const cr = h["content-range"] || "";
  const crMatch = cr.match(/\/(\d+)/);
  if (crMatch) {
    size = +crMatch[1];
  } else {
    size = +h["content-length"] || 0;
  }
  const manifest = MANIFEST_URL.test(d.url) || MANIFEST_TYPE.test(type);
  const mediaUrl = MEDIA_URL.test(d.url);
  const mediaType = /^(video|audio)\//i.test(type);
  const isSegment = /mp2t|iso\.segment/i.test(type) || /\.ts($|[?#])/i.test(d.url);

  if (manifest) {
    remember(d.tabId, { url: d.url, manifest: true, size: 0 });
    return;
  }
  if (mediaType || mediaUrl) {
    // Fast detection: accept immediate media chunks (>100KB or video/audio type or total size)
    if (!isSegment && (size > 100000 || mediaType || size === 0)) {
      remember(d.tabId, { url: d.url, manifest: false, size });
    } else if (isSegment && size > 50000) {
      remember(d.tabId, { url: d.url, manifest: false, size, isSegment: true });
    }
  }
}, { urls: ["<all_urls>"], types: ["xmlhttprequest", "media", "other"] }, ["responseHeaders"]);

async function remember(tabId, s) {
  const key = "t" + tabId;
  const list = (await session.get(key))[key] || [];
  if (list.some(x => x.url === s.url)) return;
  list.push(s);
  await session.set({ [key]: list.slice(-30) });
  action.setBadgeText({ tabId, text: String(Math.min(list.length, 30)) });
}

const tabUrls = new Map();
ext.tabs.onUpdated.addListener((tabId, info) => {
  if (info.url) {
    const oldUrl = tabUrls.get(tabId);
    tabUrls.set(tabId, info.url);
    try {
      const u1 = new URL(oldUrl || "");
      const u2 = new URL(info.url);
      const v1 = u1.searchParams.get("v"), v2 = u2.searchParams.get("v");
      // Only clear streams on true navigation (domain, path, or distinct video ID changed)
      if (u1.origin !== u2.origin || u1.pathname !== u2.pathname || (v1 && v2 && v1 !== v2)) {
        session.remove("t" + tabId);
        action.setBadgeText({ tabId, text: "" });
      }
    } catch {
      session.remove("t" + tabId);
      action.setBadgeText({ tabId, text: "" });
    }
  }
});
ext.tabs.onRemoved.addListener(tabId => {
  session.remove("t" + tabId);
  tabUrls.delete(tabId);
});

// Streams this tab played or detected, best first: manifests, then the playing file, then biggest files, then segments.
async function streamsOf(tab, src) {
  const key = "t" + tab.id;
  let list = (await session.get(key))[key] || [];
  if ((!list.length || !list.some(s => s.manifest || !s.isSegment)) && tab?.id) {
    try {
      const res = await ext.tabs.sendMessage(tab.id, { type: "extractStreams" });
      if (Array.isArray(res) && res.length) {
        list = [...list, ...res];
      }
    } catch {}
  }
  const manifests = list.filter(s => s.manifest);
  const files = list.filter(s => !s.manifest && s.url !== src && !s.isSegment).sort((a, b) => b.size - a.size);
  const segments = list.filter(s => s.isSegment);
  const playing = /^https?:/.test(src || "") ? [{ url: src, manifest: MANIFEST_URL.test(src) }] : [];
  return [...manifests, ...playing, ...files, ...segments];
}

// Page url + sniffed streams; FastDL picks: known site -> yt-dlp, else manifest, else the playing file.
async function grab(tab, pageUrl, src, extra = {}) {
  return send(pageUrl, pageUrl, "page", { streams: await streamsOf(tab, src), title: tab.title, ...extra });
}

// Messages from the video button (button.js). Content scripts can't call FastDL themselves:
// the page's CORS rules apply to them, so the background does every request.
const formatRequests = new Map();
const HANDLERS = {
  grab: (m, tab) => grab(tab, m.pageUrl, m.src).then(ok => (badge(ok), ok)),
  addStreams: (m, tab) => {
    if (Array.isArray(m.streams)) {
      for (const s of m.streams) remember(tab.id, s);
    }
    return Promise.resolve(true);
  },
  formats: async (m, tab) => {
    if (m.prefetch && !(await alive())) return false;
    // YouTube is extracted from its page URL. Playback chunks and blob URLs
    // change continuously and must not invalidate a page's quality lookup.
    const youtube = /(^|\.)(youtube\.com|youtu\.be|youtube-nocookie\.com)$/i.test(new URL(m.pageUrl).hostname);
    const streams = youtube ? [] : [...(m.pageStreams || []), ...(await streamsOf(tab, m.src))];
    const key = JSON.stringify([tab.id, m.pageUrl, youtube ? "" : m.src || "", [...new Set(streams.map(s => s.url))].sort()]);
    const previous = formatRequests.get(key);
    if (previous && Date.now() - previous.time < 60000) return previous.promise;
    const entry = { time: Date.now() };
    entry.promise = (async () => {
      try {
        // Public YouTube metadata doesn't need the account's cookies. Reading
        // them can stall before fetch's timeout starts, and signed-in extraction
        // can require additional account-specific challenges. Keep login support
        // as a fallback, and continue sending cookies for actual downloads.
        const headers = youtube ? { url: m.pageUrl, referer: m.pageUrl, ua: navigator.userAgent } :
          await browserHeaders(m.pageUrl, m.pageUrl);
        let result = await api("/formats", { ...headers, streams, title: tab.title });
        if (youtube && result.error) {
          result = await api("/formats", { ...(await browserHeaders(m.pageUrl, m.pageUrl)), streams, title: tab.title });
        }
        if (!result.ok || result.error) formatRequests.delete(key);
        return result;
      } catch (e) {
        formatRequests.delete(key);
        if (e.name === "TimeoutError" || e.name === "AbortError") {
          return { ok: true, items: [], error: "Quality lookup timed out. Check FastDL's connection to the video site, then click again to retry." };
        }
        throw e;
      }
    })();
    formatRequests.set(key, entry);
    for (const [k, v] of formatRequests) if (Date.now() - v.time >= 60000) formatRequests.delete(k);
    return entry.promise;
  },
  download: (m, tab) =>
    grab(tab, m.pageUrl, m.src, { format: m.format, folder: m.folder, filename: m.filename, desc: m.desc, later: m.later })
      .then(ok => (badge(ok), ok)),
  browse: m => api("/browse", { folder: m.folder }),
  // quality picked: FastDL asks name/folder/category in its own Download File Info window
  dialog: async (m, tab) =>
    api("/dialog", { ...(await browserHeaders(m.pageUrl, m.pageUrl)), kind: "page", streams: await streamsOf(tab, m.src),
      title: m.title, item: m.item }),
  // "Download all": every listed quality and subtitle, no questions, each into its category folder
  downloadAll: async (m, tab) => {
    const hdr = await browserHeaders(m.pageUrl, m.pageUrl), streams = await streamsOf(tab, m.src);
    let added = 0;
    for (const it of m.items) {
      const r = await api("/downloads", { ...hdr, url: it.sub_url || m.pageUrl, kind: it.sub_url ? "file" : "page", streams,
        format: it.format, filename: `${m.title}.${it.ext}`, category: /audio only/.test(it.label) ? "Music" : "Video", quiet: true });
      if (r.ok) added++;
    }
    return added;
  },
  show: () => api("/show", {}),
};

ext.runtime.onMessage.addListener((msg, sender, reply) => {
  const handle = HANDLERS[msg.type];
  if (!handle || !sender.tab) return;
  if (blocked(msg.pageUrl) || blocked(sender.tab.url)) { reply(false); return; }
  handle(msg, sender.tab).then(reply, () => reply(false)); // false = FastDL not running
  return true; // reply asynchronously
});

// ---- browser downloads: catch every file (pdf, excel, zip, exe, .torrent ...) ----
// FastDL's Options for browser downloads (asked at most every 30 s; defaults when FastDL is closed)
let opts = null, optsAt = 0;
async function browserOptions() {
  if (!opts || Date.now() - optsAt > 30000) {
    try { const r = await fetch(API + "/settings"); if (r.ok) { opts = await r.json(); optsAt = Date.now(); } } catch {}
  }
  return opts || { ask_browser: true, skip_smaller_mb: 0, skip_types: "jpg jpeg png gif webp svg ico bmp" };
}

// ---- form downloads (POST), like IDM: remember what the browser sent with a click, so FastDL can send it too ----
const posts = new Map(); // url -> { body (base64), type, t }
const b64 = bytes => { let s = ""; for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000)); return btoa(s); };
ext.webRequest.onBeforeRequest.addListener(d => {
  if (d.method !== "POST" || !d.requestBody) return;
  let entry = null;
  if (d.requestBody.formData) { // an ordinary form: send it back the same way
    const p = new URLSearchParams();
    for (const [k, vs] of Object.entries(d.requestBody.formData)) for (const v of vs) p.append(k, v);
    entry = { body: b64(new TextEncoder().encode(p.toString())), type: "application/x-www-form-urlencoded", form: true };
  } else if (d.requestBody.raw?.length) { // raw bytes (JSON etc.): content type comes in onBeforeSendHeaders
    const parts = d.requestBody.raw.filter(r => r.bytes).map(r => new Uint8Array(r.bytes));
    const all = new Uint8Array(parts.reduce((n, a) => n + a.length, 0));
    parts.reduce((off, a) => (all.set(a, off), off + a.length), 0);
    entry = { body: b64(all), type: "application/octet-stream" };
  }
  if (!entry || entry.body.length > 1_000_000) return;
  posts.set(d.url, { ...entry, t: Date.now() });
  for (const [u, e] of posts) if (Date.now() - e.t > 120000 || posts.size > 50) posts.delete(u); // keep it small
}, { urls: ["<all_urls>"], types: ["main_frame", "sub_frame", "xmlhttprequest", "other"] }, ["requestBody"]);
ext.webRequest.onBeforeSendHeaders.addListener(d => {
  const e = d.method === "POST" && posts.get(d.url);
  const ct = e && !e.form && d.requestHeaders?.find(h => h.name.toLowerCase() === "content-type");
  if (ct) e.type = ct.value;
}, { urls: ["<all_urls>"], types: ["main_frame", "sub_frame", "xmlhttprequest", "other"] }, ["requestHeaders"]);

ext.downloads.onCreated.addListener(async item => {
  const url = item.finalUrl || item.url;
  if (!/^https?:/.test(url) || item.state !== "in_progress" || blocked(url)) return;
  // Options > "Leave to the browser": these types / small files stay a normal browser download
  const o = await browserOptions();
  const name = (item.filename || new URL(url).pathname).split(/[\\/]/).pop();
  const suffix = (name.match(/\.([a-z0-9]{1,8})$/i) || [])[1]?.toLowerCase();
  const size = item.fileSize > 0 ? item.fileSize : item.totalBytes;
  if (suffix && o.skip_types.split(" ").includes(suffix)) return;
  if (o.skip_smaller_mb > 0 && size > 0 && size < o.skip_smaller_mb * 1048576) return;
  await ext.downloads.pause(item.id).catch(() => {});
  try {
    let taken;
    // a form answered with the file itself (a redirect would show up as a different, plain-link finalUrl)
    const post = posts.get(url);
    if (post) {
      posts.delete(url);
      // FastDL sends the same form at once; if the site refuses a second one, the browser keeps its download
      taken = await send(url, item.referrer, "file", { post: { body: post.body, type: post.type } });
    } else if (item.mime === "application/x-bittorrent") {
      taken = await send(url, item.referrer, "torrent"); // torrents go straight in
    } else if (o.ask_browser) { // like IDM: the Download File Info window asks first, then handles Start / Later / Cancel
      const r = await api("/dialog", { ...(await browserHeaders(url, item.referrer)), kind: "file" });
      taken = r.ok && (r.window || (await send(url, item.referrer, "file"))); // no app window: just download it
    } else {
      taken = await send(url, item.referrer, "file"); // Options: don't ask, download straight away
    }
    if (taken) {
      await ext.downloads.cancel(item.id);
      await ext.downloads.erase({ id: item.id });
      return;
    }
  } catch {}
  ext.downloads.resume(item.id).catch(() => {}); // FastDL not running: let the browser handle it
});

// Toolbar button: grab the video on the current page (works even without playback).
action.onClicked.addListener(async tab => {
  if (blocked(tab.url)) return badge(false);
  try {
    const res = await ext.tabs.sendMessage(tab.id, { type: "extractStreams" });
    if (Array.isArray(res) && res.length) {
      for (const s of res) remember(tab.id, s);
    }
  } catch {}
  grab(tab, tab.url).then(badge, () => badge(false));
});

ext.runtime.onInstalled.addListener(() => {
  ext.contextMenus.create({ id: "link", title: "Download with FastDL", contexts: ["link", "video", "audio", "image"] });
  ext.contextMenus.create({ id: "page", title: "Download video on this page with FastDL", contexts: ["page"] });
  ext.contextMenus.create({ id: "page-links", title: "Download page links with FastDL", contexts: ["page"] });
  ext.contextMenus.create({ id: "selected-links", title: "Download selected links with FastDL", contexts: ["selection"] });
});

ext.contextMenus.onClicked.addListener(async (info, tab) => {
  const done = p => p.then(badge, () => badge(false));
  if ([tab?.url, info.pageUrl, info.frameUrl, info.linkUrl, info.srcUrl].some(blocked)) return badge(false);
  if (info.menuItemId === "page-links" || info.menuItemId === "selected-links") {
    try {
      const urls = await ext.tabs.sendMessage(tab.id, { type: "extractLinks", selected: info.menuItemId === "selected-links" },
        { frameId: info.frameId ?? 0 });
      if (!Array.isArray(urls) || !urls.length) return badge(false);
      const result = await api("/batch", { urls });
      return badge(result.ok);
    } catch { return badge(false); }
  }
  if (info.mediaType === "video" || info.menuItemId === "page") {
    if (tab?.id) {
      try {
        const res = await ext.tabs.sendMessage(tab.id, { type: "extractStreams" });
        if (Array.isArray(res) && res.length) {
          for (const s of res) remember(tab.id, s);
        }
      } catch {}
    }
    return done(grab(tab, info.frameUrl || info.pageUrl, info.srcUrl));
  }
  done(send(info.linkUrl || info.srcUrl, tab?.url));
});
