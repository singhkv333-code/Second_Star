/* Charto preview — the SCENE layer: what the chat drew.
 *
 * Deliberately separate from drawings.js. User drawings are solid, editable
 * and owned by the user; scene annotations are dashed, read-only and carry
 * provenance. You must always be able to tell who drew what.
 *
 * Every coordinate here arrived from a backend detector via a tool result —
 * the model chose WHICH to show, never what the number is.
 *
 * The vocabulary is a handful of PRIMITIVES, not one kind per feature:
 *   level    — horizontal line at a value
 *   zone     — horizontal band between two values
 *   segment  — a line between two (time, value) points
 * A new detector emits these and needs no new renderer. Every primitive
 * carries a pane key, so an oscillator reading is drawn in the oscillator's
 * own pane and never mistaken for a price.
 */
"use strict";

const Scene = (() => {
  // per-module alias, like every other module here — a file that uses the
  // library without re-declaring this throws "LWC is not defined"
  const LWC = window.LightweightCharts;
  // Never the candle colours: red and green already mean "closed down / up"
  // on every bar, so a red resistance line reads as a price move rather than
  // as structure. Amber above, cyan below, violet for anything else.
  const COL = (role) => Theme.c(
    role === "resistance" ? "annRes" : role === "support" ? "annSup" : "annNeutral");
  const rgba = (hex, a) => {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  };
  const FONT = 'ui-sans-serif, -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif';
  const HIT = 6;

  function create(chart, candle, env) {
    // env: { panes, getBars, toChartTime, container, inPricePane, priceY,
    //        onChange, onHover, onSelect, onIndicator, isCursorMode }
    const state = { items: [], hover: null };
    // Event icons (results, and anything else that happens ON a bar) use the
    // library's own marker layer rather than our canvas: markers belong to
    // the series, so they track the bar through zoom, pan and interval
    // changes without any projection of ours.
    let markerApi = null;
    function syncMarkers() {
      const evs = [];
      for (const a of state.items) {
        if (a.kind !== "markers") continue;
        for (const m of a.marks || []) {
          evs.push({
            time: env.toChartTime ? env.toChartTime(m.t) : m.t,
            position: m.position || "aboveBar",
            shape: m.shape || "circle",
            color: m.color || COL(a.role),
            text: m.text || "",
          });
        }
      }
      evs.sort((x, y) => x.time - y.time);   // the API requires ascending time
      if (!markerApi) {
        if (!evs.length) return;             // don't create the layer for nothing
        markerApi = LWC.createSeriesMarkers(candle, evs);
      } else {
        markerApi.setMarkers(evs);
      }
    }
    const rus = new Map();                 // paneKey -> requestUpdate
    const _ru = () => { for (const f of rus.values()) f(); };
    const attached = new Map();            // paneKey -> {pane, prim}

    const fmt = (n) => Sym.num(n);
    /** Pane keys may be composite — "rsi@26" means the rsi pane whose line
     *  is period 26, so marks land on the variant they were computed from
     *  when two of the same indicator are open. Plain names keep working. */
    function paneFor(keyRaw) {
      const key = keyRaw || "price";
      const ps = env.panes();
      const exact = ps.find((p) => p.key === key);
      if (exact) return exact;
      const [nm, per] = String(key).split("@");
      return ps.find((p) => p.key === nm && (!per || p.period === +per))
        || ps.find((p) => p.key === nm);
    }
    function vToY(v, key) {
      const p = paneFor(key);
      return p ? p.series.priceToCoordinate(v) : null;
    }
    /** Detector times are raw unix; the chart runs on IST-shifted times, and
     *  a level found on the daily won't land on a 5m bar boundary — so fall
     *  back to interpolating a logical index.
     *
     *  A time OUTSIDE the loaded bars extrapolates by the edge bar's own
     *  spacing instead of clamping to the edge — clamping collapsed every
     *  cross-interval drawing into a vertical smear at logical 0. The
     *  extrapolation ignores closed-market gaps (it cannot know sessions it
     *  has no bars for), so it places the shape approximately until
     *  main.js's coverage loader brings in the real bars and it snaps. */
    /** logicalToCoordinate for a FRACTIONAL logical. LWC v5 silently
     *  returns 0 for any non-integer logical, which collapsed every
     *  interpolated anchor — i.e. every cross-interval drawing — onto the
     *  left edge. Project the two neighbouring integer logicals and
     *  interpolate between them ourselves. */
    function logicalToX(l) {
      const ts = chart.timeScale();
      const i = Math.floor(l), f = l - i;
      const x0 = ts.logicalToCoordinate(i);
      if (x0 === null) return null;
      if (!f) return x0;
      const x1 = ts.logicalToCoordinate(i + 1);
      return x1 === null ? x0 : x0 + (x1 - x0) * f;
    }
    function tToX(t) {
      const ts = chart.timeScale();
      const ct = env.toChartTime ? env.toChartTime(t) : t;
      const direct = ts.timeToCoordinate(ct);
      if (direct !== null) return direct;
      const bars = env.getBars();
      if (!bars.length) return null;
      let lo = 0, hi = bars.length - 1;
      if (ct <= bars[0].time) {
        const span = bars.length > 1
          ? Math.max(1, bars[1].time - bars[0].time)
          : (env.getIntervalSec ? env.getIntervalSec() : 60);
        return logicalToX(Math.max(-1e5, (ct - bars[0].time) / span));
      }
      if (ct >= bars[hi].time) {
        const span = hi > 0
          ? Math.max(1, bars[hi].time - bars[hi - 1].time)
          : (env.getIntervalSec ? env.getIntervalSec() : 60);
        return logicalToX(Math.min(hi + 1e5, hi + (ct - bars[hi].time) / span));
      }
      while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        if (bars[mid].time <= ct) lo = mid; else hi = mid;
      }
      const span = bars[hi].time - bars[lo].time || 1;
      return logicalToX(lo + (ct - bars[lo].time) / span);
    }

    const mine = (a, key) =>
      String(a.pane || "price").split("@")[0] === (key || "price");

    /* Annotation kinds that are plain geometry — composed by draw_shape from
     * resolved anchors rather than produced by a detector. They render and
     * hit-test through the same algebra as the user's own drawings, so the
     * two layers can never disagree about what a box is.
     *
     * Each entry returns an ARRAY of primitives: a fib is seven lines, and a
     * shape that needed more than one used to be undrawable from chat. */
    const SHAPES = {
      box: (a) => [Geo.box(a.a, a.b, { fill: true })],
      vline: (a) => [Geo.vline(a.t)],
      point: (a) => [Geo.point(a.a)],
      // fill/solid/stroke let a detector compose TradingView-style pattern
      // geometry: a solid outline through the defining swings plus a
      // stroke-less fill polygon, without double-drawing any edge
      poly: (a) => [Geo.poly(a.pts, {
        closed: !!a.closed, fill: !!a.fill,
        ...(a.stroke === false ? { stroke: false } : {}),
        ...(a.solid ? { dash: [] } : {}),
      })],
      // ratios and colours come from Tools, the same source the user's own fib
      // tool draws from — one ladder, so the two layers cannot drift apart
      // trade-plan overlay from plan_position: reward box per target
      // (fading with distance), one risk box, dashed entry. Same palette as
      // the user's own long/short tool so the two layers read as one idiom.
      // trade plan from plan_position — same primitive (and so the exact
      // same TradingView-style design) as the user's own long/short tool
      position: (a) => {
        const inr = (n) => Sym.price(Math.round(n), { maximumFractionDigits: 0 });
        const pct = (v) => Math.abs((v - a.entry) / a.entry * 100).toFixed(2);
        const dst = (v) => Math.abs(v - a.entry).toFixed(2);
        const amt = (i) => (a.pnl && a.pnl[i] != null) ? `, Amount: ${inr(a.pnl[i])}` : "";
        const center = [
          a.qty ? `Qty: ${a.qty}` + (a.risk_amount ? ` · Risk: ${inr(a.risk_amount)}` : "")
                : (a.side === "short" ? "Short" : "Long"),
          `Risk/reward ratio: ${a.rr ?? "—"}`];
        return [Geo.position(
          { t: a.t0, v: a.entry },
          { v: a.stop, text: `Stop: ${a.stop.toFixed(2)} (${pct(a.stop)}%) ${dst(a.stop)}`
                             + (a.risk_amount ? `, Amount: ${inr(a.risk_amount)}` : "") },
          (a.targets || []).map((tp, i) => ({
            v: tp, text: `Target: ${tp.toFixed(2)} (${pct(tp)}%) ${dst(tp)}${amt(i)}` })),
          { t1: a.t1, center })];
      },
      fib: (a) => {
        const out = [Geo.segment(a.p1, a.p2, { dash: [3, 3], width: 1 })];
        Geo.ladder(a.p1.v, a.p2.v, Tools.FIB).forEach((lv, i) => {
          out.push(Geo.segment({ t: a.p1.t, v: lv.v }, { t: a.p2.t, v: lv.v },
                               { color: Tools.FIB_COLORS[i] }));
        });
        return out;
      },
    };
    const geoEnv = (key) => ({
      tToX, vToY: (v) => vToY(v, key),
      w: env.container.clientWidth, h: env.container.clientHeight,
    });

    // ── geometry ────────────────────────────────────────
    function distSeg(px, py, x1, y1, x2, y2) {
      const dx = x2 - x1, dy = y2 - y1;
      const l2 = dx * dx + dy * dy;
      let u = l2 ? ((px - x1) * dx + (py - y1) * dy) / l2 : 0;
      u = Math.max(0, Math.min(1, u));
      return Math.hypot(px - (x1 + u * dx), py - (y1 + u * dy));
    }

    /** Which annotation is at this pane-local point? Shared by hover, click
     *  and the chart's own pin guard, so all three always agree. */
    function hitAt(y, key, x) {
      for (let i = state.items.length - 1; i >= 0; i--) {
        const a = state.items[i];
        if (!mine(a, key)) continue;
        if (a.kind === "level") {
          const ly = vToY(a.price, a.pane);
          if (ly !== null && Math.abs(y - ly) < HIT) return a;
        } else if (a.kind === "zone") {
          const y1 = vToY(a.hi, a.pane), y2 = vToY(a.lo, a.pane);
          if (y1 !== null && y2 !== null && y > y1 - HIT && y < y2 + HIT) return a;
        } else if (a.kind === "segment" && x != null) {
          const x1 = tToX(a.p1.t), x2 = tToX(a.p2.t);
          const y1 = vToY(a.p1.v, a.pane), y2 = vToY(a.p2.v, a.pane);
          if ([x1, x2, y1, y2].every((q) => q !== null)
              && distSeg(x, y, x1, y1, x2, y2) < HIT) return a;
        } else if (SHAPES[a.kind] && x != null) {
          // shapes composed via draw_shape share the drawing layer's algebra
          const e = geoEnv(a.pane);
          for (const prim of SHAPES[a.kind](a)) {
            const px = Geo.project(prim, e);
            if (px && Geo.hit(prim, px, x, y, HIT, e)) return a;
          }
        }
      }
      return null;
    }

    // ── rendering ───────────────────────────────────────
    /** Labels are queued, then de-collided before painting. Levels sit close
     *  together by nature, and three chips stacked on the same pixel row is
     *  unreadable — the whole point of showing the record is that you can
     *  read it. */
    /** Labels live in an HTML overlay, not on the canvas.
     *
     *  Painted into the pane canvas they lost to the candlestick series: the
     *  chip's border survived but its interior was overpainted, so a label
     *  crossing price action ("double top · neckline 1,271.00 · confirmed")
     *  was shredded exactly where you needed to read it. An overlay is above
     *  every canvas by construction, and the browser measures its own text —
     *  which also retires a whole class of bug where a hand-measured width
     *  disagreed with the drawn glyphs and the tail spilled out of the box.
     */
    const overlays = new Map();   // paneKey -> host div
    const chipPool = new Map();   // paneKey -> [el]

    function overlayFor(key) {
      const cached = overlays.get(key);
      if (cached && cached.isConnected) return cached;
      const p = paneFor(key);
      const host = p && p.pane.getHTMLElement && p.pane.getHTMLElement();
      if (!host) return null;
      const div = document.createElement("div");
      div.className = "scene-chips";
      host.appendChild(div);
      overlays.set(key, div);
      return div;
    }

    function paintChips(key, chips) {
      const host = overlayFor(key);
      if (!host) return;
      const pool = chipPool.get(key) || [];
      chipPool.set(key, pool);
      while (pool.length < chips.length) {
        const el = document.createElement("span");
        el.className = "scene-chip";
        host.appendChild(el);
        pool.push(el);
      }
      for (let i = chips.length; i < pool.length; i++) pool[i].style.display = "none";

      /* A LEGEND, stacked top-right — not labels pinned to their shapes.
       *
       * Anchored to the geometry they described themselves twice over: the
       * shape is already on the chart, and a name floating beside it buries
       * the price action it is naming. Three patterns and five levels put
       * eight boxes across the candles. Collected into a corner they read as
       * a list of what is drawn, the chart stays legible, and hovering an
       * entry lights up the shape it belongs to — which is what the label
       * beside the shape was trying to do by being adjacent. */
      /* The overlay host spans the pane INCLUDING its price axis, so a plain
       * right:8px parked the legend on top of the price labels. Inset by the
       * scale's own measured width instead of guessing at a constant. */
      let axis = 0;
      try { axis = chart.priceScale("right").width() || 0; } catch { axis = 0; }
      chips.forEach((c, i) => {
        const el = pool[i];
        el.style.display = "";
        el.style.right = `${axis + 8}px`;
        if (el.textContent !== c.text) el.textContent = c.text;
        el.style.color = c.col;
        el.classList.toggle("hot", !!c.hot);
        el.style.top = `${8 + i * 17}px`;   // the indicator legend's own step
        el.dataset.ann = c.id || "";
        el.onmouseenter = () => {
          const a = state.items.find((q) => q.id === c.id);
          if (!a || state.hover === c.id) return;
          state.hover = c.id;
          _ru();
          env.onHover(a, cardY(a) ?? 0);
        };
        el.onmouseleave = () => {
          if (state.hover !== c.id) return;
          state.hover = null;
          _ru();
          env.onHover(null, 0);
        };
      });
    }

    function dropOverlay(key) {
      const d = overlays.get(key);
      if (d) d.remove();
      overlays.delete(key); chipPool.delete(key);
    }

    function render(ctx, w, h, key) {
      ctx.save();
      const clearGlow = () => { ctx.shadowBlur = 0; ctx.shadowColor = "transparent"; };
      ctx.font = `11px ${FONT}`;
      const chips = [];
      let curHot = false, curId = null;   // set per annotation, read by chip()
      const chip = (text, x, y, col) => chips.push({ text, col, hot: curHot, id: curId });
      for (const a of state.items) {
        if (!mine(a, key)) continue;
        const col = COL(a.role);
        // a linked pair (a divergence's two legs) highlights as one object
        const hot = state.hover && (state.hover === a.id
          || (a.link && state.hover === a.link)
          || (a.link && state.items.some((q) => q.id === state.hover && q.link === a.link)));
        curHot = hot; curId = a.id;
        ctx.strokeStyle = col;
        ctx.fillStyle = col;
        ctx.lineWidth = hot ? 2 : 1.5;
        // Hover reads as a HALO rather than a half-pixel of extra weight —
        // at 1.5px a width bump is invisible on a busy chart, and the whole
        // point of the hover is to answer "which one am I pointing at?".
        ctx.shadowColor = hot ? rgba(col, 0.55) : "transparent";
        ctx.shadowBlur = hot ? 9 : 0;

        if (a.kind === "level") {
          const y = vToY(a.price, a.pane);
          if (y === null) continue;
          ctx.setLineDash(a.strength === "weak" ? [2, 4] : [7, 4]);
          ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
          ctx.setLineDash([]);
          chip(a.label || fmt(a.price), 8, y, col);
        } else if (a.kind === "zone") {
          const y1 = vToY(a.hi, a.pane), y2 = vToY(a.lo, a.pane);
          if (y1 === null || y2 === null) continue;
          const hgt = Math.max(4, y2 - y1);
          // A band, not two lines with a tint between them: the fill fades
          // toward the middle so the edges stay the assertion and the
          // interior reads as "somewhere in here".
          const g = ctx.createLinearGradient(0, y1, 0, y1 + hgt);
          g.addColorStop(0, rgba(col, hot ? 0.34 : 0.24));
          g.addColorStop(0.5, rgba(col, hot ? 0.14 : 0.08));
          g.addColorStop(1, rgba(col, hot ? 0.34 : 0.24));
          ctx.fillStyle = g;
          ctx.fillRect(0, y1, w, hgt);
          // solid edges — a zone is bounded; a level is a single dashed line.
          // Different shapes should not share a stroke style.
          ctx.lineWidth = hot ? 1.6 : 1.1;
          ctx.beginPath(); ctx.moveTo(0, y1); ctx.lineTo(w, y1);
          ctx.moveTo(0, y1 + hgt); ctx.lineTo(w, y1 + hgt); ctx.stroke();
          ctx.fillStyle = col;
          chip(a.label || `${fmt(a.lo)}–${fmt(a.hi)}`, 8, y1, col);
        } else if (a.kind === "segment") {
          const x1 = tToX(a.p1.t), x2 = tToX(a.p2.t);
          const y1 = vToY(a.p1.v, a.pane), y2 = vToY(a.p2.v, a.pane);
          if ([x1, x2, y1, y2].some((q) => q === null)) continue;
          ctx.setLineDash(a.dashed ? [7, 4] : []);
          ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
          ctx.setLineDash([]);
          // anchors, so you can see the swings it was fitted to
          for (const [px, py] of [[x1, y1], [x2, y2]]) {
            ctx.beginPath(); ctx.arc(px, py, hot ? 3.5 : 2.5, 0, Math.PI * 2); ctx.fill();
          }
          if (a.label) {
            // clamp by the MEASURED label width — a fixed margin let long
            // pattern labels run under the price axis
            const lw = ctx.measureText(a.label).width + 12;
            const mx = Math.min(Math.max((x1 + x2) / 2, 8), w - lw - 4);
            chip(a.label, mx, Math.min(y1, y2) - 4, col);
          }
        } else if (SHAPES[a.kind]) {
          const e = { tToX, vToY: (v) => vToY(v, a.pane), w, h };
          let anchor = null;
          for (const prim of SHAPES[a.kind](a)) {
            const px = Geo.project(prim, e);
            if (!px) continue;
            // a primitive's own colour wins — the fib ladder is colour-coded
            // by ratio, and repainting it all one role colour loses that
            // ?? not ||: an explicit empty dash means SOLID — a pattern
            // outline through real swings is an assertion, not a suggestion
            Geo.paint(ctx, prim, px,
                      { color: prim.color || col, width: hot ? 2 : 1.5,
                        dash: prim.dash ?? [7, 4], fillAlpha: 0.12 }, e);
            if (!anchor) anchor = px;
          }
          // a position paints its own pills and centre chip — the generic
          // label chip would duplicate them
          if (a.label && anchor && a.kind !== "position") {
            const ax = anchor.x ?? (anchor.p && anchor.p[0]) ?? 8;
            const ay = anchor.y ?? (anchor.p && anchor.p[1]) ?? 20;
            chip(a.label, Math.min(Math.max(ax, 8), w - 150), ay, col);
          }
        }
      }
      clearGlow();
      ctx.restore();
      // Chips are DOM, so they are positioned after the canvas pass rather
      // than drawn during it.
      paintChips(key, chips);
    }

    function makePrimitive(key) {
      return {
        attached(p) { rus.set(key, p.requestUpdate); },
        detached() { rus.delete(key); },
        updateAllViews() {},
        paneViews() {
          return [{
            zOrder: () => "top",
            renderer: () => ({
              draw(target) {
                target.useMediaCoordinateSpace(({ context, mediaSize }) =>
                  render(context, mediaSize.width, mediaSize.height, key));
              },
            }),
          }];
        },
      };
    }

    /** Attach to any new pane, drop any that went away. */
    function syncPanes() {
      const live = env.panes();
      for (const [key, rec] of [...attached]) {
        // same KEY is not same PANE: re-perioding an indicator destroys its
        // pane and creates a fresh one under the same name, and a primitive
        // left on the dead pane renders nothing, silently
        const lp = live.find((p) => p.key === key);
        if (lp && lp.pane === rec.pane) continue;
        try { rec.pane.detachPrimitive(rec.prim); } catch { /* pane already gone */ }
        attached.delete(key); rus.delete(key); dropOverlay(key);
      }
      for (const p of live) {
        if (attached.has(p.key)) continue;
        const prim = makePrimitive(p.key);
        p.pane.attachPrimitive(prim);
        attached.set(p.key, { pane: p.pane, prim });
      }
      _ru();
    }
    syncPanes();

    // ── interaction ─────────────────────────────────────
    // Resolved against the pane the pointer is in, never the chart as one
    // surface: an annotation in the RSI pane is only hittable from there.
    /** True when the pointer is over the PLOT, not the price or time axis.
     *  A level, a zone or a position leg spans the full plot width, so a
     *  press on the price scale — the rescale gesture — landed on whichever
     *  line shared that y and dragged it, silently re-writing a plan's stop
     *  and target and stamping it "user-adjusted". The axes belong to the
     *  chart; only the plot belongs to the annotations. */
    function inPlot(e) {
      const r = env.container.getBoundingClientRect();
      const x = e.clientX - r.left, y = e.clientY - r.top;
      let axisW = 0, axisH = 0;
      try { axisW = chart.priceScale("right").width(); } catch { /* pre-layout */ }
      try { axisH = chart.timeScale().height(); } catch { /* pre-layout */ }
      return x >= 0 && x < r.width - axisW && y >= 0 && y < r.height - axisH;
    }

    const pointIn = (e) => {
      const key = env.paneAt ? env.paneAt(e.clientY) : "price";
      const r = env.container.getBoundingClientRect();
      return { key, x: e.clientX - r.left, y: env.yIn ? env.yIn(e.clientY, key) : e.clientY - r.top };
    };

    /** Where a card pointing at this annotation should sit. Same maths the
     *  click path uses, so hovering and clicking never disagree by a pixel. */
    // eslint-disable-next-line no-inner-declarations
    function cardY(a) {
      if (!a) return null;
      if (a.kind === "segment") {
        const y1 = vToY(a.p1.v, a.pane), y2 = vToY(a.p2.v, a.pane);
        return y1 === null || y2 === null ? null : (y1 + y2) / 2;
      }
      return vToY(a.kind === "zone" ? a.hi : a.price, a.pane);
    }

    env.container.addEventListener("mousemove", (e) => {
      // The legend lives inside the chart container, so pointing at an entry
      // also bubbles a mousemove here — and this handler, finding no shape
      // under the pointer in the top-right corner, immediately cleared the
      // hover the entry had just set. Whoever the pointer is actually on owns
      // the hover.
      if (e.target && e.target.closest && e.target.closest(".scene-chip")) return;
      const p = pointIn(e);
      const a = inPlot(e) ? hitAt(p.y, p.key, p.x) : null;
      const id = a ? a.id : null;
      if (id !== state.hover) {
        state.hover = id;
        env.onHover(a, cardY(a) ?? p.y);
        _ru();
      }
    });

    // ── manual adjustment ───────────────────────────────
    // Chat drawings are draggable like the user's own shapes: the whole
    // shape translates, and a position's entry/stop/target lines move one
    // at a time. The id never changes; `adjusted: true` marks the geometry
    // as the USER'S revision — the backend reads these values from the
    // chart context, so a moved plan re-prices as it now stands.
    let drag = null;
    let swallowClick = false;   // a drag-release is not a select
    const setScroll = (on) => chart.applyOptions({ handleScroll: on, handleScale: on });
    const priceAt = (y, key) => {
      const p = paneFor(key);
      return p ? p.series.coordinateToPrice(y) : null;
    };
    const r2 = (v) => Math.round(v * 100) / 100;

    function positionHandle(a, my, key) {
      const near = (v) => {
        const y = vToY(v, key);
        return y !== null && Math.abs(my - y) < HIT + 2;
      };
      if (near(a.entry)) return { k: "entry" };
      if (near(a.stop)) return { k: "stop" };
      for (let i = 0; i < (a.targets || []).length; i++) {
        if (near(a.targets[i])) return { k: "target", i };
      }
      return null;
    }

    function applyDelta(a, o, dv, dt, h) {
      const mv = (p, q) => { p.v = r2(q.v + dv); p.t = q.t + dt; };
      switch (a.kind) {
        case "level": a.price = r2(o.price + dv); break;
        case "zone": a.lo = r2(o.lo + dv); a.hi = r2(o.hi + dv); break;
        case "segment": case "fib": mv(a.p1, o.p1); mv(a.p2, o.p2); break;
        case "box": mv(a.a, o.a); mv(a.b, o.b); break;
        case "vline": a.t = o.t + dt; break;
        case "point": mv(a.a, o.a); break;
        case "poly": (a.pts || []).forEach((p, i) => mv(p, o.pts[i])); break;
        case "position":
          if (h && h.k === "entry") a.entry = r2(o.entry + dv);
          else if (h && h.k === "stop") a.stop = r2(o.stop + dv);
          else if (h && h.k === "target") {
            a.targets = o.targets.map((t, i) => (i === h.i ? r2(t + dv) : t));
          } else {
            a.entry = r2(o.entry + dv); a.stop = r2(o.stop + dv);
            a.targets = o.targets.map((t) => r2(t + dv));
            a.t0 = o.t0 + dt; a.t1 = o.t1 + dt;
          }
          break;
      }
    }

    // sizing that no longer matches the moved geometry is recomputed where
    // the arithmetic is unambiguous (rr, qty from the kept risk budget) and
    // DROPPED where it is not (pnl depends on the server-side split)
    function refreshDerived(a) {
      if (a.kind !== "position") return;
      const risk = Math.abs(a.entry - a.stop);
      a.rr = risk && a.targets.length
        ? r2(Math.abs(a.targets[0] - a.entry) / risk) : null;
      if (a.risk_amount && risk) a.qty = Math.floor(a.risk_amount / risk);
      a.pnl = null;
      a.label = `${a.side} · R:R ${a.rr ?? "—"}` + (a.qty ? ` · qty ${a.qty}` : "");
    }

    env.container.addEventListener("mousedown", (e) => {
      if (e.button !== 0 || !env.isCursorMode()) return;
      if (env.userBusy && env.userBusy()) return;  // a user drawing took this press
      if (!inPlot(e)) return;      // an axis drag is a rescale, not a move
      const p = pointIn(e);
      const a = hitAt(p.y, p.key, p.x);
      if (!a || a.kind === "markers") return;
      const l0 = chart.timeScale().coordinateToLogical(p.x);
      drag = { a, key: p.key, l0, v0: priceAt(p.y, p.key), moved: false,
               orig: JSON.parse(JSON.stringify(a)),
               handle: a.kind === "position" ? positionHandle(a, p.y, p.key) : null };
      setScroll(false); e.preventDefault();
    });
    env.container.addEventListener("mousemove", (e) => {
      if (!drag) return;
      const p = pointIn(e);
      const v1 = priceAt(env.yIn(e.clientY, drag.key), drag.key);
      const l1 = chart.timeScale().coordinateToLogical(p.x);
      if (v1 === null || drag.v0 === null) return;
      const sec = env.getIntervalSec ? env.getIntervalSec() : 60;
      const dt = (l1 !== null && drag.l0 !== null)
        ? Math.round((l1 - drag.l0) * sec) : 0;
      applyDelta(drag.a, drag.orig, v1 - drag.v0, dt, drag.handle);
      drag.moved = true;
      _ru();
    });
    window.addEventListener("mouseup", () => {
      if (!drag) return;
      if (drag.moved && JSON.stringify(drag.a) !== JSON.stringify(drag.orig)) {
        drag.a.adjusted = true;
        refreshDerived(drag.a);
        swallowClick = true;
        env.onChange(count());   // persists the moved geometry
      }
      drag = null; setScroll(true);
    });

    env.container.addEventListener("click", (e) => {
      if (!env.isCursorMode()) return;
      if (swallowClick) { swallowClick = false; return; }
      if (!inPlot(e)) return;      // nor does an axis click select a shape
      const p = pointIn(e);
      const hit = hitAt(p.y, p.key, p.x);
      if (hit) {
        e.stopPropagation();
        env.onSelect(hit, cardY(hit) ?? p.y);
      }
    });

    const DRAWN = new Set(["level", "zone", "segment", "box", "vline", "point", "poly", "fib", "markers", "position"]);

    return {
      state,
      hitAt: (y, key, x) => hitAt(y, key, x),
      /** Drive the hover highlight from outside the canvas — the chat pane
       *  hovers a mention, the annotation lights up. Presentation only: it
       *  moves no meaning, it just points at what is already drawn. */
      setHover(id) {
        if (state.hover === id) return;
        state.hover = id || null;
        _ru();
      },
      cardY: (id) => cardY(state.items.find((a) => a.id === id)),
      syncPanes,
      remove(id) {
        // removing one leg of a linked pair removes the pair
        const a = state.items.find((x) => x.id === id);
        const link = a && a.link;
        state.items = state.items.filter(
          (x) => x.id !== id && !(link && x.link === link));
        _ru(); env.onChange(count());
      },
      /** Apply a scene patch. Cumulative; same id replaces in place. */
      apply(patch) {
        let drew = 0;
        // An oscillator leg can't be seen unless its pane is open, so drawing
        // one opens it. The chart follows the answer, not the other way round.
        // …but a CLEAR op references a pane to remove things FROM it —
        // auto-opening that pane resurrected the very indicator whose
        // orphaned marks were being purged
        const need = new Set((patch || [])
          .filter((a) => a.pane && a.pane !== "price"
                  && a.kind !== "clear" && a.kind !== "clear_levels")
          .map((a) => a.pane));
        let opened = false;
        for (const key of need) {
          if (env.panes().some((p) => p.key === key)) continue;
          env.onIndicator({ name: key });
          opened = true;
        }
        if (opened) syncPanes();
        for (const a of patch || []) {
          if (a.kind === "clear" || a.kind === "clear_levels") {
            const scope = a.kind === "clear_levels" ? "level" : (a.scope || "all");
            // A clear may only remove what ITS OWN tool drew. A kind-only
            // match once let get_levels(replace) silently erase a marked
            // head and shoulders drawn by get_patterns.
            // Untagged items (older scenes restored from storage) still match
            // so a clear never becomes a no-op.
            const owned = (x) => !a.owner || !x.owner || x.owner === a.owner;
            state.items = state.items.filter((x) => {
              if (!owned(x)) return true;
              return scope === "all" ? false
                : scope === "level" ? !(x.kind === "level" || x.kind === "zone")
                  : scope === "markers" ? x.kind !== "markers"
                    : scope === "id_prefix"
                      ? !(String(x.id || "").startsWith(a.prefix || " ")
                          && (!a.pane || x.pane === a.pane))
                      : scope === "pane" ? x.pane !== a.pane
                        : x.kind !== scope;
            });
            drew++; continue;
          }
          if (a.kind === "indicator") { env.onIndicator(a); drew++; continue; }
          if (a.kind === "indicator_remove") {
            if (env.onIndicatorRemove) env.onIndicatorRemove(a);
            drew++; continue;
          }
          if (!DRAWN.has(a.kind)) continue;
          const i = state.items.findIndex((x) => x.id === a.id);
          if (i >= 0) state.items[i] = a; else state.items.push(a);
          drew++;
        }
        if (drew) { syncMarkers(); _ru(); env.onChange(count()); }
        return drew;
      },
      clear() { state.items = []; syncMarkers(); _ru(); env.onChange(0); },
      count,
      requestUpdate: () => _ru(),
    };

    /** Linked legs are one annotation to the user, so count them as one. */
    function count() {
      const seen = new Set();
      for (const a of state.items) seen.add(a.link || a.id);
      return seen.size;
    }
  }

  return { create };
})();
