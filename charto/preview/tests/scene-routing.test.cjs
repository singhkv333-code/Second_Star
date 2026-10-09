const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const main = fs.readFileSync(path.join(__dirname, '../js/main.js'), 'utf8');

function functionSource(source, name, next) {
  const at = source.indexOf(`function ${name}(`);
  return source.slice(at, source.indexOf(next, at));
}

// A chat segment is raw exchange time and one piece of a detector's object
// (pattern edge, divergence leg, owner-cleared trendline). Only the scene
// converts that time and keeps the pieces together; the drawings store must
// never receive it, or the anchor lands 5h30 early and snaps to a session edge.
test('chat segments, pattern pieces and divergence legs all stay in the scene, unshifted', () => {
  const applied = [];
  const scene = { apply: (items) => applied.push(...items) };
  const context = vm.createContext({
    scene, SYMBOL: 'RELIANCE', state: { interval: '15m' },
    draw: { add: () => { throw new Error('chat output must not enter the drawings store'); } },
    Panes: { all: () => [] },
    wireIv: (iv) => ({ D: '1d', W: '1w', M: '1mo' })[iv] || String(iv || ''),
  });
  vm.runInContext(functionSource(main, 'applyScenePatch', '\n  window.__charto'), context);
  const patch = [
    { kind: 'clear', scope: 'segment', owner: 'get_trendlines' },
    { kind: 'segment', id: 'TL1', role: 'support', p1: { t: 1756783800, v: 1271 }, p2: { t: 1756878300, v: 1305.4 } },
    { kind: 'segment', id: 'P1-u', link: 'P1', role: 'bearish', p1: { t: 1, v: 2 }, p2: { t: 3, v: 4 } },
    { kind: 'segment', id: 'DV1-osc', link: 'DV1', pane: 'rsi', p1: { t: 1, v: 60 }, p2: { t: 3, v: 55 } },
  ];
  vm.runInContext('applyScenePatch', context)(patch);
  assert.deepEqual(applied.map((a) => a.id || a.kind), ['clear', 'TL1', 'P1-u', 'DV1-osc']);
  assert.equal(applied[1].p1.t, 1756783800);
  assert.equal(applied[1].p2.t, 1756878300);
});
