/* Charto preview — the indicator settings dialog.
 *
 * TradingView's (and therefore Groww's) three-tab modal, rebuilt on Pivot's
 * design tokens: Inputs / Style / Visibility, a Defaults menu in the footer,
 * Cancel and Ok, draggable by its header.
 *
 * Two rules decide what appears in it.
 *
 * 1. INPUTS ARE THE BACKEND'S. The math lives in data/indicators.py, so the
 *    Inputs tab is rendered from the `inputs` schema that file derives from
 *    its own function signatures. This dialog cannot invent a knob. That is
 *    also why some of TradingView's inputs are absent: "Basis MA Type",
 *    "Oscillator MA Type" and RSI's "Smoothing" would each need a formula we
 *    do not compute, and a control that silently does nothing is worse than
 *    one that isn't there. Same for "Offset" — plotting a line N bars into
 *    the future needs bar times that do not exist yet, and the honest
 *    alternative (silently clipping the last N points) is not an offset.
 *
 * 2. STYLE AND VISIBILITY ARE OURS. Colour, thickness, line style, plot type,
 *    precision, status line, price-scale label and the per-timeframe toggles
 *    are pure presentation, so they are wired end to end.
 *
 * Edits apply LIVE, the way TradingView's do; Cancel restores the snapshot
 * taken when the dialog opened.
 */
"use strict";

