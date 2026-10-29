// Exercise the real content script with an unresponsive extension worker.
const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const nodes = new Map(), timers = new Map();
let timerId = 0, messageListener;
let links = [], selectedLinks = new Set(), selectedText = '', collapsed = false;
function node() {
  return { style: {}, hidden: true, innerHTML: '', value: '',
    addEventListener(type, fn) { this[type] = fn; },
    getBoundingClientRect: () => ({ bottom: 0, left: 0 }),
    querySelector: () => null, focus() {} };
}
const root = { querySelector: key => {
  if (!nodes.has(key)) nodes.set(key, node());
  return nodes.get(key);
} };
const host = { attachShadow: () => root };
const context = vm.createContext({
  window: { getSelection: () => ({ isCollapsed: collapsed,
    containsNode: link => selectedLinks.has(link), toString: () => selectedText }) },
  chrome: { runtime: { getURL: s => s,
    sendMessage: () => new Promise(() => {}), onMessage: { addListener(fn) { messageListener = fn; } } } },
  document: { createElement: () => host, readyState: 'loading', body: null,
    addEventListener() {}, querySelectorAll: selector => selector === 'a[href], area[href]' ? links : [],
    documentElement: { appendChild() {} } },
  location: { href: 'https://www.youtube.com/watch?v=test', protocol: 'https:' },
  performance: { getEntriesByType: () => [] },
  MutationObserver: class { observe() {} }, addEventListener() {},
  innerWidth: 1920, URL,
  setTimeout(fn, ms) { const id = ++timerId; timers.set(id, { fn, ms }); return id; },
  clearTimeout(id) { timers.delete(id); },
});
vm.runInContext(fs.readFileSync('extension/button.js', 'utf8'), context);
(async () => {
  const pending = nodes.get('.main').click({ preventDefault() {}, stopPropagation() {} });
  const menu = nodes.get('.menu');
  assert.match(menu.innerHTML, /Finding available qualities/);
  const deadline = [...timers.values()].find(t => t.ms === 30000);
  assert.ok(deadline, 'menu must have a deadline even if messaging never resolves');
  deadline.fn();
  await pending;
  assert.match(menu.innerHTML, /did not return qualities within 30 seconds/);
  assert.doesNotMatch(menu.innerHTML, /Finding available qualities/);
  console.log('Content script stalled-worker regression passed');
  const extract = selected => {
    let result;
    messageListener({ type: 'extractLinks', selected }, {}, value => { result = Array.from(value); });
    return result;
  };
  links = [
    { href: 'https://files.test/a.zip#one' }, { href: 'https://files.test/a.zip#two' },
    { href: 'javascript:alert(1)' }, { href: 'mailto:user@example.com' },
    { href: '/b.pdf' }, { href: 'https://other.test/c.zip' },
  ];
  assert.deepEqual(extract(false), ['https://files.test/a.zip', 'https://www.youtube.com/b.pdf', 'https://other.test/c.zip']);
  selectedLinks.add(links[5]);
  selectedText = 'Get https://files.test/text.zip';
  assert.deepEqual(extract(true), ['https://other.test/c.zip', 'https://files.test/text.zip']);
  collapsed = true;
  assert.deepEqual(extract(true), []);
  links = Array.from({ length: 505 }, (_, i) => ({ href: `https://files.test/${i}.zip` }));
  assert.equal(extract(false).length, 500);
  console.log('4 page/selection link-collection scenarios passed');
})().catch(e => { console.error(e); process.exitCode = 1; });
