/* Charto preview — alerts: the client half of the watcher.
 *
 * The engine is data/alerts.py. This file is three things and nothing else:
 * the state the Alerts widget paints, the stream that tells it something
 * fired, and the one dialog where a rule is written.
 *
 * WHY THE CONDITION FIELDS ARE PART DROPDOWN AND PART FREE TEXT
 * The backend takes a composed expression — `left <op> right`, where both
 * sides are ADDRESSES ("close", "sma(200)", "avg(volume,20)", "draw:D7") from
 * a grammar it publishes. A dialog of fixed dropdowns would quietly become the
 * limit on what the engine can express, which is the exact failure the engine
 * was shaped to avoid. So the selects carry the handful of things people ask
 * for every day, and every field also accepts a typed address. The grammar in
 * the hint is the SERVER's copy, delivered with /alerts, so there is no second
 * list here to drift.
 *
 * WHY THE STREAM IS fetch() AND NOT EventSource
 * EventSource cannot set an Authorization header, and the only alternative is
 * a bearer token in the query string — which is a bearer token in nginx's
 * access log. chat.js already reads SSE off a fetch body reader twice; this is
 * the third, for the same reason.
 *
 * WHAT THIS DOES NOT DO. With every tab shut, nothing is delivered anywhere.
 * The log and the bell are waiting when you come back, and no string in this
 * file may suggest otherwise — the honest boundary is the feature.
 */
"use strict";