const IndSettings = (() => {
  // TradingView's own display names — the words a user has read on every
  // other chart they have used.
  const TITLES = {
    sma: "Moving Average", ema: "Moving Average Exponential",
    wma: "Moving Average Weighted", hma: "Hull Moving Average",
    dema: "Double EMA", bbands: "Bollinger Bands",
    keltner: "Keltner Channels", donchian: "Donchian Channels",
    supertrend: "Supertrend", psar: "Parabolic SAR",
    vwap: "VWAP", anchored_vwap: "Anchored VWAP",
    rsi: "Relative Strength Index", macd: "MACD",
    stoch: "Stochastic", stochrsi: "Stochastic RSI",
    adx: "Average Directional Index", atr: "Average True Range",
    cci: "Commodity Channel Index", williams_r: "Williams %R",
    mfi: "Money Flow Index", obv: "On Balance Volume",
    ad: "Accumulation/Distribution", cmf: "Chaikin Money Flow",
    roc: "Rate of Change", aroon: "Aroon",
  };

  const SOURCE_LABEL = {
    close: "close", open: "open", high: "high", low: "low",
    hl2: "hl2", hlc3: "hlc3", ohlc4: "ohlc4", volume: "volume",
  };

  // TradingView's swatch grid, generated rather than typed: a row of
  // neutrals over ten hues, each in five steps from tint to shade. Ten
  // columns wide, so a column IS a hue and picking "a darker green" is a
  // vertical move — which is the whole point of the layout.
  const HUES = ["#f23645", "#ff9800", "#ffeb3b", "#4caf50", "#089981",
                "#00bcd4", "#2962ff", "#673ab7", "#9c27b0", "#e91e63"];
  const NEUTRALS = ["#ffffff", "#d1d4dc", "#b2b5be", "#9598a1", "#787b86",
                    "#5d606b", "#434651", "#2a2e39", "#1e222d", "#000000"];
  const STEPS = [0.62, 0.34, 0, -0.28, -0.52];   // + toward white, − toward black

  const hex2 = (n) => Math.max(0, Math.min(255, Math.round(n)))
    .toString(16).padStart(2, "0");

  function step(hex, t) {
    const n = parseInt(hex.slice(1), 16);
    const c = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    const to = t >= 0 ? 255 : 0;
    const k = Math.abs(t);
    return "#" + c.map((x) => hex2(x + (to - x) * k)).join("");
  }

  const PALETTE_ROWS = [NEUTRALS, ...STEPS.map((t) => HUES.map((h) => step(h, t)))];

  let wrap = null, dlg = null;
  let ind = null;                // the indicator manager
  let id = null, origId = null;  // the id can change: Length re-mints the def
  let snapshot = null;           // what Cancel restores
  let tab = "inputs";
  let notify = () => {};
  let subtitle = "";
  let swatchPop = null;
  let selectMenu = null;   // our replacement for the OS's <select> list

  const st = () => ind.settings(id);
  const def = () => ind.CATALOG.find((c) => c.id === id);
  const esc = (s) => String(s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const clone = (v) => JSON.parse(JSON.stringify(v));

  const trimNum = (v) => {
    const n = Number(v);
    return Number.isFinite(n) ? String(Number(n.toFixed(4))) : String(v);
  };

  // ── markup ──────────────────────────────────────────────
  function inputsHTML() {
    const d = def();
    const s = st();
    const fields = d.inputs || [];
    if (!fields.length) {
      // Two very different silences. An indicator with no knobs is a fact;
      // an unreachable catalogue is a failure, and saying "no inputs" to
      // cover it is exactly the fake-success this codebase forbids.
      return (d.formula
        ? `<p class="dlg-empty">${esc(TITLES[d.name] || d.base)} takes no inputs —
           it reads the bars directly.</p>`
        : `<p class="dlg-empty">Could not read the indicator catalogue from the
           data server, so this indicator's inputs are unknown. Style and
           Visibility still work. Check that the server on :5174 is running,
           then reload.</p>`) + formulaHTML();
    }
    const cur = (f) => (s.params[f.key] != null ? s.params[f.key] : f.default);
    const rows = fields.map((f) => {
      if (f.type === "source") {
        const v = cur(f);
        return row(f.label, `<select class="dlg-select" data-param="source">` +
          f.options.map((o) =>
            `<option value="${o}"${o === v ? " selected" : ""}>${SOURCE_LABEL[o] || o}</option>`
          ).join("") + `</select>`);
      }
      if (f.type === "enum") {
        const v = cur(f);
        return row(f.label, `<select class="dlg-select" data-param="${f.key}">` +
          f.options.map((o) =>
            `<option value="${o.value}"${o.value === v ? " selected" : ""}>${esc(o.label)}</option>`
          ).join("") + `</select>`);
      }
      if (f.type === "bool") {
        return `<div class="dlg-row"><label class="dlg-check">
          <input type="checkbox" data-param="${f.key}"${cur(f) ? " checked" : ""}>
          <span>${esc(f.label)}</span></label></div>`;
      }
      // Length is the catalogue id, so it is edited through setPeriod; every
      // other number is a plain param
      const isPeriod = f.key === "period";
      return row(f.label,
        numberHTML(f, isPeriod ? d.period : cur(f), isPeriod ? "period" : f.key));
    }).join("");
    return rows + formulaHTML();
  }

  function formulaHTML() {
    const d = def();
    return d.formula
      ? `<p class="dlg-formula"><span>Formula</span>${esc(d.formula)}</p>` : "";
  }

  function numberHTML(f, value, key) {
    return `<input class="dlg-input" type="number" data-param="${key}"
             value="${trimNum(value)}" min="${f.min}" max="${f.max}" step="${f.step}">`;
  }

  const row = (label, control, cls = "") =>
    `<div class="dlg-row ${cls}"><label>${esc(label)}</label>${control}</div>`;

  function styleHTML() {
    const d = def();
    const s = st();
    const plots = (d.lines || []).map((n) => {
      const p = s.style.plots[n] || {};
      const isHist = p.plotType === "columns";
      const swatches = isHist
        ? swatchHTML(n, "color", p.color, "Growing")
          + swatchHTML(n, "colorDown", p.colorDown || p.color, "Falling")
        : swatchHTML(n, "color", p.color, "Colour");
      const widthSel = isHist ? "" :
        `<select class="dlg-select tight" data-plot="${n}" data-key="width">` +
        Indicators.WIDTHS.map((w) =>
          `<option value="${w}"${w === p.width ? " selected" : ""}>${w}px</option>`).join("") +
        `</select>`;
      const styleSel = isHist ? "" :
        `<select class="dlg-select tight" data-plot="${n}" data-key="lineStyle">` +
        Indicators.LINE_STYLES.map((o) =>
          `<option value="${o.id}"${o.id === (p.lineStyle || 0) ? " selected" : ""}>${o.label}</option>`).join("") +
        `</select>`;
      const typeSel =
        `<select class="dlg-select" data-plot="${n}" data-key="plotType">` +
        Indicators.PLOT_TYPES.map((o) =>
          `<option value="${o.id}"${o.id === p.plotType ? " selected" : ""}>${o.label}</option>`).join("") +
        `</select>`;
      return `<div class="dlg-row plot">
        <label class="dlg-check">
          <input type="checkbox" data-plot="${n}" data-key="visible"${p.visible !== false ? " checked" : ""}>
          <span>${esc(Indicators.lineLabel(n))}</span>
        </label>
        <div class="dlg-ctl">${swatches}${widthSel}${styleSel}${typeSel}</div>
      </div>`;
    }).join("");

    const prec = ["default", 0, 1, 2, 3, 4, 5, 6, 7, 8].map((v) =>
      `<option value="${v}"${String(v) === String(s.style.precision) ? " selected" : ""}>` +
      `${v === "default" ? "Default" : v}</option>`).join("");

    return plots + `<div class="dlg-sep"></div>` +
      row("Precision", `<select class="dlg-select" data-style="precision">${prec}</select>`) +
      toggleRow("statusLine", "Values in status line", s.style.statusLine) +
      toggleRow("priceLabel", "Labels on price scale", s.style.priceLabel) +
      toggleRow("priceLine", "Price line", s.style.priceLine);
  }

  const toggleRow = (key, label, on) =>
    `<div class="dlg-row"><label class="dlg-check">
       <input type="checkbox" data-style="${key}"${on ? " checked" : ""}>
       <span>${esc(label)}</span></label></div>`;

  function swatchHTML(line, key, color, title) {
    return `<button type="button" class="dlg-swatch" title="${title}"
      data-swatch="${line}" data-key="${key}"
      style="--sw:${color}"><span></span></button>`;
  }

  function visibilityHTML() {
    const s = st();
    return `<p class="dlg-note">The timeframes this indicator draws on. An
      interval that is switched off keeps the settings — the plot simply does
      not appear there.</p>` +
      Indicators.BUCKETS.map((b) => `<div class="dlg-row">
        <label class="dlg-check">
          <input type="checkbox" data-vis="${b.key}"${s.visibility[b.key] !== false ? " checked" : ""}>
          <span>${b.label}</span></label>
        <span class="dlg-hint">${b.note}</span>
      </div>`).join("");
  }

  function bodyHTML() {
    if (tab === "style") return styleHTML();
    if (tab === "visibility") return visibilityHTML();
    return inputsHTML();
  }

  /** Groww's dialog carries the indicator's name alone. Charto can have two
   *  of the same indicator open at once, so the instance label is appended
   *  only when it would otherwise be ambiguous which one you are editing. */
  function titleText() {
    const d = def();
    const name = TITLES[d.name] || d.base;
    const twins = ind.CATALOG.filter((c) =>
      c.name === d.name && ind.isActive(c.id)).length > 1;
    return twins ? `${name} · ${d.label}` : name;
  }

  /** Take the card out of the backdrop's centring and give it a position of
   *  its own. Everything that resizes it afterwards — a tab whose controls
   *  need a wider card, a drag — then moves the RIGHT and BOTTOM edges only.
   *  Centred, a card that grows re-centres, so switching tabs slid the whole
   *  dialog out from under the pointer that was about to click the next one. */
  // The anchor is kept HERE, not read back off the DOM. .dlg-wrap animates
  // in with a scale, and an ancestor transform both moves a fixed child's
  // containing block and shows up in getBoundingClientRect — so measuring
  // the card to reposition it made the left edge creep by a few px on every
  // tab. A number we set is a number we can trust.
  let pinX = 0, pinY = 0;
  function pin(left, top) {
    pinX = Math.round(left);
    pinY = Math.round(top);
    dlg.style.position = "fixed";
    dlg.style.margin = "0";
    dlg.style.left = `${pinX}px`;
    dlg.style.top = `${pinY}px`;
  }
  const pinned = () => dlg.style.position === "fixed";

  /** Keep a pinned card on screen after its content changed size. The left
   *  and top edges move only as a last resort — when growing right or down
   *  would otherwise push the card off the viewport. offsetWidth/Height are
   *  layout metrics, so they ignore the wrapper's animation transform. */
  function reclamp() {
    const w = dlg.offsetWidth, h = dlg.offsetHeight;
    pin(Math.max(8, Math.min(pinX, innerWidth - w - 8)),
        Math.max(8, Math.min(pinY, innerHeight - h - 8)));
  }

  function render() {
    dlg.querySelector(".dlg-title").textContent = titleText();
    dlg.querySelectorAll(".dlg-tab").forEach((b) =>
      b.classList.toggle("active", b.dataset.tab === tab));
    dlg.querySelector(".dlg-body").innerHTML = bodyHTML();
    dressSelects(dlg.querySelector(".dlg-body"));
    if (pinned()) reclamp();
  }

  // ── select → the app's own menu ─────────────────────────
  // A native <select> renders its list with the OS, which is the one surface
  // in this app no stylesheet can reach: a white box with a blue-black
  // highlight and system type, inside a dialog built to Pivot's spec. The
  // <select> stays in the DOM as the value holder — every existing input /
  // change handler keeps working untouched — and a button drawn like the
  // other fields shows the value, with a .dropdown menu for the options.
  function dressSelects(root) {
    root.querySelectorAll("select.dlg-select").forEach((sel) => {
      if (sel.dataset.dressed) return;
      sel.dataset.dressed = "1";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = sel.className.replace("dlg-select", "select-btn");
      btn.innerHTML = `<span class="val"></span>${Icons.svg("chevronDown", "xs")}`;
      const paint = () => {
        const o = sel.options[sel.selectedIndex];
        btn.querySelector(".val").textContent = o ? o.textContent : "";
      };
      paint();
      sel.after(btn);
      sel.classList.add("is-dressed");
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        openSelectMenu(sel, btn, paint);
      });
      sel.addEventListener("change", paint);
    });
  }

  function openSelectMenu(sel, btn, paint) {
    closeSelectMenu();
    selectMenu = document.createElement("div");
    selectMenu.className = "dropdown select-menu open";
    selectMenu.innerHTML = [...sel.options].map((o, i) =>
      `<div class="item${i === sel.selectedIndex ? " on" : ""}" data-i="${i}">` +
      `<span class="lead">${esc(o.textContent)}</span>` +
      `${i === sel.selectedIndex ? Icons.svg("check", "xs") : ""}</div>`).join("");
    document.body.appendChild(selectMenu);
    selectMenu.style.minWidth = `${Math.max(btn.offsetWidth, 132)}px`;
    place(selectMenu, btn);
    selectMenu.addEventListener("pointerdown", (e) => e.stopPropagation());
    selectMenu.addEventListener("click", (e) => {
      e.stopPropagation();
      const it = e.target.closest("[data-i]");
      if (!it) return;
      sel.selectedIndex = Number(it.dataset.i);
      paint();
      // the same two events the native control fired, so nothing downstream
      // needs to know the list was ours
      sel.dispatchEvent(new Event("input", { bubbles: true }));
      sel.dispatchEvent(new Event("change", { bubbles: true }));
      closeSelectMenu();
    });
  }
  function closeSelectMenu() {
    if (selectMenu) { selectMenu.remove(); selectMenu = null; }
  }

  /** Only the header needs repainting after a live edit — rebuilding the
   *  body would blow away the field the user is in. */
  function repaintTitle() {
    dlg.querySelector(".dlg-title").textContent = titleText();
  }

  // ── colour popover ──────────────────────────────────────
  function openSwatch(btn) {
    closeSwatch();
    closeSelectMenu();
    const line = btn.dataset.swatch, key = btn.dataset.key;
    const cur = (st().style.plots[line] || {})[key] || "#ffffff";
    const alpha = alphaOf(cur);
    let hex = hexOf(cur);

    swatchPop = document.createElement("div");
    swatchPop.className = "dlg-swatch-pop";
    swatchPop.innerHTML =
      `<div class="grid">${PALETTE_ROWS.map((row) => row.map((c) =>
        `<button type="button" class="sw" data-c="${c}" style="--sw:${c}"
           title="${c}"></button>`).join("")).join("")}</div>
       <div class="pop-row">
         <button type="button" class="custom-tile" title="Custom colour">${Icons.svg("plus")}</button>
         <span class="pop-label">Custom</span>
       </div>
       <div class="pk" hidden>
         <div class="pk-area"><span class="pk-dot"></span></div>
         <input class="pk-hue" type="range" min="0" max="360" step="1" value="0"
                aria-label="Hue">
         <div class="pop-row pk-foot">
           <span class="pk-preview"></span>
           <input class="input pk-hex" type="text" spellcheck="false" maxlength="7"
                  aria-label="Hex colour">
         </div>
       </div>
       <div class="pop-sep"></div>
       <div class="pop-row opacity">
         <span class="pop-label">Opacity</span>
         <input type="range" min="0" max="100" step="1" value="${Math.round(alpha * 100)}">
         <output>${Math.round(alpha * 100)}%</output>
       </div>`;
    document.body.appendChild(swatchPop);
    // the popover lives on <body>, so its own presses must not reach the
    // document handler that dismisses it — dragging the opacity slider was
    // closing the thing being dragged
    swatchPop.addEventListener("pointerdown", (e) => e.stopPropagation());
    place(swatchPop, btn);

    const range = swatchPop.querySelector(".opacity input");
    const out = swatchPop.querySelector("output");
    const pk = swatchPop.querySelector(".pk");
    const area = swatchPop.querySelector(".pk-area");
    const dot = swatchPop.querySelector(".pk-dot");
    const hue = swatchPop.querySelector(".pk-hue");
    const hexIn = swatchPop.querySelector(".pk-hex");
    const preview = swatchPop.querySelector(".pk-preview");

    function markSelected() {
      const want = hex.toLowerCase();
      swatchPop.querySelectorAll(".sw").forEach((s) =>
        s.classList.toggle("sel", s.dataset.c.toLowerCase() === want));
    }
    function pick(next, fromPicker) {
      hex = next;
      const a = Number(range.value) / 100;
      const v = a >= 1 ? next : Indicators.withAlpha(next, a);
      set(line, key, v);
      btn.style.setProperty("--sw", v);
      markSelected();
      if (!fromPicker && !pk.hidden) paintPicker();
      if (fromPicker) { preview.style.background = next; hexIn.value = next; }
    }

    // ── the custom picker: saturation/value field + hue, in the page's own
    // idiom. The native <input type="color"> opened Chrome's OS panel —
    // an eyedropper, R/G/B spinners and a system typeface dropped into the
    // middle of a Pivot dialog.
    function paintPicker() {
      const { h, s, v } = rgbToHsv(hex);
      hue.value = Math.round(h);
      area.style.setProperty("--hue", h);
      dot.style.left = `${s * 100}%`;
      dot.style.top = `${(1 - v) * 100}%`;
      dot.style.background = hex;
      preview.style.background = hex;
      hexIn.value = hex;
    }
    function fromArea(e) {
      const r = area.getBoundingClientRect();
      const s = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
      const v = 1 - Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
      const next = hsvToHex(Number(hue.value), s, v);
      dot.style.left = `${s * 100}%`;
      dot.style.top = `${(1 - v) * 100}%`;
      dot.style.background = next;
      pick(next, true);
    }
    area.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      area.setPointerCapture(e.pointerId);
      fromArea(e);
      const move = (ev) => fromArea(ev);
      const up = () => {
        area.removeEventListener("pointermove", move);
        area.removeEventListener("pointerup", up);
      };
      area.addEventListener("pointermove", move);
      area.addEventListener("pointerup", up);
    });
    hue.addEventListener("input", () => {
      const { s, v } = rgbToHsv(hex);
      area.style.setProperty("--hue", hue.value);
      const next = hsvToHex(Number(hue.value), s, v);
      dot.style.background = next;
      pick(next, true);
    });
    hexIn.addEventListener("change", () => {
      const m = /^#?([0-9a-f]{6})$/i.exec(hexIn.value.trim());
      if (!m) { hexIn.value = hex; return; }
      pick(`#${m[1].toLowerCase()}`);
      paintPicker();
    });

    markSelected();

    swatchPop.addEventListener("click", (e) => {
      e.stopPropagation();
      const c = e.target.closest("[data-c]");
      if (c) { pick(c.dataset.c); return; }
      if (e.target.closest(".custom-tile")) {
        pk.hidden = !pk.hidden;
        swatchPop.classList.toggle("picking", !pk.hidden);
        if (!pk.hidden) { paintPicker(); place(swatchPop, btn); }
      }
    });
    range.addEventListener("input", () => {
      out.textContent = `${range.value}%`;
      pick(hex);
    });
  }

  /** Anchor a popover to a control, kept inside the viewport. It opens below
   *  and left-aligned, flipping above only when there is no room under. */
  function place(pop, anchor) {
    const r = anchor.getBoundingClientRect();
    const w = pop.offsetWidth, h = pop.offsetHeight;
    const left = Math.max(8, Math.min(r.left, innerWidth - w - 8));
    const below = r.bottom + 6;
    const top = below + h <= innerHeight - 8 ? below
      : Math.max(8, r.top - h - 6);
    pop.style.left = `${left}px`;
    pop.style.top = `${top}px`;
  }
  function closeSwatch() {
    if (swatchPop) { swatchPop.remove(); swatchPop = null; }
  }

  function hexOf(c) {
    const m = /^#([0-9a-f]{6})$/i.exec(String(c).trim());
    if (m) return `#${m[1]}`;
    const r = /^rgba?\(([^)]+)\)$/i.exec(String(c).trim());
    if (!r) return "#ffffff";
    const p = r[1].split(",").map((x) => parseInt(x.trim(), 10));
    return `#${p.slice(0, 3).map((n) => n.toString(16).padStart(2, "0")).join("")}`;
  }
  /** #rrggbb → {h 0-360, s 0-1, v 0-1} and back. Kept local: the picker is
   *  the only thing in the app that thinks in HSV, because a square of
   *  saturation against value is the only way to pick a shade by eye. */
  function rgbToHsv(hexStr) {
    const n = parseInt(hexOf(hexStr).slice(1), 16);
    const r = ((n >> 16) & 255) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    let h = 0;
    if (d) {
      if (max === r) h = ((g - b) / d) % 6;
      else if (max === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h *= 60;
      if (h < 0) h += 360;
    }
    return { h, s: max ? d / max : 0, v: max };
  }
  function hsvToHex(h, s, v) {
    const c = v * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = v - c;
    const seg = [[c, x, 0], [x, c, 0], [0, c, x], [0, x, c], [x, 0, c], [c, 0, x]];
    const [r, g, b] = seg[Math.min(5, Math.floor(h / 60))] || seg[0];
    const to = (u) => Math.round((u + m) * 255).toString(16).padStart(2, "0");
    return `#${to(r)}${to(g)}${to(b)}`;
  }

  function alphaOf(c) {
    const r = /^rgba\(([^)]+)\)$/i.exec(String(c).trim());
    if (!r) return 1;
    const p = r[1].split(",");
    return p.length > 3 ? Math.max(0, Math.min(1, parseFloat(p[3]))) : 1;
  }

  // ── writes ──────────────────────────────────────────────
  function set(line, key, value) {
    const patch = { style: { plots: { [line]: { [key]: value } } } };
    // any colour the user picks is a decision the theme toggle must respect
    if (key === "color" || key === "colorDown") patch.style.plots[line].custom = true;
    ind.applySettings(id, patch).then(notify).catch(() => {});
  }

  async function setParam(key, raw) {
    const d = def();
    const f = (d.inputs || []).find((x) => x.key === key);
    if (!f) return;
    let v;
    if (f.type === "source" || f.type === "enum") {
      v = String(raw);
    } else if (f.type === "bool") {
      v = !!raw;
    } else {
      v = Number(raw);
      if (!Number.isFinite(v)) return;
      v = Math.max(f.min, Math.min(f.max, v));
      if (f.type === "int") v = Math.round(v);
    }
    if (key === "period") {
      if (v === d.period) return;
      try {
        id = await ind.setPeriod(id, v);
      } catch (e) { return; }
      repaintTitle();
      notify();
      return;
    }
    try {
      await ind.applySettings(id, { params: { [key]: v } });
      repaintTitle();
      notify();
    } catch (e) { /* the manager keeps the old series; nothing to undo */ }
  }

  // ── wiring ──────────────────────────────────────────────
  function onInput(e) {
    const t = e.target;
    if (t.dataset.param) return;                 // numbers commit on change
    if (t.dataset.plot) {
      const v = t.type === "checkbox" ? t.checked
        : (t.dataset.key === "width" || t.dataset.key === "lineStyle"
            ? Number(t.value) : t.value);
      set(t.dataset.plot, t.dataset.key, v);
      if (t.dataset.key === "plotType") render();   // the row's controls change
      return;
    }
    if (t.dataset.style) {
      const key = t.dataset.style;
      const v = t.type === "checkbox" ? t.checked
        : (t.value === "default" ? "default" : Number(t.value));
      ind.applySettings(id, { style: { [key]: v } }).then(notify).catch(() => {});
      return;
    }
    if (t.dataset.vis) {
      ind.applySettings(id, { visibility: { [t.dataset.vis]: t.checked } })
        .then(notify).catch(() => {});
    }
  }

  // A select fires BOTH input and change; only the typed number fields want
  // change (they must not refetch on every keystroke), so the two handlers
  // divide the field types between them rather than overlapping.
  function onChange(e) {
    const t = e.target;
    if (!t.dataset.param) return;
    setParam(t.dataset.param, t.type === "checkbox" ? t.checked : t.value);
  }

  function onClick(e) {
    e.stopPropagation();
    const sw = e.target.closest("[data-swatch]");
    if (sw) { openSwatch(sw); return; }
    closeSwatch();

    const d = e.target.closest("[data-def]");
    if (d) { applyDefaultsAction(d.dataset.def); return; }

    const tb = e.target.closest(".dlg-tab");
    if (tb) { tab = tb.dataset.tab; render(); return; }

    const act = e.target.closest("[data-act]");
    if (!act) return;
    if (act.dataset.act === "defaults") {
      const m = dlg.querySelector(".dlg-def-menu");
      m.querySelector('[data-def="clear"]').style.display =
        ind.hasDefault(id) ? "" : "none";
      m.classList.toggle("open");
    } else if (act.dataset.act === "cancel") cancel();
    else close();                       // ok / ×: the edits are already live
  }

  function applyDefaultsAction(what) {
    dlg.querySelector(".dlg-def-menu").classList.remove("open");
    if (what === "reset") {
      // a reset that leaves the length alone is not a reset
      const d = def();
      const factory = ind.factory(id);
      const back = (d.inputs || []).find((f) => f.key === "period");
      const restore = () => ind.replaceSettings(id, factory).then(() => {
        render(); notify();
      });
      if (back && d.period !== back.default) {
        ind.setPeriod(id, back.default).then((nid) => { id = nid; restore(); });
      } else restore();
      return;
    }
    if (what === "save") ind.saveAsDefault(id);
    if (what === "clear") ind.clearDefault(id);
  }

  async function cancel() {
    // Length re-mints the catalogue id, so undoing it comes first — then the
    // snapshot goes back onto whatever id we ended up on.
    const back = ind.CATALOG.find((c) => c.id === origId);
    if (id !== origId && back) {
      try { id = await ind.setPeriod(id, back.period); } catch {}
    }
    try { await ind.replaceSettings(id, snapshot); } catch {}
    notify();
    close();
  }

  // ── drag by the header, the way TradingView's dialog moves ──
  function startDrag(e) {
    if (e.target.closest("button")) return;
    const dx = e.clientX - pinX, dy = e.clientY - pinY;
    const w = dlg.offsetWidth;
    const move = (ev) => {
      pin(Math.max(8, Math.min(ev.clientX - dx, innerWidth - w - 8)),
          Math.max(8, Math.min(ev.clientY - dy, innerHeight - 60)));
    };
    const up = () => {
      removeEventListener("pointermove", move);
      removeEventListener("pointerup", up);
    };
    addEventListener("pointermove", move);
    addEventListener("pointerup", up);
  }

  function build() {
    wrap = document.createElement("div");
    wrap.className = "dlg-wrap";
    wrap.innerHTML = `
      <div class="dlg" role="dialog" aria-modal="true">
        <header class="dlg-head">
          <div class="dlg-title"></div>
          <button class="btn icon" data-act="close" title="Close"></button>
        </header>
        <nav class="dlg-tabs">
          <button class="dlg-tab active" data-tab="inputs">Inputs</button>
          <button class="dlg-tab" data-tab="style">Style</button>
          <button class="dlg-tab" data-tab="visibility">Visibility</button>
        </nav>
        <div class="dlg-body"></div>
        <footer class="dlg-foot">
          <div class="dlg-def">
            <button class="btn outline" data-act="defaults">Defaults</button>
            <div class="dropdown up dlg-def-menu">
              <div class="item" data-def="reset"><span class="lead">Reset settings</span></div>
              <div class="item" data-def="save"><span class="lead">Save as default</span></div>
              <div class="item" data-def="clear"><span class="lead">Clear saved default</span></div>
            </div>
          </div>
          <span class="spacer"></span>
          <button class="btn outline" data-act="cancel">Cancel</button>
          <button class="btn cta" data-act="ok">Ok</button>
        </footer>
      </div>`;
    document.body.appendChild(wrap);
    dlg = wrap.querySelector(".dlg");
    dlg.querySelector('[data-act="close"]').innerHTML = Icons.svg("x", "sm");
    dlg.querySelector('[data-act="defaults"]').innerHTML =
      "Defaults" + Icons.svg("chevronDown", "chev");
    dlg.addEventListener("click", onClick);
    dlg.addEventListener("input", onInput);
    dlg.addEventListener("change", onChange);
    dlg.querySelector(".dlg-head").addEventListener("pointerdown", startDrag);
    // clicking the dimmed backdrop is Cancel, not a silent commit
    wrap.addEventListener("pointerdown", (e) => { if (e.target === wrap) cancel(); });
    addEventListener("keydown", (e) => {
      if (!wrap || !wrap.classList.contains("open")) return;
      if (e.key === "Escape") { e.stopPropagation(); cancel(); }
    }, true);
    document.addEventListener("pointerdown", () => {
      if (!dlg) return;
      const m = dlg.querySelector(".dlg-def-menu");
      if (m) m.classList.remove("open");
      closeSwatch();
      closeSelectMenu();
    });
    // a pinned card holds absolute coordinates, so a shrinking window can
    // strand it off the edge — it never re-centres, it just comes back in
    addEventListener("resize", () => {
      if (wrap.classList.contains("open") && pinned()) reclamp();
    });
  }

  function open(manager, indicatorId, opts = {}) {
    ind = manager;
    if (!wrap) build();
    id = indicatorId;
    origId = indicatorId;
    subtitle = opts.subtitle || "";
    notify = opts.onChange || (() => {});
    snapshot = clone(ind.settings(id));
    tab = "inputs";
    // Centre it ONCE, arithmetically, then pin. The card is anchored from
    // this point on, so every later size change grows rightward and down.
    dlg.style.position = "";
    dlg.style.left = dlg.style.top = dlg.style.margin = "";
    render();
    wrap.classList.add("open");          // it must be laid out to have a size
    pin(Math.max(8, (innerWidth - dlg.offsetWidth) / 2),
        Math.max(8, (innerHeight - dlg.offsetHeight) / 2));
  }

  function close() {
    closeSwatch();
    closeSelectMenu();
    if (wrap) wrap.classList.remove("open");
  }

  return { open, close, isOpen: () => !!(wrap && wrap.classList.contains("open")) };
})();
