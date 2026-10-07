// IDM-style video button: hover a video -> "Download this video" -> quality menu -> Download File Info dialog.
(() => {
  if (window.__fastdl) return;
  window.__fastdl = true;

  const CATS = ["Video", "Music", "Documents", "Compressed", "Programs", "Other"];
  const host = document.createElement("div");
  const root = host.attachShadow({ mode: "closed" }); // page CSS can't touch our UI
  root.innerHTML = `<style>
    :host { all: initial; }
    * { box-sizing: border-box; }
    .ui { --bg: #ffffff; --bar: #f3f4f6; --fg: #1d2127; --muted: #5f6672; --line: #c9ced6; --hover: #e5efff; --accent: #1f5fd6;
      font: 13px/1.35 "Segoe UI", system-ui, sans-serif; color: var(--fg); }
    @media (prefers-color-scheme: dark) {
      .ui { --bg: #2b2d31; --bar: #232428; --fg: #e8e9eb; --muted: #a3a8b0; --line: #4a4d55; --hover: #3a4558; --accent: #4c9aff; }
    }
    [hidden] { display: none !important; }

    .bar { position: fixed; z-index: 2147483647; display: none; gap: 3px; align-items: center; font-weight: 600; }
    .bar button { all: initial; box-sizing: border-box; cursor: pointer; display: inline-flex; align-items: center;
      border: 1px solid #8a9bb0; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,.35);
      background: linear-gradient(#fefefe, #e9edf2 50%, #d9dfe7 51%, #e4e9ef); }
    .bar button:hover { border-color: #3c7fb1; background: linear-gradient(#f2f9fe, #d8eefb 50%, #bee6fd 51%, #a7d9f5); }
    .bar button:active { background: linear-gradient(#e5f4fc, #c4e5f6 50%, #98d1ef 51%, #68b3db); }
    .bar .main { gap: 6px; height: 24px; padding: 0 12px 0 7px; color: #e8541c; font: 600 13px "Segoe UI", system-ui, sans-serif;
      text-shadow: 0 1px 0 #fff; white-space: nowrap; }
    .bar .small { justify-content: center; width: 20px; height: 20px; color: #34475e; font: 700 12px "Segoe UI", sans-serif; }
    .bar .logo { width: 18px; height: 18px; flex: none; border-radius: 4px; }

    .menu { position: fixed; z-index: 2147483647; width: min(620px, calc(100vw - 16px)); max-height: min(420px, 70vh); overflow: auto;
      padding: 6px 0; background: var(--bg); border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 10px 30px rgba(0,0,0,.35); }
    .mi { all: unset; box-sizing: border-box; display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 14px; align-items: center;
      width: 100%; padding: 7px 14px; cursor: pointer; color: var(--fg); font: inherit; }
    .mi:hover, .mi:focus-visible { background: var(--hover); }
    .mt { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .ml { white-space: nowrap; }
    .ms { color: var(--muted); white-space: nowrap; min-width: 64px; text-align: right; font-variant-numeric: tabular-nums; }
    .mstate { padding: 10px 14px; color: var(--muted); }
    .mi.all { grid-template-columns: 1fr; font-weight: 600; }
    .msep { height: 1px; margin: 4px 10px; background: var(--line); }
    .err { color: #d93025; }

    .overlay { position: fixed; inset: 0; z-index: 2147483647; display: grid; place-items: center; background: rgba(0,0,0,.35); }
    .dlg { width: min(680px, calc(100vw - 32px)); background: var(--bg); border: 1px solid var(--line); border-radius: 8px;
      box-shadow: 0 20px 60px rgba(0,0,0,.45); overflow: hidden; color: var(--fg); }
    .dhead { display: flex; align-items: center; justify-content: space-between; padding: 0 0 0 14px; height: 38px; background: var(--bar);
      border-bottom: 1px solid var(--line); }
    .dhead button { all: unset; width: 44px; height: 38px; display: grid; place-items: center; cursor: pointer; color: var(--fg); }
    .dhead button:hover { background: #c42b1c; color: #fff; }
    .dbody { display: flex; gap: 18px; padding: 18px; }
    .fields { flex: 1; min-width: 0; display: grid; grid-template-columns: 90px minmax(0, 1fr); gap: 12px 10px; align-items: center; }
    .fields label { text-align: right; color: var(--fg); }
    .row { display: flex; gap: 8px; min-width: 0; }
    input, select { font: inherit; color: var(--fg); background: var(--bg); height: 32px; padding: 0 8px; min-width: 0; width: 100%;
      border: 1px solid var(--line); border-radius: 4px; outline: 0; }
    input:focus, select:focus { border-color: var(--accent); box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 30%, transparent); }
    input[readonly] { color: var(--muted); }
    .side { flex: none; width: 96px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; }
    .ext { width: 56px; height: 64px; display: grid; place-items: end center; padding-bottom: 10px; border-radius: 6px;
      background: linear-gradient(135deg, #ff9a3c, #e8541c); color: #fff; font-weight: 700; font-size: 12px; }
    .size { font-weight: 600; font-variant-numeric: tabular-nums; text-align: center; }
    .foot { display: flex; justify-content: flex-end; gap: 10px; padding: 0 18px 18px; flex-wrap: wrap; }
    .btn { font: inherit; height: 32px; min-width: 120px; padding: 0 14px; border: 1px solid var(--line); border-radius: 4px;
      background: var(--bar); color: var(--fg); cursor: pointer; }
    .btn:hover { background: var(--hover); }
    .btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }
    .browse { width: 40px; min-width: 40px; }
  </style>
  <div class="ui">
    <div class="bar">
      <button class="main"><img class="logo" src="${chrome.runtime.getURL("icons/32.png")}" alt="">
        <span class="label">Download this video</span></button>
      <button class="small help" title="Open FastDL">?</button>
      <button class="small close" title="Hide for this video">&#x2715;</button>
    </div>
    <div class="menu" role="menu" aria-label="Choose quality" hidden></div>
    <div class="overlay" hidden>
      <form class="dlg" role="dialog" aria-modal="true" aria-labelledby="dt">
        <div class="dhead"><span id="dt">Download File Info</span><button type="button" class="x" aria-label="Close">&#x2715;</button></div>
        <div class="dbody">
          <div class="fields">
            <label for="fUrl">URL</label><input id="fUrl" readonly>
            <label for="fCat">Category</label>
            <select id="fCat">${CATS.map(c => `<option>${c}</option>`).join("")}</select>
            <label for="fSave">Save As</label>
            <div class="row"><input id="fSave" required spellcheck="false"><button type="button" class="btn browse" aria-label="Choose folder" title="Choose folder">...</button></div>
            <label for="fDesc">Description</label><input id="fDesc" placeholder="Optional note">
          </div>
          <div class="side"><div class="ext" id="fExt">MP4</div><div class="size" id="fSize"></div></div>
        </div>
        <div class="foot">
          <button type="button" class="btn later">Download Later</button>
          <button class="btn primary">Start Download</button>
          <button type="button" class="btn cancel">Cancel</button>
        </div>
      </form>
    </div>
  </div>`;

  const $ = s => root.querySelector(s);
  const bar = $(".bar"), label = $(".label"), menu = $(".menu"), overlay = $(".overlay"), save = $("#fSave");
  const hidden = new WeakSet();
  let video = null, hideTimer = null, menuOpen = false, dlgOpen = false, info = null, pick = null, ctx = null;

  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`);
  const size = n => { const u = ["B", "KB", "MB", "GB"]; let i = 0; while (n >= 1024 && i < 3) { n /= 1024; i++; } return n.toFixed(i ? 2 : 0) + " " + u[i]; };
  const safe = s => (s || "video").replace(/[\\/:*?"<>|\x00-\x1f]/g, "_").trim().slice(0, 150) || "video";
  const split = p => { const i = Math.max(p.lastIndexOf("\\"), p.lastIndexOf("/")); return [p.slice(0, i), p.slice(i + 1)]; };
  const send = msg => chrome.runtime.sendMessage(msg).catch(() => false); // false: FastDL off or extension reloaded

  function place() {
    if (!video || !video.isConnected) { if (!menuOpen && !dlgOpen) bar.style.display = "none"; return; }
    const r = video.getBoundingClientRect();
    bar.style.top = Math.max(r.top + 8, 8) + "px";
    bar.style.left = Math.max(r.right - bar.offsetWidth - 8, r.left + 8) + "px";
    if (menuOpen) {
      const b = bar.getBoundingClientRect();
      menu.style.top = b.bottom + 4 + "px";
      menu.style.left = Math.max(8, Math.min(b.left, innerWidth - menu.offsetWidth - 8)) + "px";
    }
  }

  // elementsFromPoint sees videos under player overlays, and keeps the video found while over our bar
  // ponytail: videos inside closed shadow roots aren't found; toolbar button still works there
  document.addEventListener("pointermove", e => {
    if (menuOpen || dlgOpen) return; // stay put while the user picks
    const v = document.elementsFromPoint(e.clientX, e.clientY).find(el => el.tagName === "VIDEO");
    if (v && !hidden.has(v) && v.offsetWidth > 160 && v.offsetHeight > 90) {
      clearTimeout(hideTimer);
      hideTimer = null;
      if (v !== video) { video = v; label.textContent = "Download this video"; }
      bar.style.display = "flex";
      place();
    } else if (video && !hideTimer) {
      hideTimer = setTimeout(() => { bar.style.display = "none"; video = null; hideTimer = null; }, 1500);
    }
  }, { passive: true });
  addEventListener("scroll", place, { capture: true, passive: true });
  addEventListener("resize", place, { passive: true });

  // Typing in our dialog must not trigger the site's shortcuts (YouTube: f = fullscreen, k = pause ...).
  // Page listeners see events from our closed shadow root as coming from `host`.
  for (const type of ["keydown", "keyup", "keypress"]) {
    addEventListener(type, e => {
      if ((menuOpen || dlgOpen) && e.composedPath()[0] === host) {
        e.stopImmediatePropagation();
        if (type === "keydown" && e.key === "Escape") dlgOpen ? closeDialog() : closeMenu();
      }
    }, true);
  }

  const on = (sel, fn) => $(sel).addEventListener("click", e => { e.preventDefault(); e.stopPropagation(); fn(e); });

  // ---- quality menu ----
  function closeMenu() { menuOpen = false; menu.hidden = true; }
  on(".main", async () => {
    if (menuOpen) return closeMenu();
    ctx = { pageUrl: location.href, src: video?.currentSrc };
    menuOpen = true;
    menu.hidden = false;
    menu.innerHTML = `<div class="mstate">Finding available qualities... (starts FastDL if it's closed)</div>`;
    place();
    const r = await send({ type: "formats", ...ctx });
    if (!menuOpen) return;
    if (!r || !r.ok) {
      menu.innerHTML = `<div class="mstate err">Couldn't start FastDL. Open FastDL.exe once (it sets up this button), then try again.</div>`;
      return;
    }
    info = r;
    menu.innerHTML = (r.error ? `<div class="mstate err">${esc(r.error)}</div>` : "") +
      (r.items.length > 1 ? `<button class="mi all" role="menuitem" data-all="1"><span class="mt">Download all</span></button><div class="msep"></div>` : "") +
      r.items.map((it, i) =>
      `<button class="mi" role="menuitem" data-i="${i}"><span class="mt">${i + 1}. ${esc(r.title)}</span>` +
      `<span class="ml">${esc(it.label)}</span><span class="ms">${it.size ? size(it.size) : ""}</span></button>`).join("");
    place();
    menu.querySelector(".mi")?.focus();
  });
  menu.addEventListener("click", async e => {
    const b = e.target.closest(".mi");
    if (!b) return;
    e.stopPropagation();
    closeMenu();
    if (b.dataset.all) { // IDM's "Download all": every quality and subtitle, straight into the list
      label.textContent = "Adding...";
      const n = await send({ type: "downloadAll", ...ctx, title: info.title, items: info.items });
      label.textContent = n ? `Added ${n} downloads to FastDL ✓` : "Couldn't start FastDL";
      return;
    }
    const item = info.items[+b.dataset.i];
    // FastDL's own "Download File Info" window, on top of the browser, like IDM's
    const r = await send({ type: "dialog", ...ctx, title: info.title, item });
    if (r && r.window) label.textContent = "Download this video";
    else openDialog(item); // no FastDL app window (browser-only mode): ask right here instead
  });
  document.addEventListener("click", () => { if (menuOpen) closeMenu(); }); // click elsewhere closes it

  // ---- Download File Info dialog ----
  function openDialog(item) {
    pick = item;
    dlgOpen = true;
    const cat = /audio only/i.test(item.label) ? "Music" : "Video";
    $("#fUrl").value = ctx.pageUrl;
    $("#fCat").value = cat;
    save.value = `${info.folder}\\${cat}\\${safe(info.title || document.title)}.${item.ext}`;
    $("#fDesc").value = "";
    $("#fExt").textContent = String(item.ext || "file").toUpperCase();
    $("#fSize").textContent = item.size ? size(item.size) : "Size unknown";
    overlay.hidden = false;
    save.focus();
  }
  function closeDialog() { dlgOpen = false; overlay.hidden = true; }

  $("#fCat").addEventListener("change", () => {
    save.value = `${info.folder}\\${$("#fCat").value}\\${split(save.value)[1]}`;
  });
  on(".browse", async () => {
    const [dir, name] = split(save.value);
    const r = await send({ type: "browse", folder: dir });
    if (r && r.folder) save.value = `${r.folder}\\${name}`;
  });

  async function start(later) {
    const [folder, filename] = split(save.value.trim());
    if (!folder || !filename) { save.focus(); return; }
    closeDialog();
    bar.style.display = "flex";
    label.textContent = "Sending...";
    const ok = await send({ type: "download", ...ctx, format: pick.format, folder, filename, desc: $("#fDesc").value.trim(), later });
    label.textContent = ok ? (later ? "Added to FastDL ✓" : "Downloading in FastDL ✓") : "Couldn't start FastDL";
  }
  $(".dlg").addEventListener("submit", e => { e.preventDefault(); start(false); });
  on(".later", () => start(true));
  on(".cancel", closeDialog);
  on(".x", closeDialog);

  on(".help", () => send({ type: "show" })); // brings the FastDL window to the front
  on(".close", () => { if (video) hidden.add(video); bar.style.display = "none"; video = null; closeMenu(); });

  document.documentElement.appendChild(host);
})();
