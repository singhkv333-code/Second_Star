const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const main = fs.readFileSync(path.join(__dirname, '../js/main.js'), 'utf8');
const sceneSource = fs.readFileSync(path.join(__dirname, '../js/scene.js'), 'utf8');

function functionSource(source, name, next) {
  return source.slice(source.indexOf(`function ${name}(`), source.indexOf(next, source.indexOf(`function ${name}(`)));
}

test('coverage includes chat segments, fibs, boxes, labels, session bands, positions and trades', () => {
  const items = [
    { kind: 'segment', p1: { t: 20 }, p2: { t: 40 } },
    { kind: 'fib', p1: { t: 10 }, p2: { t: 40 } },
    { kind: 'box', a: { t: 9 }, b: { t: 40 } },
    { kind: 'poly', pts: [{ t: 8 }, { t: 40 }] },
    { kind: 'label', a: { t: 7 } },
    { kind: 'vband', t1: 6, t2: 40 },
    { kind: 'position', t0: 5, t1: 40 },
    { kind: 'trade', entry: { t: 4 }, exit: { t: 40 } },
    { kind: 'exposure', spans: [[3, 40]] },
  ];
  const context = vm.createContext({ scene: { state: { items: [] } } });
  vm.runInContext(functionSource(main, 'sceneEarliest', '\n  /** After an interval'), context);
  assert.equal(vm.runInContext('sceneEarliest()', context), Infinity);
  for (const item of items) {
    context.scene.state.items.push(item);
    const expected = [20, 10, 9, 8, 7, 6, 5, 4, 3][context.scene.state.items.length - 1];
    assert.equal(vm.runInContext('sceneEarliest()', context), expected);
  }
});

test('past anchors wait for actual bars instead of extrapolating across closed sessions', () => {
  const bars = [{ time: 100 }, { time: 200 }];
  const context = vm.createContext({
    env: { getBars: () => bars, toChartTime: (t) => t + 10 },
    chart: { timeScale: () => ({ timeToCoordinate: (t) => t === 100 ? 25 : null }) },
    tToLogical: () => { throw new Error('Must not extrapolate historical anchors'); },
    logicalToX: () => { throw new Error('Must not project unloaded bars'); },
  });
  vm.runInContext(functionSource(sceneSource, 'tToX', '\n    /* ── marking a bar'), context);
  assert.equal(vm.runInContext('tToX(50)', context), null);
  assert.equal(vm.runInContext('tToX(NaN)', context), null);
  assert.equal(vm.runInContext('tToX(90)', context), 25);
});

test('scroll and scene coverage share the in-flight historical page', async () => {
  let calls = 0, release;
  const context = vm.createContext({ fetchOlderPage: () => {
    calls++;
    return new Promise((resolve) => { release = resolve; });
  } });
  vm.runInContext('let olderPageTask = null;\n' + functionSource(main, 'loadOlderPage', '\n  async function fetchOlderPage'), context);
  const first = vm.runInContext('loadOlderPage()', context);
  const second = vm.runInContext('loadOlderPage()', context);
  assert.equal(first, second);
  assert.equal(calls, 1);
  release(300);
  assert.equal(await second, 300);
  const next = vm.runInContext('loadOlderPage()', context);
  assert.equal(calls, 2);
  release(100);
  assert.equal(await next, 100);
});
