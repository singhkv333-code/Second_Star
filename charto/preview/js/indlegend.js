/* Charto — the in-chart indicator legend.
 *
 * TradingView writes its indicators ON the chart, one row per study under the
 * symbol/OHLC block, and hangs the controls off the row itself: the eye, the
 * gear, the ×, the ⋯. Charto used to keep that list in the header as a strip
 * of chips, which meant the two halves of one idea — "SMA 20 is on this
 * chart" and "here is the SMA 20 line" — sat at opposite ends of the window,
 * and the chip could not say what the indicator was WORTH at the bar under
 * the pointer. This module is the move: same controls, same vocabulary, on
 * the chart, reading the crosshair.
 *
 * One instance per chart. `mgr.legendRows(time)` is the whole model; nothing
 * here knows how an indicator is computed, only how a row looks.
 *
 * Two surfaces, because a study lives in one of two places:
 *   · overlays (SMA, VWAP, Bollinger) → rows in `host`, which is the readout
 *     block, directly under the OHLC line;
 *   · panes (RSI, MACD) → a floating box pinned to that pane's own top-left,
 *     positioned off the pane rows LWC lays out as a <table>.
 *
 * The collapse toggle is TradingView's "⌄ 2": one control, under the OHLC,
 * that folds every indicator row on this chart — pane boxes included — into
 * a count. It is the first thing a reader reaches for when the legend starts
 * covering the candles it is describing.
 */
"use strict";

