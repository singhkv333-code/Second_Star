const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = vm.createContext({ window: { LightweightCharts: {} }, location: { hostname: 'localhost' }, Sym: { of: s => ({ tz: /USD/.test(s) ? 0 : 19800 }) } });
vm.runInContext(fs.readFileSync(require.resolve('../js/compare.js'), 'utf8'), context);
const compare = vm.runInContext('ChartCompare', context);
const rows = [
  { symbol: 'A', points: [{time: 1, value: 50}, {time: 2, value: 100}, {time: 3, value: 110}, {time: 4, value: 90}] },
  { symbol: 'B', points: [{time: 2, value: 1000}, {time: 3, value: 1200}, {time: 4, value: 950}, {time: 5, value: 500}] },
];
const plain = x => JSON.parse(JSON.stringify(x));
test('percentage lines share the same observed timestamp, not individual history starts', () => {
  const n = compare.normalize(rows, 'percent');
  assert.equal(n.start, 2);
  assert.equal(n.end, 4);
  for (const row of n.rows) assert.equal(row.points[0].value, 0);
  assert.ok(Math.abs(n.rows[0].points[1].value - 10) < 1e-10);
  assert.ok(Math.abs(n.rows[1].points[1].value - 20) < 1e-10);
  assert.equal(n.rows[0].base, 100);
  assert.equal(n.rows[1].base, 1000);
});
test('indexed-to-100 and independent prices preserve correct magnitudes', () => {
  const indexed = compare.normalize(rows, 'indexed');
  assert.equal(indexed.rows[0].points[0].value, 100);
  assert.equal(indexed.rows[1].points[1].value, 120);
  const price = compare.normalize(rows, 'price');
  assert.equal(price.rows[0].points[0].value, 100);
  assert.equal(price.rows[1].points[0].value, 1000);
});
test('rebasing uses the first shared bar at or after the requested start', () => {
  const n = compare.normalize(rows, 'percent', 2.5);
  assert.equal(n.start, 3);
  assert.equal(n.rows[0].points[0].value, 0);
  assert.equal(n.rows[1].points[0].value, 0);
});
test('missing observations remain whitespace instead of invented or carried-forward prices', () => {
  const changed = structuredClone(rows);
  changed[1].points = changed[1].points.filter(p => p.time !== 3);
  const n = compare.normalize(changed, 'percent');
  assert.deepEqual(plain(n.rows[1].points[1]), { time: 3 });
  assert.equal(n.rows[0].points.at(-1).time, 4);
});
test('unavailable or non-overlapping history is an explicit error', () => {
  for (const points of [[], [{ time: 6, value: 10 }], [{ time: 2, value: 0 }], [{ time: 2, value: NaN }]]) {
    assert.throws(() => compare.normalize([rows[0], {symbol:'B',points}], 'percent'), /unavailable|shared history/);
  }

});
test('observations are sorted, duplicate timestamps are deterministic, and invalid prices are skipped', () => {
  const n = compare.normalize([{ symbol: 'A', points: [{time:3,value:12},{time:1,value:10},{time:1,value:20},{time:2,value:-1}] }], 'price');
  assert.deepEqual(plain(n.rows[0].points), [{time:1,value:20},{time:3,value:12}]);
});
test('intraday comparisons align actual instants while daily charts align trading dates across timezones', () => {
  const midnight = Date.UTC(2026, 8, 4) / 1000;
  const equityDaily = midnight - 19800;
  assert.equal(compare.alignedTime(equityDaily, 'RELIANCE', '1d', 'RELIANCE'), midnight);
  assert.equal(compare.alignedTime(midnight, 'BTC-USD', '1d', 'RELIANCE'), midnight);
  assert.equal(compare.alignedTime(midnight, 'BTC-USD', '5m', 'RELIANCE'), midnight + 19800);
});


test('a viewport newer than available history falls back to a real shared window', () => {
  const n = compare.normalize(rows, 'percent', 6);
  assert.equal(n.start, 2);
  assert.equal(n.end, 4);
  assert.equal(n.shifted, true);
  assert.equal(n.rows[0].points[0].value, 0);
  assert.equal(n.rows[1].points[0].value, 0);
});

test('a lone last common bar is not presented as a comparable chart', () => {
  assert.throws(() => compare.normalize([{ symbol:'A', points:[{time:1,value:20}] },{symbol:'B',points:[{time:1,value:10}]}], 'percent'), /shared history/);
  const n = compare.normalize(rows, 'percent', 4);
  assert.equal(n.start, 2);
  assert.equal(n.rows[0].points.length, 3);
});

test('history cutoffs use to and retain the complete last intraday candle across timezones', () => {
  const raw = 1784713500;
  const display = raw + 19800;
  assert.equal(compare.historyTo(display, 'RELIANCE', '15m', 'RELIANCE'), raw + 900);
  assert.equal(compare.historyTo(display, 'BTC-USD', '15m', 'RELIANCE'), raw + 900);
  assert.equal(compare.historyTo(display, 'RELIANCE', '4h', 'RELIANCE'), raw + 14400);
  const midnight = Date.UTC(2026, 6, 22) / 1000;
  assert.equal(compare.historyTo(midnight, 'RELIANCE', '1d', 'RELIANCE'), midnight - 19800 + 1);
  assert.equal(compare.historyTo(midnight, 'BTC-USD', '1d', 'RELIANCE'), midnight + 1);
});

