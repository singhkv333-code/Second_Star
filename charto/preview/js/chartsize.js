/* Keep chart canvases in step with their hosts during workspace restoration.
 * Secondary charts are created detached, and docking moves the primary host.
 * Measure the whole chart table: a study pane can legitimately be tiny. */
"use strict";
const ChartSize = (() => {
  /* The library pins its wrapper to FIXED PIXELS whenever it is resized with
   * autoSize off (lightweight-charts' resize: `if (!autoSizeActive)
   * wrapper.style.width = w + "px"`), and switching autoSize back on never
   * releases it — from then on autoSize resizes only the table INSIDE the
   * wrapper. sync() below goes through autoSize off to force a stale chart to
   * its host's size, so a sync that ran while a tile was briefly narrow (a
   * desk mid-restore) left the wrapper at that width for the session: the
   * table kept growing and was clipped to it — candles stopping a third of
   * the way across, the price axis gone, every size check passing because
   * none of them measured the wrapper. The wrapper is handed back to its
   * container after every forced resize, and one found pinned is released
   * at the start of every sync, which also repairs a session already stuck. */
  function unpin(chart) {
    const el = typeof chart.chartElement === "function" ? chart.chartElement() : null;
    if (!el || !el.style || (el.style.width === "100%" && el.style.height === "100%")) return false;
    el.style.width = "100%";
    el.style.height = "100%";
    return true;
  }
  function sync(chart, host) {
    unpin(chart);
    const w = host.clientWidth, h = host.clientHeight;
    if (!host.isConnected || w < 40 || h < 80) return;
    const table = host.querySelector("table");
    if (!table) return;
    const rect = table.getBoundingClientRect();
    // A full-sized table can still contain a collapsed canvas after a hidden
    // boot. Compare each canvas to its cell, not to the whole chart: study
    // panes and the time axis are deliberately shorter than the price pane.
    const ratio = typeof window !== "undefined" ? (window.devicePixelRatio || 1) : 1;
    const collapsed = [...table.querySelectorAll("td")].some((cell) => {
      if (cell.clientWidth < 2 || cell.clientHeight < 2) return false;
      const canvases = cell.querySelectorAll ? [...cell.querySelectorAll("canvas")] : [cell.querySelector("canvas")].filter(Boolean);
      return canvases.some(canvas => {
        const size = canvas.getBoundingClientRect();
        return Math.abs(size.width - cell.clientWidth) > 2 || Math.abs(size.height - cell.clientHeight) > 2 ||
          (typeof canvas.width === "number" && Math.abs(canvas.width - Math.round(size.width * ratio)) > 2) ||
          (typeof canvas.height === "number" && Math.abs(canvas.height - Math.round(size.height * ratio)) > 2);
      });
    });
    const cells = [...table.querySelectorAll("td")].filter(cell => cell.querySelector("canvas"));
    const plotCell = typeof table.querySelector === "function" ? table.querySelector("tr td:nth-child(2)") : null;
    const plotWidth = plotCell?.querySelector("canvas") ? plotCell.clientWidth : Math.max(0, ...cells.map(cell => cell.clientWidth));
    const scale = typeof chart.timeScale === "function" ? chart.timeScale() : null;
    const staleModel = plotWidth > 0 && typeof scale?.width === "function" && Math.abs(scale.width() - plotWidth) > 2;
    if (!collapsed && !staleModel && Math.abs(rect.width - w) <= 2 && Math.abs(rect.height - h) <= 2) return;
    chart.applyOptions({ autoSize: false, width: w, height: h });
    // A cached outer size can be correct while pane/canvas sizes are stale.
    // Make the library recalculate its layout, then repaint at the exact size.
    if (typeof chart.resize === "function") {
      chart.resize(w - 1, h, false);
      chart.resize(w, h, true);
    }
    chart.applyOptions({ autoSize: true });
    unpin(chart);
  }
  function watch(chart, host) {
    let frame = 0, followup = 0, stopped = false;
    const check = () => {
      frame = 0;
      if (stopped) return;
      sync(chart, host);
      cancelAnimationFrame(followup);
      followup = requestAnimationFrame(() => { followup = 0; if (!stopped) sync(chart, host); });
    };
    const schedule = () => {
      if (!stopped && !frame) frame = requestAnimationFrame(() => { frame = requestAnimationFrame(check); });
    };
    const observer = new ResizeObserver(schedule);
    observer.observe(host);
    const intersection = typeof IntersectionObserver === "function" ? new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) schedule();
    }) : null;
    intersection?.observe(host);
    const events = ["charto:dock-layout", "charto:viewport", "visibilitychange", "transitionend"];
    events.forEach(event => document.addEventListener(event, schedule));
    const viewport = typeof window !== "undefined" ? window : null;
    ["resize", "pageshow"].forEach(event => viewport?.addEventListener(event, schedule));
    schedule();
    return () => {
      stopped = true;
      observer.disconnect(); intersection?.disconnect();
      cancelAnimationFrame(frame); cancelAnimationFrame(followup);
      events.forEach(event => document.removeEventListener(event, schedule));
      ["resize", "pageshow"].forEach(event => viewport?.removeEventListener(event, schedule));
    };
  }
  return { sync, watch };
})();
