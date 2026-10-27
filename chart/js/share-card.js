/* The share card (1200 x 630) a published setup unfurls into: the author's
 * chart, exactly as it is on their screen, edge to edge.
 *
 * The chart is captured, not redrawn: takeScreenshot(true) returns the
 * primary pane at device resolution with every series primitive composited —
 * the author's drawings, what the chat drew, indicators, the axes. A redraw
 * could only ever approximate that. On top of it go the things the canvas
 * does not carry, as the screen shows them: the legend (logo, ticker,
 * interval, venue, the OHLC row, each indicator's reading) copied off the
 * page in its own fonts and colours, the alien badge in its corner, and the
 * author's name as a faint watermark inside the plot. The legend's controls
 * stay behind: a card has nothing to click.
 *
 * Returns "" when there is no chart to capture, so the caller falls back to
 * SharePreview. */
"use strict";

const ShareCard = (() => {
  const W = 1200, H = 630;
  const MAX_URL = 640_000;                        // under shares.OG_IMAGE_MAX
  const SANS = "Inter, ui-sans-serif, -apple-system, Arial, sans-serif";
  const LEGEND = 1.5;                             // card px per css px of legend
  const CARD_FONT = 17;                           // axis type in the capture

  const loadImage = (src, cors) => new Promise((resolve) => {
    if (!src) return resolve(null);
    const img = new Image();
    // A mark from another origin is used only if it is served for CORS;
    // otherwise it would taint the canvas and the card could not be encoded.
    if (cors) img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = src;
  });

  const font = (ctx, spec, tracking = 0) => {
    ctx.font = spec;
    if ("letterSpacing" in ctx) ctx.letterSpacing = `${tracking}px`;
  };

  /** Halve until the next step lands within 2x of the target, then let the
   *  final drawImage finish: one big downscale in a single pass aliases thin
   *  lines and small text, which is exactly what a chart is made of. */
  function stepDown(src, tw, th) {
    let cur = src, w = src.width, h = src.height;
    while (w / 2 >= tw && h / 2 >= th) {
      w = Math.round(w / 2); h = Math.round(h / 2);
      const c = document.createElement("canvas");
      c.width = w; c.height = h;
      const x = c.getContext("2d");
      x.imageSmoothingEnabled = true; x.imageSmoothingQuality = "high";
      x.drawImage(cur, 0, 0, w, h);
      cur = c;
    }
    return cur;
  }

  /** The theme's ink, read off the chart background: light text on a dark
   *  chart, dark on a light one. */
  function inkFor(bg) {
    const m = /#?([0-9a-f]{6})/i.exec(bg || "");
    if (!m) return { ink: "#ffffff", dark: true };
    const n = parseInt(m[1], 16);
    const lum = (0.2126 * ((n >> 16) & 255) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255)) / 255;
    return lum < 0.5 ? { ink: "#ffffff", dark: true } : { ink: "#0d0d0e", dark: false };
  }

  /** The page's legend, redrawn on the card: every visible piece of text in
   *  `.readout` at its own position, font and colour, scaled by `k` from the
   *  chart's top-left corner, plus the company logo. Icons, buttons and
   *  anything faded to nothing are skipped — those are the controls. Returns
   *  the box it covered (card px) so a plate can sit under it. */
  const SKIP = "svg, button, .ind-acts, .company-page-icon, .ind-toggle";
  function legendRuns(origin) {
    const ro = document.querySelector(".readout");
    if (!ro) return [];
    const runs = [];
    const seen = (el) => {
      let a = 1;
      for (let e = el; e && e !== ro.parentElement; e = e.parentElement) {
        const cs = getComputedStyle(e);
        if (cs.display === "none" || cs.visibility === "hidden") return 0;
        a *= Number(cs.opacity);
      }
      return a;
    };
    const walk = document.createTreeWalker(ro, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    for (let n = walk.currentNode; n; n = walk.nextNode()) {
      const el = n.nodeType === 1 ? n : n.parentElement;
      if (!el || el.closest(SKIP)) continue;
      if (n.nodeType === 1 && n.tagName !== "IMG") continue;
      if (n.nodeType === 3 && !n.nodeValue.trim()) continue;
      const alpha = seen(el);
      if (alpha < 0.05) continue;
      let r;
      if (n.nodeType === 3) { const rg = document.createRange(); rg.selectNodeContents(n); r = rg.getBoundingClientRect(); }
      else r = n.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      const cs = getComputedStyle(el);
      runs.push({
        img: n.nodeType === 1 ? n.currentSrc || n.src : "",
        text: n.nodeType === 3 ? n.nodeValue.replace(/\s+/g, " ") : "",
        x: r.left - origin.left, y: r.top - origin.top, w: r.width, h: r.height,
        font: [cs.fontStyle, cs.fontWeight, cs.fontSize, cs.fontFamily],
        tracking: parseFloat(cs.letterSpacing) || 0,
        color: cs.color, alpha, round: parseFloat(cs.borderTopLeftRadius) >= r.width / 2 - 1,
      });
    }
    return runs;
  }

  async function drawLegend(ctx, runs, k, bg) {
    if (!runs.length) return;
    const imgs = await Promise.all(runs.map((r) => (r.img ? loadImage(r.img, true) : null)));
    // One plate per legend row, hugging it: a level running through the row
    // breaks once, cleanly, around the whole line rather than between words.
    const rows = [];
    for (const r of runs) {
      const mid = r.y + r.h / 2;
      const row = rows.find((w) => Math.abs(w.mid - mid) < r.h / 2);
      if (row) { row.x0 = Math.min(row.x0, r.x); row.x1 = Math.max(row.x1, r.x + r.w); row.y0 = Math.min(row.y0, r.y); row.y1 = Math.max(row.y1, r.y + r.h); }
      else rows.push({ mid, x0: r.x, x1: r.x + r.w, y0: r.y, y1: r.y + r.h });
    }
    ctx.save();
    ctx.fillStyle = bg; ctx.globalAlpha = 0.9;
    for (const w of rows) {
      ctx.beginPath();
      ctx.roundRect(w.x0 * k - 6, w.y0 * k - 1, (w.x1 - w.x0) * k + 12, (w.y1 - w.y0) * k + 2, 6);
      ctx.fill();
    }
    ctx.restore();
    runs.forEach((r, i) => {
      ctx.save();
      ctx.globalAlpha = r.alpha;
      if (r.img) {
        const im = imgs[i];
        if (im) {
          ctx.beginPath();
          if (r.round) ctx.arc((r.x + r.w / 2) * k, (r.y + r.h / 2) * k, (r.w / 2 + 2) * k, 0, Math.PI * 2);
          ctx.fillStyle = bg; ctx.fill();
          ctx.beginPath();
          if (r.round) ctx.arc((r.x + r.w / 2) * k, (r.y + r.h / 2) * k, (r.w / 2) * k, 0, Math.PI * 2);
          else ctx.rect(r.x * k, r.y * k, r.w * k, r.h * k);
          ctx.clip();
          ctx.drawImage(im, r.x * k, r.y * k, r.w * k, r.h * k);
        }
      } else {
        const [style, weight, size, family] = r.font;
        font(ctx, `${style} ${weight} ${parseFloat(size) * k}px ${family}`, r.tracking * k);
        ctx.textBaseline = "alphabetic";
        const m = ctx.measureText(r.text);
        const asc = m.fontBoundingBoxAscent, desc = m.fontBoundingBoxDescent;
        // the range box is the font's content area: centre that on the run
        const bx = r.x * k, by = r.y * k + (r.h * k - (asc + desc)) / 2 + asc;
        // A halo of the chart's own ground, not a plate: a line or a wick
        // stops a hair short of each letter and carries on between them.
        ctx.lineJoin = "round"; ctx.lineWidth = 4 * k; ctx.strokeStyle = bg;
        ctx.strokeText(r.text, bx, by);
        ctx.fillStyle = r.color; ctx.fillText(r.text, bx, by);
      }
      ctx.restore();
    });
  }

  /** The chart, laid out at the card's shape for the instant of the
   *  screenshot, then put back. Same bars in view, same drawings, same axes:
   *  the author's chart at the card's shape rather than a crop of it. All of
   *  it runs in one task, so the screen never paints the card-sized chart.
   *  The chart runs with autoSize, which ignores resize() until it is off.
   *
   *  Feeds show this card 200–400px wide, where a 12px axis label or a 1px
   *  drawing is a smudge. On a high-density screen the chart is laid out at
   *  2/3 of the card and downsampled, so every line, label and drawing lands
   *  1.5x heavier and still pixel-sharp; on a 1x screen it is laid out at
   *  full size with the axis type raised to CARD_FONT instead. */
  function captureAtCardSize(chart, el) {
    const ts = chart.timeScale();
    const range = ts.getVisibleLogicalRange();
    const k = (window.devicePixelRatio || 1) >= 1.5 ? 1.5 : 1;  // card px per css px
    const fontSize = (chart.options().layout || {}).fontSize || 12;
    const w0 = el.clientWidth, h0 = el.clientHeight;
    let shot = null, axisW = 0, axisH = 0;
    try {
      chart.applyOptions({ autoSize: false, layout: { fontSize: Math.round(CARD_FONT / k) } });
      chart.resize(Math.round(W / k), Math.round(H / k), true);
      if (range) ts.setVisibleLogicalRange(range);
      shot = chart.takeScreenshot(true);
      try { axisW = chart.priceScale("right").width() * k; } catch {}
      try { axisH = ts.height() * k; } catch {}
    } finally {
      chart.applyOptions({ layout: { fontSize } });
      chart.resize(w0, h0, true);
      chart.applyOptions({ autoSize: true });
      if (range) ts.setVisibleLogicalRange(range);
    }
    if (shot) { shot.axisW = axisW; shot.axisH = axisH; }
    return shot;
  }

  function encode(canvas) {
    for (const q of [0.95, 0.9, 0.84, 0.76]) {
      const url = canvas.toDataURL("image/jpeg", q);
      if (url.length <= MAX_URL) return url;
    }
    return "";
  }

  /* opts: { by } — the author, for the watermark */
  async function render(opts = {}) {
    const c = window.__pivot;
    const el = document.getElementById("chart");
    if (!c || !c.chart || !el) return "";
    // Read before the capture: resizing the chart re-renders the legend, and
    // for that moment an indicator's reading is blank.
    const runs = legendRuns(el.getBoundingClientRect());
    const shot = captureAtCardSize(c.chart, el);
    if (!shot || !shot.width || !shot.height) return "";

    // The chart's own ground, as the chart paints it: the CSS variable can
    // lag a theme switch, the chart's layout option cannot.
    let bg = "";
    try { const b = (c.chart.options().layout || {}).background || {}; bg = b.color || b.topColor || ""; } catch {}
    if (!/^#[0-9a-f]{6}$/i.test(bg)) {
      try { bg = Theme.c("chartBg"); } catch { bg = ""; }
    }
    if (!/^#[0-9a-f]{6}$/i.test(bg)) bg = "#0d0e12";
    const { ink, dark } = inkFor(bg);
    if (document.fonts && document.fonts.load) {
      await Promise.all([`700 22px ${SANS}`, `600 72px ${SANS}`].map((s) => document.fonts.load(s).catch(() => null)));
    }
    const badge = await loadImage("assets/pivot-alien.png?v=5");

    const canvas = document.createElement("canvas");
    canvas.width = W; canvas.height = H;
    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) return "";
    ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);
    ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = "high";

    // The capture has the card's shape, so it fills the card with nothing
    // cropped; stepDown takes it from device pixels to card pixels.
    ctx.drawImage(stepDown(shot, W, H), 0, 0, W, H);

    // The plot, in card pixels: the card minus the price and time axes, read
    // while the chart was at card size (captureAtCardSize keeps them).
    const plot = { x0: 0, y0: 0, x1: W - shot.axisW, y1: H - shot.axisH };

    // The author, as a watermark inside the plot: there to credit, never to
    // compete with a candle.
    const by = String(opts.by || "").trim();
    if (by) {
      ctx.save();
      ctx.globalAlpha = dark ? 0.075 : 0.06;
      ctx.fillStyle = ink; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      let size = 76;
      font(ctx, `600 ${size}px ${SANS}`, -2);
      const room = (plot.x1 - plot.x0) * 0.7;
      while (size > 28 && ctx.measureText(by).width > room) { size -= 4; font(ctx, `600 ${size}px ${SANS}`, -2); }
      ctx.fillText(by, (plot.x0 + plot.x1) / 2, (plot.y0 + plot.y1) / 2);
      ctx.restore();
    }

    // The legend, as large as fits left of the price axis.
    const span = runs.length ? Math.max(...runs.map((r) => r.x + r.w)) : 0;
    await drawLegend(ctx, runs, Math.min(LEGEND, span ? (plot.x1 - 24) / span : LEGEND), bg);

    // The alien badge, where the chart keeps it: bottom left, above the time
    // axis, on its white plate with a thin ring.
    if (badge) {
      const d = 40 * LEGEND, x = 10 * LEGEND, y = plot.y1 - 10 * LEGEND - d;
      ctx.save();
      ctx.shadowColor = "rgba(19,23,34,.18)"; ctx.shadowBlur = 3; ctx.shadowOffsetY = 1;
      ctx.beginPath(); ctx.arc(x + d / 2, y + d / 2, d / 2, 0, Math.PI * 2);
      ctx.fillStyle = "#ffffff"; ctx.fill();
      ctx.shadowColor = "transparent";
      ctx.strokeStyle = "rgba(19,23,34,.12)"; ctx.lineWidth = 1; ctx.stroke();
      const inset = 2 * LEGEND;
      ctx.drawImage(badge, x + inset, y + inset, d - 2 * inset, d - 2 * inset);
      ctx.restore();
    }
    return encode(canvas);
  }

  return { render };
})();
