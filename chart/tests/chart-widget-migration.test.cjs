const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(require.resolve('../js/dock.js'), 'utf8');
const migration = source.match(/  function migrateChartWidgets\(s\) \{[\s\S]*?\n  \}/)[0];
function migrate(state) {
  const context = vm.createContext({ state: structuredClone(state) });
  vm.runInContext('const CHART = "main";\n' + migration + '\nmigrateChartWidgets(state);', context);
  return JSON.parse(JSON.stringify(context.state));
}
function workspace(inst, groups, closed = [], floats = []) {
  return { inst, groups, closed, floats,
    tree: { k: 'row', c: Object.keys(groups).filter(gid => !floats.some(f => f.gid === gid)).map(g => ({ k: 'g', g })), s: [0.7, 0.3] } };
}
const main = { type: 'main', cfg: { title: 'My chart', link: 'A' } };
const ref = { type: 'chart', cfg: { sym: 'TCS', iv: '1h' } };
const watch = { type: 'watchlist', cfg: { list: 2 } };

test('keeps the full chart and other widgets in their saved positions', () => {
  const state = workspace({ main, mini: ref, mini2: ref, watch }, {
    left: { tabs: ['main'], active: 'main' },
    right: { tabs: ['mini', 'watch', 'mini2'], active: 'mini' },
  });
  const next = migrate(state);
  assert.deepEqual(next.inst, { main, watch });
  assert.deepEqual(next.groups.left, state.groups.left);
  assert.deepEqual(next.groups.right, { tabs: ['watch'], active: 'watch' });
  assert.deepEqual(next.tree, state.tree);
});

test('replaces a visible reference chart when the full chart was closed', () => {
  const state = workspace({ main, mini: ref, watch }, {
    left: { tabs: ['watch', 'mini'], active: 'mini' },
  }, [{ id: 'main', gid: 'old', pos: 0 }, { id: 'mini', gid: 'left', pos: 1 }]);
  const next = migrate(state);
  assert.deepEqual(next.groups.left, { tabs: ['watch', 'main'], active: 'main' });
  assert.deepEqual(next.inst.main, main);
  assert.deepEqual(next.closed, []);
});

test('multiple reference charts become just one full chart without copying mini settings', () => {
  const state = workspace({ mini: ref, mini2: ref }, {
    left: { tabs: ['mini'], active: 'mini' },
    right: { tabs: ['mini2'], active: 'mini2' },
  });
  const next = migrate(state);
  assert.deepEqual(next.inst, { main: { type: 'main', cfg: {} } });
  assert.deepEqual(next.groups.left, { tabs: ['main'], active: 'main' });
  assert.deepEqual(next.groups.right.tabs, []);
  assert.deepEqual(migrate(next), next);
});

test('keeps an intentionally closed full chart closed when no reference chart is visible', () => {
  const state = workspace({ main, mini: ref, watch }, {
    left: { tabs: ['watch'], active: 'watch' },
  }, [{ id: 'main', gid: 'left', pos: 1 }, { id: 'mini', gid: 'left', pos: 2 }]);
  const next = migrate(state);
  assert.deepEqual(next.closed, [state.closed[0]]);
  assert.deepEqual(next.groups, state.groups);
});

test('a floating reference chart reuses its window dimensions for the full chart', () => {
  const state = workspace({ mini: ref }, {
    window: { tabs: ['mini'], active: 'mini' },
  }, [], [{ gid: 'window', x: 120, y: 50, w: 780, h: 540 }]);
  const next = migrate(state);
  assert.deepEqual(next.groups.window, { tabs: ['main'], active: 'main' });
  assert.deepEqual(next.floats, state.floats);
});

test('normalizes an old reference instance using the reserved primary id', () => {
  const next = migrate(workspace({ main: ref, mini: ref }, {
    left: { tabs: ['main', 'mini'], active: 'main' },
  }));
  assert.deepEqual(next.inst, { main: { type: 'main', cfg: {} } });
  assert.deepEqual(next.groups.left.tabs, ['main']);
});

test('does not reopen a reference chart stored in an orphaned group', () => {
  const state = workspace({ main, mini: ref, watch }, {
    left: { tabs: ['watch'], active: 'watch' },
    orphan: { tabs: ['mini'], active: 'mini' },
  }, [{ id: 'main', gid: 'left', pos: 1 }]);
  state.tree.c.pop();
  const next = migrate(state);
  assert.deepEqual(next.closed, state.closed);
  assert.deepEqual(next.groups.orphan.tabs, []);
});


test('preserves additional full chart widgets and their independent settings', () => {
  const second = { type: 'main', cfg: { symbol: 'TCS', interval: '15m', comparison: { symbols: ['RELIANCE'], mode: 'indexed' } } };
  const state = workspace({ main, second, mini: ref }, {
    left: { tabs: ['main'], active: 'main' },
    right: { tabs: ['second', 'mini'], active: 'second' },
  });
  const next = migrate(state);
  assert.deepEqual(next.inst, { main, second });
  assert.deepEqual(next.groups.right, { tabs: ['second'], active: 'second' });
});
