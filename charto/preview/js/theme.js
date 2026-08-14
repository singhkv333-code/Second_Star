/* Charto preview — theme module.
 *
 * One palette per mode, one source of truth. The CSS side reads shadcn-style
 * tokens off :root[data-theme]; the canvas side (lightweight-charts, the
 * drawing primitive, the scene layer, the pane legends) can't read CSS vars
 * from inside a canvas paint, so it reads the same values from here.
 *
 * Anything that paints on canvas must call Theme.c(key) at PAINT time, not
 * capture it once — otherwise it goes stale the moment the user toggles.
 */
"use strict";

const Theme = (() => {
  const KEY = "charto_theme";

  const PALETTES = {
    dark: {
      chartBg: "#0d0e12",
      grid: "rgba(255,255,255,.075)",
      axisText: "#b2b5be",        // TradingView's scale text — the axes are a
                                  // reading surface, not chrome; dimming them
                                  // costs legibility for no calm
      crosshairLabel: "#434651",  // LWC picks contrasting label text itself
      border: "#22252d",
      separator: "#3a3f4a",
      crosshair: "#9598a1",
      up: "#089981",
      down: "#f23645",
      accent: "#2962ff",
      legend: "#9aa0b0",
      chipBg: "rgba(13,14,18,.88)",
      volUp: "rgba(8,153,129,.42)",
      volDown: "rgba(242,54,69,.42)",
      handleFill: "#ffffff",
      measureText: "#0d0e12",
      // Annotations never borrow the candle colours. Red and green mean
      // "this bar closed down / up" everywhere else on the chart; a red
      // resistance line reads as a price move, not as structure.
      annRes: "#f5a524", annSup: "#22d3ee", annNeutral: "#c084fc",
      // indicator series
      s1: "#f2c14e", s2: "#4ea8f2", s3: "#c678dd", s4: "#e06c75",
      s5: "#56b6c2", s6: "#d99552",
      bandStrong: "rgba(78,168,242,.9)", bandSoft: "rgba(78,168,242,.4)",
      histUp: "rgba(8,153,129,.55)", histDown: "rgba(242,54,69,.55)",
      // the dashed 70/30 kind of line on an oscillator pane: a reference the
      // eye should find when it looks for it and ignore when it does not, so
      // it sits below the grid in weight rather than competing with the study
      guide: "rgba(255,255,255,.22)",
    },
    light: {
      chartBg: "#ffffff",
      grid: "rgba(0,0,0,.085)",
      axisText: "#26292e",
      crosshairLabel: "#3a3f4a",
      border: "#e3e6ea",
      separator: "#c8ced6",
      crosshair: "#787b86",
      up: "#089981",
      down: "#f23645",
      accent: "#2962ff",
      legend: "#4b5158",
      chipBg: "rgba(255,255,255,.9)",
      volUp: "rgba(8,153,129,.38)",
      volDown: "rgba(242,54,69,.38)",
      handleFill: "#ffffff",
      measureText: "#ffffff",
      annRes: "#b45309", annSup: "#0e7490", annNeutral: "#7c3aed",
      // darker/denser variants — the dark-mode pastels wash out on white
      s1: "#b8860b", s2: "#1a73e8", s3: "#8b3fbe", s4: "#d1495b",
      s5: "#0f8b95", s6: "#c0651b",
      bandStrong: "rgba(26,115,232,.85)", bandSoft: "rgba(26,115,232,.35)",
      histUp: "rgba(8,153,129,.5)", histDown: "rgba(242,54,69,.5)",
      guide: "rgba(0,0,0,.2)",
    },
  };

  let mode = "dark";
  const subs = [];

  function apply(next, persist = true) {
    mode = next === "light" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", mode);
    if (persist) { try { localStorage.setItem(KEY, mode); } catch {} }
    for (const fn of subs) { try { fn(mode, PALETTES[mode]); } catch (e) { console.error(e); } }
  }

  function init() {
    let saved = null;
    try { saved = localStorage.getItem(KEY); } catch {}
    if (!saved) {
      saved = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches
        ? "light" : "dark";
    }
    apply(saved, false);
  }

  return {
    init,
    get mode() { return mode; },
    get palette() { return PALETTES[mode]; },
    /** Read one colour at paint time. */
    c: (k) => PALETTES[mode][k],
    toggle() { apply(mode === "dark" ? "light" : "dark"); return mode; },
    set: apply,
    /** cb(mode, palette) — fired on every change, never on init. */
    onChange(cb) { subs.push(cb); },
  };
})();
