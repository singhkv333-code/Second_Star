/* Charto preview — the pictures on the widget hub's cards.
 *
 * Each widget is shown by a small drawing of itself, the way a product shot
 * shows a product: a candle chart for the chart, a ladder for the order book,
 * a grid for the sheet. They are ILLUSTRATIONS, not data — there is no
 * number, ticker or price anywhere in them, so none can be mistaken for a
 * quote. Drawn in the page's own ink, with the market's green and red where
 * the picture is of a market.
 *
 * The finish is photographic rather than flat, and it is all SVG (no image
 * files, nothing to license): a soft-focus layer BEHIND the subject (depth of
 * field) and the subject sharp in front, both dissolving at the edges into
 * the sheet's glass — no plate, no box. On hover the card's CSS adds a slow
 * push-in.
 */
"use strict";

const HubArt = (() => {
  const W = 320, H = 180;
  let seq = 0;

  // a seeded walk, so a picture is the same picture on every open
  function rng(seed) {
    let s = seed >>> 0;
    return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
  }

  function candles(seed, n, x0, x1, yMid, amp) {
    const r = rng(seed), out = [];
    let p = 0;
    const step = (x1 - x0) / n;
    for (let i = 0; i < n; i++) {
      const o = p, c = p + (r() - .48) * amp * .55;
      const hi = Math.max(o, c) + r() * amp * .25, lo = Math.min(o, c) - r() * amp * .25;
      out.push({ x: x0 + i * step + step / 2, o: yMid - o, c: yMid - c, h: yMid - hi, l: yMid - lo, w: step * .56 });
      p = c;
    }
    return out;
  }
  const candleSvg = (cs, op = 1) => cs.map((k) => {
    const up = k.c < k.o, col = up ? "var(--up)" : "var(--down)";
    const top = Math.min(k.o, k.c), h = Math.max(1.2, Math.abs(k.o - k.c));
    return `<line x1="${k.x}" x2="${k.x}" y1="${k.h}" y2="${k.l}" stroke="${col}" stroke-width="1.1" opacity="${op}"/>` +
      `<rect x="${k.x - k.w / 2}" y="${top}" width="${k.w}" height="${h}" rx=".8" fill="${col}" opacity="${op}"/>`;
  }).join("");
  const smooth = (pts) => pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const ink = (o) => `fill="var(--foreground)" fill-opacity="${o}"`;
  const stroke = (o, w = 1.2) => `stroke="var(--foreground)" stroke-opacity="${o}" stroke-width="${w}" fill="none"`;
  const bar = (x, y, w, h, o, rx = 3) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" ${ink(o)}/>`;

  /** The frame every picture sits in. No plate, no box: the drawing floats
   *  on the glass of the sheet. Depth comes from a soft-focus layer behind
   *  the subject, and an edge mask dissolves both into the glass so no
   *  rectangle is ever drawn. */
  function frame(id, back, front) {
    return `<svg class="hub-art" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" aria-hidden="true">` +
      `<defs>` +
        `<filter id="b${id}"><feGaussianBlur stdDeviation="2.4"/></filter>` +
        `<radialGradient id="m${id}" cx="50%" cy="50%" r="62%">` +
          `<stop offset="58%" stop-color="#fff" stop-opacity="1"/><stop offset="100%" stop-color="#fff" stop-opacity="0"/></radialGradient>` +
        `<mask id="k${id}"><rect width="${W}" height="${H}" fill="url(#m${id})"/></mask>` +
        `<linearGradient id="f${id}" x1="0" y1="0" x2="0" y2="1">` +
          `<stop offset="0" stop-color="var(--foreground)" stop-opacity=".10"/><stop offset="1" stop-color="var(--foreground)" stop-opacity="0"/></linearGradient>` +
      `</defs>` +
      `<g mask="url(#k${id})">` +
        `<g class="hub-back" filter="url(#b${id})" opacity=".32">${back || ""}</g>` +
        `<g class="hub-front">${front}</g>` +
      `</g>` +
      `</svg>`;
  }

  const ART = {
    chart(id) {
      const cs = candles(7, 26, 34, 296, 92, 44);
      const ma = cs.map((k, i) => [k.x, (k.o + k.c) / 2 + Math.sin(i / 3) * 3 + 6]);
      const back = candleSvg(candles(3, 30, 0, 320, 70, 50), .5);
      return frame(id, back,
        `<path d="${smooth(ma)}" ${stroke(.55, 1.4)}/>` + candleSvg(cs) +
        `<line x1="200" x2="200" y1="20" y2="160" ${stroke(.35, 1)} stroke-dasharray="2 3"/>` +
        `<line x1="20" x2="300" y1="104" y2="104" ${stroke(.35, 1)} stroke-dasharray="2 3"/>`);
    },
    watch(id) {
      const row = (y, up, w) =>
        bar(36, y, 92, 22, .07, 6) + bar(46, y + 8, 14, 6, .45, 3) + bar(66, y + 8, w, 6, .22, 3) +
        `<rect x="${244 - 22}" y="${y + 6}" width="46" height="10" rx="5" fill="${up ? "var(--up)" : "var(--down)"}" fill-opacity=".85"/>` +
        `<rect x="34" y="${y - 3}" width="252" height="28" rx="7" fill="${up ? "var(--up)" : "var(--down)"}" fill-opacity=".06"/>`;
      return frame(id, row(14, true, 50) + row(140, false, 40),
        row(44, true, 46) + row(78, true, 34) + row(112, false, 52));
    },
    alerts(id) {
      const cs = candles(11, 20, 40, 290, 110, 40);
      return frame(id, candleSvg(candles(5, 24, 0, 320, 80, 40), .4),
        candleSvg(cs, .9) +
        `<line x1="28" x2="292" y1="70" y2="70" stroke="var(--foreground)" stroke-width="1.4" stroke-dasharray="5 4" opacity=".7"/>` +
        `<g transform="translate(262 52)"><circle r="15" ${ink(1)}/>` +
        `<path d="M-5 3h10M-4 3V-1a4 4 0 0 1 8 0v4M-1.5 6h3" stroke="var(--background)" stroke-width="1.6" fill="none" stroke-linecap="round"/></g>`);
    },
    journal(id) {
      const pts = [], r = rng(4);
      let y = 120;
      for (let x = 30; x <= 290; x += 13) { y += (r() - .62) * 14; pts.push([x, Math.max(30, y)]); }
      return frame(id, bar(20, 20, 280, 140, .04, 10),
        `<path d="${smooth(pts)} L290 150 L30 150 Z" fill="url(#f${id})"/>` +
        `<path d="${smooth(pts)}" ${stroke(.85, 1.8)}/>` +
        [0, 1, 2].map((i) => bar(40, 132 + i * 0, 0, 0, 0)).join(""));
    },
    screener(id) {
      const rows = [0, 1, 2, 3, 4].map((i) => {
        const y = 28 + i * 27, hit = i === 1 || i === 3;
        return `<rect x="40" y="${y}" width="240" height="21" rx="6" ${ink(hit ? .1 : .04)}/>` +
          bar(50, y + 7, 46, 7, hit ? .6 : .25) + bar(110, y + 7, 70, 7, .14) +
          `<rect x="236" y="${y + 6}" width="34" height="9" rx="4.5" fill="${i % 2 ? "var(--up)" : "var(--down)"}" fill-opacity="${hit ? .9 : .35}"/>`;
      }).join("");
      return frame(id, `<path d="M10 10h300l-110 90v70l-80 0v-70z" ${ink(.06)}/>`, rows);
    },
    depth(id) {
      const ask = [60, 44, 52, 30, 22, 14], bid = [16, 26, 34, 50, 40, 64];
      const rows = ask.map((w, i) => `<rect x="${280 - w * 2.6}" y="${18 + i * 12}" width="${w * 2.6}" height="9" rx="2" fill="var(--down)" fill-opacity=".28"/>` +
          bar(44, 20 + i * 12, 34, 5, .5) + bar(120, 20 + i * 12, 26, 5, .2)).join("") +
        bar(36, 90, 248, 1.5, .4, 0) +
        bid.map((w, i) => `<rect x="${280 - w * 2.6}" y="${98 + i * 12}" width="${w * 2.6}" height="9" rx="2" fill="var(--up)" fill-opacity=".3"/>` +
          bar(44, 100 + i * 12, 34, 5, .5) + bar(120, 100 + i * 12, 26, 5, .2)).join("");
      return frame(id, bar(0, 0, 320, 180, 0), rows);
    },
    notes(id) {
      const lines = [0, 1, 2, 3, 4].map((i) => bar(60, 46 + i * 18, [170, 140, 180, 96, 150][i], 6, .22)).join("");
      return frame(id, bar(40, 10, 240, 170, .05, 10),
        `<rect x="44" y="18" width="232" height="152" rx="10" fill="var(--background)" stroke="var(--foreground)" stroke-opacity=".12"/>` +
        bar(60, 28, 90, 9, .75) + lines +
        `<rect x="60" y="132" width="110" height="18" rx="5" ${ink(.08)} stroke="var(--foreground)" stroke-opacity=".25"/>` +
        bar(68, 139, 70, 4, .5) +
        `<rect x="190" y="132" width="12" height="12" rx="3" ${stroke(.6, 1.4)}/><path d="m192.5 138 2.5 2.5 4.5-5" ${stroke(.8, 1.6)}/>`);
    },
    sheet(id) {
      const cells = [];
      for (let r = 0; r < 6; r++) for (let c = 0; c < 5; c++) {
        const x = 42 + c * 48, y = 40 + r * 20;
        cells.push(`<rect x="${x}" y="${y}" width="48" height="20" fill="none" stroke="var(--foreground)" stroke-opacity=".14"/>`);
        if (r && c) cells.push(bar(x + 8, y + 7, (c * 7 + r * 5) % 26 + 10, 5, .25, 2));
      }
      return frame(id, bar(20, 20, 280, 150, .04, 8),
        bar(42, 20, 240, 16, .06, 4) + `<text x="50" y="32" font-size="10" font-family="var(--mono)" ${ink(.7)}>fx</text>` +
        bar(68, 26, 120, 5, .3) + cells.join("") +
        `<rect x="138" y="80" width="48" height="20" fill="var(--up)" fill-opacity=".12" stroke="var(--up)" stroke-width="1.6"/>` +
        `<rect x="183" y="97" width="6" height="6" fill="var(--up)"/>`);
    },
    code(id) {
      const ln = [[0, 60, .5], [14, 90, .3], [14, 40, .6], [28, 120, .25], [28, 70, .45], [14, 50, .3], [0, 30, .55]];
      return frame(id, bar(20, 14, 280, 156, .05, 10),
        `<rect x="34" y="22" width="252" height="140" rx="10" fill="var(--foreground)" fill-opacity=".92"/>` +
        ln.map(([ind, w, o], i) => `<rect x="${70 + ind}" y="${38 + i * 16}" width="${w}" height="6" rx="3" fill="var(--background)" fill-opacity="${o + .15}"/>` +
          `<rect x="46" y="${38 + i * 16}" width="10" height="6" rx="3" fill="var(--background)" fill-opacity=".2"/>`).join("") +
        `<rect x="${70 + 40}" y="${38 + 2 * 16}" width="34" height="6" rx="3" fill="var(--up)"/>`);
    },
    financials(id) {
      const v = [40, 52, 48, 64, 70, 82, 78, 96];
      const bars = v.map((h, i) => `<rect x="${52 + i * 29}" y="${150 - h}" width="17" height="${h}" rx="3" ${ink(i === v.length - 1 ? .85 : .22)}/>`).join("");
      const line = v.map((h, i) => [60 + i * 29, 132 - h * .9]);
      return frame(id, bar(0, 150, 320, 1, .2, 0), bars +
        `<path d="${smooth(line)}" stroke="var(--up)" stroke-width="1.8" fill="none"/>` +
        line.map(([x, y]) => `<circle cx="${x}" cy="${y}" r="2.4" fill="var(--up)"/>`).join(""));
    },
    browser(id) {
      return frame(id, bar(10, 10, 300, 170, .05, 12),
        `<rect x="30" y="20" width="260" height="146" rx="10" fill="var(--background)" stroke="var(--foreground)" stroke-opacity=".16"/>` +
        `<path d="M30 30a10 10 0 0 1 10-10h240a10 10 0 0 1 10 10v12H30z" ${ink(.07)}/>` +
        [0, 1, 2].map((i) => `<circle cx="${42 + i * 10}" cy="31" r="3" ${ink(.3)}/>`).join("") +
        `<rect x="80" y="25" width="170" height="12" rx="6" fill="var(--background)" stroke="var(--foreground)" stroke-opacity=".15"/>` +
        bar(46, 56, 110, 10, .6) + bar(46, 74, 180, 6, .18) + bar(46, 86, 160, 6, .18) +
        `<rect x="46" y="102" width="108" height="50" rx="6" ${ink(.08)}/><rect x="166" y="102" width="108" height="50" rx="6" ${ink(.08)}/>`);
    },
    docs(id) {
      const page = (x, y, r, o) => `<g transform="rotate(${r} ${x + 50} ${y + 65})">` +
        `<rect x="${x}" y="${y}" width="100" height="130" rx="6" fill="var(--background)" stroke="var(--foreground)" stroke-opacity=".18"/>` +
        [0, 1, 2, 3, 4, 5].map((i) => bar(x + 12, y + 18 + i * 14, [70, 56, 74, 40, 66, 50][i], 5, o)).join("") + `</g>`;
      return frame(id, page(40, 30, -10, .12) + page(180, 26, 9, .12),
        page(70, 26, -4, .14) + page(140, 22, 3, .22) +
        `<rect x="196" y="122" width="34" height="18" rx="4" fill="var(--down)"/>` +
        `<text x="213" y="135" text-anchor="middle" font-size="9" font-weight="700" fill="#fff" font-family="var(--font)">PDF</text>`);
    },
    news(id) {
      const item = (y, o) => `<rect x="40" y="${y}" width="44" height="34" rx="5" ${ink(.14 * o)}/>` +
        bar(96, y + 4, 160, 7, .6 * o) + bar(96, y + 17, 120, 5, .2 * o) + bar(96, y + 27, 40, 4, .3 * o);
      return frame(id, item(4, .8) + item(150, .8), item(30, 1) + item(74, 1) + item(118, .9));
    },
    tv(id) {
      return frame(id, "",
        `<rect x="56" y="26" width="208" height="122" rx="10" fill="var(--foreground)" fill-opacity=".9"/>` +
        `<path d="M70 120 L110 92 L140 104 L180 70 L220 84 L250 60" stroke="var(--up)" stroke-width="2" fill="none" opacity=".9"/>` +
        `<path d="M150 74 l22 13 -22 13z" fill="var(--background)" fill-opacity=".9"/>` +
        `<rect x="68" y="36" width="34" height="12" rx="3" fill="var(--down)"/>` +
        `<text x="85" y="45" text-anchor="middle" font-size="8" font-weight="700" fill="#fff" font-family="var(--font)">LIVE</text>` +
        `<rect x="130" y="152" width="60" height="5" rx="2.5" ${ink(.35)}/>`);
    },
    calendar(id) {
      const cells = [];
      for (let r = 0; r < 4; r++) for (let c = 0; c < 7; c++) {
        const x = 50 + c * 32, y = 54 + r * 26;
        cells.push(`<rect x="${x}" y="${y}" width="26" height="20" rx="4" ${ink(.05)}/>`);
        if ((r * 7 + c) % 5 === 2) cells.push(`<circle cx="${x + 13}" cy="${y + 10}" r="3.2" fill="${(r + c) % 2 ? "var(--up)" : "var(--down)"}"/>`);
      }
      return frame(id, bar(30, 14, 260, 160, .05, 10),
        bar(50, 30, 90, 9, .7) + bar(230, 30, 40, 9, .2) + cells.join(""));
    },
  };

  /** The picture for a widget type, or a quiet blank plate. */
  function svg(type) {
    const id = "ha" + (++seq);
    const f = ART[type];
    return f ? f(id) : frame(id, "", bar(120, 70, 80, 40, .08, 8));
  }

  return { svg };
})();
