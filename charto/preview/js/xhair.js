/* Charto preview — the crosshair's two plates.
 *
 * The price under the pointer, printed on the price scale, and the time under
 * it, printed on the time scale. lightweight-charts draws both itself — into
 * the axis CANVAS — and that is exactly why these are ours instead.
 *
 * GLASS. The app's menus and its ask group are one material (an SVG
 * displacement map driven through backdrop-filter, vendor/liquid-glass.js),
 * and these two were the last opaque slate rectangles left on the surface. A
 * canvas cannot be glass: backdrop-filter is a property of an ELEMENT with a
 * backdrop behind it. So the plates left the canvas to join the rest of the
 * app, and every chart that draws them turns the library's pair off
 * (`crosshair.{horz,vert}Line.labelVisible: false`) — nothing draws twice.
 *
 * A MODULE, not a block inside the primary chart's file, because a split
 * layout puts three more charts on screen (js/panes.js) and a secondary pane
 * wearing the library's dark slate beside a primary wearing glass is two
 * products in one window.
 *
 * NOT OVER THE AXES. The plates appear while the pointer is over the PLOT and
 * nowhere else. An earlier version deliberately held the crosshair alive while
 * the pointer was on the price scale, so the alert ⊕ would have a plate to
 * ride — but the price it printed there was the price of the row the pointer
 * was already on, so the plate covered the very tick it was repeating. The ⊕
 * carries a disc of its own for that reach (see .alert-plus in index.html).
 */
"use strict";

