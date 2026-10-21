const { test } = require('node:test');
const assert = require('node:assert/strict');
const Labels = require('../js/labels.js');

const measure = (t) => t.length * 5.5;
const overlap = (a, b) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;

test('a detector caption keeps its first clause and loses the card detail', () => {
  assert.equal(Labels.shorten('Support zone 1,293 · 3 touches, 0 of 1 re-tests held'), 'Support zone 1,293');
  assert.equal(Labels.shorten('Recent rebound leg +66.90 (+5.27%) · 2 bars'), 'Recent rebound leg +66.90');
  assert.equal(Labels.shorten('Q1 FY27 results'), 'Q1 FY27 results');
  assert.ok(Labels.shorten('x'.repeat(60)).length <= 28);
});

test('captions on stacked levels never overlap each other or the chrome', () => {
  const w = 700, h = 500;
  const reqs = [1293, 1271, 1310, 1333, 1300].map((v, i) => ({
    id: `L${i}`, text: `Level ${v}`, col: '#fff', prio: 5, hot: false,
    cands: Labels.alongLevel(100 + i * 6, w),           // five lines 6px apart
  }));
  const header = { x0: 0, y0: 0, x1: 420, y1: 70 };
  const got = Labels.place(reqs, { w, h, measure, blocked: [header] });
  assert.ok(got.length >= 3);
  for (const a of got) {
    assert.ok(!overlap(a, header), 'caption under the OHLC readout');
    assert.ok(a.x0 >= 0 && a.x1 <= w && a.y0 >= 0 && a.y1 <= h);
    for (const b of got) if (a !== b) assert.ok(!overlap(a, b), `${a.id} overlaps ${b.id}`);
  }
});

test('a caption moves off the candles when there is room beside them', () => {
  const w = 600, h = 400;
  const sky = Labels.sky(w, 3);
  sky.add(380, 600, 0, 400);                                 // bars fill the right side
  const [got] = Labels.place([{ id: 'z', text: 'Resistance 1,330', col: '#f00', prio: 5,
                                cands: Labels.alongLevel(200, w) }], { w, h, measure, sky });
  assert.ok(got.x1 <= 380, `placed over the bars at ${got.x0}-${got.x1}`);
});

test('close vertical lines are named up the line, side by side', () => {
  const w = 600, h = 400;
  const reqs = [100, 116, 132].map((x, i) => ({ id: `v${i}`, text: `Q${i + 1} FY26 results`,
    col: '#a0f', prio: 3, cands: Labels.alongVline(x, h) }));
  const got = Labels.place(reqs, { w, h, measure });
  assert.equal(got.length, 3);
  assert.ok(got.every((g) => g.rot));
  for (const a of got) for (const b of got) if (a !== b) assert.ok(!overlap(a, b));
});

test('a busy chart names its most important shapes first, and the hovered one always', () => {
  const w = 400, h = 300;
  const reqs = Array.from({ length: 12 }, (_, i) => ({ id: `s${i}`, text: `Shape ${i}`, col: '#fff',
    prio: i < 3 ? 5 : 1, hot: i === 11, cands: Labels.alongLevel(30 + i * 22, w) }));
  const got = Labels.place(reqs, { w, h, measure, max: 4 });
  const ids = got.map((g) => g.id);
  assert.ok(ids.includes('s11'), 'the hovered caption must show');
  for (const k of ['s0', 's1', 's2']) assert.ok(ids.includes(k));
  assert.equal(got.filter((g) => !g.hot).length, 4);
});

test('a caption holds its spot from frame to frame', () => {
  const w = 600, h = 400;
  const req = { id: 'a', text: 'Support 1,289', col: '#0f0', prio: 5, cands: Labels.alongLevel(200, w) };
  const first = Labels.place([req], { w, h, measure, prev: new Map([['a', 3]]) });
  assert.equal(first[0].k, 3);
});

test('shortening keeps the number whole and cuts on a word', () => {
  assert.equal(Labels.shorten('POC / near-term balance 1,304'), 'POC 1,304');
  assert.equal(Labels.shorten('Support / Triangle Lower Edge (approx.)'), 'Support');
  const s = Labels.shorten('Ascending broadening wedge breakout retest zone');
  assert.ok(s.endsWith('…') && !/\s…$/.test(s) && s.length <= 28);
});

test('a caption steps off another shape\'s line', () => {
  const w = 600, h = 400;
  const [got] = Labels.place([{ id: 'a', text: 'Support 1,289', col: '#0f0', prio: 5,
    cands: Labels.alongLevel(200, w) }], { w, h, measure, hlines: [192] });
  assert.ok(!(192 > got.y0 && 192 < got.y1), 'line runs through the caption');
});
