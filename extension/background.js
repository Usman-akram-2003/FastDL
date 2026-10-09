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
const MANIFEST_URL = /\.(m3u8|mpd)(\?|$)/i;
const MANIFEST_TYPE = /mpegurl|dash\+xml/i;

async function call(path, body) {
  const r = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-FastDL": "1" },
    body: JSON.stringify(body),
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
  } catch { // nothing listening
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
  const type = h["content-type"] || "";
  const size = +(h["content-range"] || "").split("/")[1] || +h["content-length"] || 0;
  const manifest = MANIFEST_URL.test(d.url) || MANIFEST_TYPE.test(type);
  // ponytail: video/* over 2 MB counts as a real file; sites streaming bare mp4 segments without a manifest can fool it
  const file = /^video\//i.test(type) && !/mp2t|iso\.segment/i.test(type) && size > 2e6;
  if (manifest || file) remember(d.tabId, { url: d.url, manifest, size });
}, { urls: ["<all_urls>"], types: ["xmlhttprequest", "media", "other"] }, ["responseHeaders"]);

async function remember(tabId, s) {
  const key = "t" + tabId;
  const list = (await session.get(key))[key] || [];
  if (list.some(x => x.url === s.url)) return;
  list.push(s);
  await session.set({ [key]: list.slice(-20) });
  action.setBadgeText({ tabId, text: String(Math.min(list.length, 20)) });
}

ext.tabs.onUpdated.addListener((tabId, info) => {
  if (info.url) session.remove("t" + tabId); // new page (or YouTube-style in-page navigation)
});
ext.tabs.onRemoved.addListener(tabId => session.remove("t" + tabId));

// Streams this tab played, best first: manifests, then the playing file, then the biggest files.
async function streamsOf(tab, src) {
  const key = "t" + tab.id;
  const list = (await session.get(key))[key] || [];
  const manifests = list.filter(s => s.manifest);
  const files = list.filter(s => !s.manifest && s.url !== src).sort((a, b) => b.size - a.size);
  const playing = /^https?:/.test(src || "") ? [{ url: src, manifest: MANIFEST_URL.test(src) }] : [];
  return [...manifests, ...playing, ...files];
}

// Page url + sniffed streams; FastDL picks: known site -> yt-dlp, else manifest, else the playing file.
async function grab(tab, pageUrl, src, extra = {}) {
  return send(pageUrl, pageUrl, "page", { streams: await streamsOf(tab, src), title: tab.title, ...extra });
}

// Messages from the video button (button.js). Content scripts can't call FastDL themselves:
// the page's CORS rules apply to them, so the background does every request.
const HANDLERS = {
  grab: (m, tab) => grab(tab, m.pageUrl, m.src).then(ok => (badge(ok), ok)),
  formats: async (m, tab) =>
    api("/formats", { ...(await browserHeaders(m.pageUrl, m.pageUrl)), streams: await streamsOf(tab, m.src), title: tab.title }),
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
  const ext = (name.match(/\.([a-z0-9]{1,8})$/i) || [])[1]?.toLowerCase();
  const size = item.fileSize > 0 ? item.fileSize : item.totalBytes;
  if (ext && o.skip_types.split(" ").includes(ext)) return;
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

// Toolbar button: grab the video on the current page.
action.onClicked.addListener(tab =>
  blocked(tab.url) ? badge(false) : grab(tab, tab.url).then(badge, () => badge(false)));

ext.runtime.onInstalled.addListener(() => {
  ext.contextMenus.create({ id: "link", title: "Download with FastDL", contexts: ["link", "video", "audio", "image"] });
  ext.contextMenus.create({ id: "page", title: "Download video on this page with FastDL", contexts: ["page"] });
});

ext.contextMenus.onClicked.addListener((info, tab) => {
  const done = p => p.then(badge, () => badge(false));
  if ([tab?.url, info.pageUrl, info.frameUrl, info.linkUrl, info.srcUrl].some(blocked)) return badge(false);
  if (info.mediaType === "video" || info.menuItemId === "page") return done(grab(tab, info.frameUrl || info.pageUrl, info.srcUrl));
  done(send(info.linkUrl || info.srcUrl, tab?.url));
});
