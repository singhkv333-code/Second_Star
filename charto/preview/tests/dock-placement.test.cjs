const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../js/dock.js'), 'utf8');
const hit = source.match(/  function hit\(x, y\) \{[\s\S]*?\n  \}/)[0];
const rect = (left, top, width, height) => ({ left, top, width, height, right: left + width, bottom: top + height });
function target({ free = false, floating = false, x = 500, y = 300, ownOnly = false } = {}) {
  const area = rect(0, 0, 1000, 700);
  const layer = { children: [] };
  const tile = gid => ({ dataset: { gid }, isConnected: true, parentNode: layer,
    getBoundingClientRect: () => area });
  const own = tile('source'), other = tile('target');
  if (floating) layer.children = [own];
  other.parentNode = null;
  const context = vm.createContext({
    drag: { source: { kind: 'group', gid: 'source' }, free,
      floating: floating ? { w: 480, h: 320 } : null, offset: floating ? { x: 30, y: 18 } : null },
    S: { groups: { source: { tabs: ['widget'] }, target: { tabs: ['main'] } } },
    CHART: 'main', canvas: { getBoundingClientRect: () => area }, hubOpen: false,
    floatLayer: layer, groupEls: new Map(ownOnly ? [['source', own]] : [['source', own], ['target', other]]),
    hasChart: gid => gid === 'target', floatOf: gid => floating && gid === 'source',
    isBare: () => true, EDGE_SHARE: .27, FLOAT_DEF: { w: 380, h: 420 },
    clamp: (n, lo, hi) => Math.max(lo, Math.min(n, hi)),
    inside: (x, y, r) => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom,
  });
  return JSON.parse(JSON.stringify(vm.runInContext(hit + `;hit(${x},${y})`, context)));
}
test('floating source does not intercept a split target behind itself', () => {
  assert.equal(target({ floating: true, x: 50 }).kind, 'split');
  assert.equal(target({ floating: true, x: 50 }).gid, 'target');
});
test('a widget can float over the centre of its previous tile', () => {
  assert.equal(target({ ownOnly: true }).kind, 'float');
});
test('Alt bypasses both outer-edge and tile snapping', () => {
  const result = target({ free: true, x: 4 });
  assert.equal(result.kind, 'float');
  assert.equal(result.x, 8);
});
test('free dragging preserves window dimensions and pointer offset', () => {
  const result = target({ floating: true, free: true });
  assert.equal(result.w, 480);
  assert.equal(result.h, 320);
  assert.equal(result.x, 470);
  assert.equal(result.y, 282);
});
test('normal canvas edge docking remains structured', () => {
  assert.equal(target({ x: 4 }).kind, 'edge');
  assert.equal(target({ x: 4 }).side, 'left');
});
test('drops outside the canvas cannot lose a widget', () => {
  assert.equal(target({ x: -10 }), null);
});
test('window z-order follows stacking order, including floating charts', () => {
  const place = source.match(/  function placeFloats\(\) \{[\s\S]*?\n  \}/)[0];
  const nodes = new Map(['chart', 'watch'].map(gid => [gid, { style: {} }]));
  const context = vm.createContext({ floatLayer: { clientWidth: 1000, clientHeight: 700 },
    S: { floats: ['chart', 'watch'].map(gid => ({ gid, w: 380, h: 300, x: 20, y: 20 })) },
    groupEls: nodes, maxed: null, clamp: (n, lo, hi) => Math.max(lo, Math.min(n, hi)) });
  vm.runInContext(place + ';placeFloats()', context);
  assert.equal(nodes.get('chart').style.zIndex, '1');
  assert.equal(nodes.get('watch').style.zIndex, '2');
  vm.runInContext('S.floats.reverse();placeFloats()', context);
  assert.equal(nodes.get('chart').style.zIndex, '2');
});
test('pointer release resolves its current target rather than a stale preview', () => {
  const finish = source.match(/  function finishDrag\(commit\) \{[\s\S]*?\n  \}/)[0];
  let checked;
  const context = vm.createContext({ drag: { x: -10, y: 80, raf: 1, target: { kind: 'edge', side: 'left' } },
    hit: (x,y) => { checked = [x,y]; return null; }, cancelAnimationFrame() {}, removeEventListener() {}, escDrag() {},
    document: { body: { classList: { remove() {} } }, querySelectorAll: () => [] },
    preview: { classList: { remove() {} } }, ghost: { classList: { remove() {} } }, hubEl: null });
  vm.runInContext(finish + ';finishDrag(true)', context);
  assert.deepEqual(checked, [-10,80]);
  assert.equal(context.drag, null);
});