const Alerts = (() => {
  const API = ["localhost", "127.0.0.1"].includes(location.hostname)
    ? "http://127.0.0.1:5174" : "";

  const el = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  /* ── state ───────────────────────────────────────────────────────────────
   * One copy, and every reader takes it from here. `loaded` separates "you
   * have no alerts" from "we have not asked yet" — the empty state and the
   * blank state are different sentences and the panel prints both.
   */
  const state = {
    alerts: [], log: [], unseen: 0, loaded: false, signedIn: false,
    feed: null, vocab: null, error: "",
  };
  const listeners = [];
  const emit = () => listeners.forEach((f) => { try { f(state); } catch {} });

  async function call(path, body, opts = {}) {
    const res = await fetch(API + path, {
      method: body === undefined ? "GET" : "POST",
      headers: Auth.headers(body === undefined
        ? {} : { "Content-Type": "application/json" }),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = {};
    try { data = await res.json(); } catch {}
    if (!res.ok) {
      const err = new Error(data.error || `request failed (${res.status})`);
      err.payload = data;      // the server hands back the whole grammar on a
      throw err;               // refusal; the dialog shows it rather than "400"
    }
    return data;
  }

  async function load() {
    if (!Auth.user) {
      state.alerts = []; state.log = []; state.unseen = 0;
      state.loaded = true; state.signedIn = false;
      emit();
      return;
    }
    try {
      const d = await call("/alerts");
      state.alerts = d.alerts || [];
      state.log = d.log || [];
      state.unseen = d.unseen || 0;
      state.feed = d.feed || null;
      state.vocab = d.vocab || null;
      state.error = "";
    } catch (e) {
      state.error = e.message;
    }
    state.loaded = true; state.signedIn = true;
    emit();
  }

  /* ── the stream ──────────────────────────────────────────────────────── */

  let streaming = false, backoff = 2000;

  async function stream() {
    if (streaming || !Auth.user) return;
    streaming = true;
    try {
      const res = await fetch(API + "/alerts/stream", { headers: Auth.headers() });
      if (!res.ok || !res.body) throw new Error(`stream ${res.status}`);
      backoff = 2000;                       // a connection that opened is proof
      const rd = res.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await rd.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const frames = buf.split("\n\n");
        buf = frames.pop() || "";
        for (const f of frames) {
          const line = f.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;              // ": ping" keepalives land here
          let ev = null;
          try { ev = JSON.parse(line.slice(6)); } catch { continue }
          if (ev && ev.type === "fired") onFired(ev);
        }
      }
    } catch {
      /* a dropped stream is normal — reconnect below */
    } finally {
      streaming = false;
      if (Auth.user) {
        setTimeout(stream, backoff);
        backoff = Math.min(backoff * 2, 60000);
      }
    }
  }

  /** Something fired. Three surfaces, in the order they matter: the record,
   *  the room, then the operating system. */
  function onFired(ev) {
    if (ev.log) {
      state.log = [ev.log, ...state.log].slice(0, 500);
      state.unseen += 1;
    }
    if (ev.alert) {
      const i = state.alerts.findIndex((a) => a.id === ev.alert.id);
      if (i >= 0) {
        state.alerts[i] = Object.assign({}, state.alerts[i], ev.alert,
                                        { fired_at: ev.log && ev.log.ts });
      } else {
        load();     // fired before this tab had ever listed it
      }
    }
    emit();
    const l = ev.log || {};
    const line = `${l.symbol} ${l.verb} ${l.level}`;
    toast(l.late ? `${line} (found on reconnect)` : line);
    notify(line, l);
  }

  /* ── delivery on this machine ────────────────────────────────────────── */

  /** Permission is asked at the moment a first alert is created, never on page
   *  load: a site that demands notifications before you have asked it for
   *  anything gets denied once and permanently. */
  function askPermission() {
    if (!("Notification" in window)) return;
    if (Notification.permission !== "default") return;
    try { Notification.requestPermission(); } catch {}
  }

  function notify(line, l) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    // A tab that is open and looking at the panel already showed the toast and
    // the row; a second OS banner over it is noise.
    if (document.visibilityState === "visible" && Panels && Panels.openWidget
        && Panels.openWidget() === "alerts") return;
    try {
      const n = new Notification("Charto alert", {
        body: `${line}\n${l.meta || ""} · saw ${l.value}`,
        tag: `charto-alert-${l.id}`, silent: false,
      });
      n.onclick = () => {
        window.focus();
        if (Panels && Panels.show) Panels.show("alerts");
        n.close();
      };
    } catch {}
  }

  /** The app's toast, borrowed rather than re-made — layouts.js owns the one
   *  element and the one timer. */
  function toast(msg) {
    let t = el("layoutToast");
    if (!t) {
      t = document.createElement("div");
      t.id = "layoutToast";
      t.className = "layout-toast";
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => t.classList.remove("show"), 3200);
  }

  /* ── mutations ───────────────────────────────────────────────────────── */

  async function create(rule) {
    const d = await call("/alerts", rule);
    state.alerts = [d.alert, ...state.alerts];
    emit();
    askPermission();
    return d;
  }

  async function patch(id, body) {
    const d = await call(`/alerts/${id}`, body);
    if (d.deleted) {
      state.alerts = state.alerts.filter((a) => a.id !== d.deleted);
    } else if (d.alert) {
      const i = state.alerts.findIndex((a) => a.id === d.alert.id);
      if (i >= 0) state.alerts[i] = d.alert;
    }
    emit();
    return d;
  }

  /** One click, one alert, no dialog — what the ⊕ on the price axis does.
   *
   * `crossing` in EITHER direction on purpose: the click says "this level
   * matters", not which side it will be approached from, and picking a side for
   * the user is how an instant alert ends up armed the wrong way round. The
   * dialog is still there for anything more specific, and this rule can be
   * edited into one.
   */
  async function quick(symbol, level, interval) {
    const d = await create({
      symbol, interval, freq: "once",
      when: [{ left: "close", op: "cross", right: Number(level) }],
    });
    const feed = d.feed && d.feed.symbol;
    toast(`Alert set: ${d.alert.symbol} crossing ${d.alert.level}` +
          (feed && !feed.streaming ? " — not being watched live yet" : ""));
    return d;
  }

  const remove = (id) => patch(id, { delete: true });
  const toggle = (a) =>
    patch(a.id, { state: a.state === "paused" ? "armed" : "paused" });

  async function markSeen() {
    if (!state.unseen) return;
    state.unseen = 0;
    state.log = state.log.map((l) => Object.assign({}, l, { seen: true }));
    emit();
    try { await call("/alerts/seen", {}); } catch {}
  }

  /* ══ the dialog ══════════════════════════════════════════════════════════
   * The one screen the fixture never had. Built on DlgKit so it drags,
   * centres and clamps the way the indicator and chart dialogs do.
   */

  /* ── the address catalogue ────────────────────────────────────────────────
   * Grouped and ordered the way the eye wants them, and it is a SHORTLIST, not
   * the vocabulary: both fields are free text, so anything the engine's grammar
   * knows can be typed whether it is listed here or not. That is deliberate —
   * a fixed menu would quietly become the ceiling on what the engine expresses.
   *
   * Rendered in the app's own dropdown. The first version used a native
   * <datalist>, which the OS paints itself: on a light page it dropped a tall
   * black panel over half the dialog, and no stylesheet can reach it.
   */
  const CATALOG = [
    ["Price", [
      ["close", "Close"], ["open", "Open"], ["high", "High"], ["low", "Low"],
      ["close[1]", "Previous bar close"],
      ["hl2", "Median price"], ["hlc3", "Typical price"],
    ]],
    ["Volume", [
      ["volume", "Volume"],
      ["avg(volume,20)", "Average volume, 20 bars"],
    ]],
    ["Session", [
      ["day.open", "Day open"], ["day.high", "Day high"], ["day.low", "Day low"],
      ["pday.close", "Previous day close"],
      ["pday.high", "Previous day high"], ["pday.low", "Previous day low"],
    ]],
    ["Highs and lows", [
      ["52w.high", "52-week high"], ["52w.low", "52-week low"],
      ["20d.high", "20-day high"], ["20d.low", "20-day low"],
    ]],
    ["Moving averages", [
      ["sma(20)", "SMA 20"], ["sma(50)", "SMA 50"], ["sma(200)", "SMA 200"],
      ["ema(21)", "EMA 21"], ["vwap()", "VWAP"],
    ]],
    ["Indicators", [
      ["rsi(14)", "RSI 14"], ["macd().macd", "MACD"],
      ["macd().signal", "MACD signal"], ["atr(14)", "ATR 14"],
      ["bbands(20).upper", "Bollinger upper"],
      ["bbands(20).lower", "Bollinger lower"],
      ["stoch(14).k", "Stochastic %K"], ["supertrend(10)", "Supertrend"],
    ]],
    ["Volume profile", [
      ["poc", "Point of control"], ["vah", "Value area high"],
      ["val", "Value area low"],
    ]],
  ];

  /* Patterns are LEFT-hand subjects only — they pair with `completes`, which has
   * no right side. Kept out of the shared catalogue so the right-hand menu
   * cannot offer something that can never be a target. */
  const COMPLETIONS = ["Patterns", [
    ["pattern(bullish_engulfing)", "Bullish engulfing"],
    ["pattern(bearish_engulfing)", "Bearish engulfing"],
    ["pattern(hammer)", "Hammer"],
    ["pattern(morning_star)", "Morning star"],
    ["pattern(double_bottom)", "Double bottom"],
    ["pattern(double_top)", "Double top"],
    ["pattern(falling_wedge)", "Falling wedge"],
    ["pattern(bull_flag)", "Bull flag"],
    ["divergence(rsi)", "RSI divergence"],
    ["results()", "Results day"],
  ]];

  /** The user's own drawings, offered as addresses. Read from the key
   *  drawings.js persists under, because the drawing layer lives inside
   *  main.js's closure and is not reachable from here — and a stale read is
   *  harmless, since the engine re-resolves the ref against the live row and
   *  refuses one that is gone. */
  function drawingGroup(symbol) {
    let items = [];
    try {
      const raw = localStorage.getItem(
        "charto_drawings_v2_" + String(symbol).toUpperCase());
      items = JSON.parse(raw || "[]") || [];
    } catch { items = []; }
    const rows = items
      .filter((d) => (d.ref || d.id) && (d.pts || []).length)
      .map((d) => [`draw:${d.ref || d.id}`,
                   `your ${String(d.type || "drawing").replace(/_/g, " ")}` +
                   ((d.pts || []).length > 1 ? " — tracks the line" : "")]);
    return rows.length ? ["Your drawings", rows] : null;
  }
  const OPLABEL = {
    cross: "Crossing", cross_up: "Crossing up", cross_down: "Crossing down",
    above: "Greater than", below: "Less than",
    rises_pct: "Rising by %", falls_pct: "Falling by %",
    changes_pct: "Moving by %", enters: "Entering range",
    exits: "Leaving range", is_true: "Completes",
  };
  /* A picture of what each operator does, drawn in js/icons.js to one grammar:
   * the dashed line is the level, the solid stroke is the price. Read down the
   * menu's left edge, the difference between crossing up, greater than and
   * entering a range is visible before the words are. */
  const OPICON = {
    cross: "opCross", cross_up: "opCrossUp", cross_down: "opCrossDown",
    above: "opAbove", below: "opBelow",
    rises_pct: "opRise", falls_pct: "opFall", changes_pct: "opMove",
    enters: "opEnter", exits: "opExit", is_true: "opCompletes",
  };
  const FREQS = [["once", "Once only"], ["per_bar", "Once per bar"],
                 ["per_bar_close", "Once per bar close"],
                 ["per_day", "Once per day"]];
  const IVS = ["1m", "3m", "5m", "15m", "30m", "1h", "1d"];
  const EXPIRY = [["0", "Never"], ["1", "In a day"], ["7", "In a week"],
                  ["30", "In a month"]];

  let wrap = null, dlg = null, card = null, draft = null, editing = null;
  let checkTimer = null;

  const blank = (symbol) => ({
    symbol, interval: "5m", freq: "once", all: true, note: "", expires_days: "0",
    when: [{ left: "close", op: "cross_up", right: "" }],
  });

  /** A dressed select. `icons` maps a value to a glyph, which DlgKit draws
   *  before the label in both the list and the closed control. */
  function sel(name, opts, value, cls = "", icons = null) {
    return `<select class="dlg-select ${cls}" data-f="${name}">` + opts.map(([v, l]) =>
      `<option value="${esc(v)}"` +
      (icons && icons[v] ? ` data-icon="${esc(icons[v])}"` : "") +
      (String(v) === String(value) ? " selected" : "") +
      `>${esc(l)}</option>`).join("") + `</select>`;
  }

  /* ── the address field ────────────────────────────────────────────────────
   * An input with the app's own menu behind it: focusing or typing opens a
   * grouped, filtered list, and picking one fills the field. The field stays
   * free text throughout, so the menu is a shortcut and never a constraint.
   */
  function combo(side, value, placeholder) {
    return `<div class="al-combo">` +
      `<input class="dlg-input ${side}" data-f="${side}" data-combo="${side}" ` +
        `value="${esc(value ?? "")}" placeholder="${esc(placeholder)}" ` +
        `autocomplete="off" spellcheck="false">` +
      `<button type="button" class="al-caret" data-act="pick-${side}" ` +
        `tabindex="-1" aria-label="Choose">${Icons.svg("chevronDown", "xs")}` +
      `</button></div>`;
  }

  let comboMenu = null, comboOff = null, comboFor = null;
  // Set while a pick is being applied. The pick fires `input` so the rest of the
  // dialog reacts exactly as it does to typing — and without this flag that
  // same event immediately re-opened the menu the pick had just closed.
  let picking = false, suppressOpen = false;

  function closeCombo() {
    if (!comboMenu) return;
    comboMenu.remove();
    comboMenu = comboFor = null;
    document.removeEventListener("pointerdown", comboOff, true);
    removeEventListener("resize", closeCombo);
    comboOff = null;
  }

  /** Groups for one side, filtered by what has been typed so far. Matching on
   *  BOTH the address and its description: someone typing "yesterday" is
   *  looking for pday.close and has no reason to know that. */
  function comboGroups(side, query) {
    const groups = CATALOG.slice();
    const drawn = drawingGroup(draft.symbol);
    if (drawn) groups.push(drawn);
    if (side === "left") groups.push(COMPLETIONS);
    const q = String(query || "").trim().toLowerCase();
    if (!q) return groups;
    return groups
      .map(([name, rows]) => [name, rows.filter(([a, d]) =>
        a.toLowerCase().includes(q) || d.toLowerCase().includes(q))])
      .filter(([, rows]) => rows.length);
  }

  /** `typed` distinguishes the two ways this opens. Focusing a field that
   *  already holds "close" must show the WHOLE list — filtering by the current
   *  value there hides every other choice, which is the opposite of what
   *  opening a list is for. Filtering starts when the user actually types. */
  function openCombo(input, side, typed = false) {
    const groups = comboGroups(side, typed ? input.value : "");
    // Nothing matched, and that is not an error: what was typed may well be a
    // valid address this shortlist does not carry. An empty menu would say the
    // opposite, so none is opened and the preview line does the judging.
    if (!groups.length) return closeCombo();
    if (comboMenu && comboFor === input) {
      comboMenu.innerHTML = comboHTML(groups);
      DlgKit.place(comboMenu, input);
      return;
    }
    closeCombo();
    const m = document.createElement("div");
    // NOT `.dropdown`, deliberately. Three things go wrong when it is: main.js
    // strips `.open` from every `.dropdown.open` on any document click (and the
    // click that focuses this field is one), `.dropdown.floating` is declared
    // inside a media query so it outranks anything written here, and
    // `.dropdown .head` is styled for ONE heading at the top of a menu — this
    // one has seven. Self-contained styling, same design tokens.
    m.className = "al-menu";
    m.innerHTML = comboHTML(groups);
    document.body.appendChild(m);
    comboMenu = m;
    comboFor = input;
    DlgKit.place(m, input);
    // mousedown must not blur the input — the pick handler needs it
    m.addEventListener("pointerdown", (e) => e.preventDefault());
    m.addEventListener("click", (e) => {
      const it = e.target.closest("[data-addr]");
      if (!it) return;
      input.value = it.dataset.addr;
      closeCombo();
      picking = true;
      try {
        // the same two events a keystroke fires, so nothing downstream needs to
        // know the value came from a menu
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
      } finally {
        picking = false;
      }
      // Focus goes back to the field this pick belongs to — `input`, not a
      // re-query by side, which would find the FIRST such field and put the
      // caret in the wrong row of a two-condition rule. The menu stays shut:
      // the pick was the answer to the question it was asking, and focusin
      // would otherwise re-open it immediately.
      suppressOpen = true;
      input.focus();
      setTimeout(() => { suppressOpen = false; }, 0);
    });
    comboOff = (ev) => {
      if (!m.contains(ev.target) && ev.target !== input) closeCombo();
    };
    setTimeout(() => document.addEventListener("pointerdown", comboOff, true), 0);
    addEventListener("resize", closeCombo);
  }

  const comboHTML = (groups) => groups.map(([name, rows]) =>
    `<div class="grp">${esc(name)}</div>` + rows.map(([addr, desc]) =>
      `<div class="row" data-addr="${esc(addr)}" role="button" tabindex="-1">` +
        `<b>${esc(addr)}</b><span>${esc(desc)}</span></div>`).join("")).join("");

  /** Does this condition want the × field?
   *
   * Only where a multiplier MEANS something: scaling a moving baseline ("twice
   * the average volume") is the whole point of it, and scaling a literal you
   * typed is just a second way to type a different literal. Keeping it off the
   * common row is also what makes the line fit — the label plus the field cost
   * ~80px against a card capped at 560. One predicate, because the row that
   * draws it and the handler that decides to redraw must never disagree.
   */
  const wantsX = (c) => {
    if (["rises_pct", "falls_pct", "changes_pct", "enters", "exits", "is_true"]
        .includes(c.op)) return false;
    const s = String(c.right ?? "").trim();
    return (s !== "" && !isFinite(Number(s))) || c.x != null;
  };

  function condRow(c, i) {
    const isMove = ["rises_pct", "falls_pct", "changes_pct"].includes(c.op);
    const isBand = ["enters", "exits"].includes(c.op);
    const isBool = c.op === "is_true";
    const showX = wantsX(c);
    /* One condition, STACKED — the subject, then the operator, then the target,
     * each on its own line. It was one horizontal line before, which is where
     * every layout problem came from: five controls plus two units in 528px,
     * wrapping unpredictably as the operator changed the field set. A column
     * reads as a sentence, never wraps, and is the shape every charting tool
     * settled on for the same reason. */
    return `<div class="al-cond-row" data-i="${i}">
      <div class="al-line">
        ${combo("left", c.left, "close, rsi(14), …")}
        ${draft.when.length > 1
          ? `<button type="button" class="al-mini danger" data-act="drop-cond"
               title="Remove this condition">${Icons.svg("x", "xs")}</button>`
          : ""}
      </div>
      <div class="al-line">
        ${sel("op", Object.keys(OPLABEL).map((k) => [k, OPLABEL[k]]), c.op,
              "op", OPICON)}
      </div>
      ${isBool ? `<div class="al-line"><span class="al-unit">on a closed bar</span></div>`
        : (isMove
          ? `<div class="al-line">
               <input class="dlg-input tiny" data-f="right"
                 value="${esc(c.right ?? "")}" placeholder="2" title="percent">
               <span class="al-unit">% within</span>
               <input class="dlg-input tiny" data-f="within"
                 value="${esc(c.within || 1)}" title="bars">
               <span class="al-unit">bars</span>
             </div>`
          : `<div class="al-line">
               ${combo("right", c.right, "a price, or an address")}
               ${showX ? `<span class="al-unit">×</span>
                 <input class="dlg-input tiny" data-f="x" value="${esc(c.x ?? "")}"
                   placeholder="1"
                   title="multiply the right side — 2 means twice it">` : ""}
             </div>
             ${isBand ? `<div class="al-line">
               <span class="al-unit">to</span>
               <input class="dlg-input" data-f="right2"
                 value="${esc(c.right2 ?? "")}" placeholder="upper">
             </div>` : ""}`)}
    </div>`;
  }

  /* A label column and a control column, one setting per line — the shape the
   * shared .dlg-body grid was built for, and the shape every charting tool's
   * alert dialog uses. It replaced a sectioned block layout that crammed a
   * condition onto one horizontal line.
   *
   * The grammar reference that used to sit at the bottom is gone: it was a wall
   * of monospace under a dialog whose job is one sentence, and everything it
   * listed is now one click away inside the field it belongs to. */
  function body() {
    const row = (label, ctl) =>
      `<div class="dlg-row"><label>${label}</label>${ctl}</div>`;
    return `
      <div class="dlg-row al-condrow">
        <label>Condition</label>
        <div class="al-stack">
          ${draft.when.map(condRow).join(
            `<div class="al-join">${draft.all ? "and" : "or"}</div>`)}
          <div class="al-addrow">
            <button type="button" class="al-add" data-act="add-cond">
              ${Icons.svg("plus", "xs")} Add condition</button>
            ${draft.when.length > 1
              // Reads as the ACTION, not as a label: "needs all of them" beside
              // a list already showing AND says nothing and looks like a
              // statement you cannot act on.
              ? `<button type="button" class="al-add" data-act="flip-all">
                   fire on ${draft.all ? "any one" : "all"}</button>` : ""}
          </div>
        </div>
      </div>
      <div class="dlg-sep"></div>
      ${row("Interval", sel("interval", IVS.map((v) => [v, v]), draft.interval))}
      ${row("Trigger", sel("freq", FREQS, draft.freq))}
      ${row("Expires", sel("expires_days", EXPIRY, draft.expires_days))}
      ${row("Note", `<input class="dlg-input wide" data-f="note" ` +
            `value="${esc(draft.note)}" placeholder="Optional">`)}`;
  }

  function paint() {
    dlg.querySelector(".dlg-body").innerHTML = body();
    dlg.querySelector(".dlg-title").textContent =
      `${editing ? "Edit alert" : "Alert"} on ${draft.symbol}`;
    DlgKit.dressSelects(dlg);
    // ONLY a card the user has dragged. reclamp() clamps the coordinates it is
    // already pinned at, and an un-pinned card reads those as (0,0) — so
    // calling it after every repaint tore the dialog out of the backdrop's
    // centring and stuck it in the top-left corner. A centred card needs no
    // reclamp: the flexbox re-centres it for free when its height changes.
    if (card && card.pinned()) card.reclamp();
    scheduleCheck();
  }

  /** Ask the server to resolve the rule without arming it, so the dialog can
   *  say what it is looking at RIGHT NOW — and so an unreadable address is
   *  refused here rather than at 09:20 by never firing. */
  function scheduleCheck() {
    clearTimeout(checkTimer);
    checkTimer = setTimeout(runCheck, 350);
  }

  async function runCheck() {
    const box = el("alCheck");
    if (!box) return;
    const rule = toRule();
    if (rule.when.some((c) => c.op !== "is_true"
                              && (c.right === "" || c.right == null))) {
      box.className = "al-check";
      box.textContent = "";
      return;
    }
    box.className = "al-check busy";
    box.textContent = "checking…";
    try {
      const d = await call("/alerts/check", rule);
      const lines = d.conditions.map((c) =>
        `${c.left} ${fmtNum(c.value)} vs ${fmtNum(c.target)}`);
      const feed = (d.feed && d.feed.symbol && d.feed.symbol.note) || "";
      box.className = "al-check ok";
      box.innerHTML =
        `<div class="al-check-now">Now: ${esc(lines.join(" · "))}</div>` +
        (d.already_true
          ? `<div class="al-check-warn">Already true. The alert will wait for ` +
            `this to reset before it can fire.</div>` : "") +
        (feed ? `<div class="al-check-feed">${esc(feed)}</div>` : "");
    } catch (e) {
      box.className = "al-check bad";
      box.textContent = e.message;
    }
  }

  const fmtNum = (v) => (v == null ? "—"
    : Number(v).toLocaleString("en-IN", { maximumFractionDigits: 4 }));

  /** A number stays a number and an address stays a string. That distinction
   *  is load-bearing on the server: its magnitude guard only runs when exactly
   *  one side is a literal, so sending "1420" as text would skip the check
   *  that catches a slipped decimal. */
  const numOrAddr = (v) => {
    const s = String(v).trim();
    return s !== "" && isFinite(Number(s)) ? Number(s) : s;
  };

  function toRule() {
    const out = {
      symbol: draft.symbol, interval: draft.interval, freq: draft.freq,
      all: draft.all, note: draft.note,
      when: draft.when.map((c) => {
        const o = { left: numOrAddr(c.left), op: c.op };
        for (const k of ["right", "right2", "x", "within"]) {
          if (c[k] === "" || c[k] == null) continue;
          o[k] = numOrAddr(c[k]);
        }
        return o;
      }),
    };
    const days = Number(draft.expires_days || 0);
    if (days) out.expires = Math.floor(Date.now() / 1000) + days * 86400;
    return out;
  }

  function build() {
    wrap = document.createElement("div");
    wrap.className = "dlg-wrap";
    wrap.innerHTML = `
      <div class="dlg al-dlg" role="dialog" aria-modal="true">
        <header class="dlg-head">
          <div class="dlg-title"></div>
          <button class="btn icon" data-act="close" title="Close"></button>
        </header>
        <div class="dlg-body"></div>
        <!-- OUTSIDE the body, deliberately. It is a status line about the whole
             rule rather than one of its settings, so it belongs above the
             buttons and must stay put when the form scrolls. It also cannot be
             a row of that grid: as a full-width item among label/control pairs
             it landed in the same row band as the last field and drew over it.
             Filled by runCheck(), so a repaint of the form never clears it. -->
        <div class="al-check" id="alCheck"></div>
        <footer class="dlg-foot">
          <span class="spacer"></span>
          <button class="btn outline" data-act="cancel">Cancel</button>
          <button class="btn cta" data-act="ok">Create</button>
        </footer>
      </div>`;
    document.body.appendChild(wrap);
    dlg = wrap.querySelector(".dlg");
    dlg.querySelector('[data-act="close"]').innerHTML = Icons.svg("x", "sm");
    dlg.addEventListener("click", onClick);
    dlg.addEventListener("input", onEdit);
    dlg.addEventListener("change", onEdit);
    // The address fields open their menu on focus and keep it filtered as you
    // type. `focusin` rather than `focus` because the row is rebuilt on every
    // repaint and this listener lives on the dialog, not on the input.
    dlg.addEventListener("focusin", (e) => {
      const f = e.target.closest("[data-combo]");
      if (f && !suppressOpen) openCombo(f, f.dataset.combo);
      else if (!f) closeCombo();
    });
    dlg.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && comboMenu) {
        // the menu goes first — Escape with a list open means "close the list",
        // not "throw away the alert I am halfway through writing"
        e.stopPropagation();
        return closeCombo();
      }
      if (e.key === "Enter" && e.target.closest("[data-combo]")) {
        e.preventDefault();
        closeCombo();
      }
    });
    card = DlgKit.draggable(dlg, dlg.querySelector(".dlg-head"));
    wrap.addEventListener("pointerdown", (e) => { if (e.target === wrap) close(); });
    addEventListener("keydown", (e) => {
      if (!wrap || !wrap.classList.contains("open")) return;
      if (e.key === "Escape") { e.stopPropagation(); close(); }
    }, true);
  }

  function rowOf(node) {
    const r = node.closest(".al-cond-row");
    return r ? draft.when[Number(r.dataset.i)] : null;
  }

  function onEdit(e) {
    const f = e.target.closest("[data-f]");
    if (!f) return;
    const key = f.dataset.f, val = f.value;
    const c = rowOf(f);
    if (c) {
      c[key] = val;
      // keep the open menu filtered to what has been typed so far — but never
      // re-open the one a pick just closed
      if (f.dataset.combo && e.type === "input" && !picking) {
        openCombo(f, f.dataset.combo, true);
      }
      // the op decides which fields exist, so changing it has to redraw
      if (key === "op") return paint();
      // The × field appears once the right side becomes an address. Compared
      // against WHAT IS ON SCREEN, not against the model: the model was already
      // updated by the `input` event that preceded this one, so a model-vs-model
      // comparison always found them equal and the field never appeared.
      // Redrawn on `change` (blur/Enter/pick) and never on `input` — rebuilding
      // the row mid-word would take the caret with it.
      if (key === "right" && e.type === "change") {
        const row = f.closest(".al-cond-row");
        if (row && wantsX(c) !== !!row.querySelector('[data-f="x"]')) return paint();
      }
      return scheduleCheck();
    }
    draft[key] = val;
    if (key === "interval") return scheduleCheck();
  }

  async function onClick(e) {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const act = b.dataset.act;
    if (act === "close" || act === "cancel") return close();
    if (act === "add-cond") {
      if (draft.when.length >= 4) return toast("Four conditions is the ceiling");
      draft.when.push({ left: "volume", op: "above", right: "avg(volume,20)", x: 2 });
      return paint();
    }
    if (act === "drop-cond") {
      const r = b.closest(".al-cond-row");
      draft.when.splice(Number(r.dataset.i), 1);
      return paint();
    }
    if (act === "flip-all") { draft.all = !draft.all; return paint(); }
    if (act === "pick-left" || act === "pick-right") {
      // the caret is a second door to the same menu the field opens on focus
      const side = act.slice(5);
      const input = b.closest(".al-combo").querySelector("[data-combo]");
      if (comboMenu && comboFor === input) return closeCombo();
      input.focus();
      return openCombo(input, side);
    }
    if (act === "ok") return commit(b);
  }

  async function commit(btn) {
    closeCombo();
    btn.disabled = true;
    const was = btn.textContent;
    btn.textContent = editing ? "Saving…" : "Creating…";
    try {
      if (editing) {
        const r = toRule();
        await patch(editing, { when: r.when, all: r.all, interval: r.interval,
                               freq: r.freq, note: r.note,
                               expires: r.expires || null });
        toast("Alert updated");
      } else {
        const d = await create(toRule());
        const note = d.feed && d.feed.symbol && !d.feed.symbol.streaming
          ? " — " + d.feed.symbol.note : "";
        toast(`Watching ${d.alert.symbol}: ${d.alert.cond} ${d.alert.level}${note}`);
      }
      close();
    } catch (err) {
      const box = el("alCheck");
      if (box) { box.className = "al-check bad"; box.textContent = err.message; }
      btn.disabled = false;
      btn.textContent = was;
    }
  }

  function close() {
    closeCombo();
    if (wrap) wrap.classList.remove("open");
    editing = null;
    clearTimeout(checkTimer);
  }

  /** open({symbol, level, edit}) — the one entry point. `level` prefills the
   *  right side, which is what makes right-click-on-the-chart work. */
  function open(opts = {}) {
    if (!Auth.user) return toast("Sign in to create alerts");
    if (!wrap) build();
    const sym = (opts.symbol || (typeof SYMBOL !== "undefined" ? SYMBOL : "")
                 || "RELIANCE").toUpperCase();
    if (opts.edit) {
      const a = state.alerts.find((x) => x.id === opts.edit);
      if (!a) return toast("That alert is gone");
      editing = a.id;
      draft = {
        symbol: a.symbol, interval: a.interval, freq: a.freq, all: a.all !== false,
        note: a.note || "", expires_days: "0",
        when: (a.when || []).map((c) => Object.assign({}, c)),
      };
    } else {
      editing = null;
      draft = blank(sym);
      if (opts.level != null) {
        draft.when[0].right = Number(opts.level);
        // Which side you are coming from is not a preference — it is a fact
        // about where price is, and getting it backwards arms an alert that
        // fires instantly or never.
        const last = opts.last != null ? Number(opts.last) : null;
        if (last != null) {
          draft.when[0].op = Number(opts.level) > last ? "cross_up" : "cross_down";
        }
      }
      if (opts.left) draft.when[0].left = opts.left;
      if (opts.op) draft.when[0].op = opts.op;
      // an address, not a number — how "alert on this trendline" arrives
      if (opts.right != null) draft.when[0].right = opts.right;
      if (opts.interval && IVS.includes(opts.interval)) {
        draft.interval = opts.interval;
      }
    }
    paint();
    dlg.querySelector('[data-act="ok"]').textContent = editing ? "Save" : "Create";
    dlg.querySelector('[data-act="ok"]').disabled = false;
    wrap.classList.add("open");
    if (card) card.centre();          // after .open, so the flex centring applies
    const first = dlg.querySelector(".dlg-input.right");
    if (first) { first.focus(); first.select(); }
  }

  /* ── boot ────────────────────────────────────────────────────────────── */

  Auth.onChange(() => { load(); stream(); });

  return {
    state,
    onChange(fn) { listeners.push(fn); },
    load, open, quick, patch, remove, toggle, markSeen, toast,
    get unseen() { return state.unseen; },
  };
})();
