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
    const collapsed = [...table.querySelectorAll("td")].some((cell) => {
      const canvas = cell.querySelector("canvas");
      if (!canvas || cell.clientWidth < 2 || cell.clientHeight < 2) return false;
      const size = canvas.getBoundingClientRect();
      return Math.abs(size.width - cell.clientWidth) > 2 ||
             Math.abs(size.height - cell.clientHeight) > 2;
    });
    if (!collapsed && Math.abs(rect.width - w) <= 2 && Math.abs(rect.height - h) <= 2) return;
    chart.applyOptions({ autoSize: false, width: w, height: h });
    chart.applyOptions({ autoSize: true });
    unpin(chart);
  }
  function watch(chart, host) {
    let frame = 0, stopped = false;
    const check = () => { frame = 0; if (!stopped) sync(chart, host); };
    const schedule = () => {
      if (!stopped && !frame) frame = requestAnimationFrame(check);
    };
    const observer = new ResizeObserver(schedule);
    observer.observe(host);
    // Run after the library's observer and the restored flex/grid layout.
    frame = requestAnimationFrame(() => { frame = requestAnimationFrame(check); });
    document.addEventListener("pivot:dock-layout", schedule);
    return () => {
      stopped = true;
      observer.disconnect();
      cancelAnimationFrame(frame);
      document.removeEventListener("pivot:dock-layout", schedule);
    };
  }
  return { sync, watch };
})();
