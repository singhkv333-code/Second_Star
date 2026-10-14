// ChartSize must never leave the chart library's wrapper pinned to pixels.
// lightweight-charts writes `wrapper.style.width = "<n>px"` whenever it is
// resized with autoSize off, and re-enabling autoSize never clears it; a sync
// during a narrow moment then clipped the chart for the whole session.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function load() {
  const context = vm.createContext({
    window: { devicePixelRatio: 1, addEventListener() {}, removeEventListener() {} },
    document: { addEventListener() {}, removeEventListener() {} },
    requestAnimationFrame: () => 0, cancelAnimationFrame() {},
    ResizeObserver: class { observe() {} disconnect() {} },
  });
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../js/chartsize.js'), 'utf8') + '\nthis.ChartSize = ChartSize;', context);
  return context.ChartSize;
}

function chartWith(wrapper, { stale }) {
  const width = 800, height = 500;
  const cell = { clientWidth: width - 80, clientHeight: height - 28,
    querySelector: () => ({ width: width - 80, height: height - 28, getBoundingClientRect: () => ({ width: width - 80, height: height - 28 }) }),
    querySelectorAll: () => [] };
  const table = { getBoundingClientRect: () => ({ width: stale ? 300 : width, height }),
    querySelectorAll: () => [cell], querySelector: () => null };
  const host = { isConnected: true, clientWidth: width, clientHeight: height, querySelector: () => table };
  const chart = {
    applyOptions(o) { if (o.autoSize === false) { wrapper.style.width = `${o.width}px`; wrapper.style.height = `${o.height}px`; } },
    resize(w, h) { wrapper.style.width = `${w}px`; wrapper.style.height = `${h}px`; },
    chartElement: () => wrapper,
  };
  return { chart, host };
}

test('a forced resize hands the wrapper back to its container', () => {
  const ChartSize = load();
  const wrapper = { style: { width: '100%', height: '100%' } };
  const { chart, host } = chartWith(wrapper, { stale: true });
  ChartSize.sync(chart, host);
  assert.equal(wrapper.style.width, '100%');
  assert.equal(wrapper.style.height, '100%');
});

test('a wrapper already pinned (a stuck session) is released on the next sync', () => {
  const ChartSize = load();
  const wrapper = { style: { width: '196px', height: '904px' } };
  const { chart, host } = chartWith(wrapper, { stale: false });
  ChartSize.sync(chart, host);
  assert.equal(wrapper.style.width, '100%');
  assert.equal(wrapper.style.height, '100%');
});