const Xhair = (() => {
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
               "Oct", "Nov", "Dec"];
  const PLATE_H = 22;          // must match .xh-plate in index.html
  const p2 = (n) => String(n).padStart(2, "0");

  /**
   * @param chart  the lightweight-charts instance
   * @param env    { root, canvas, panes, intervalSec }
   *   root        the positioned box the plates are laid out in (.stage / .subchart)
   *   canvas      the chart's own container, for the pointer listeners
   *   panes       () => [{ key, pane, series }] — the same shape both owners
   *               already build for the drawing layer
   *   intervalSec () => seconds per bar, for "does this chart carry a clock"
   */
  function attach(chart, env) {
    const price = document.createElement("div");
    price.className = "xh-plate price";
    const time = document.createElement("div");
    time.className = "xh-plate time";
    for (const n of [price, time]) {
      n.setAttribute("aria-hidden", "true");
      env.root.appendChild(n);
    }

    /* The two measurements the stylesheet cannot take. Published on the plates'
     * own root rather than on the chart container: custom properties inherit,
     * so everything inside the chart still reads them, and the plates — which
     * are siblings of it, not children — can read them too. Both move on their
     * own (the scale re-sizes itself around a wider price the moment the symbol
     * changes), so neither can be a constant in the CSS. */
    const seen = { w: 0, h: 0 };
    function metrics() {
      let w = 0, h = 0;
      try { w = chart.priceScale("right").width(); } catch { /* pre-layout */ }
      try { h = chart.timeScale().height(); } catch { /* ditto */ }
      if (w && w !== seen.w) { seen.w = w; env.root.style.setProperty("--axis-w", w + "px"); }
      if (h && h !== seen.h) { seen.h = h; env.root.style.setProperty("--time-axis-h", h + "px"); }
      return seen;
    }

    /* Glazed per plate, and only once it is BOTH visible and carrying its text:
     * the module builds its displacement map from the measured size, and a map
     * built for an empty 2px box is a filter that does nothing for the rest of
     * the session. The two reach that state at different moments (the time
     * plate stays away in the right-hand margin), hence one at a time.
     *
     * Gentler than the menus': this is a plate two numbers wide, and a lens
     * strong enough to be obvious at the rim would bend the digits it exists to
     * show. The module falls back to frosted blur on Safari and Firefox by
     * itself; the CSS backdrop-filter is the floor under both. */
    const glazed = new WeakSet();
    const lenses = [];
    function glaze(n) {
      if (!window.liquidGlass || glazed.has(n)) return;
      if (!n.classList.contains("show") || n.offsetWidth < 8) return;
      glazed.add(n);
      try {
        lenses.push(liquidGlass(n, { scale: -26, chroma: 2, border: .12,
                                     mapBlur: 6, blur: 6, saturate: 1.4,
                                     fallbackBlur: 14 }));
      } catch { /* a plate with no refraction is still a readable plate */ }
    }

    /** The pane row the pointer is in — so a plate over the RSI pane prints an
     *  RSI reading and not a rupee price. */
    function rowAt(clientY) {
      for (const p of env.panes()) {
        const e = p.pane && p.pane.getHTMLElement && p.pane.getHTMLElement();
        if (!e) continue;
        const r = e.getBoundingClientRect();
        if (clientY >= r.top && clientY <= r.bottom) return p;
      }
      return null;
    }

    /** Over the PLOT: inside a pane, and left of the price scale. The pane's
     *  own element spans the scale too, which is why this is not just a
     *  bounding-box test. */
    function inPlot(row, clientX, clientY, axisW) {
      const e = row.pane.getHTMLElement && row.pane.getHTMLElement();
      if (!e) return false;
      const r = e.getBoundingClientRect();
      return clientY >= r.top && clientY <= r.bottom
          && clientX >= r.left && clientX < r.right - axisW;
    }

    /** The pane's OWN units. Every series carries the formatter its scale is
     *  printed with, so this is the same string the ticks beside it are drawn
     *  from — precision setting, volume suffixes and all. */
    function fmtValue(row, v) {
      try { return row.series.priceFormatter().format(v); }
      catch { return Number(v).toFixed(2); }
    }

    /** The time, as the axis under it writes dates. Intraday carries the clock;
     *  a daily chart does not, because every bar on it is the same time of day
     *  and a repeated 00:00 is noise. Chart times are shifted to the exchange's
     *  clock at the source, so the UTC getters render its wall clock (Sym.tz). */
    function fmtTime(t) {
      const d = new Date(t * 1000);
      const date = `${p2(d.getUTCDate())} ${MON[d.getUTCMonth()]} `
        + `'${String(d.getUTCFullYear()).slice(2)}`;
      if ((env.intervalSec() || 0) >= 86400) return date;
      return `${date}  ${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}`;
    }

    /* WHERE THE READOUT IS, as one object, because two things draw on it: this
     * module, and the alert ⊕ that stands at the plate's left end (js/main.js).
     * The mark used to take its own price and its own y from the pointer, which
     * is a second answer to the same question — and the moment the plate stopped
     * following the pointer onto the scale, the two disagreed and the mark was
     * left standing on bare axis with nothing under it. */
    const cur = { shown: false, top: 0, key: null, value: null };

    /* THE REACH. The plates do not follow the pointer onto the price scale —
     * over there the price they would print is the price of the row the pointer
     * is already on, under a plate covering that very tick. But the ⊕ IS on the
     * scale (it has to be: on the plot it covered candles and ate the clicks
     * meant for drawings), so hiding everything the instant the pointer crosses
     * the boundary makes the one-click alert unreachable.
     *
     * So the plate holds — frozen, not tracking — for a STRAIGHT reach at the
     * mark, and for nothing else. `reachY` is the y the pointer left the plot
     * at; drift more than a mark's height from it and the hold is over and does
     * not come back until the pointer has been on the plot again. Hovering the
     * scale, running up and down it, dragging it to rescale, or arriving on it
     * from the toolbar all break the leash on the first move, which is every
     * way of being "on the price axis" that is not reaching for the mark. */
    const REACH = 14;
    let reachY = null;

    function hide() {
      reachY = null;
      cur.shown = false; cur.key = null; cur.value = null;
      price.classList.remove("show");
      time.classList.remove("show");
    }

    /** Freeze: leave both plates exactly where they are. Nothing is recomputed,
     *  so the price under the mark is still the price the pointer chose. */
    const holdStill = () => cur;

    function sync(clientX, clientY) {
      const m = metrics();
      const row = rowAt(clientY);
      if (!row || !inPlot(row, clientX, clientY, m.w)) {
        const box0 = env.canvas.getBoundingClientRect();
        const onScale = clientX >= box0.right - m.w && clientX <= box0.right
          && clientY >= box0.top && clientY <= box0.bottom;
        if (onScale && reachY !== null && Math.abs(clientY - reachY) <= REACH
            && cur.shown) return holdStill();
        return hide();
      }
      reachY = clientY;
      const box = env.canvas.getBoundingClientRect();

      const pe = row.pane.getHTMLElement();
      const y = clientY - pe.getBoundingClientRect().top;
      const v = row.series.coordinateToPrice(y);
      if (v == null || !isFinite(v)) {
        price.classList.remove("show");
        cur.shown = false; cur.value = null;
      } else {
        price.textContent = fmtValue(row, v);
        cur.top = clientY - box.top;
        cur.shown = true; cur.key = row.key; cur.value = v;
        price.style.top = cur.top + "px";
        price.classList.add("show");
      }

      // coordinateToTime answers null in the right-hand margin, where there are
      // no bars — and a plate there would print a time nothing happened at.
      let t = null;
      try { t = chart.timeScale().coordinateToTime(clientX - box.left); } catch {}
      if (t == null) time.classList.remove("show");
      else {
        time.textContent = fmtTime(t);
        time.classList.add("show");
        // Centred on the pointer, then kept whole: a plate half off the left
        // edge or running under the price scale is a plate you cannot read.
        const w = time.offsetWidth, lim = box.width - m.w;
        time.style.left =
          Math.round(Math.max(0, Math.min(clientX - box.left - w / 2, lim - w))) + "px";
      }
      glaze(price); glaze(time);
    }

    const onMove = (e) => sync(e.clientX, e.clientY);
    env.canvas.addEventListener("mousemove", onMove);
    env.canvas.addEventListener("mouseleave", hide);

    return {
      sync, hide,
      /** The price plate's current state, for anything that has to stand ON it.
       *  `top` is in the chart container's coordinates, `value` is the pane's
       *  own raw number and `key` says which pane's units that is. */
      plate: () => cur,
      /** For an owner that is torn down — a secondary pane dies with its
       *  layout, and a live SVG filter per pane ever opened is a leak. */
      destroy() {
        env.canvas.removeEventListener("mousemove", onMove);
        env.canvas.removeEventListener("mouseleave", hide);
        for (const l of lenses) { try { l.destroy(); } catch {} }
        lenses.length = 0;
        price.remove(); time.remove();
      },
    };
  }

  return { attach, PLATE_H };
})();
