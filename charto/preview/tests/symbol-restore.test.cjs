const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(require.resolve('../js/store.js'), 'utf8');
const bootstrap = source.slice(0, source.indexOf('const Sym ='));
function boot(url, remembered, blocked = false) {
  const saved = new Map(remembered ? [['charto:last-symbol', remembered]] : []);
  const location = new URL(url);
  const context = vm.createContext({ URL, location,
    localStorage: {
      getItem(key) { if (blocked) throw Error('unavailable'); return saved.get(key); },
      setItem(key, value) { if (blocked) throw Error('unavailable'); saved.set(key, value); },
    },
    history: { state: { shell: true }, replaceState(state, _, next) {
      assert.deepEqual(state, { shell: true }); location.href = next;
    } },
  });
  vm.runInContext(bootstrap, context);
  return { url: location, remembered: saved.get('charto:last-symbol') };
}

test('a shell remount keeps the last instrument and other URL parameters', () => {
  const result = boot('http://localhost/chart-app/index.html?layout=12#chart', 'BAJAJ-AUTO');
  assert.equal(result.url.searchParams.get('symbol'), 'BAJAJ-AUTO');
  assert.equal(result.url.searchParams.get('layout'), '12');
  assert.equal(result.url.hash, '#chart');
});
test('explicit instruments take precedence over the remembered layout instrument', () => {
  const result = boot('http://localhost/?symbol=TCS', 'RELIANCE');
  assert.equal(result.url.searchParams.get('symbol'), 'TCS');
  assert.equal(result.remembered, 'TCS');
});
test('shared setups leave the remembered working instrument alone', () => {
  const result = boot('http://localhost/?symbol=TCS&view=shared', 'BAJAJ-AUTO');
  assert.equal(result.remembered, 'BAJAJ-AUTO');
  assert.equal(result.url.searchParams.get('symbol'), 'TCS');
});
test('first visit falls back to RELIANCE', () => {
  assert.equal(boot('http://localhost/').url.searchParams.get('symbol'), 'RELIANCE');
});
test('blocked storage does not stop an explicit symbol from loading', () => {
  assert.equal(boot('http://localhost/?symbol=TCS', undefined, true).url.searchParams.get('symbol'), 'TCS');
});
