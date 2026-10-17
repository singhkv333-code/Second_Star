/* The share card (1200 x 630) a published setup unfurls into: the author's
 * chart, exactly as it is on their screen, edge to edge.
 *
 * The chart is captured, not redrawn: takeScreenshot(true) returns the
 * primary pane at device resolution with every series primitive composited —
 * the author's drawings, what the chat drew, indicators, the axes. A redraw
 * could only ever approximate that. On top of it go three things the canvas
 * does not carry: the company's mark and symbol where the legend sits, the
 * author's name as a faint watermark inside the plot, and the Pivot wordmark
 * (assets/pivot-logo.svg) small in the corner. No other words.
 *
 * Returns "" when there is no chart to capture, so the caller falls back to
 * SharePreview. */
"use strict";

const ShareCard = (() => {
  const W = 1200, H = 630;
  const MAX_URL = 640_000;                        // under shares.OG_IMAGE_MAX
  const SANS = "Inter, ui-sans-serif, -apple-system, Arial, sans-serif";
  const LOGO_W = 847, LOGO_H = 319;               // assets/pivot-logo.svg
  const CARD_FONT = 17;                           // axis type in the capture
  const INSET = 30;                               // inside every platform's crop

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

  function tinted(img, w, h, color) {
    const c = document.createElement("canvas");
    c.width = Math.round(w * 2); c.height = Math.round(h * 2);
    const x = c.getContext("2d");
    x.drawImage(img, 0, 0, c.width, c.height);
    x.globalCompositeOperation = "source-in";
    x.fillStyle = color; x.fillRect(0, 0, c.width, c.height);
    return c;
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

  /* opts: { symbol, interval, logo, by } */
  async function render(opts = {}) {
    const c = window.__charto;
    const el = document.getElementById("chart");
    if (!c || !c.chart || !el) return "";
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
    const src = opts.logo || "";
    const [mark, wordmark] = await Promise.all([
      loadImage(src, src && !src.startsWith("/") && !src.startsWith(location.origin)),
      loadImage("assets/pivot-logo.svg"),
    ]);

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

    // The company's mark and symbol, where the chart's legend sits, on a soft
    // plate of the chart's own ground so a candle under it cannot cut it.
    const lx = plot.x0 + INSET, ly = plot.y0 + INSET - 4, ls = 40;
    font(ctx, `700 22px ${SANS}`, -0.2);
    const symW = ctx.measureText(String(opts.symbol || "").toUpperCase().slice(0, 18)).width;
    font(ctx, `600 14px ${SANS}`, 0.3);
    const ivW = opts.interval ? ctx.measureText(String(opts.interval).slice(0, 6)).width + 26 : 0;
    ctx.save();
    ctx.beginPath(); ctx.roundRect(lx - 8, ly - 7, ls + 12 + symW + ivW + 24, ls + 14, 26);
    ctx.fillStyle = bg; ctx.globalAlpha = 0.86; ctx.fill();
    ctx.restore();
    ctx.save();
    ctx.beginPath(); ctx.arc(lx + ls / 2, ly + ls / 2, ls / 2, 0, Math.PI * 2);
    ctx.fillStyle = mark ? "#ffffff" : (dark ? "#1b1d21" : "#eceef0"); ctx.fill();
    if (mark) {
      ctx.clip();
      const k = Math.min((ls - 6) / mark.width, (ls - 6) / mark.height);
      ctx.drawImage(mark, lx + (ls - mark.width * k) / 2, ly + (ls - mark.height * k) / 2, mark.width * k, mark.height * k);
    } else {
      ctx.fillStyle = ink; font(ctx, `700 17px ${SANS}`);
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(String(opts.symbol || "?").slice(0, 1).toUpperCase(), lx + ls / 2, ly + ls / 2 + 1);
    }
    ctx.restore();
    ctx.save();
    ctx.fillStyle = ink; ctx.textBaseline = "middle";
    font(ctx, `700 22px ${SANS}`, -0.2);
    const sym = String(opts.symbol || "").toUpperCase().slice(0, 18);
    ctx.fillText(sym, lx + ls + 12, ly + ls / 2 + 1);
    if (opts.interval) {
      const ix = lx + ls + 12 + ctx.measureText(sym).width + 10;
      font(ctx, `600 14px ${SANS}`, 0.3);
      const label = String(opts.interval).slice(0, 6), w = ctx.measureText(label).width + 16;
      ctx.beginPath(); ctx.roundRect(ix, ly + ls / 2 - 12, w, 24, 7);
      ctx.fillStyle = dark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.06)"; ctx.fill();
      ctx.fillStyle = dark ? "rgba(255,255,255,0.72)" : "rgba(0,0,0,0.62)";
      ctx.fillText(label, ix + 8, ly + ls / 2 + 1);
    }
    ctx.restore();

    // The Pivot wordmark, small, bottom left of the plot.
    if (wordmark) {
      const h = 26, w = h * LOGO_W / LOGO_H;
      ctx.save();
      // on the same soft plate as the legend: it sits over the volume bars
      ctx.beginPath();
      ctx.roundRect(plot.x0 + INSET - 12, plot.y1 - h - INSET - 2, w + 24, h + 16, 12);
      ctx.fillStyle = bg; ctx.globalAlpha = 0.86; ctx.fill();
      ctx.globalAlpha = 0.85;
      ctx.drawImage(tinted(wordmark, w, h, ink), plot.x0 + INSET, plot.y1 - h - INSET + 6, w, h);
      ctx.restore();
    }
    return encode(canvas);
  }

  return { render };
})();