const IndLegend = (() => {
  const esc = (s) => String(s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  /* One menu for the whole page, the way the instrument picker does it:
   * appended to <body> so a pane's own overflow cannot clip it, and closed
   * by the next click anywhere. */
  let moreEl = null;
  function closeMore() {
    if (moreEl) { moreEl.remove(); moreEl = null; }
  }
  document.addEventListener("click", closeMore);

  function create(o) {
    // o = { chart, chartEl, mgr, stage, host, storeKey, onChange, openSettings }
    const { chart, chartEl, mgr, stage, host } = o;
    const notify = () => { if (o.onChange) o.onChange(); };

    let collapsed = o.storeKey ? !!Store.get(o.storeKey, false) : false;
    let at = null;                       // crosshair time, null = latest bar
    const boxes = new Map();             // pane index -> floating box element
    const valEls = new Map();            // indicator id -> its .ind-vals span

    // ── markup ──────────────────────────────────────────
    /** The readings, led by the same middle dot the title row uses between
     *  the ticker, the interval and the venue — so "SMA 20 · 1,269.47" is one
     *  sentence rather than a label and a number that happen to be adjacent.
     *  Nothing at all when there is nothing to quote, which is not the same
     *  as a dash: the row still says the indicator is there. */
    function valsHTML(r) {
      if (!r.values.length) return "";
      return `<span class="sep">·</span>` + r.values.map((v) =>
        `<b style="color:${esc(v.color)}">${esc(v.text)}</b>`).join(" ");
    }

    /** A row is a label, a reading and four controls. The label wears the
     *  plot's own colour — that is what maps the words to the line, now that
     *  there is no swatch and no chip to do it. */
    function rowHTML(r) {
      const cls = r.hidden ? " muted" : (r.off ? " off" : "");
      return `<div class="ind-row${cls}" data-ind-row="${esc(r.id)}">` +
        `<span class="ind-name" style="color:${esc(r.color)}">${esc(r.label)}</span>` +
        `<span class="ind-vals">${valsHTML(r)}</span>` +
        `<span class="ind-acts">` +
        `<span class="ind-act${r.hidden ? " pinned" : ""}" data-eye="${esc(r.id)}" ` +
          `title="${r.hidden ? "Show" : "Hide"}">` +
          `${Icons.svg(r.hidden ? "eyeOff" : "eye")}</span>` +
        `<span class="ind-act" data-cfg="${esc(r.id)}" title="Settings">` +
          `${Icons.svg("settings")}</span>` +
        `<span class="ind-act" data-rm="${esc(r.id)}" title="Remove">` +
          `${Icons.svg("x")}</span>` +
        `<span class="ind-act" data-more="${esc(r.id)}" title="More">` +
          `${Icons.svg("more")}</span>` +
        `</span></div>`;
    }

    /** TradingView's "⌄ 2". Collapsed it carries the COUNT, because a folded
     *  legend that does not say how much it is hiding reads as an empty
     *  chart; expanded it is a bare chevron, since the rows underneath are
     *  already the answer. */
    function toggleHTML(n) {
      if (!n) return "";
      return `<button class="ind-toggle" data-toggle type="button" ` +
        `title="${collapsed ? `Show ${n} indicator${n > 1 ? "s" : ""}` : "Hide indicators"}">` +
        Icons.svg(collapsed ? "chevronDown" : "chevronUp", "xs") +
        (collapsed ? `<span>${n}</span>` : "") + "</button>";
    }

    // ── render ──────────────────────────────────────────
    /** Full rebuild. Structural only — never on a crosshair move, because
     *  replacing the node under the pointer drops its :hover and the eye you
     *  were aiming at flickers out from under the cursor. */
    function render() {
      const rows = mgr.legendRows(at);
      const overlay = rows.filter((r) => r.kind !== "pane");
      const byPane = new Map();
      for (const r of rows) {
        if (r.kind !== "pane") continue;
        if (!byPane.has(r.pane)) byPane.set(r.pane, []);
        byPane.get(r.pane).push(r);
      }

      host.innerHTML = (collapsed ? "" : overlay.map(rowHTML).join(""))
        + toggleHTML(rows.length);
      host.classList.toggle("empty", !rows.length);

      // panes the legend no longer has anything to say about
      for (const [i, box] of [...boxes]) {
        if (collapsed || !byPane.has(i)) { box.remove(); boxes.delete(i); }
      }
      if (!collapsed) {
        for (const [i, list] of byPane) {
          let box = boxes.get(i);
          if (!box) {
            box = document.createElement("div");
            box.className = "ind-legend pane-legend";
            box.addEventListener("click", onClick);
            stage.appendChild(box);
            boxes.set(i, box);
          }
          box.innerHTML = list.map(rowHTML).join("");
        }
      }

      valEls.clear();
      for (const el of [host, ...boxes.values()]) {
        el.querySelectorAll("[data-ind-row]").forEach((row) => {
          valEls.set(row.dataset.indRow, row.querySelector(".ind-vals"));
        });
      }
      position();
      // Adding an oscillator creates a pane and re-lays the table, and the
      // sink fires before LWC has done it — so measure again next frame or
      // the new box lands on the pane boundary it was born at.
      if (boxes.size) requestAnimationFrame(position);
    }

    /** The crosshair path: rewrite the readings and nothing else. `.ind-vals`
     *  holds no controls, so replacing it cannot steal a click or a hover. */
    function paintValues() {
      if (!valEls.size) return;
      for (const r of mgr.legendRows(at)) {
        const el = valEls.get(r.id);
        if (!el) continue;
        const next = valsHTML(r);
        if (el.innerHTML !== next) el.innerHTML = next;   // no needless reflow
      }
    }

    // ── pane boxes ──────────────────────────────────────
    /* LWC lays its panes out as table rows — one <tr> of three <td> per pane,
     * a 1px separator <tr> between each, and the time axis as the last row of
     * three. Measuring those rows is exact and survives a separator drag; the
     * alternative, summing pane.getHeight() and guessing the seam, is a
     * number that has to be re-derived every time the library changes. */
    function paneRows() {
      const tbl = chartEl && chartEl.querySelector("table");
      if (!tbl) return [];
      return [...tbl.querySelectorAll("tr")].filter((r) => r.children.length >= 3);
    }

    function position() {
      if (!boxes.size) return;
      const rows = paneRows();
      const base = stage.getBoundingClientRect();
      for (const [i, box] of boxes) {
        const tr = rows[i];
        // A pane that has not been laid out yet has no row to pin to. Hide
        // rather than park the box at 0,0 on top of the price legend.
        if (!tr || !tr.offsetHeight) { box.style.visibility = "hidden"; continue; }
        const r = tr.getBoundingClientRect();
        box.style.visibility = "";
        box.style.top = `${r.top - base.top + 4}px`;
        box.style.left = `${r.left - base.left + 10}px`;
        box.style.maxWidth = `${Math.max(80, r.width - 84)}px`;
      }
    }

    // ── the ⋯ menu ──────────────────────────────────────
    /** What the row's three icons cannot say. Reset and the two default
     *  actions are otherwise reachable only from inside the settings dialog,
     *  which is a strange place to keep "put this back how it was". */
    function openMore(id, anchor) {
      closeMore();
      if (window.__chartoCloseMenus) window.__chartoCloseMenus(null);
      const hidden = mgr.isHidden(id);
      const pop = document.createElement("div");
      pop.className = "dropdown floating open ind-more";
      pop.innerHTML =
        `<div class="item" data-do="cfg"><span class="lead">` +
          `${Icons.svg("settings", "sm")}Settings…</span></div>` +
        `<div class="item" data-do="eye"><span class="lead">` +
          `${Icons.svg(hidden ? "eye" : "eyeOff", "sm")}` +
          `${hidden ? "Show on chart" : "Hide on chart"}</span></div>` +
        `<div class="sep"></div>` +
        `<div class="item" data-do="reset"><span class="lead">` +
          `${Icons.svg("eraser", "sm")}Reset settings</span></div>` +
        (mgr.hasDefault(id)
          ? `<div class="item" data-do="cleardef"><span class="lead">` +
            `${Icons.svg("pin", "sm")}Remove default</span></div>`
          : `<div class="item" data-do="savedef"><span class="lead">` +
            `${Icons.svg("pin", "sm")}Save as default</span></div>`) +
        `<div class="sep"></div>` +
        `<div class="item danger" data-do="rm"><span class="lead">` +
          `${Icons.svg("trash", "sm")}Remove</span></div>`;
      document.body.appendChild(pop);
      moreEl = pop;

      const r = anchor.getBoundingClientRect();
      const W = 200;
      pop.style.width = `${W}px`;
      pop.style.left = `${Math.max(8, Math.min(r.left, innerWidth - W - 8))}px`;
      // flipped up when the bottom of the window has no room for it
      if (r.bottom + 240 > innerHeight && r.top > 240) {
        pop.style.bottom = `${innerHeight - r.top + 6}px`;
      } else {
        pop.style.top = `${r.bottom + 6}px`;
      }

      pop.addEventListener("click", (e) => {
        e.stopPropagation();
        const it = e.target.closest("[data-do]");
        if (!it) return;
        closeMore();
        runMore(it.dataset.do, id);
      });
    }

    /** Undo a length change before restoring the settings object: a period
     *  edit re-mints the catalogue id, so a reset that only replaced the
     *  style would leave "SMA 200" sitting on factory colours and call it
     *  done. Same order the settings dialog's own Reset uses. */
    async function resetOne(id) {
      const def = mgr.CATALOG.find((c) => c.id === id);
      const back = def && (def.inputs || []).find((f) => f.key === "period");
      let cur = id;
      if (back && def.period !== back.default) {
        try { cur = await mgr.setPeriod(id, back.default); } catch { /* keep id */ }
      }
      await mgr.replaceSettings(cur, mgr.factory(cur));
    }

    function runMore(what, id) {
      if (what === "cfg") { if (o.openSettings) o.openSettings(id); return; }
      if (what === "eye") { mgr.setHidden(id, !mgr.isHidden(id)); notify(); return; }
      if (what === "savedef") { mgr.saveAsDefault(id); render(); return; }
      if (what === "cleardef") { mgr.clearDefault(id); render(); return; }
      if (what === "rm") { mgr.remove(id); notify(); return; }
      if (what === "reset") {
        resetOne(id).then(notify).catch((e) => {
          if (o.status) o.status(`could not reset: ${e.message}`);
        });
      }
    }

    // ── clicks ──────────────────────────────────────────
    function onClick(e) {
      if (e.target.closest("[data-toggle]")) {
        e.stopPropagation();
        collapsed = !collapsed;
        if (o.storeKey) Store.set(o.storeKey, collapsed);
        render();
        return;
      }
      const more = e.target.closest("[data-more]");
      if (more) { e.stopPropagation(); openMore(more.dataset.more, more); return; }

      const eye = e.target.closest("[data-eye]");
      if (eye) {
        e.stopPropagation();
        mgr.setHidden(eye.dataset.eye, !mgr.isHidden(eye.dataset.eye));
        notify();
        return;
      }
      const cfg = e.target.closest("[data-cfg]");
      if (cfg) {
        e.stopPropagation();
        if (o.openSettings) o.openSettings(cfg.dataset.cfg);
        return;
      }
      const rm = e.target.closest("[data-rm]");
      if (rm) {
        e.stopPropagation();
        mgr.remove(rm.dataset.rm);
        notify();
        return;
      }
      // anywhere else on the row is the gear — the way double-clicking a
      // TradingView legend row opens its settings
      const row = e.target.closest("[data-ind-row]");
      if (row && o.openSettings) {
        e.stopPropagation();
        o.openSettings(row.dataset.indRow);
      }
    }
    host.addEventListener("click", onClick);

    // ── wiring ──────────────────────────────────────────
    mgr.setLegendSink(render);
    chart.subscribeCrosshairMove((p) => {
      const next = p && p.time != null ? p.time : null;
      if (next !== at) { at = next; paintValues(); }
      // A separator drag moves the panes without resizing the chart, and the
      // pointer is over the chart the whole time it happens — so the same
      // stream that feeds the readings is also the cheapest honest place to
      // keep the pane boxes pinned.
      position();
    });
    if (window.ResizeObserver) new ResizeObserver(position).observe(stage);

    return {
      render,
      /** Layout changes and pane adds land after LWC has re-laid the table;
       *  one frame later is when the rows can actually be measured. */
      reposition: () => requestAnimationFrame(position),
      destroy() {
        mgr.setLegendSink(null);
        for (const [, box] of boxes) box.remove();
        boxes.clear();
        host.removeEventListener("click", onClick);
      },
    };
  }

  return { create };
})();
