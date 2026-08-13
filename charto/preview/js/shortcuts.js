/* Charto preview — the keyboard, and the sheet that lists it.
 *
 * ONE catalogue, two consumers: the dispatcher that actually fires the
 * shortcut, and the "Keyboard shortcuts" dialog that tells the reader it
 * exists. That is the whole point of the file. A shortcuts sheet maintained
 * beside the handlers is a promise the app stops keeping the first time a
 * binding moves — it goes on advertising Alt+F for a tool that no longer
 * answers, and a printed lie about the keyboard is worse than no sheet.
 *
 * The bindings are TradingView's, because the reader's hands already know
 * them: Alt for a drawing tool, Ctrl+Z/Ctrl+Y for the stack, Alt+R for the
 * view, type letters for a symbol and digits for an interval. Where charto
 * has something TradingView does not (the chat), it takes an unclaimed
 * chord and says so by living under its own heading.
 *
 * NOTHING here knows how the app works. Every row names an `act`, and the
 * modules that own those verbs register a handler for it — js/main.js does
 * nearly all of them at the foot of its boot. A row whose act has no handler
 * is simply not listed and not dispatched: the sheet cannot advertise a key
 * that would do nothing, which is the same rule the rest of this app follows
 * about saying things it cannot back up.
 *
 * Two rows are DOCUMENTED but not dispatched (`owner`): Delete and Escape
 * belong to js/drawings.js, which has the selection and the open draft in
 * hand and must answer them from there. They are on the sheet because the
 * reader does not care which module owns a key.
 *
 * Matching is on `e.code`, never `e.key` — with Alt held, a Windows layout
 * reports the composed character (and a dead key on several European
 * layouts), so the physical key is the only thing that means "T" everywhere
 * this runs. The free-text rows (symbol, interval) are the exception: those
 * read `e.key`, because there the CHARACTER is the payload.
 */
"use strict";

