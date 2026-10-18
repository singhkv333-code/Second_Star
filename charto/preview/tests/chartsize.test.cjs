const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function fixture({ bitmapMismatch = false, width = 800, height = 500, tableWidth = width, tableHeight = height, connected = true, collapsed = false } = {}) {
  const calls = [], frames = new Map(), listeners = new Map();
  let next = 0, resize, disconnected = false;
  const host = { isConnected: connected, clientWidth: width, clientHeight: height,
    querySelector: selector => selector === 'table' ? { getBoundingClientRect: () => ({ width: tableWidth, height: tableHeight }), querySelectorAll: () => [{ clientWidth: width - 80, clientHeight: height - 28, querySelector: () => ({ width: bitmapMismatch ? 200 : width - 80, height: height - 28, getBoundingClientRect: () => ({ width: width - 80, height: collapsed ? 2 : height - 28 }) }) }] } : null };
  const chart = { applyOptions: options => calls.push(options) };
  const context = vm.createContext({
    requestAnimationFrame: fn => { frames.set(++next, fn); return next; },
    cancelAnimationFrame: id => frames.delete(id),
    ResizeObserver: class { constructor(fn) { resize = fn; } observe() {} disconnect() { disconnected = true; } },
    document: { addEventListener: (key, fn) => listeners.set(key, fn), removeEventListener: key => listeners.delete(key) },
  });
  vm.runInContext(fs.readFileSync(require.resolve('../js/chartsize.js'), 'utf8'), context);
  const sizing = vm.runInContext('ChartSize', context);
  return { sizing, host, chart, calls, resize: () => resize(), listeners,
    disconnected: () => disconnected, flush() { const pending = [...frames.values()]; frames.clear(); pending.forEach(fn => fn()); } };
}

test('repairs stale width even when height is correct after docking', () => {
  const f = fixture({ tableWidth: 1400 });
  f.sizing.sync(f.chart, f.host);
  assert.equal(f.calls.length, 2);
  assert.equal(f.calls[0].width, 800);
  assert.equal(f.calls[0].height, 500);
  assert.equal(f.calls[1].autoSize, true);
});
test('repairs collapsed height and ignores healthy whole-chart dimensions', () => {
  const f = fixture({ tableHeight: 2 });
  f.sizing.sync(f.chart, f.host);
  assert.equal(f.calls.length, 2);
  const healthy = fixture();
  healthy.sizing.sync(healthy.chart, healthy.host);
  assert.equal(healthy.calls.length, 0);
});
test('defers sizing detached or hidden charts', () => {
  for (const opts of [{ connected: false }, { width: 0 }, { height: 0 }]) {
    const f = fixture({ ...opts, tableWidth: 2, tableHeight: 2 });
    f.sizing.sync(f.chart, f.host);
    assert.equal(f.calls.length, 0);
  }
});
test('checks restored layouts after frames and releases secondary observers on teardown', () => {
  const f = fixture({ tableWidth: 2 });
  const stop = f.sizing.watch(f.chart, f.host);
  f.flush(); f.flush();
  assert.equal(f.calls.length, 2);
  f.listeners.get('charto:dock-layout')();
  stop(); f.flush(); f.resize(); f.flush();
  assert.equal(f.calls.length, 2);
  assert.equal(f.listeners.size, 0);
  assert.equal(f.disconnected(), true);
});

test('repairs collapsed canvas inside a correctly sized table', () => {
  const f = fixture({ collapsed: true });
  f.sizing.sync(f.chart, f.host);
  assert.equal(f.calls.length, 2);
});

test('repairs a stale canvas bitmap even when CSS and table size are correct', () => {
  const f = fixture({ bitmapMismatch: true });
  f.sizing.sync(f.chart,f.host);
  assert.equal(f.calls.length,2);
});
test('rechecks chart after visibility and parent viewport changes', () => {
  const f=fixture({tableWidth:2}); const stop=f.sizing.watch(f.chart,f.host);
  f.flush(); f.flush(); f.flush(); const before=f.calls.length;
  f.listeners.get('visibilitychange')(); f.flush(); f.flush();
  assert.ok(f.calls.length>before);
  assert.ok(f.listeners.has('charto:viewport'));
  stop(); assert.equal(f.listeners.size,0);
});

test('repairs stale internal plot width even when DOM and canvas dimensions are healthy', () => {
  const f=fixture(); f.chart = {applyOptions:options=>f.calls.push(options),timeScale:()=>({width:()=>200})};
  f.sizing.sync(f.chart,f.host);
  assert.equal(f.calls.length,2);
  assert.equal(f.calls[0].width,800);
});
