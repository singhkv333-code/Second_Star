/* Charto preview — hover a price in a reply, see it on the chart.
 *
 * A reply says "resistance at 83,354-83,740"; the reader then has to find
 * 83,354 on the axis. Hovering the number now shows it: a shaded band for a
 * range, a dashed line for one price, on the main chart, for as long as the
 * pointer is on the number. Nothing is drawn into the scene, the drawings or
 * the undo history — it is a view, not a mark — and the number itself is not
 * underlined or restyled.
 *
 * Cheap by construction:
 *  · numbers are wrapped ONCE, when a reply finishes (mark()), and only those
 *    that fall inside the chart's own price range — so RSI 61, "50 EMA", a
 *    bar count or a date on an 83,000 chart are never candidates;
 *  · ONE delegated pointerover/pointerout pair for every reply, no listener
 *    per number;
 *  · ONE overlay element, reused, positioned with the series' own
 *    priceToCoordinate; it follows the chart only while it is showing.
 */
"use strict";

const PriceHover = (() => {
  const NUM = String.raw`(?:[₹$]\s?)?\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|(?:[₹$]\s?)?\d+(?:\.\d+)?`;
  // a number, optionally a range; not part of a word, a time, a percent or a date
  const RX = new RegExp(String.raw`(?<![\w.,:/])(${NUM})(?:\s?(?:–|—|-|to)\s?(${NUM}))?(?![\w%:/]|\.\d)`, "g");
  // what a number right after these words is: never a price
  const NOT_PRICE = /\b(?:EMA|SMA|MA|RSI|MACD|ADX|DI|ATR|VWAP|period|periods|bars?|touch(?:es)?|days?|weeks?|months?|of|top|last|Q[1-4]|FY)\s*$/i;
  const HINT_KEY = "px_hover_hint";

  const num = (s) => Number(String(s).replace(/[₹$,\s]/g, ""));
  const fmt = (v) => v.toLocaleString("en-IN", { maximumFractionDigits: 2 });

  function chartRange() {
    const c = window.__charto;
    const bars = c && c.state && c.state.bars;
    if (!bars || !bars.length) return null;
    let lo = Infinity, hi = -Infinity;
    for (const b of bars) { if (b.low < lo) lo = b.low; if (b.high > hi) hi = b.high; }
    return lo < hi ? { lo: lo * 0.8, hi: hi * 1.25 } : null;
  }

  /** Wrap the price numbers in a finished reply. Idempotent per element. */
  function mark(root) {
    if (!root) return;
    const range = chartRange();
    if (!range) return;
    const ok = (v) => Number.isFinite(v) && v >= range.lo && v <= range.hi;
    const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => n.parentElement.closest("a, code, pre, .px-hv")
        ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    const jobs = [];
    for (let n = walk.nextNode(); n; n = walk.nextNode()) if (/\d/.test(n.nodeValue)) jobs.push(n);
    let found = 0;
    for (const node of jobs) {
      const text = node.nodeValue;
      const frag = document.createDocumentFragment();
      let last = 0, m;
      RX.lastIndex = 0;
      while ((m = RX.exec(text))) {
        const a = num(m[1]), b = m[2] != null ? num(m[2]) : null;
        if (!ok(a) || (b != null && !ok(b)) || NOT_PRICE.test(text.slice(Math.max(0, m.index - 12), m.index))) continue;
        if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
        const span = document.createElement("span");
        span.className = "px-hv";
        span.dataset.lo = String(b == null ? a : Math.min(a, b));
        span.dataset.hi = String(b == null ? a : Math.max(a, b));
        span.textContent = m[0];
        frag.appendChild(span);
        last = m.index + m[0].length;
        found++;
      }
      if (!last) continue;
      if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
      node.parentNode.replaceChild(frag, node);
    }
    if (found) hint(root.querySelector(".px-hv"));
  }

  /* ── the overlay ─────────────────────────────────────────────────────── */
  let box = null, tag = null, cur = null, sub = false, raf = 0;

  function ensure(stage) {
    if (box && box.parentNode === stage) return;
    box = document.createElement("div");
    box.className = "px-hl";
    tag = document.createElement("span");
    tag.className = "px-hl-tag";
    box.appendChild(tag);
    stage.appendChild(box);
  }

  function place() {
    raf = 0;
    const c = window.__charto, stage = document.getElementById("stage");
    if (!cur || !c || !c.candle || !stage) return hide();
    ensure(stage);
    const H = stage.clientHeight;
    let axis = 0;
    try { axis = c.chart.priceScale("right").width(); } catch (_) { /* no right scale */ }
    const y1 = c.candle.priceToCoordinate(cur.hi), y2 = c.candle.priceToCoordinate(cur.lo);
    if (y1 == null || y2 == null) return hide();
    const top = Math.max(0, Math.min(y1, y2)), bot = Math.min(H, Math.max(y1, y2));
    const off = y2 < 0 ? "above" : y1 > H ? "below" : "";
    box.classList.toggle("line", cur.lo === cur.hi);
    box.classList.toggle("off", !!off);
    box.style.right = `${axis}px`;
    box.style.top = `${off === "below" ? H - 2 : off === "above" ? 0 : top}px`;
    box.style.height = `${off ? 2 : Math.max(0, bot - top)}px`;
    tag.textContent = (cur.lo === cur.hi ? fmt(cur.lo) : `${fmt(cur.lo)} – ${fmt(cur.hi)}`)
      + (off ? (off === "above" ? "  ↑ above view" : "  ↓ below view") : "");
    box.hidden = false;
  }
  const schedule = () => { if (!raf) raf = requestAnimationFrame(place); };

  function show(span) {
    cur = { lo: Number(span.dataset.lo), hi: Number(span.dataset.hi) };
    const c = window.__charto;
    if (!sub && c && c.chart) {
      c.chart.timeScale().subscribeVisibleLogicalRangeChange(schedule);
      sub = true;
    }
    schedule();
  }
  function hide() {
    cur = null;
    if (box) box.hidden = true;
    const c = window.__charto;
    if (sub && c && c.chart) {
      c.chart.timeScale().unsubscribeVisibleLogicalRangeChange(schedule);
      sub = false;
    }
  }

  document.addEventListener("pointerover", (e) => {
    const s = e.target.closest && e.target.closest(".px-hv");
    if (s) { dropHint(true); show(s); }
  });
  document.addEventListener("pointerout", (e) => {
    const s = e.target.closest && e.target.closest(".px-hv");
    if (s && !(e.relatedTarget && s.contains(e.relatedTarget))) hide();
  });

  /* ── the hint, now and then ─────────────────────────────────────────────
   * Nothing on the numbers says they do anything, so every so often a small
   * pop-up over the first one says so: once a session, five sessions at most,
   * and never again once the reader has hovered one. */
  let hintEl = null, hintShown = false;
  function seen() {
    try { return JSON.parse(localStorage.getItem(HINT_KEY) || "{}"); } catch (_) { return {}; }
  }
  function save(v) { try { localStorage.setItem(HINT_KEY, JSON.stringify(v)); } catch (_) { /* private mode */ } }
  function hint(span) {
    if (!span || hintShown) return;
    const s = seen();
    if (s.used || (s.n || 0) >= 5) return;
    hintShown = true;
    save({ ...s, n: (s.n || 0) + 1 });
    requestAnimationFrame(() => {
      const r = span.getBoundingClientRect();
      if (!r.width) return;
      hintEl = document.createElement("div");
      hintEl.className = "px-hint";
      hintEl.textContent = "Hover a price to see it on the chart";
      document.body.appendChild(hintEl);
      const w = hintEl.offsetWidth;
      hintEl.style.left = `${Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2))}px`;
      hintEl.style.top = `${Math.max(8, r.top - hintEl.offsetHeight - 8)}px`;
      setTimeout(() => dropHint(false), 5000);
    });
  }
  function dropHint(used) {
    if (used) { const s = seen(); if (!s.used) save({ ...s, used: true }); }
    if (!hintEl) return;
    hintEl.remove();
    hintEl = null;
  }

  return { mark };
})();