const Shortcuts = (() => {
  const MAC = /Mac|iPhone|iPod|iPad/i.test(
    (navigator.userAgentData && navigator.userAgentData.platform)
    || navigator.platform || navigator.userAgent || "");

  /* ── the catalogue ───────────────────────────────────────────────────
   * `keys` is what the sheet prints and what the dispatcher parses — they
   * cannot disagree, because there is only one of them. A row with two
   * entries in `keys` accepts either (Ctrl+Y and Ctrl+Shift+Z are both
   * redo, the way TradingView takes both).
   *
   * `free` marks the two rows that are not a chord at all but a way of
   * typing, and carries the words the sheet prints in place of key caps.
   */
  const SECTIONS = [
    { heading: "Chart", rows: [
      { act: "symbol", label: "Symbol search",
        free: "Type any letter" },
      { act: "interval", label: "Change the interval",
        free: "Type a number, then Enter" },
      { act: "reset-view", label: "Reset the chart view", keys: ["Alt + R"] },
      { act: "invert", label: "Invert the price scale", keys: ["Alt + I"] },
      { act: "snapshot", label: "Take a snapshot", keys: ["Alt + S"] },
    ] },

    /* The tool rows carry `tool` instead of an act: one handler arms any of
     * them, so the catalogue can grow a row without the app growing a
     * branch. The letters are TradingView's own. */
    { heading: "Drawing tools", rows: [
      { act: "tool", tool: "trend", label: "Trend line", keys: ["Alt + T"] },
      { act: "tool", tool: "hline", label: "Horizontal line", keys: ["Alt + H"] },
      { act: "tool", tool: "hray", label: "Horizontal ray", keys: ["Alt + J"] },
      { act: "tool", tool: "vline", label: "Vertical line", keys: ["Alt + V"] },
      { act: "tool", tool: "crossline", label: "Crossline", keys: ["Alt + C"] },
      { act: "tool", tool: "fib", label: "Fib retracement", keys: ["Alt + F"] },
      { act: "tool", tool: "channel", label: "Parallel channel", keys: ["Alt + P"] },
      { act: "magnet", label: "Magnet — snap to OHLC", keys: ["Ctrl + Alt + M"] },
    ] },

    { heading: "Working with drawings", rows: [
      { act: "undo", label: "Undo", keys: ["Ctrl + Z"] },
      { act: "redo", label: "Redo", keys: ["Ctrl + Y", "Ctrl + Shift + Z"] },
      { act: "delete", label: "Remove the selected drawing",
        keys: ["Delete", "Backspace"], owner: "drawings" },
      { act: "fold", label: "Hide / show every drawing", keys: ["Ctrl + Alt + H"] },
      { act: "escape", label: "Cancel the drawing, or deselect",
        keys: ["Esc"], owner: "drawings" },
    ] },

    { heading: "Panels", rows: [
      { act: "watchlist", label: "Watchlist", keys: ["Alt + W"] },
      { act: "alerts", label: "Alerts", keys: ["Alt + A"] },
      { act: "chat", label: "Chat", keys: ["Ctrl + Alt + C"] },
    ] },

    { heading: "General", rows: [
      /* Saving the desk. It lived in js/layouts.js as a keydown of its own,
       * which is exactly the drift this file exists to prevent: the menu
       * advertised ⌘S on every platform, the sheet did not list it at all,
       * and it fired while you were typing in the chat — the one guard the
       * dispatcher here applies to every other chord. */
      { act: "save-layout", label: "Save layout", keys: ["Ctrl + S"] },
      { act: "shortcuts", label: "Keyboard shortcuts", keys: ["Ctrl + /"],
        always: true },
    ] },
  ];

  /* Typed interval tokens → the interval ids this app can actually fetch.
   * TradingView's convention: minutes as a bare number, and D/W/M for the
   * long ones. Uppercased before the lookup, so "1m" and "1M" cannot both
   * mean minute — a typed M is a month, which is what the M cap on
   * TradingView's own box means. */
  const IV_TOKENS = {
    "1": "1m", "5": "5m", "15": "15m", "30": "30m", "60": "1h",
    "D": "1d", "1D": "1d", "W": "1w", "1W": "1w", "M": "1mo", "1M": "1mo",
  };
  const IV_NAMES = {
    "1m": "1 minute", "5m": "5 minutes", "15m": "15 minutes",
    "30m": "30 minutes", "1h": "1 hour", "1d": "1 day",
    "1w": "1 week", "1mo": "1 month",
  };

  // ── parsing a printed combo into something matchable ────
  const NAMED = {
    DELETE: "Delete", BACKSPACE: "Backspace", ESC: "Escape",
    ESCAPE: "Escape", ENTER: "Enter", "/": "Slash",
  };

  /** "Ctrl + Shift + Z" → { ctrl, alt, shift, code }. Ctrl means "Ctrl OR
   *  the Command key": every chord it appears in is one a Mac hand plays
   *  with ⌘, and the sheet prints ⌘ there for the same reason. */
  function parse(combo) {
    const parts = combo.split("+").map((p) => p.trim()).filter(Boolean);
    const spec = { ctrl: false, alt: false, shift: false, code: null };
    for (const p of parts) {
      const u = p.toUpperCase();
      if (u === "CTRL") spec.ctrl = true;
      else if (u === "ALT") spec.alt = true;
      else if (u === "SHIFT") spec.shift = true;
      else if (NAMED[u]) spec.code = NAMED[u];
      else if (/^[A-Z]$/.test(u)) spec.code = `Key${u}`;
      else if (/^[0-9]$/.test(u)) spec.code = `Digit${u}`;
    }
    return spec.code ? spec : null;
  }

  /** The key caps for one printed combo, mac-mapped. */
  function caps(combo) {
    return combo.split("+").map((p) => p.trim()).filter(Boolean).map((p) => {
      const u = p.toUpperCase();
      if (u === "CTRL") return MAC ? "⌘" : "Ctrl";
      if (u === "ALT") return MAC ? "⌥" : "Alt";
      if (u === "SHIFT") return MAC ? "⇧" : "Shift";
      if (u === "BACKSPACE") return MAC ? "⌫" : "Backspace";
      return p;
    });
  }

  // ── handlers ────────────────────────────────────────────
  const acts = new Map();

  /** Claim a verb. Called by whichever module owns it — see the file header
   *  for why a verb with no claimant is left off the sheet entirely. */
  function on(act, fn) { acts.set(act, fn); }
  const has = (act) => acts.has(act);
  function run(act, arg) {
    const fn = acts.get(act);
    if (!fn) return false;
    try { return fn(arg) !== false; } catch (e) { console.error(e); return false; }
  }

  /** The rows a reader should actually be shown: documented-elsewhere rows
   *  stay (their owner answers them), unclaimed ones go. */
  const live = (r) => !!r.owner || has(r.act);
  const sections = () => SECTIONS
    .map((s) => ({ heading: s.heading, rows: s.rows.filter(live) }))
    .filter((s) => s.rows.length);

  // ── the guard ───────────────────────────────────────────
  const typing = (t) => !!t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)
    || t.isContentEditable);

  /** Which modal, if any, is over the chart. Anything modal takes the
   *  keyboard: Alt+T behind an open Settings card would arm a tool the
   *  reader cannot see, on a chart they are not looking at — and a letter
   *  typed on the sign-in screen must not open a symbol search underneath
   *  it. The two settings dialogs are .dlg-wrap; the sign-in screen is its
   *  own full-bleed surface, and it counts for exactly the same reason. */
  const MODAL = ".dlg-wrap.open, .auth-screen.open";
  const openDlg = () => document.querySelector(MODAL);
  const ours = () => wrap && wrap.classList.contains("open");

  function blocked(e, row) {
    if (typing(e.target)) return true;
    const d = openDlg();
    // our own sheet is the one dialog Ctrl+/ may reach through — it is how
    // the chord closes what it opened
    if (d && !(row && row.always && d === wrap)) return true;
    return false;
  }

  // ── the interval quick-entry ────────────────────────────
  /* TradingView's box: type 1 5 Enter and the chart is on 15 minutes. It is
   * the one shortcut in here that needs a surface of its own, because a
   * chord commits on keydown and this commits on Enter — so the reader has
   * to be able to see what they have typed so far, and what it will mean.
   *
   * It says the RESOLVED interval under the digits ("15 minutes") rather
   * than waiting for Enter to reveal it. A quick-entry that accepts three
   * keystrokes in silence and then does nothing is indistinguishable from a
   * broken one. */
  let ivBox = null, ivBuf = "";

  function ivResolve() {
    return IV_TOKENS[ivBuf.toUpperCase()] || null;
  }

  function ivPaint() {
    if (!ivBox) return;
    const iv = ivResolve();
    ivBox.querySelector(".sc-iv-in").textContent = ivBuf || "—";
    const note = ivBox.querySelector(".sc-iv-note");
    note.textContent = iv ? IV_NAMES[iv] || iv : "no such interval";
    note.classList.toggle("bad", !iv);
  }

  function ivOpen() {
    if (ivBox) return;
    ivBox = document.createElement("div");
    ivBox.className = "sc-iv";
    ivBox.innerHTML = '<span class="sc-iv-in"></span>'
      + '<span class="sc-iv-note"></span>';
    // Centred on the CHART, not on the window: the chat pane owns the right
    // third of a desktop layout, and a box centred on the viewport sits half
    // over the conversation — beside the one thing it is not about.
    (document.getElementById("stage") || document.body).appendChild(ivBox);
  }

  function ivClose() {
    if (ivBox) { ivBox.remove(); ivBox = null; }
    ivBuf = "";
  }

  /** The quick-entry's own keyboard, while it is open. Returns true when it
   *  consumed the press — every key it accepts is one the rest of the
   *  dispatcher must not also see. */
  function ivKey(e) {
    if (!ivBox) return false;
    if (e.key === "Escape") { ivClose(); return true; }
    if (e.key === "Backspace") {
      ivBuf = ivBuf.slice(0, -1);
      if (!ivBuf) ivClose(); else ivPaint();
      return true;
    }
    if (e.key === "Enter") {
      const iv = ivResolve();
      // An interval the loader cannot fetch is not applied and not
      // pretended: the box stays up saying so, and Esc or a Backspace is
      // the way out. Firing the nearest one instead would put the chart on
      // a timeframe nobody asked for.
      if (iv && run("interval", iv)) ivClose();
      return true;
    }
    if (/^[0-9]$/.test(e.key) || /^[dwmhDWMH]$/.test(e.key)) {
      if (ivBuf.length < 4) ivBuf += e.key;
      ivPaint();
      return true;
    }
    return false;
  }

  // ── the dispatcher ──────────────────────────────────────
  /* Capture phase, for the reason js/main.js's undo handler already gives:
   * nothing downstream should be able to eat a chord the sheet has printed.
   * The guard above is what keeps that from stealing keys out of a field. */
  addEventListener("keydown", (e) => {
    if (e.isComposing) return;

    // the quick-entry, first and alone: while it is up it owns the digits
    if (ivBox && !typing(e.target)) {
      if (ivKey(e)) { e.preventDefault(); e.stopPropagation(); }
      return;
    }

    for (const s of SECTIONS) {
      for (const r of s.rows) {
        if (r.owner || !r.keys || !has(r.act)) continue;
        for (const combo of r.keys) {
          const spec = parse(combo);
          if (!spec || e.code !== spec.code) continue;
          if (spec.ctrl !== (e.ctrlKey || e.metaKey)) continue;
          if (spec.alt !== e.altKey) continue;
          if (spec.shift !== e.shiftKey) continue;
          if (blocked(e, r)) return;
          e.preventDefault();
          e.stopPropagation();
          run(r.act, r.tool);
          return;
        }
      }
    }

    // ── the two typed rows ──
    // Bare keys, so every modifier disqualifies them — Alt+S is a snapshot,
    // not the letter S, and the loop above has already had its chance.
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (typing(e.target) || openDlg()) return;
    if (/^[0-9]$/.test(e.key) && has("interval")) {
      e.preventDefault();
      ivOpen(); ivBuf = e.key; ivPaint();
      return;
    }
    if (/^[a-zA-Z]$/.test(e.key) && has("symbol")) {
      // The letter is NOT forwarded into the search box. It opens the
      // picker and the picker takes it from there — seeding the field would
      // mean owning a second copy of "what the search does with a
      // keystroke", which is js/main.js's job and only its.
      e.preventDefault();
      run("symbol");
    }
  }, true);

  // ── the sheet ───────────────────────────────────────────
  let wrap = null, dlg = null, card = null;

  const esc = (s) => String(s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function rowHTML(r) {
    const keys = r.free
      ? `<span class="sc-free">${esc(r.free)}</span>`
      : r.keys.map((combo) => caps(combo)
          .map((k) => `<kbd>${esc(k)}</kbd>`).join(""))
        .join('<span class="sc-or">or</span>');
    return `<div class="sc-row"><span class="sc-label">${esc(r.label)}</span>`
      + `<span class="sc-keys">${keys}</span></div>`;
  }

  function build() {
    wrap = document.createElement("div");
    wrap.className = "dlg-wrap";
    wrap.innerHTML = `
      <div class="dlg shortcuts" role="dialog" aria-modal="true"
           aria-label="Keyboard shortcuts">
        <header class="dlg-head">
          <div class="dlg-title">Keyboard shortcuts</div>
          <button class="btn icon" data-act="close" title="Close"></button>
        </header>
        <div class="dlg-body"></div>
      </div>`;
    document.body.appendChild(wrap);
    dlg = wrap.querySelector(".dlg");
    dlg.querySelector('[data-act="close"]').innerHTML = Icons.svg("x", "sm");
    dlg.addEventListener("click", (e) => {
      if (e.target.closest('[data-act="close"]')) close();
    });
    card = DlgKit.draggable(dlg, dlg.querySelector(".dlg-head"));
    wrap.addEventListener("pointerdown", (e) => { if (e.target === wrap) close(); });
    // Escape, from the sheet itself. It is not in the dispatcher above
    // because Escape belongs to whatever is on top, and while this is open
    // that is this.
    addEventListener("keydown", (e) => {
      if (!ours()) return;
      if (e.key === "Escape") { e.stopPropagation(); close(); }
    }, true);
  }

  /** Rebuilt on every open rather than once: which rows are live depends on
   *  who has registered, and a sheet built at boot would be the set of verbs
   *  claimed by whoever happened to run first. */
  function render() {
    dlg.querySelector(".dlg-body").innerHTML = sections().map((s) =>
      `<div class="dlg-group">${esc(s.heading)}</div>`
      + s.rows.map(rowHTML).join("")).join("");
  }

  function open() {
    if (!wrap) build();
    render();
    card.centre();
    wrap.classList.add("open");        // it must be laid out to have a size
    card.pinCentred();
  }

  function close() { if (wrap) wrap.classList.remove("open"); }
  function toggle() { if (ours()) close(); else open(); }

  // The one verb this module owns outright, so Ctrl + / works in a build
  // where nothing else has registered anything yet.
  on("shortcuts", toggle);

  /** The chord for an act, printed the way a MENU row prints one — "Ctrl + S",
   *  or "⌘ + S" on a Mac. The sheet sets each cap in its own <kbd>; a menu
   *  row has one faint trailing span (.sc) and joins them with a plus, which
   *  is what js/main.js's tool flyout already does.
   *
   *  It reads the same catalogue for the same reason everything else here
   *  does: a menu that printed its own copy of a chord would be a second
   *  place for the keyboard to be described, and the first one to go stale. */
  function chord(act) {
    for (const s of SECTIONS) {
      for (const r of s.rows) {
        if (r.act === act && r.keys && r.keys.length) {
          return caps(r.keys[0]).join(" + ");
        }
      }
    }
    return "";
  }

  return {
    on, has, sections, chord, open, close, toggle,
    isOpen: () => ours(),
  };
})();
