const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function client(fetch) {
  let now = 0;
  const context = vm.createContext({ fetch, URL, Headers, Response, setTimeout,
    location: { href: 'http://localhost:3000/chart' }, Date: { now: () => now } });
  const source = fs.readFileSync(require.resolve('../js/store.js'), 'utf8');
  vm.runInContext(source.slice(source.indexOf('const Net =')), context);
  return { net: vm.runInContext('Net', context), advance: ms => { now += ms; } };
}

test('coalesces requests and gives every consumer its own JSON body', async () => {
  let calls = 0;
  const { net, advance } = client(async () => {
    calls++; await new Promise(r => setTimeout(r, 5));
    return Response.json({ bars: [1] });
  });
  const url = '/bars?symbol=TCS&interval=15m&limit=300';
  const responses = await Promise.all(Array.from({ length: 12 }, () => net.get(url)));
  assert.equal(calls, 1);
  for (const res of responses) assert.deepEqual(await res.json(), { bars: [1] });
  await net.get(url); assert.equal(calls, 1);
  advance(1001); await net.get(url); assert.equal(calls, 2);
  net.clear(); await net.get(url); assert.equal(calls, 3);
});

test('separates sessions and query parameters; never caches quotes or state', async () => {
  let calls = 0;
  const { net } = client(async () => Response.json({ call: ++calls }));
  for (const token of ['a', 'b']) {
    await net.get('/bars?symbol=TCS', { headers: { Authorization: token } });
  }
  await net.get('/bars?symbol=INFY');
  for (const path of ['/quotes?symbols=TCS', '/workspace', '/auth/me']) {
    await net.get(path); await net.get(path);
  }
  assert.equal(calls, 9);
});

test('does not retain failures and recovers after network rejection', async () => {
  let calls = 0;
  const { net } = client(async () => {
    calls++;
    if (calls === 1) throw new Error('offline');
    return Response.json({}, { status: calls === 2 ? 500 : 200 });
  });
  await assert.rejects(net.get('/indicator?name=rsi'));
  assert.equal((await net.get('/indicator?name=rsi')).status, 500);
  assert.equal((await net.get('/indicator?name=rsi')).status, 200);
  assert.equal(calls, 3);
});

test('retains historical pages longer, but bounds the cache by entry count', async () => {
  let calls = 0;
  const { net, advance } = client(async () => Response.json({ call: ++calls }));
  await net.get('/bars?symbol=TCS&to=100');
  advance(2000); await net.get('/bars?symbol=TCS&to=100');
  assert.equal(calls, 1);
  for (let i = 0; i < 64; i++) await net.get(`/bars?symbol=S${i}`);
  await net.get('/bars?symbol=TCS&to=100'); assert.equal(calls, 66);
});
