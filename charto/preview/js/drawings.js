/* Charto preview — the drawing runtime.
 *
 * Tools are declarations (js/tools.js); geometry is shared (js/geometry.js).
 * This file owns only the things a tool must never re-implement:
 *   · one renderer, one hit-tester, one drag handler, over every tool
 *   · multi-pane routing by stable pane KEY ("price" / "rsi"), so a drawing
 *     survives an indicator reshuffle
 *   · the placement state machine (drag-draw and click-click both work)
 *   · magnet snap, persistence, and the tool-usage ledger
 *   · the consumedDown handshake that stops a drawing gesture also pinning
 *     the candle underneath it
 */
"use strict";

const Drawings = (() => {
  const STORE_KEY = "charto_drawings_v2_" +
    ((new URLSearchParams(location.search).get("symbol") || "RELIANCE").toUpperCase());
  const USAGE_KEY = "charto_tool_usage_v1";
  const HIT = 7;
  const G = Geo;

  function create(chart, candle, env) {
    // env: { getBars, getIntervalSec, container, stage, panes, setStatus,
    //        onToolDone, onChange }
    // Short human-readable ref per drawing ("D3"), monotonic and never
    // recycled — it is what the chat tags and what the tools resolve by.
    let refSeq = 0;
    const state = {
      tool: "cursor",
      magnet: false,
      drawings: load(),
      selId: null,
      draft: null,
      drag: null,
      mouse: null,
      consumedDown: false,
      // Folded away, not deleted. A chart carrying a dozen shapes is
      // unreadable exactly when you want to look at the bars underneath them,
      // and the only thing on offer used to be the trash. This is the eye:
      // the drawings stay in state, in storage and in the chat's context —
      // they are simply not painted, and not hittable while they are not.
      hidden: false,
    };
    const rus = new Map();
    const _ru = () => { for (const f of rus.values()) f(); };
    const attached = new Map();
    const el = env.container;

    // ── persistence + telemetry ─────────────────────────
    function load() {
      try {
        const raw = (JSON.parse(localStorage.getItem(STORE_KEY) || "[]") || [])
          .filter((d) => Tools.SPECS[d.type])
          .map((d) => ({ ...d, pane: d.pane || "price" }));
        // Refs must never be reused: a chat turn that says "D3" has to keep
        // meaning the same shape, so a deleted D3 leaves a hole rather than
        // renumbering the survivors. Backfill anything saved before refs.
        let max = 0;
        for (const d of raw) {
          const n = d.ref && /^D(\d+)$/.exec(d.ref);
          if (n) max = Math.max(max, +n[1]);
        }
        for (const d of raw) if (!d.ref) d.ref = "D" + (++max);
        refSeq = Math.max(refSeq, max);
        return raw;
      } catch { return []; }
    }
    const save = () => {
      try { localStorage.setItem(STORE_KEY, JSON.stringify(state.drawings)); } catch {}
      // Every path that changes a drawing ends here — placement, the drag
      // release, the Delete key, the card's Remove, clear-all — so this is
      // the one line the undo stack has to hear about. It is a no-op while
      // the stack is itself writing (js/history.js).
      Undo.touch();
      // …and the one line anything COUNTING drawings has to hear about. The
      // scene layer has had this since it was written; the drawing layer only
      // ever announced its selection, so a control that wanted to say "3
      // drawings" had no event to say it on.
      if (env.onChange) env.onChange(state.drawings.length);
    };
    /** Announce which drawing is selected. The chat listens and offers to
     *  tag it, so "is this any good?" carries a ref instead of leaving the
     *  model to guess which shape "this" meant. */
    function emitSelect(via) {
      const d = state.drawings.find((q) => q.id === state.selId) || null;
      document.dispatchEvent(new CustomEvent("charto:draw-select", {
        detail: d && {
          id: d.id, ref: d.ref, type: d.type, pane: d.pane, via: via || "click",
          label: Tools.SPECS[d.type] ? Tools.SPECS[d.type].label : d.type,
        },
      }));
    }
    function logUse(tool) {
      let u = {};
      try { u = JSON.parse(localStorage.getItem(USAGE_KEY) || "{}"); } catch {}
      u[tool] = (u[tool] || 0) + 1;
      localStorage.setItem(USAGE_KEY, JSON.stringify(u));
      console.log("[charto:tool-usage]", JSON.stringify(u));
    }

    // ── panes ───────────────────────────────────────────
    const paneFor = (key) => env.panes().find((p) => p.key === (key || "price"));
    function paneAtClient(clientY) {
      const live = env.panes();
      for (const p of live) {
        const e = p.pane.getHTMLElement && p.pane.getHTMLElement();
        if (!e) continue;
        const r = e.getBoundingClientRect();
        if (clientY >= r.top && clientY <= r.bottom) return p;
      }
      return live[0];
    }
    function yInPane(clientY, key) {
      const p = paneFor(key);
      const e = p && p.pane.getHTMLElement && p.pane.getHTMLElement();
      return e ? clientY - e.getBoundingClientRect().top : clientY;
    }
    function paneHeight(key) {
      const p = paneFor(key);
      const e = p && p.pane.getHTMLElement && p.pane.getHTMLElement();
      return e ? e.clientHeight : el.clientHeight;
    }

    // ── coordinates ─────────────────────────────────────
    const ts = () => chart.timeScale();
    /** LWC v5's logicalToCoordinate silently returns 0 for any FRACTIONAL
     *  logical — which is what every one of the calls below passes — so
     *  project the neighbouring integers and interpolate ourselves. Same
     *  fix as Scene.logicalToX; a drawing and a detector mark must not
     *  disagree about where a time lands. */
    function logicalToX(l) {
      const i = Math.floor(l), f = l - i;
      const x0 = ts().logicalToCoordinate(i);
      if (x0 === null) return null;
      if (!f) return x0;
      const x1 = ts().logicalToCoordinate(i + 1);
      return x1 === null ? x0 : x0 + (x1 - x0) * f;
    }
    function tToX(t) {
      const x = ts().timeToCoordinate(t);
      if (x !== null) return x;
      const bars = env.getBars(); if (!bars.length) return null;
      const iv = env.getIntervalSec(), last = bars.length - 1;
      if (t > bars[last].time) return logicalToX(last + (t - bars[last].time) / iv);
      if (t < bars[0].time) return logicalToX((t - bars[0].time) / iv);
      // In range but not ON a bar: sessions are not uniformly spaced, so the
      // midpoint of two anchors usually falls in a weekend or an overnight
      // gap. Interpolate a fractional index — without this, every label
      // placed at a midpoint silently fails to render.
      let lo = 0, hi = last;
      while (hi - lo > 1) {
        const m = (lo + hi) >> 1;
        if (bars[m].time <= t) lo = m; else hi = m;
      }
      const span = bars[hi].time - bars[lo].time || 1;
      return logicalToX(lo + (t - bars[lo].time) / span);
    }
    function xToTime(x) {
      const bars = env.getBars(); if (!bars.length) return null;
      const iv = env.getIntervalSec();
      const logical = ts().coordinateToLogical(x);
      if (logical === null) return null;
      const last = bars.length - 1, li = Math.round(logical);
      if (li >= 0 && li <= last) return bars[li].time + Math.round((logical - li) * iv);
      if (li > last) return bars[last].time + Math.round((logical - last) * iv);
      return bars[0].time + Math.round(logical * iv);
    }
    const vToY = (v, key) => { const p = paneFor(key); return p ? p.series.priceToCoordinate(v) : null; };
    const yToV = (y, key) => { const p = paneFor(key); return p ? p.series.coordinateToPrice(y) : null; };
    const envFor = (key, w, h) => ({ tToX, vToY: (v) => vToY(v, key), w, h });

    // ── tool build context ──────────────────────────────
    const fmt = (n) => Sym.num(n);
    const buildCtx = {
      fmt,
      fmtPct: (p) => `${p >= 0 ? "+" : ""}${p.toFixed(2)}%`,
      barsBetween(t0, t1) {
        return Math.max(1, Math.round(Math.abs(t1 - t0) / env.getIntervalSec()));
      },
      valuesBetween(t0, t1) {
        const bars = env.getBars();
        const lo = Math.min(t0, t1), hi = Math.max(t0, t1);
        return bars.filter((b) => b.time >= lo && b.time <= hi).map((b) => b.close);
      },
    };

    /** A drawing → its primitives. The single place a tool becomes geometry. */
    function primsOf(d) {
      const spec = Tools.SPECS[d.type];
      if (!spec) return [];
      try { return spec.build(d.pts, buildCtx, d) || []; } catch { return []; }
    }

    // ── rendering ───────────────────────────────────────
    function styleOf(d, prim, selected, isDraft) {
      const base = d.color || Theme.c("accent");
      return {
        color: prim.color || base,
        width: prim.width || (selected ? 2 : 1.5),
        dash: isDraft ? [4, 4] : (prim.dash || d.dash || []),
        fillAlpha: prim.fillAlpha,
      };
    }

    function render(ctx, w, h, key) {
      // A draft still paints while hidden — arming a tool un-hides (setTool),
      // so the only way to reach this is a draft that was already open.
      if (state.hidden && !state.draft) return;
      const e = envFor(key, w, h);
      const paint = (d, selected, isDraft) => {
        if ((d.pane || "price") !== key) return;
        for (const prim of primsOf(d)) {
          const px = G.project(prim, e);
          if (px) G.paint(ctx, prim, px, styleOf(d, prim, selected, isDraft), e);
        }
        if (selected) handles(ctx, d, e);
      };
      if (!state.hidden) {
        for (const d of state.drawings) paint(d, d.id === state.selId, false);
      }
      if (state.draft) paint(state.draft, false, true);
    }

    function handles(ctx, d, e) {
      ctx.save();
      ctx.fillStyle = Theme.c("handleFill");
      ctx.strokeStyle = Theme.c("accent");
      ctx.lineWidth = 1.5;
      for (const a of d.pts) {
        const x = tToX(a.t), y = e.vToY(a.v);
        if (x === null || y === null) continue;
        ctx.beginPath(); ctx.arc(x, y, 4.5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      }
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
    function syncPanes() {
      const live = env.panes();
      for (const [key, rec] of [...attached]) {
        // same KEY is not same PANE: re-perioding an indicator destroys its
        // pane and creates a fresh one under the same name, and a primitive
        // left on the dead pane renders nothing, silently
        const lp = live.find((p) => p.key === key);
        if (lp && lp.pane === rec.pane) continue;
        try { rec.pane.detachPrimitive(rec.prim); } catch {}
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

    // ── hit-testing ─────────────────────────────────────
    function hitTest(mx, my, key) {
      // Nothing invisible is grabbable. A hidden shape that still answered
      // the pointer would select, drag and delete from under a chart that
      // shows no reason for any of it to be happening.
      if (state.hidden) return null;
      const e = envFor(key, el.clientWidth, paneHeight(key));
      for (let i = state.drawings.length - 1; i >= 0; i--) {
        const d = state.drawings[i];
        if ((d.pane || "price") !== key) continue;
        // An anchor is always grabbable, even when the shape does not pass
        // through it. A regression channel is a FIT — it deliberately misses
        // the points you clicked — and you would still expect to grab the
        // thing where you put it.
        if (handleAt(d, mx, my, key) >= 0) return d.id;
        for (const prim of primsOf(d)) {
          const px = G.project(prim, e);
          if (px && G.hit(prim, px, mx, my, HIT, e)) return d.id;
        }
      }
      return null;
    }
    function handleAt(d, mx, my, key) {
      const e = envFor(key, el.clientWidth, paneHeight(key));
      for (let i = 0; i < d.pts.length; i++) {
        const x = tToX(d.pts[i].t), y = e.vToY(d.pts[i].v);
        if (x !== null && y !== null && Math.hypot(mx - x, my - y) < 9) return i;
      }
      return -1;
    }

    // ── snapping ────────────────────────────────────────
    function snap(t, v, key) {
      if (!state.magnet || key !== "price") return { t, v };
      const bars = env.getBars(); if (!bars.length) return { t, v };
      let lo = 0, hi = bars.length - 1;
      while (hi - lo > 1) { const m = (lo + hi) >> 1; if (bars[m].time <= t) lo = m; else hi = m; }
      const b = Math.abs(bars[lo].time - t) <= Math.abs(bars[hi].time - t) ? bars[lo] : bars[hi];
      let best = v, bestD = Infinity;
      for (const q of [b.open, b.high, b.low, b.close]) {
        const dd = Math.abs(q - v);
        if (dd < bestD) { bestD = dd; best = q; }
      }
      const y0 = vToY(v, key), y1 = vToY(best, key);
      return (y0 !== null && y1 !== null && Math.abs(y0 - y1) < 12) ? { t: b.time, v: best } : { t, v };
    }

    /** Screen event → an anchor in the units of the pane it landed in. */
    function anchorAt(e2, forceKey) {
      const key = forceKey || (paneAtClient(e2.clientY) || {}).key || "price";
      const r = el.getBoundingClientRect();
      const t = xToTime(e2.clientX - r.left);
      const v = yToV(yInPane(e2.clientY, key), key);
      if (t === null || v === null) return null;
      return { ...snap(t, v, key), key };
    }

    // ── interaction ─────────────────────────────────────
    /** True when the pointer is over the PLOT, not the price or time axis.
     *  A level, a zone or a position leg spans the full plot width, so
     *  without this test a drag on the price scale — the rescale gesture —
     *  hit the nearest horizontal line and moved it instead of rescaling,
     *  quietly re-writing a plan's stop and target. main.js guards its pan
     *  the same way (isPaneMouse); this is the drawing half of it. */
    function inPlot(e2) {
      const r = el.getBoundingClientRect();
      const x = e2.clientX - r.left, y = e2.clientY - r.top;
      let axisW = 0, axisH = 0;
      try { axisW = chart.priceScale("right").width(); } catch { /* pre-layout */ }
      try { axisH = chart.timeScale().height(); } catch { /* pre-layout */ }
      return x >= 0 && x < r.width - axisW && y >= 0 && y < r.height - axisH;
    }

    const setScroll = (on) => chart.applyOptions({ handleScroll: on, handleScale: on });
    const newId = () => "d" + Date.now().toString(36) + Math.floor(Math.random() * 999);

    el.addEventListener("mousedown", (e2) => {
      if (e2.button !== 0) return;
      state.consumedDown = false;
      if (!inPlot(e2)) return;   // the axes belong to the chart, not to shapes
      const a = anchorAt(e2);
      if (!a) return;
      const r = el.getBoundingClientRect();
      const mx = e2.clientX - r.left, my = yInPane(e2.clientY, a.key);

      if (state.tool === "cursor") {
        if (state.selId) {
          const d = state.drawings.find((q) => q.id === state.selId);
          if (d && (d.pane || "price") === a.key) {
            const hi = handleAt(d, mx, my, a.key);
            if (hi >= 0) {
              state.drag = { id: d.id, handle: hi, pane: a.key, start: a,
                             orig: JSON.parse(JSON.stringify(d.pts)) };
              state.consumedDown = true; setScroll(false); e2.preventDefault(); return;
            }
          }
        }
        const hit = hitTest(mx, my, a.key);
        if (hit) {
          const d = state.drawings.find((q) => q.id === hit);
          state.selId = hit;
          emitSelect();
          state.drag = { id: hit, handle: -1, pane: a.key, start: a,
                         orig: JSON.parse(JSON.stringify(d.pts)) };
          state.consumedDown = true; setScroll(false); e2.preventDefault();
        } else {
          state.consumedDown = !!state.selId;   // the click spent itself deselecting
          state.selId = null;
          emitSelect();
        }
        _ru(); return;
      }

      // placing
      const spec = Tools.SPECS[state.tool];
      if (!spec) return;
      state.consumedDown = true;
      e2.preventDefault();
      const pt = { t: a.t, v: a.v };
      if (!state.draft) {
        state.draft = { id: "draft", type: state.tool, pane: a.key, pts: [pt] };
        if (spec.anchors === 1) return commit();
        state.draft.pts.push({ ...pt });          // the moving anchor
      } else if (a.key === state.draft.pane) {
        state.draft.pts[state.draft.pts.length - 1] = pt;
        if (state.draft.pts.length >= spec.anchors) return commit();
        state.draft.pts.push({ ...pt });
      }
      _ru();
    });

    el.addEventListener("mousemove", (e2) => {
      const forced = state.draft ? state.draft.pane : (state.drag ? state.drag.pane : null);
      const a = anchorAt(e2, forced);
      if (!a) return;
      const r = el.getBoundingClientRect();
      state.mouse = [e2.clientX - r.left, yInPane(e2.clientY, a.key)];

      if (env.stage && state.tool === "cursor" && !state.drag && !state.draft) {
        // over an axis the cursor must not promise a grab it won't honour
        env.stage.classList.toggle("overdraw", inPlot(e2) &&
          hitTest(state.mouse[0], state.mouse[1], a.key) !== null);
      }

      if (state.drag) {
        const d = state.drawings.find((q) => q.id === state.drag.id);
        if (d) {
          if (state.drag.handle >= 0) {
            d.pts[state.drag.handle] = { t: a.t, v: a.v };
          } else {
            const dt = a.t - state.drag.start.t, dv = a.v - state.drag.start.v;
            d.pts = state.drag.orig.map((q) => ({ t: q.t + dt, v: q.v + dv }));
          }
          _ru();
        }
        return;
      }
      if (state.draft && a.key === state.draft.pane) {
        const spec = Tools.SPECS[state.draft.type];
        if (spec.anchors === "free") state.draft.pts.push({ t: a.t, v: a.v });
        else state.draft.pts[state.draft.pts.length - 1] = { t: a.t, v: a.v };
        _ru();
      }
    });

    el.addEventListener("mouseup", () => {
      if (state.drag) { state.drag = null; setScroll(true); save(); _ru(); return; }
      if (!state.draft) return;
      const spec = Tools.SPECS[state.draft.type];
      if (spec.anchors === "free") { state.draft.pts.length > 2 ? commit() : cancel(); return; }
      // drag-draw: if the pointer travelled, the gesture already placed the
      // last anchor, so finish. Otherwise stay in click-click mode.
      if (spec.anchors === 2 && state.draft.pts.length === 2) {
        const p0 = state.draft.pts[0], p1 = state.draft.pts[1];
        const moved = Math.hypot((tToX(p1.t) ?? 0) - (tToX(p0.t) ?? 0),
                                 (vToY(p1.v, state.draft.pane) ?? 0)
                                 - (vToY(p0.v, state.draft.pane) ?? 0));
        if (moved > 6) commit();
      }
    });

    /* ── touch, for the phone ────────────────────────────────────────────
     * The three handlers above are mouse-only, which was fine while the
     * drawing rail was a desktop affordance. The phone toolbar can arm a
     * tool now, so a finger has to be able to place it — otherwise the
     * toolbar offers something the chart will not honour, which is worse
     * than not offering it.
     *
     * Bridged, not reimplemented: a touch is replayed as the same mouse
     * event on the same element, so there is one placement state machine
     * and a phone cannot drift from a desktop.
     *
     * CAPTURE phase, and only while a tool is armed or a draft is open.
     * lightweight-charts binds its own touch handlers on the canvas below —
     * taking the gesture before they see it is what stops the chart panning
     * out from under an anchor. In cursor mode nothing is intercepted at
     * all, so a finger pans and pinches exactly as it did.
     */
    (function touchToMouse() {
      let live = false;
      const armed = () => state.tool !== "cursor" || !!state.draft;
      const relay = (type, touch) => {
        if (!touch) return;
        el.dispatchEvent(new MouseEvent(type, {
          clientX: touch.clientX, clientY: touch.clientY,
          bubbles: false, cancelable: true, button: 0,
        }));
      };
      const opts = { capture: true, passive: false };
      el.addEventListener("touchstart", (e2) => {
        live = armed() && e2.touches.length === 1;   // a pinch is never a draw
        if (!live) return;
        e2.preventDefault(); e2.stopPropagation();
        relay("mousedown", e2.touches[0]);
      }, opts);
      el.addEventListener("touchmove", (e2) => {
        if (!live) return;
        e2.preventDefault(); e2.stopPropagation();
        relay("mousemove", e2.touches[0]);
      }, opts);
      const end = (e2) => {
        if (!live) return;
        e2.preventDefault(); e2.stopPropagation();
        // the drag-draw test in mouseup reads the draft's own anchors, which
        // the last move already placed — so this is just the release
        relay("mouseup", e2.changedTouches[0]);
        live = false;
      };
      el.addEventListener("touchend", end, opts);
      el.addEventListener("touchcancel", end, opts);
    })();

    /* ── the text box ─────────────────────────────────────────────────────
     * A text annotation used to be `window.prompt("Text")` — a modal, in the
     * middle of the screen, over the chart you were annotating, in the
     * browser's own type. You could not see where the label was going while
     * you wrote it, which is the one thing that matters about a label.
     *
     * This is TradingView's answer instead: the box opens ON the chart, at
     * the anchor, in the size and position the finished chip will occupy —
     * so what you type is already the drawing. It is placed in the CHIP's
     * own geometry (see G.chip: 11px type, a 15px band, 6px of side padding,
     * offset +4/-9 from the anchor), which is what makes it WYSIWYG rather
     * than merely nearby.
     *
     * Enter or a click away keeps it; Escape or an empty box throws it away.
     * The chart is frozen while it is open — a pan under a fixed-position
     * editor would leave the box pointing at a bar it no longer belongs to.
     */
    let measurer = null;
    function textWidth(str, font) {
      measurer = measurer || document.createElement("canvas").getContext("2d");
      measurer.font = font;
      return measurer.measureText(str).width;
    }

    function openTextBox(d, done) {
      const key = d.pane || "price";
      const p = paneFor(key);
      const paneEl = p && p.pane.getHTMLElement && p.pane.getHTMLElement();
      const x = tToX(d.pts[0].t), y = vToY(d.pts[0].v, key);
      if (x === null || y === null) return done(null);
      const cr = el.getBoundingClientRect();
      const pr = (paneEl || el).getBoundingClientRect();

      const box = document.createElement("input");
      box.className = "text-box";
      box.type = "text";
      box.placeholder = "Add text";
      box.spellcheck = false;
      box.style.left = Math.round(cr.left + x + 4) + "px";
      box.style.top = Math.round(pr.top + y - 9) + "px";
      document.body.appendChild(box);

      const font = getComputedStyle(box).font;
      // 62 is the stylesheet's own min-width: setting a narrower inline
      // width would just be overridden, and the two would disagree about
      // how wide an empty box is
      const fit = () => {
        const w = textWidth(box.value || box.placeholder, font);
        box.style.width = Math.max(62, Math.ceil(w + 16)) + "px";
      };
      fit();
      box.focus();

      setScroll(false);
      let closed = false;
      const close = (txt) => {
        if (closed) return;
        closed = true;
        box.remove();
        setScroll(state.tool === "cursor");
        done(txt);
      };
      box.addEventListener("input", fit);
      box.addEventListener("keydown", (e2) => {
        // never let a keystroke inside the box reach the chart's own
        // shortcuts — Escape there cancels a draft, Delete removes a drawing
        e2.stopPropagation();
        if (e2.key === "Enter") { e2.preventDefault(); close(box.value.trim()); }
        if (e2.key === "Escape") { e2.preventDefault(); close(null); }
      });
      // A click anywhere else is a commit, the way it is in TradingView —
      // but the click that OPENED the box must not immediately close it.
      setTimeout(() => box.addEventListener("blur", () => close(box.value.trim())), 0);
    }

    function commit() {
      const d = state.draft;
      state.draft = null;
      if (!d) return;
      const spec = Tools.SPECS[d.type];
      if (spec.text) {
        _ru();                       // the box is the preview; drop the draft
        openTextBox(d, (txt) => {
          if (!txt) { _ru(); env.onToolDone(); return; }
          d.text = txt;
          place(d);
        });
        return;
      }
      place(d);
    }

    /** Everything a commit does once the drawing is finally known. */
    function place(d) {
      const spec = Tools.SPECS[d.type];
      d.id = newId();
      d.ref = "D" + (++refSeq);
      state.drawings.push(d);
      state.selId = d.id;
      save(); logUse(d.type);
      emitSelect("create");
      env.setStatus(`${spec.label.toLowerCase()} added (${state.drawings.length})`);
      _ru();
      env.onToolDone();
    }
    function cancel() { state.draft = null; _ru(); }

    window.addEventListener("keydown", (e2) => {
      if (/^(INPUT|TEXTAREA)$/.test(e2.target.tagName)) return;
      if ((e2.key === "Delete" || e2.key === "Backspace") && state.selId) {
        state.drawings = state.drawings.filter((d) => d.id !== state.selId);
        state.selId = null; save(); _ru(); emitSelect();
        env.setStatus("drawing deleted");
      }
      if (e2.key === "Escape") { state.draft = null; state.selId = null; _ru(); emitSelect(); env.onToolDone(); }
    });

    /** Hidden drops the SELECTION too. The selection is what the composer
     *  offers to tag ("about D3…"), and a ref pointing at a shape nobody can
     *  see is an offer the chart cannot back up. */
    function setHidden(v) {
      const next = !!v;
      if (state.hidden === next) return next;
      state.hidden = next;
      if (next && state.selId) { state.selId = null; emitSelect(); }
      _ru();
      return next;
    }

    return {
      state,
      SPECS: Tools.SPECS,
      GROUPS: Tools.GROUPS,
      setTool(tool) {
        state.tool = tool;
        state.draft = null;
        // Arming a tool un-folds. Drawing a trendline onto a chart that is
        // hiding its trendlines would put the new one straight into the hole
        // the old ones are in — the gesture would look like it failed.
        if (tool !== "cursor" && state.hidden) setHidden(false);
        setScroll(tool === "cursor");
        el.classList.toggle("drawing", tool !== "cursor");
        _ru();
      },
      toggleMagnet() { state.magnet = !state.magnet; return state.magnet; },
      /** Fold every shape away, or bring them back. Presentation only — it
       *  writes nothing to storage and touches no undo step, because nothing
       *  about the drawings themselves has changed. */
      setHidden,
      isHidden: () => state.hidden,
      /** Delete one drawing by id — the same path the Delete key takes, so
       *  the card's Remove button cannot drift from the keyboard's. */
      remove(id) {
        const before = state.drawings.length;
        state.drawings = state.drawings.filter((d) => d.id !== id);
        if (state.drawings.length === before) return false;
        if (state.selId === id) state.selId = null;
        save(); _ru(); emitSelect();
        env.setStatus("drawing deleted");
        return true;
      },
      clearAll() { state.drawings = []; state.selId = null; state.draft = null; save(); _ru(); emitSelect(); },
      /** Replace the whole set at once — the undo stack's write path.
       *
       *  Selection is DROPPED rather than carried across: the shape it
       *  pointed at may not exist in the state being restored, and a
       *  selection with no drawing under it leaves handles on the chart
       *  that nothing can move. An open draft goes for the same reason.
       *
       *  Refs stay monotonic. A restored D7 must not let the next drawing
       *  mint a second D7 — a chat turn that said "D7" has to keep meaning
       *  one shape, which is the same promise load() makes at boot. */
      setAll(list) {
        state.drawings = (list || []).map((d) => ({ ...d, pane: d.pane || "price" }));
        state.selId = null;
        state.draft = null;
        for (const d of state.drawings) {
          const n = d.ref && /^D(\d+)$/.exec(d.ref);
          if (n) refSeq = Math.max(refSeq, +n[1]);
        }
        save(); _ru(); emitSelect();
      },
      count: () => state.drawings.length,
      syncPanes,
      /** Geometry of one drawing, for the backend to score. */
      geometryOf(id) {
        const d = state.drawings.find((q) => q.id === (id || state.selId));
        return d ? { id: d.id, type: d.type, pane: d.pane, pts: d.pts } : null;
      },
      exportJSON() {
        let usage = {};
        try { usage = JSON.parse(localStorage.getItem(USAGE_KEY) || "{}"); } catch {}
        return JSON.stringify({ symbol: "RELIANCE", drawings: state.drawings,
                                tool_usage: usage }, null, 2);
      },
      requestUpdate: () => _ru(),
    };
  }

  return { create };
})();
