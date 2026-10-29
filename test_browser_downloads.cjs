// Exercise the actual browser-event handlers without a browser or network.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const event = () => ({ addListener(fn) { this.listener = fn; } });
let operations = [], requests = [], accept = true, extraction;
const options = { ask_browser: true, skip_smaller_mb: 1, skip_types: 'jpg png' };
const chrome = {
  action: { onClicked: event(), setBadgeText() {}, setBadgeBackgroundColor() {} },
  webRequest: { onHeadersReceived: event(), onBeforeRequest: event(), onBeforeSendHeaders: event() },
  tabs: { onUpdated: event(), onRemoved: event(), sendMessage: async (tabId, message, options) => {
    extraction = { tabId, message, options };
    return ['https://files.example/a.zip'];
  } },
  runtime: { onMessage: event(), onInstalled: event() },
  downloads: { onCreated: event(),
    pause: async id => operations.push(['pause', id]),
    cancel: async id => operations.push(['cancel', id]),
    erase: async query => operations.push(['erase', query.id]),
    resume: async id => operations.push(['resume', id]) },
  contextMenus: { onClicked: event(), create() {} },
  cookies: { getAll: async () => [] }, storage: {},
};
const context = vm.createContext({ chrome, URL, URLSearchParams, TextEncoder, Uint8Array,
  navigator: { userAgent: 'test' }, setTimeout: () => 0, console, AbortSignal,
  fetch: async (url, request) => {
    if (url.endsWith('/settings')) return { ok: true, json: async () => options };
    requests.push({ url, body: JSON.parse(request.body) });
    return { ok: accept, json: async () => accept ? { window: true, ok: true } : { error: 'declined' } };
  },
});
vm.runInContext(fs.readFileSync('extension/background.js', 'utf8'), context);
const item = { id: 10, url: 'https://files.example/a.zip', filename: 'a.zip',
  state: 'in_progress', totalBytes: 3_000_000, referrer: 'https://files.example/' };
async function download(overrides = {}) {
  operations = []; requests = [];
  await chrome.downloads.onCreated.listener({ ...item, ...overrides });
}
(async () => {
  await download();
  assert.deepEqual(operations, [['pause', 10], ['cancel', 10], ['erase', 10]]);
  assert.ok(requests[0].url.endsWith('/dialog'));
  accept = false;
  await download();
  assert.deepEqual(operations, [['pause', 10], ['resume', 10]], 'declined handoff resumes the browser');
  accept = true; options.ask_browser = false;
  await download();
  assert.ok(requests[0].url.endsWith('/downloads'));
  assert.equal(operations[1][0], 'cancel');
  await download({ filename: 'image.jpg' });
  assert.deepEqual(operations, [], 'excluded file types stay in the browser');
  await download({ totalBytes: 100 });
  assert.deepEqual(operations, [], 'small files stay in the browser');
  console.log('5 browser-download handoff scenarios passed');
  for (const id of ['page-links', 'selected-links']) {
    operations = []; requests = [];
    await chrome.contextMenus.onClicked.listener({ menuItemId: id, frameId: 3 }, { id: 9, url: 'https://files.example' });
    assert.ok(requests[0].url.endsWith('/batch'));
    assert.deepEqual(requests[0].body.urls, ['https://files.example/a.zip']);
    assert.equal(extraction.message.selected, id === 'selected-links');
    assert.equal(extraction.options.frameId, 3, 'collect links from the clicked frame');
    assert.deepEqual(operations, [], 'batch links are reviewed rather than downloaded immediately');
  }
  console.log('2 browser batch-menu handoff scenarios passed');
})().catch(e => { console.error(e); process.exitCode = 1; });
