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
  // How much of the pane the volume histogram may claim. Wide enough to read
  // the shape, narrow enough that the candles stay the subject.
  const vpBand = (w) => Math.max(40, Math.min(150, w * 0.22));

  function create(chart, candle, env) {
    // env: { panes, getBars, toChartTime, container, inPricePane, priceY,
    //        onChange, onHover, onSelect, onIndicator, isCursorMode,
    //        foldState, onFold }
    // `hidden` folds every annotation away — canvas marks, the corner legend
    // and the bar markers alike — without removing one of them. What the chat
    // drew is still in state, still restored on reload, still in the context
    // envelope; it is simply not on screen. See Drawings' own flag: one
    // control in the readout drives both, because "the chart is too busy" is
    // never a question about which layer drew what.
    const state = { items: [], hover: null, hidden: false };
    // Event icons (results, and anything else that happens ON a bar) use the
    // library's own marker layer rather than our canvas: markers belong to
    // the series, so they track the bar through zoom, pan and interval
    // changes without any projection of ours.
    let markerApi = null;
    function syncMarkers() {
      const evs = [];
      for (const a of (state.hidden ? [] : state.items)) {
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

    /* ── marking a bar ────────────────────────────────────────────────────
     * A `candle` annotation is ONE DOT, sitting above the bar's high and
     * centred on it. It says "this one" without covering the bar it points
     * at — with a candlestick pattern the body against the wick IS the
     * evidence, so anything drawn over it hides the reason it qualified.
     *
     * The gap is PIXELS, not price: a fixed price offset drifts with the
     * scale and stops looking like the same distance the moment the axis is
     * log or the range changes. */
    const MARK_GAP = 6;        // px from the bar's high to its dot
    const MARK_DOT = 2.3;      // dot radius — a pointer, not a bullet

    /** The loaded bar at a detector time, or null when this interval has no
     *  bar there — a daily pattern viewed on 5m has no 5m bar at that stamp.
     *  Only ever a FALLBACK: an annotation that carries its own hi/lo keeps
     *  its geometry across interval changes, which is why the drawer sends
     *  them. */
    function barAt(t) {
      const bars = env.getBars();
      if (!bars.length) return null;
      const ct = env.toChartTime ? env.toChartTime(t) : t;
      let lo = 0, hi = bars.length - 1;
      while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        if (bars[mid].time <= ct) lo = mid; else hi = mid;
      }
      for (const k of [lo, hi]) if (bars[k] && bars[k].time === ct) return bars[k];
      return null;
    }

    /** A plan projects into blank chart to the RIGHT of the last bar, and the
     *  chart leaves five bars of it. Widen the margin so the window the trade
     *  would live in is actually on screen.
     *
     *  Outward only, and bounded: this moves the user's view, so it may open
     *  room that was not there and must never take room away or push the
     *  candles into a corner. */
    function fitProjection() {
      const bars = env.getBars();
      if (bars.length < 2) return;
      const last = bars[bars.length - 1].time;
      const span = Math.max(1, last - bars[bars.length - 2].time);
      let need = 0;
      for (const a of state.items) {
        if (a.kind !== "position" || a.t1 == null) continue;
        const ct = env.toChartTime ? env.toChartTime(a.t1) : a.t1;
        need = Math.max(need, Math.ceil((ct - last) / span));
      }
      if (need <= 0) return;
      const ts = chart.timeScale();
      const want = Math.min(need + 2, 40);
      if (want > (ts.options().rightOffset || 0)) {
        ts.applyOptions({ rightOffset: want });
      }
    }

    /** Where a candle mark's dot goes. One function, so the renderer and the
     *  hit-tester can never disagree about where the mark is.
     *
     *  A multi-bar pattern (an engulfing is two bars, a morning star three)
     *  centres its dot over the SPAN and hangs it off the highest of those
     *  bars — one mark for one pattern, rather than a dot per bar, which
     *  would read as several findings. */
    function markDot(a) {
      const c1 = tToX(a.t1), c2 = tToX(a.t2 == null ? a.t1 : a.t2);
      if (c1 === null || c2 === null) return null;
      const bar = a.hi == null ? barAt(a.t1) : null;
      const hiV = a.hi == null ? (bar && bar.high) : a.hi;
      if (hiV == null) return null;
      const yH = vToY(hiV, a.pane);
      if (yH === null) return null;
      return { cx: (c1 + c2) / 2, cy: yH - MARK_GAP - MARK_DOT };
    }

    const mine = (a, key) =>
      String(a.pane || "price").split("@")[0] === (key || "price");

    /** A segment's anchor pixels, and the two points it is actually STROKED
     *  between — different the moment it extends.
     *
     *  `extend:"right"` has been in the data since draw_shape learned to
     *  draw a ray, and this renderer ignored it: every ray stopped dead at
     *  its second anchor, so the one property that makes it a ray was
     *  invisible. Clipping is parametric, so a vertical needs no special
     *  case. The LABEL still hangs off the anchors — a ray's midpoint after
     *  clipping is somewhere off in blank chart. */
    function segPx(a, w, h) {
      const x1 = tToX(a.p1.t), x2 = tToX(a.p2.t);
      const y1 = vToY(a.p1.v, a.pane), y2 = vToY(a.p2.v, a.pane);
      if ([x1, x2, y1, y2].some((q) => q === null)) return null;
      const draw = (a.extend && a.extend !== "none")
        ? (Geo.clipToRect(x1, y1, x2, y2, w, h,
                          a.extend === "right" ? 0 : -1e9, 1e9)
           || [[x1, y1], [x2, y2]])
        : [[x1, y1], [x2, y2]];
      return { a: [x1, y1], b: [x2, y2], draw };
    }

    /* Annotation kinds that are plain geometry — composed by draw_shape from
     * resolved anchors rather than produced by a detector. They render and
     * hit-test through the same algebra as the user's own drawings, so the
     * two layers can never disagree about what a box is.
     *
     * Each entry returns an ARRAY of primitives: a fib is seven lines, and a
     * shape that needed more than one used to be undrawable from chat. */
    // Filled objects opt out of the hover halo — see the note at its use.
    const NO_HALO = new Set(["position", "trade", "exposure"]);
    const SHAPES = {
      box: (a) => [Geo.box(a.a, a.b, { fill: true })],
      vline: (a) => [Geo.vline(a.t)],
      point: (a) => [Geo.point(a.a)],
      // A stretch of TIME, full height. The primitive existed for the user's
      // own date-range tool and the scene simply never exposed it, so the
      // chat could mark a moment but not a span — and a span is what a
      // session, an event window or a month actually is. A box faked it only
      // by inventing price bounds it had no business asserting.
      vband: (a) => [Geo.vband(a.t1, a.t2, { fillAlpha: 0.16, stroke: false })],
      // Text pinned to a point, with no shape under it. Every other
      // annotation's label describes the geometry it belongs to; this one IS
      // the annotation — "results" on the day, and nothing implied about
      // price there.
      label: (a) => [Geo.label(a.a, a.text || a.label || "")],
      // fill/solid/stroke let a detector compose TradingView-style pattern
      // geometry: a solid outline through the defining swings plus a
      // stroke-less fill polygon, without double-drawing any edge
      poly: (a) => [Geo.poly(a.pts, {
        closed: !!a.closed, fill: !!a.fill,
        ...(a.stroke === false ? { stroke: false } : {}),
        ...(a.solid ? { dash: [] } : {}),
      })],
      /* The strategy layer. A backtest arrives as a LIST of trades, not as
       * one shape, so each trade is its own annotation with its own id —
       * that way the pointer can answer for a single trade out of forty,
       * and clearing the strategy is one id_prefix scope rather than a
       * kind sweep that would take the user's own boxes with it. */
      trade: (a) => [Geo.trade(a.entry, a.exit,
                               { win: !!a.win, open: !!a.open, text: a.text })],
      exposure: (a) => [Geo.exposure(a.spans || [])],
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
        // The centre chip takes the colour of the half the market is in —
        // the same reading, off the same last close, as the user's own
        // long/short tool. See the `tone` note in js/geometry.js.
        const bars = env.getBars();
        const last = bars.length ? bars[bars.length - 1].close : null;
        return [Geo.position(
          { t: a.t0, v: a.entry },
          { v: a.stop, text: `Stop: ${a.stop.toFixed(2)} (${pct(a.stop)}%) ${dst(a.stop)}`
                             + (a.risk_amount ? `, Amount: ${inr(a.risk_amount)}` : "") },
          (a.targets || []).map((tp, i) => ({
            v: tp, text: `Target: ${tp.toFixed(2)} (${pct(tp)}%) ${dst(tp)}${amt(i)}` })),
          { t1: a.t1, center, tone: Geo.positionTone(a.entry, last, a.side) })];
      },
      fib: (a) => {
        const out = [Geo.segment(a.p1, a.p2, { dash: [3, 3], width: 1 })];
        Geo.ladder(a.p1.v, a.p2.v, Tools.FIB).forEach((lv, i) => {
          out.push(Geo.segment({ t: a.p1.t, v: lv.v }, { t: a.p2.t, v: lv.v },
                               { color: Tools.FIB_COLORS[i] }));
        });
        return out;
      },
      /* Every catalogued tool, by name, from resolved anchors.
       *
       * The alternative was a `SHAPES` entry per tool — a second Gann square,
       * a second fib wedge, written here in the renderer and kept in step
       * with the rail's by hand. Fifteen ratio tools is fifteen chances for
       * the chat's 61.8% to sit somewhere the user's 61.8% does not, and the
       * bug would be invisible until someone drew both. So this delegates to
       * the SAME builder the rail runs, with the same build context: there is
       * one construction of a Gann fan in this app, and both layers call it.
       *
       * `tool` is not validated against a list of blessed names — an unknown
       * one simply builds nothing, which is what an unknown one should do.
       * The catalogue is the capability; there is no second gate. */
      drawing: (a) => {
        const spec = Tools.SPECS[a.tool];
        if (!spec || !Array.isArray(a.pts) || !a.pts.length) return [];
        // padded the same way the rail pads a half-placed tool (js/drawings.js
        // primsOf), so a chat drawing that arrives one anchor short renders
        // what it has instead of silently rendering nothing
        let pts = a.pts;
        if (spec.anchors !== "free" && pts.length < spec.anchors) {
          pts = pts.concat(Array(spec.anchors - pts.length)
            .fill(pts[pts.length - 1]));
        }
        try { return spec.build(pts, buildCtx, a) || []; } catch { return []; }
      },
    };
    // the rail's context, built from THIS layer's readers — see
    // Tools.makeCtx for why there is only one of these in the app
    const buildCtx = Tools.makeCtx({
      getBars: env.getBars,
      getIntervalSec: () => (env.getIntervalSec ? env.getIntervalSec() : 60),
      toBarTime: (t) => (env.toChartTime ? env.toChartTime(t) : t),
      fromBarTime: (t) => (env.fromChartTime ? env.fromChartTime(t) : t),
      tToX, vToY,
    });
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
      // Folded away is not there. Hover, click and the chart's own pin guard
      // all resolve through here, so this one line is what stops a hidden
      // level raising a provenance card over an empty chart.
      if (state.hidden) return null;
      for (let i = state.items.length - 1; i >= 0; i--) {
        const a = state.items[i];
        if (!mine(a, key)) continue;
        if (a.kind === "vprofile") {
          // the histogram answers for itself; its POC/VAH/VAL lines answer
          // anywhere along their length
          const w = plotW();
          if (x != null && w && x > w - vpBand(w)) {
            const y1 = vToY(a.rows[a.rows.length - 1].hi, a.pane);
            const y2 = vToY(a.rows[0].lo, a.pane);
            if (y1 !== null && y2 !== null && y > y1 - HIT && y < y2 + HIT) return a;
          }
          for (const v of [a.poc, a.vah, a.val]) {
            const ly = vToY(v, a.pane);
            if (ly !== null && Math.abs(y - ly) < HIT) return a;
          }
        } else if (a.kind === "level") {
          const ly = vToY(a.price, a.pane);
          if (ly !== null && Math.abs(y - ly) < HIT) return a;
        } else if (a.kind === "zone") {
          const y1 = vToY(a.hi, a.pane), y2 = vToY(a.lo, a.pane);
          if (y1 !== null && y2 !== null && y > y1 - HIT && y < y2 + HIT) return a;
        } else if (a.kind === "segment" && x != null) {
          // the DRAWN line answers, not the anchor pair — a ray you can see
          // but cannot point at is a ray that reads as dead
          const s = segPx(a, plotW(), env.container.clientHeight);
          if (s && distSeg(x, y, s.draw[0][0], s.draw[0][1],
                           s.draw[1][0], s.draw[1][1]) < HIT) return a;
        } else if (a.kind === "candle" && x != null) {
          // the dot answers, the bar under it does not — otherwise a mark
          // would swallow every click on the candle it is pointing at
          const m = markDot(a);
          if (m && Math.hypot(x - m.cx, y - m.cy) < HIT) return a;
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
      paintFold(key, host, chips.length, axis);
      // render() is the chart's own repaint, so this is every pan, zoom,
      // rescale and tick — exactly when what is behind a chip changes.
      schedulePlates();
    }

    /* ── the fold control ────────────────────────────────
     *
     * TradingView's "⌄ 2", for what is DRAWN. The indicator legend has had
     * this since it was written — one control that turns a stack of rows into
     * a count — and the drawings, which cover far more of the chart than a
     * legend does, had only the trash: the two things a reader wants when the
     * candles vanish under their own annotations were "delete everything" and
     * nothing.
     *
     * It stands at the foot of the chip list, because that list is where the
     * annotations say what they are, and a control folds the thing it sits
     * under. Its COUNT comes from main.js and includes the user's own shapes:
     * one number, one fold, both layers — a reader who wants to see the bars
     * does not care that the trendline is theirs and the neckline is the
     * chat's, and asking them to find two toggles would be asking them to
     * hold a distinction the question does not contain.
     */
    const folds = new Map();          // paneKey -> button element

    function paintFold(key, host, nChips, axis) {
      let b = folds.get(key);
      // Price pane only. The oscillator panes carry their own marks, but a
      // second fold in each of them would be three controls for one decision.
      const s = key === "price" && env.foldState ? env.foldState() : null;
      if (!s || !s.n) {
        if (b) { b.remove(); folds.delete(key); }
        return;
      }
      if (!b || !b.isConnected) {
        b = document.createElement("button");
        b.type = "button";
        b.className = "ind-toggle scene-fold";
        /* The press must not reach the chart, or mousedown starts LWC's pan
         * gesture under a button you meant to click and the bars scroll while
         * the pointer never moves.
         * stopPropagation ONLY — never preventDefault. Cancelling a pointerdown
         * suppresses the compatibility mouse events Chrome derives `click`
         * from, so the button would take the press and then never report it:
         * the exact silence this control was already failing with. */
        b.addEventListener("pointerdown", (e) => { e.stopPropagation(); });
        b.addEventListener("click", (e) => { e.stopPropagation(); env.onFold(); });
        host.appendChild(b);
        folds.set(key, b);
      }
      /* REBUILD ONLY WHEN THE STATE CHANGED — load-bearing, not a micro-tune.
       *
       * render() runs on every repaint, and moving the pointer onto this
       * button IS a crosshair move. An unconditional rewrite therefore
       * replaced the <svg> between the mousedown and the mouseup of every
       * single press. The browser derives a click from the nearest common
       * ancestor of those two targets, and with the mousedown's target
       * detached there is none — so NO CLICK EVENT FIRES AT ALL. The button
       * took the press, wore a focus ring, and did nothing. That was the bug.
       *
       * Guarded on a STATE KEY, never on `innerHTML` itself. Comparing the
       * serialized DOM looks equivalent and is not: the browser re-serializes
       * `<path .../>` as `<path ...></path>`, so a string built from
       * Icons.svg() never equals what it just wrote and the guard silently
       * does nothing. (The indicator legend can compare innerHTML because its
       * content is <span>/<b> text, which round-trips unchanged.) Two values
       * decide everything drawn here, so those two ARE the key. */
      const key2 = `${s.collapsed ? 1 : 0}:${s.n}`;
      if (b.dataset.fold !== key2) {
        b.dataset.fold = key2;
        b.title = s.collapsed
          ? `Show ${s.n} drawing${s.n > 1 ? "s" : ""}`
          // "Hide", never "clear" — the word has to carry the promise the
          // behaviour makes, because the only other control that acts on
          // every drawing at once is the trash.
          : `Hide ${s.n} drawing${s.n > 1 ? "s" : ""} — nothing is deleted`;
        b.innerHTML = Icons.svg(s.collapsed ? "chevronDown" : "chevronUp", "xs")
          // Folded, the control carries the COUNT — the indicator toggle's own
          // rule: a chart that has quietly stopped showing eight objects reads
          // as a chart that never had them.
          + (s.collapsed ? `<span>${s.n}</span>` : "");
      }
      // The chips run right-aligned from `axis + 8`; the control closes the
      // column on that same edge, one 17px step below the last of them. Minus
      // its own 5px of padding, so it is the GLYPH that lines up with the chip
      // text and the hover plate that hangs outside — exactly what the
      // indicator toggle does at the other end, mirrored. `right` rather than
      // `left`, so a two-digit count grows inward instead of walking the
      // control out over the price scale.
      b.style.right = `${axis + 3}px`;
      b.style.top = `${8 + nChips * 17 + (nChips ? 2 : 0)}px`;
    }

    /* ── the plate ───────────────────────────────────────
     *
     * The indicator legend's rectangle, at the other end of the chart. Same
     * reasoning, same module (js/occlusion.js): a chip is a reading laid over
     * the plot, and over candles it is unreadable without a wash of paper
     * behind it — while over empty sky the wash is a box drawn around nothing.
     * So the trigger is whether the series is actually there, per chip, and
     * the fold control at the foot of the list answers to it too.
     *
     * These chips carry no hover-revealed controls, so unlike the indicator
     * rows there is no second state to keep clear of: the plate is the whole
     * treatment. */
    const occl = typeof Occlusion === "undefined" ? null : Occlusion.create(chart);

    function paintPlates() {
      if (!occl) return;
      for (const [key, host] of overlays) {
        if (!host.isConnected) continue;
        const p = paneFor(key);
        let idx = 0;
        try { idx = p.pane.paneIndex(); } catch { continue; }
        const els = [...host.children].filter((e) => e.style.display !== "none");
        if (!els.length) continue;
        const hit = occl.probe(idx, els.map((e) => e.getBoundingClientRect()));
        els.forEach((e, k) => e.classList.toggle("over-chart", !!hit[k]));
      }
    }

    /* Coalesced to a frame, and never run INSIDE the canvas pass that asks
     * for it: the probe reads layout, and reading layout from a draw callback
     * stalls the frame the library is in the middle of painting. */
    let plateReq = 0;
    function schedulePlates() {
      if (plateReq || !occl) return;
      plateReq = requestAnimationFrame(() => { plateReq = 0; paintPlates(); });
    }
    if (occl) occl.onData(schedulePlates);

    function dropOverlay(key) {
      const d = overlays.get(key);
      if (d) d.remove();
      overlays.delete(key); chipPool.delete(key);
    }

    function render(ctx, w, h, key) {
      // The chips are DOM and outlive a skipped canvas pass, so folding away
      // has to paint an EMPTY legend rather than simply not painting one —
      // otherwise the names of the annotations survive in the corner while
      // the annotations themselves are gone.
      if (state.hidden) { paintChips(key, []); return; }
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
        /* Hover reads as a HALO rather than a half-pixel of extra weight —
         * at 1.5px a width bump is invisible on a busy chart, and the whole
         * point of the hover is to answer "which one am I pointing at?".
         *
         * That reasoning is about HAIRLINES, and it does not survive contact
         * with a filled shape. A canvas shadow applies to fillRect as much as
         * to stroke, so a trade plan — two big filled zones — was casting a
         * 9px wash of the role colour around and into its own boxes: a blue
         * rim around the whole shape and a dirty edge over the green and the
         * red. The plan does not need a halo to be findable; it answers the
         * pointer by putting up its target, stop and R:R plates, which is a
         * far louder answer than a glow. So it opts out, and hover on a plan
         * changes nothing but the cards. */
        /* …and the same is true of every filled object added since:
         * a trade body or an exposure rail wearing a 9px wash of
         * violet is exactly the glow this layer is supposed not to
         * have. They answer the pointer in grey, in their own
         * painter, which is both quieter and more precise. */
        const halo = hot && !NO_HALO.has(a.kind);
        ctx.shadowColor = halo ? rgba(col, 0.55) : "transparent";
        ctx.shadowBlur = halo ? 9 : 0;

        if (a.kind === "vprofile") {
          // Volume at price: a histogram hanging off the right edge, so it
          // reads against the axis it shares rather than over the candles.
          // The value area is the assertion, the rest is context — so the
          // fill carries the emphasis and only POC/VAH/VAL get a line.
          const band = vpBand(w);
          const rs = a.rows || [];
          if (!rs.length) continue;
          clearGlow();
          for (const r of rs) {
            const y1 = vToY(r.hi, a.pane), y2 = vToY(r.lo, a.pane);
            if (y1 === null || y2 === null) continue;
            const bh = Math.max(1, y2 - y1 - 1);
            const bw = Math.max(1, band * (r.share || 0));
            const inVA = r.lo >= a.val - 1e-9 && r.hi <= a.vah + 1e-9;
            const isPoc = a.poc >= r.lo && a.poc < r.hi;
            ctx.fillStyle = rgba(col, isPoc ? (hot ? 0.92 : 0.8)
              : inVA ? (hot ? 0.5 : 0.4) : (hot ? 0.24 : 0.17));
            ctx.fillRect(w - bw, y1 + 0.5, bw, bh);
          }
          // the three numbers a profile actually asserts
          ctx.lineWidth = hot ? 2 : 1.5;
          for (const [v, dash, tag] of [[a.poc, [], "POC"],
                                        [a.vah, [5, 4], "VAH"],
                                        [a.val, [5, 4], "VAL"]]) {
            const y = vToY(v, a.pane);
            if (y === null) continue;
            ctx.globalAlpha = tag === "POC" ? 1 : 0.62;
            ctx.setLineDash(dash);
            ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w - band, y); ctx.stroke();
            ctx.setLineDash([]);
            ctx.globalAlpha = 1;
            chip(`${tag} ${fmt(v)}`, 8, y, col);
          }
        } else if (a.kind === "level") {
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
        } else if (a.kind === "candle") {
          const m = markDot(a);
          if (!m) continue;
          ctx.setLineDash([]);
          ctx.beginPath();
          ctx.arc(m.cx, m.cy, hot ? MARK_DOT + 1.2 : MARK_DOT, 0, Math.PI * 2);
          ctx.fill();
          if (a.label) {
            const lw = ctx.measureText(a.label).width + 12;
            chip(a.label, Math.min(Math.max(m.cx - lw / 2, 8), w - lw - 4),
                 m.cy - MARK_DOT - 3, col);
          }
        } else if (a.kind === "segment") {
          const s = segPx(a, w, h);
          if (!s) continue;
          const [x1, y1] = s.a, [x2, y2] = s.b;
          ctx.setLineDash(a.dashed ? [7, 4] : []);
          ctx.beginPath();
          ctx.moveTo(s.draw[0][0], s.draw[0][1]);
          ctx.lineTo(s.draw[1][0], s.draw[1][1]);
          ctx.stroke();
          ctx.setLineDash([]);
          // No anchor dots. A segment already ENDS at the swing it was fitted
          // to — where the stroke stops is the claim — so a filled circle
          // there restates it and nothing more. On a pattern like a wedge,
          // whose two legs are two segments, it put four dots on the chart
          // that read as handles you could grab and cannot.
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
            // `detail` is the position tool's second reading — its target,
            // stop and R:R plates. Same rule as the user's own long/short
            // tool (js/drawings.js): the plan's SHAPE is always on, its
            // NUMBERS come up when the pointer is on it, so a plan the chat
            // drew does not sit on the candles it was drawn against. Here it
            // is hover and never selection: clicking a scene annotation opens
            // nothing (main.js's onSelect is empty), so pointing at one IS how
            // this layer is addressed.
            Geo.paint(ctx, prim, px,
                      { color: prim.color || col, width: hot ? 2 : 1.5,
                        dash: prim.dash ?? [7, 4], fillAlpha: 0.12,
                        detail: hot }, e);
            if (!anchor) anchor = px;
          }
          // a position paints its own pills and centre chip — the generic
          // label chip would duplicate them
          /* The strategy layer gets ONE line in the legend, carried by the
           * rail, and none from its trades.
           *
           * Forty-nine marks would be forty-nine chips — a corner that has
           * become a list of everything rather than a list of what is drawn.
           * But without any line at all a reader is left with blue chevrons
           * and no way to learn what they are; "what do these even mean" is
           * a question the chart has to be able to answer about its own ink.
           * So the layer names itself once, on the object that already spans
           * all of it. */
          if (a.kind === "exposure" && a.label) {
            chip(a.label, 8, 20, Theme.c("accent"));
          } else if (a.label && anchor && !NO_HALO.has(a.kind)) {
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
        try { rec.host.detachPrimitive(rec.prim); } catch { /* pane already gone */ }
        attached.delete(key); rus.delete(key); dropOverlay(key);
      }
      for (const p of live) {
        if (attached.has(p.key)) continue;
        const prim = makePrimitive(p.key);
        // Attach to the SERIES, not the pane. Both accept a primitive and
        // both honour zOrder "top", but they paint on DIFFERENT canvases: a
        // pane primitive lands on the same canvas as the candles (z-index 1)
        // and competes with them there, while a series primitive lands on the
        // overlay canvas above it (z-index 2). Measured, both ways, by
        // filling an opaque rect from each and reading the pixels back.
        //
        // That is why a plan overlay was disappearing behind the very bars it
        // was drawn across: half of it was under the wicks.
        const host = p.series || p.pane;
        host.attachPrimitive(prim);
        attached.set(p.key, { pane: p.pane, host, prim });
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
    /** Plot width in pane-local pixels — what render() is handed as `w`.
     *  hitAt needs the same number to know where the volume histogram is. */
    function plotW() {
      const r = env.container.getBoundingClientRect();
      let axisW = 0;
      try { axisW = chart.priceScale("right").width(); } catch { /* pre-layout */ }
      return r.width - axisW;
    }

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
      if (a.kind === "vprofile") return vToY(a.poc, a.pane);
      if (a.kind === "label" || a.kind === "point") return vToY(a.a.v, a.pane);
      if (a.kind === "trade") return vToY(a.entry.v, a.pane);
      // the rail is pinned in pixels and owns no price, so it has no
      // price-space anchor to raise a card against
      if (a.kind === "exposure") return null;
      if (a.kind === "candle") {
        const m = markDot(a);
        return m ? m.cy : null;
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
        case "vband": a.t1 = o.t1 + dt; a.t2 = o.t2 + dt; break;
        case "point": case "label": mv(a.a, o.a); break;
        case "poly": (a.pts || []).forEach((p, i) => mv(p, o.pts[i])); break;
        // a catalogued tool moves as its ANCHORS move — the construction is
        // rebuilt from them on the next frame, so nothing derived can be left
        // behind pointing at where the shape used to be
        case "drawing": (a.pts || []).forEach((p, i) => mv(p, o.pts[i])); break;
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
      // A computed profile has no geometry the user owns — dragging it would
      // mean nothing and would stamp it "adjusted", so it is hoverable but
      // not movable, like markers. A candle mark is the same: its geometry
      // IS a particular bar, so dragging it off that bar destroys its only
      // claim.
      if (!a || a.kind === "markers" || a.kind === "vprofile"
          || a.kind === "candle") return;
      const l0 = chart.timeScale().coordinateToLogical(p.x);
      /* A linked set moves as ONE RIGID BODY, never a part of one.
       *
       * A chart pattern is not a drawing, it is a measurement drawn: an
       * outline through its swings, a stroke-less polygon filling down to the
       * neckline, and the neckline itself, emitted as three annotations that
       * share a `link`. Dragging picked up whichever one the pointer happened
       * to land on, so the shading slid off its own outline and what was left
       * described a formation that never occurred — and it was then stamped
       * `adjusted: true` and read back as the user's own geometry.
       *
       * Deletion and the hover highlight already treated a link as one
       * object; only the drag did not. Now the whole group is snapshotted and
       * the same delta lands on every member, so the parts cannot come apart
       * by any gesture. The handle stays with the annotation actually
       * grabbed — it only exists for `position`, which is never linked. */
      const group = a.link
        ? state.items.filter((x) => x.link === a.link)
        : [a];
      drag = { a, group, key: p.key, l0, v0: priceAt(p.y, p.key), moved: false,
               orig: group.map((x) => JSON.parse(JSON.stringify(x))),
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
      drag.group.forEach((x, i) => applyDelta(
        x, drag.orig[i], v1 - drag.v0, dt, x === drag.a ? drag.handle : null));
      drag.moved = true;
      _ru();
    });
    window.addEventListener("mouseup", () => {
      if (!drag) return;
      if (drag.moved && JSON.stringify(drag.group) !== JSON.stringify(drag.orig)) {
        drag.group.forEach((x) => { x.adjusted = true; refreshDerived(x); });
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

    const DRAWN = new Set(["level", "zone", "segment", "box", "vline", "vband", "point", "poly", "fib", "drawing", "markers", "position", "vprofile", "candle", "label",
                            "trade", "exposure"]);

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
      /** Fold every annotation away, or bring them back. Presentation only:
       *  the items are untouched, so nothing is saved and nothing is undoable.
       *  The hover goes with them — it points at something no longer drawn. */
      setHidden(v) {
        const next = !!v;
        if (state.hidden === next) return next;
        state.hidden = next;
        if (next) state.hover = null;
        syncMarkers(); _ru();
        return next;
      },
      isHidden: () => state.hidden,
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
        if (drew) { syncMarkers(); fitProjection(); _ru(); env.onChange(count()); }
        return drew;
      },
      clear() { state.items = []; syncMarkers(); _ru(); env.onChange(0); },
      /** Replace every annotation at once — the undo stack's write path.
       *  Exactly the bookkeeping clear() does, with a list instead of
       *  nothing; the hover is dropped because the annotation it pointed at
       *  may not be in the state being restored. */
      setItems(list) {
        state.items = (list || []).slice();
        state.hover = null;
        syncMarkers(); _ru(); env.onChange(count());
      },
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
