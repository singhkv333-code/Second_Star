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

    const fmt = (n) => Number(n).toLocaleString("en-IN", { maximumFractionDigits: 2 });
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
      position: (a) => {
        // a point comes FIRST because the generic a.label chip anchors on the
        // first primitive's projection, and only point/box expose an x,y —
        // this pins the chip to the entry line where traders expect it
        const e = { t: a.t0, v: a.entry }, x1 = a.t1;
        const out = [Geo.point(e),
                     Geo.segment(e, { t: x1, v: a.entry },
                                 { dash: [4, 4], width: 1 })];
        (a.targets || []).forEach((tp, i) => {
          out.push(Geo.box(e, { t: x1, v: tp }, { fill: "#22d3ee" }));
          out.push(Geo.label({ t: x1, v: tp }, `T${i + 1} ${tp.toFixed(2)}`));
        });
        out.push(Geo.box(e, { t: x1, v: a.stop }, { fill: "#f5a524" }));
        out.push(Geo.label({ t: x1, v: a.stop }, `stop ${a.stop.toFixed(2)}`));
        return out;
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
    function placeChips(ctx, chips) {
      const H = 15;
      const placed = [];
      for (const c of chips.slice().sort((a, b) => a.y - b.y)) {
        c.w = ctx.measureText(c.text).width + 12;
        let y = c.y;
        let guard = 0;
        while (guard++ < 40 && placed.some((p) =>
          c.x < p.x + p.w && p.x < c.x + c.w && Math.abs(y - p.y) < H + 2)) {
          y += H + 2;
        }
        c.y = y;
        placed.push(c);
        ctx.fillStyle = Theme.c("chipBg");
        ctx.fillRect(c.x, y - H, c.w, H);
        ctx.fillStyle = c.col;
        ctx.fillText(c.text, c.x + 6, y - 4);
      }
    }

    function render(ctx, w, h, key) {
      ctx.save();
      ctx.font = `11px ${FONT}`;
      const chips = [];
      const chip = (text, x, y, col) => chips.push({ text, x, y, col });
      for (const a of state.items) {
        if (!mine(a, key)) continue;
        const col = COL(a.role);
        // a linked pair (a divergence's two legs) highlights as one object
        const hot = state.hover && (state.hover === a.id
          || (a.link && state.hover === a.link)
          || (a.link && state.items.some((q) => q.id === state.hover && q.link === a.link)));
        ctx.strokeStyle = col;
        ctx.fillStyle = col;
        ctx.lineWidth = hot ? 2 : 1.5;

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
          if (a.label && anchor) {
            const ax = anchor.x ?? (anchor.p && anchor.p[0]) ?? 8;
            const ay = anchor.y ?? (anchor.p && anchor.p[1]) ?? 20;
            chip(a.label, Math.min(Math.max(ax, 8), w - 150), ay, col);
          }
        }
      }
      placeChips(ctx, chips);
      ctx.restore();
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
        attached.delete(key); rus.delete(key);
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
    const pointIn = (e) => {
      const key = env.paneAt ? env.paneAt(e.clientY) : "price";
      const r = env.container.getBoundingClientRect();
      return { key, x: e.clientX - r.left, y: env.yIn ? env.yIn(e.clientY, key) : e.clientY - r.top };
    };

    env.container.addEventListener("mousemove", (e) => {
      const p = pointIn(e);
      const a = hitAt(p.y, p.key, p.x);
      const id = a ? a.id : null;
      if (id !== state.hover) {
        state.hover = id;
        env.onHover(a);
        _ru();
      }
    });

    env.container.addEventListener("click", (e) => {
      if (!env.isCursorMode()) return;
      const p = pointIn(e);
      const hit = hitAt(p.y, p.key, p.x);
      if (hit) {
        e.stopPropagation();
        const y = hit.kind === "segment"
          ? (vToY(hit.p1.v, hit.pane) + vToY(hit.p2.v, hit.pane)) / 2
          : vToY(hit.kind === "zone" ? hit.hi : hit.price, hit.pane);
        env.onSelect(hit, y);
      }
    });

    const DRAWN = new Set(["level", "zone", "segment", "box", "vline", "point", "poly", "fib", "markers", "position"]);

    return {
      state,
      hitAt: (y, key, x) => hitAt(y, key, x),
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
