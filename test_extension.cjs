// Run with node test_extension.cjs. No browser, playback, or network required.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const event = () => ({ addListener() {} });
let calls = 0, available = true, fail = false, timeout = false, body, cookieStall = false, cookieReads = 0;
const chrome = {
  action: { onClicked: event() },
  webRequest: { onHeadersReceived: event(), onBeforeRequest: event(), onBeforeSendHeaders: event() },
  tabs: { onUpdated: event(), onRemoved: event(), sendMessage: async () => [] },
  runtime: { onMessage: event(), onInstalled: event() },
  downloads: { onCreated: event() }, contextMenus: { onClicked: event() },
  cookies: { getAll: async () => { cookieReads++; return cookieStall ? new Promise(() => {}) : []; } }, storage: {},
};
const context = vm.createContext({ chrome, URL, URLSearchParams, TextEncoder, Uint8Array,
  navigator: { userAgent: 'test' }, setTimeout, console, AbortSignal,
  fetch: async (url, options) => {
    if (url.endsWith('/power')) return { ok: available };
    calls++;
    assert.equal(options.signal instanceof AbortSignal, true, 'quality requests have a deadline');
    if (timeout) throw Object.assign(new Error('timed out'), { name: 'TimeoutError' });
    body = JSON.parse(options.body);
    await new Promise(resolve => setTimeout(resolve, 5));
    return { ok: true, json: async () => fail ? { error: 'temporary error', items: [] } :
      { items: [{ format: '22', label: '720p' }] } };
  },
});
vm.runInContext(fs.readFileSync('extension/background.js', 'utf8') +
  '\nglobalThis.handlers = HANDLERS;', context);
(async () => {
  const tab = { id: 1, title: 'Video' };
  const request = { pageUrl: 'https://www.youtube.com/watch?v=first', pageStreams: [] };
  available = false;
  assert.equal(await context.handlers.formats({ ...request, prefetch: true }, tab), false);
  assert.equal(calls, 0, 'hover must not start a closed app');
  available = true;
  cookieStall = true;
  const [a, b] = await Promise.race([Promise.all([
    context.handlers.formats({ ...request, prefetch: true }, tab),
    context.handlers.formats(request, tab),
  ]), new Promise((_, reject) => setTimeout(() => reject(new Error('Public YouTube qualities blocked on cookie lookup')), 200))]);
  cookieStall = false;
  assert.equal(cookieReads, 0, 'public YouTube quality lookup must not wait for account cookies');
  assert.equal(calls, 1, 'hover and click share the same extraction');
  assert.equal(a.items[0].format, b.items[0].format);
  assert.equal(body.streams.length, 0, 'YouTube lookup works before playback');
  await context.handlers.formats(request, tab);
  assert.equal(calls, 1, 'reopening uses the cached result');
  await context.handlers.formats({ ...request, src: 'blob:playing',
    pageStreams: [{ url: 'https://cdn.example/new-chunk.mp4' }] }, tab);
  assert.equal(calls, 1, 'YouTube playback changes must not invalidate page qualities');
  const next = { ...request, pageUrl: 'https://www.youtube.com/watch?v=second' };
  fail = true;
  await context.handlers.formats(next, tab);
  assert.equal(cookieReads, 1, 'extraction errors retry with account cookies for restricted videos');
  fail = false;
  await context.handlers.formats(next, tab);
  assert.equal(calls, 4, 'new videos and retries after extraction errors get fresh results');
  timeout = true;
  const stalled = { ...request, pageUrl: 'https://www.youtube.com/watch?v=stalled' };
  const result = await context.handlers.formats(stalled, tab);
  assert.match(result.error, /timed out/);
  timeout = false;
  const retry = await context.handlers.formats(stalled, tab);
  assert.equal(retry.items[0].format, '22', 'a timeout must allow a fresh retry');
  console.log('10 extension regression scenarios passed');
})().catch(e => { console.error(e); process.exitCode = 1; });
