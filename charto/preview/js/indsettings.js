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
 *
 * The pieces this shares with the chart-settings dialog — the colour picker,
 * the dressed <select>, the card's position and drag — live in js/dlgkit.js.
 * Two dialogs, one picker.
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

  let wrap = null, dlg = null;
  let card = null;               // DlgKit's position/drag handle for .dlg
  let ind = null;                // the indicator manager
  let id = null, origId = null;  // the id can change: Length re-mints the def
  let snapshot = null;           // what Cancel restores
  let tab = "inputs";
  let notify = () => {};
  let subtitle = "";

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

  function render() {
    dlg.querySelector(".dlg-title").textContent = titleText();
    dlg.querySelectorAll(".dlg-tab").forEach((b) =>
      b.classList.toggle("active", b.dataset.tab === tab));
    dlg.querySelector(".dlg-body").innerHTML = bodyHTML();
    DlgKit.dressSelects(dlg.querySelector(".dlg-body"));
    if (card.pinned()) card.reclamp();
  }

  /** Only the header needs repainting after a live edit — rebuilding the
   *  body would blow away the field the user is in. */
  function repaintTitle() {
    dlg.querySelector(".dlg-title").textContent = titleText();
  }

  /** The kit's picker, aimed at one plot's colour key. */
  function openSwatch(btn) {
    const line = btn.dataset.swatch, key = btn.dataset.key;
    DlgKit.openSwatch(btn, {
      value: (st().style.plots[line] || {})[key] || "#ffffff",
      onPick: (v) => set(line, key, v),
    });
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
    DlgKit.closeSwatch();

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
    // position, drag and the off-screen clamp — the kit's, so both dialogs
    // in this app move the same way
    card = DlgKit.draggable(dlg, dlg.querySelector(".dlg-head"));
    // clicking the dimmed backdrop is Cancel, not a silent commit
    wrap.addEventListener("pointerdown", (e) => { if (e.target === wrap) cancel(); });
    addEventListener("keydown", (e) => {
      if (!wrap || !wrap.classList.contains("open")) return;
      if (e.key === "Escape") { e.stopPropagation(); cancel(); }
    }, true);
    // the kit dismisses its own popovers on a document press; the Defaults
    // menu is this dialog's, so it is dismissed here
    document.addEventListener("pointerdown", () => {
      if (!dlg) return;
      const m = dlg.querySelector(".dlg-def-menu");
      if (m) m.classList.remove("open");
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
    card.centre();
    render();
    wrap.classList.add("open");          // it must be laid out to have a size
    card.pinCentred();
  }

  function close() {
    DlgKit.closePopovers();
    if (wrap) wrap.classList.remove("open");
  }

  return { open, close, isOpen: () => !!(wrap && wrap.classList.contains("open")) };
})();