test('newer securities are fetched back to the oldest available end before comparison', async () => {
  const calls = [];
  const primary = { symbol:'A', points:[{time:8,value:80},{time:9,value:90},{time:10,value:100}] };
  const loaded = await compare.sharedHistories(primary, ['B'], async (symbol, end) => {
    calls.push([symbol,end]);
    return symbol === 'B'
      ? {symbol,points:[{time:1,value:200},{time:2,value:220},{time:3,value:240}]}
      : {symbol,points:[{time:1,value:50},{time:2,value:55},{time:3,value:60}]};
  });
  assert.deepEqual(calls,[['B',10],['A',3]]);
  const n=compare.normalize(loaded,'percent',8);
  assert.equal(n.start,1);
  assert.equal(n.end,3);
  assert.ok(Math.abs(n.rows[0].points[2].value-20)<1e-10);
  assert.ok(Math.abs(n.rows[1].points[2].value-20)<1e-10);
});

test('candle comparisons normalize every OHLC field against the same observed close', () => {
  const input = [
    { symbol:'A', points:[{time:1,value:100,open:95,high:105,low:90,close:100},{time:2,value:110,open:100,high:120,low:98,close:110}] },
    { symbol:'B', points:[{time:1,value:1000},{time:2,value:1200}] },
  ];
  for (const mode of ['percent','indexed','price']) {
    const normalized = compare.normalize(input,mode);
    const candles = compare.plotPoints(normalized.rows[0],'candles',mode);
    const expected = mode === 'percent' ? [-5,5,-10,0] : mode === 'indexed' ? [95,105,90,100] : [95,105,90,100];
    for (const [i,key] of ['open','high','low','close'].entries()) assert.ok(Math.abs(candles[0][key]-expected[i]) < 1e-10);
    assert.deepEqual(plain(compare.plotPoints(normalized.rows[0],'bars',mode)),plain(candles));
    assert.deepEqual(plain(compare.plotPoints(normalized.rows[1],'line',mode)),plain(normalized.rows[1].points));
    assert.deepEqual(plain(compare.plotPoints(normalized.rows[0],'area',mode)),plain(normalized.rows[0].points));
  }
});

test('missing OHLC stays whitespace and is never synthesized from a close', () => {
  const n = compare.normalize(rows,'percent');
  assert.deepEqual(plain(compare.plotPoints(n.rows[0],'candles','percent')),[{time:2},{time:3},{time:4}]);
});

test('axis tags retain the actual plotted price while their position uses normalized units', () => {
  vm.runInContext('Sym.of = () => ({ price: value => "₹" + value });',context);
  let primitive, updates = 0;
  const host = { priceToCoordinate: value => 300 - value, attachPrimitive: p => { primitive = p; p.attached({requestUpdate: () => updates++}); } };
  let n = compare.normalize(rows,'percent');
  const repaint = compare.axisTag(host,()=>n.rows[0],()=> 'percent','#3478c4');
  const view = primitive.priceAxisViews()[0];
  assert.ok(Math.abs(view.coordinate()-310) < 1e-10);
  assert.equal(view.text(),'₹90 · -10.00%');
  assert.equal(view.backColor(),'#3478c4');
  assert.equal(view.visible(),true);
  repaint(); assert.equal(updates,1);
  primitive.detached(); repaint(); assert.equal(updates,1);
});

test('live OHLC replaces the forming bar, appends new observed bars, and rejects invalid or stale ticks', () => {
  const row = {symbol:'A',points:[{time:19801,value:100,open:95,high:105,low:90,close:100}]};
  vm.runInContext('Sym.of = () => ({ tz: 19800, price: value => "₹" + value });',context);
  assert.equal(compare.advanceHistory(row,{t:1,o:95,h:112,l:90,c:110},'A','15m','A'),true);
  assert.equal(row.points.length,1);
  assert.equal(row.points[0].close,110);
  assert.equal(row.points[0].high,112);
  assert.equal(compare.advanceHistory(row,{t:2,o:110,h:116,l:108,c:115},'A','15m','A'),true);
  assert.equal(row.points.length,2);
  const before = structuredClone(row);
  assert.equal(compare.advanceHistory(row,{t:1,o:95,h:105,l:90,c:100},'A','15m','A'),false);
  assert.equal(compare.advanceHistory(row,{t:3,o:110,h:116,l:108,c:NaN},'A','15m','A'),false);
  assert.deepEqual(plain(row),plain(before));
  const peer={symbol:'B',points:[{time:19801,value:1000},{time:19802,value:1100}]};
  const n=compare.normalize([row,peer],'percent');
  const candles=compare.plotPoints(n.rows[0],'candles','percent');
  assert.ok(Math.abs(candles[1].close - 100*(115/110-1)) < 1e-10);
  assert.ok(Math.abs(n.rows[1].points[1].value-10) < 1e-10);
});

test('charts and comparison overlays share live connections and release them after the last subscriber', () => {
  const streams=[];
  context.EventSource=class {
    constructor(url){ this.url=url;this.closed=false;streams.push(this); }
    close(){this.closed=true;}
  };
  let primary=0,overlay=0,other=0,errors=0;
  const stopPrimary=compare.subscribeBars('A',()=>primary++,()=>errors++);
  const stopOverlay=compare.subscribeBars('A',()=>overlay++);
  const stopOther=compare.subscribeBars('B',()=>other++);
  assert.equal(streams.length,2);
  streams[0].onmessage({data:JSON.stringify({type:'bar',bars:{}})});
  assert.equal(primary,1);assert.equal(overlay,1);assert.equal(other,0);
  streams[0].onmessage({data:'bad json'});
  streams[0].onerror();assert.equal(errors,1);
  stopPrimary();assert.equal(streams[0].closed,false);
  streams[0].onmessage({data:JSON.stringify({type:'bar',bars:{}})});
  assert.equal(primary,1);assert.equal(overlay,2);
  streams[0].onerror();assert.equal(errors,1);
  stopOverlay();assert.equal(streams[0].closed,true);
  stopOther();assert.equal(streams[1].closed,true);
});
