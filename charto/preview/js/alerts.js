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

  // The everyday shortlist. NOT the vocabulary — `custom` is the door to the
  // rest of it, and the hint under the field is the server's own list.
  const LEFTS = [
    ["close", "Price"], ["high", "Bar high"], ["low", "Bar low"],
    ["volume", "Volume"],
    ["rsi(14)", "RSI (14)"], ["macd().macd", "MACD line"],
    ["sma(50)", "SMA (50)"], ["sma(200)", "SMA (200)"],
    ["vwap()", "VWAP"], ["atr(14)", "ATR (14)"],
    ["day.high", "Today's high"], ["day.low", "Today's low"],
  ];
  const RIGHTS = [
    ["", "a price or value…"],
    ["sma(50)", "SMA (50)"], ["sma(200)", "SMA (200)"], ["vwap()", "VWAP"],
    ["avg(volume,20)", "average volume (20)"],
    ["pday.close", "yesterday's close"], ["pday.high", "yesterday's high"],
    ["pday.low", "yesterday's low"],
    ["day.high", "today's high"], ["day.low", "today's low"],
    ["52w.high", "the 52-week high"], ["52w.low", "the 52-week low"],
    ["poc", "the point of control"], ["vah", "value-area high"],
    ["val", "value-area low"], ["macd().signal", "the MACD signal line"],
  ];
  const OPLABEL = {
    cross: "crossing", cross_up: "crossing up", cross_down: "crossing down",
    above: "above", below: "below",
    rises_pct: "rising % over", falls_pct: "falling % over",
    changes_pct: "moving % over", enters: "entering the band",
    exits: "leaving the band", is_true: "completes",
  };
  const FREQS = [["once", "Only once"], ["per_bar", "Once per bar"],
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

  function sel(name, opts, value, cls = "") {
    return `<select class="dlg-select ${cls}" data-f="${name}">` + opts.map(([v, l]) =>
      `<option value="${esc(v)}"${String(v) === String(value) ? " selected" : ""}>` +
      `${esc(l)}</option>`).join("") + `</select>`;
  }

  function condRow(c, i) {
    const custom = !LEFTS.some(([v]) => v === c.left);
    const isMove = ["rises_pct", "falls_pct", "changes_pct"].includes(c.op);
    const isBand = ["enters", "exits"].includes(c.op);
    const isBool = c.op === "is_true";
    // The multiplier only appears where it MEANS something: scaling a moving
    // baseline ("twice the average volume") is the whole point of it, and
    // scaling a literal you typed is just a second way to type a different
    // literal. Keeping it off the common row is also what makes the line fit —
    // the label plus the field cost ~80px, and the card is capped at 560.
    const rightIsAddr = String(c.right ?? "").trim() !== ""
      && !isFinite(Number(c.right));
    const showX = !isMove && !isBand && (rightIsAddr || c.x != null);
    return `<div class="al-cond-row" data-i="${i}">
      <div class="al-f">
        ${sel("left", LEFTS.concat(custom ? [[c.left, c.left]] : []), c.left, "left")}
        <button type="button" class="al-mini" data-act="custom-left"
          title="Type any address the engine knows — rsi(14), avg(volume,20), draw:D3"
          aria-label="Type a custom address">${Icons.svg("pen", "xs")}</button>
      </div>
      ${sel("op", Object.keys(OPLABEL).map((k) => [k, OPLABEL[k]]), c.op, "op")}
      ${isBool ? "" : `<div class="al-f">
        <input class="dlg-input right" data-f="right" value="${esc(c.right ?? "")}"
          placeholder="${isMove ? "percent" : "price, or an address"}"
          list="alRightList" autocomplete="off">
        ${isMove ? `<span class="al-unit">% in</span>
          <input class="dlg-input tiny" data-f="within" value="${esc(c.within || 1)}"
            title="bars">` : ""}
        ${isBand ? `<span class="al-unit">to</span>
          <input class="dlg-input" data-f="right2" value="${esc(c.right2 ?? "")}"
            placeholder="upper">` : ""}
        ${showX ? `<span class="al-unit">×</span>
          <input class="dlg-input tiny" data-f="x" value="${esc(c.x ?? "")}"
            placeholder="1" title="multiply the right side — 2 means twice it">`
          : ""}
      </div>`}
      ${draft.when.length > 1
        ? `<button type="button" class="al-mini danger" data-act="drop-cond"
             title="Remove this condition">${Icons.svg("x", "xs")}</button>`
        : `<span class="al-mini-gap"></span>`}
    </div>`;
  }

  function body() {
    const grammar = (state.vocab && state.vocab.operands) || {};
    return `
      <div class="al-dlg-body">
        <div class="al-when">${draft.when.map(condRow).join(
          `<div class="al-join">${draft.all ? "and" : "or"}</div>`)}</div>
        <div class="al-addrow">
          <button type="button" class="al-add" data-act="add-cond">
            ${Icons.svg("plus", "xs")} Add a condition</button>
          ${draft.when.length > 1
            // Reads as the ACTION, not as a label: "needs all of them" beside a
            // list already showing AND says nothing and looks like a statement
            // you cannot act on.
            ? `<button type="button" class="al-add" data-act="flip-all">
                 fire on ${draft.all ? "any one instead" : "all of them instead"}
               </button>` : ""}
        </div>

        <div class="al-grid">
          <label>Interval${sel("interval", IVS.map((v) => [v, v]), draft.interval)}</label>
          <label>Trigger${sel("freq", FREQS, draft.freq)}</label>
          <label>Expires${sel("expires_days", EXPIRY, draft.expires_days)}</label>
        </div>
        <label class="al-note">Note
          <input class="dlg-input" data-f="note" value="${esc(draft.note)}"
                 placeholder="why you are watching this — optional"></label>

        <div class="al-check" id="alCheck"></div>
        <details class="al-help">
          <summary>What can go in a field</summary>
          <dl>${Object.keys(grammar).map((k) =>
            `<dt>${esc(k)}</dt><dd>${esc(grammar[k])}</dd>`).join("")}</dl>
        </details>
      </div>
      <datalist id="alRightList">${RIGHTS.filter(([v]) => v).map(([v, l]) =>
        `<option value="${esc(v)}">${esc(l)}</option>`).join("")}</datalist>`;
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
        `${c.left} ${fmtNum(c.value)} vs ${fmtNum(c.target)}` +
        (c.true_now ? " — true now" : ""));
      const feed = (d.feed && d.feed.symbol && d.feed.symbol.note) || "";
      box.className = "al-check ok";
      box.innerHTML =
        `<div class="al-check-now">Right now: ${esc(lines.join(" · "))}</div>` +
        (d.already_true
          ? `<div class="al-check-warn">This is already true — the alert will
             wait for it to reset and happen again.</div>` : "") +
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
      const wasAddr = String(c[key] ?? "").trim() !== "" && !isFinite(Number(c[key]));
      c[key] = val;
      // the op decides which fields exist, so changing it has to redraw
      if (key === "op" || key === "left") return paint();
      // The × field appears once the right side becomes an ADDRESS. Redrawn on
      // `change` (blur/Enter), never on `input` — rebuilding the row under a
      // pointer mid-word would take the caret with it.
      const isAddr = String(val).trim() !== "" && !isFinite(Number(val));
      if (key === "right" && e.type === "change" && isAddr !== wasAddr) {
        return paint();
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
    if (act === "custom-left") {
      const c = rowOf(b);
      const got = prompt(
        "Address for the left side.\n\nAnything the grammar accepts — " +
        "close, high[1], rsi(14), macd().signal, avg(volume,20), day.high, " +
        "52w.high, poc, draw:D3, pattern(bullish_engulfing)", c.left);
      if (got != null && got.trim()) { c.left = got.trim(); paint(); }
      return;
    }
    if (act === "ok") return commit(b);
  }

  async function commit(btn) {
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
    load, open, patch, remove, toggle, markSeen, toast,
    get unseen() { return state.unseen; },
  };
})();
