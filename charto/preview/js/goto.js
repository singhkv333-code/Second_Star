/* Charto preview — Go to: one box for every widget and workspace action.
 *
 * Ctrl K (⌘K on a Mac), or the header's Go to button. Type a few letters and
 * press Enter:
 *   - an OPEN widget comes forward (its tab, its tile, a flash);
 *   - a CLOSED one opens where it last was, or where it usually lives;
 *   - a widget you can have several of offers "New …" as well;
 *   - and the workspace's own verbs — Widgets, Focus, Fullscreen, Lock the
 *     chart, Chat, Keyboard shortcuts, Reset — sit in the same list.
 *
 * It is the keyboard's way round the workspace now that the drawing rail
 * carries only drawing tools. Nothing here owns state: every row calls the
 * dock (or clicks the real header button), and the list is rebuilt from the
 * dock each time it opens, so it cannot advertise a widget that is gone.
 */
"use strict";

(() => {
  if (typeof Dock === "undefined" || !Dock.catalog) return;
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const ic = (n, cls) => Icons.svg(n, cls);
  const MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  const KEY = MAC ? "⌘K" : "Ctrl K";

  let wrap = null, input = null, list = null, rows = [], at = 0, opener = null;

  /** Every row, freshly read off the dock. */
  function build() {
    const st = Dock.state();
    const opened = Dock.opened();
    const cat = Dock.catalog();
    const out = [];
    for (const o of opened) {
      out.push({ sec: "On the workspace", icon: o.icon, title: o.title, sub: o.sub,
                 hint: o.visible ? "Showing" : "Go to", run: () => Dock.reveal(o.id),
                 words: "open go switch " + o.type });
    }
    for (const w of cat) {
      if (w.single && w.open) continue;              // already listed above
      out.push({ sec: "Add a widget", icon: w.icon, anim: w.anim,
                 title: w.open ? `New ${w.title.toLowerCase()}` : w.title, sub: w.desc,
                 hint: w.key || (w.open ? "New" : "Open"), keyHint: !!w.key,
                 run: () => w.open ? Dock.open(w.type, null, { fresh: true }) : Dock.open(w.type),
                 words: `add new open widget ${w.type} ${w.group}` });
    }
    const chord = (a) => (typeof Shortcuts !== "undefined" && Shortcuts.chord ? Shortcuts.chord(a) : "");
    const chatOn = !document.getElementById("chatPanel")?.classList.contains("hidden");
    out.push(
      { sec: "Workspace", icon: "widgets", title: "Browse all widgets", sub: "The widget drawer, with pictures",
        run: () => Dock.hub(true), words: "hub drawer gallery add" },
      { sec: "Workspace", icon: "focus", title: st.focus ? "Leave focus" : "Focus on the chart",
        sub: "Hide every widget for a moment", hint: chord("focus-chart"), keyHint: true,
        run: () => Dock.setFocus(), words: "zen hide" },
      { sec: "Workspace", icon: st.fullscreen ? "fullscreenExit" : "fullscreen",
        title: st.fullscreen ? "Exit fullscreen" : "Fullscreen", hint: chord("fullscreen"), keyHint: true,
        run: () => Dock.fullscreen(), words: "full screen" },
      { sec: "Workspace", icon: st.lock ? "unlock" : "lock",
        title: st.lock ? "Make the chart movable" : "Lock the chart in place",
        sub: st.lock ? "Drag it anywhere, like any widget" : "No header, no dragging",
        run: () => Dock.setLock(), words: "pin fixed move" },
      { sec: "Workspace", icon: "chat", title: chatOn ? "Hide the chat" : "Show the chat",
        hint: chord("chat"), keyHint: true,
        run: () => document.getElementById("chatToggle")?.click(), words: "conversation assistant ai" },
      { sec: "Workspace", icon: "keyboard", title: "Keyboard shortcuts", hint: chord("shortcuts"), keyHint: true,
        run: () => typeof Shortcuts !== "undefined" && Shortcuts.open && Shortcuts.open(), words: "keys help" },
      { sec: "Workspace", icon: "rotateCw", title: "Reset the workspace", sub: "Close every widget; the chart stays",
        run: () => Dock.reset(), words: "clear default layout" },
    );
    return out;
  }

  /** Every word typed must appear; a title that STARTS with the query wins. */
  function filter(all, q) {
    q = q.trim().toLowerCase();
    if (!q) return all;
    const words = q.split(/\s+/);
    return all
      .map((r) => {
        const hay = `${r.title} ${r.sub || ""} ${r.sec} ${r.words || ""}`.toLowerCase();
        if (!words.every((w) => hay.includes(w))) return null;
        const t = r.title.toLowerCase();
        const score = t.startsWith(q) ? 0 : t.split(/\s+/).some((x) => x.startsWith(words[0])) ? 1 : 2;
        return { r, score };
      })
      .filter(Boolean)
      .sort((a, b) => a.score - b.score)
      .map((x) => x.r);
  }

  function paint() {
    rows = filter(build(), input.value);
    at = Math.min(at, Math.max(0, rows.length - 1));
    let sec = null;
    list.innerHTML = rows.length ? rows.map((r, i) => {
      const head = r.sec !== sec && !input.value.trim() ? `<div class="gt-sec">${esc(r.sec)}</div>` : "";
      sec = r.sec;
      return head +
        `<div class="gt-row${i === at ? " on" : ""}" role="option" aria-selected="${i === at}" data-i="${i}" data-anim="${esc(r.anim || "")}">` +
          `<span class="gt-ic">${ic(r.icon || "widgets")}</span>` +
          `<span class="gt-txt"><b>${esc(r.title)}</b>${r.sub ? `<em>${esc(r.sub)}</em>` : ""}</span>` +
          (r.hint ? (r.keyHint ? `<kbd>${esc(r.hint)}</kbd>` : `<span class="gt-hint">${esc(r.hint)}</span>`) : "") +
        `</div>`;
    }).join("") : `<div class="gt-none">Nothing matches “${esc(input.value.trim())}”.</div>`;
    const on = list.querySelector(".gt-row.on");
    if (on) on.scrollIntoView({ block: "nearest" });
  }

  function move(d) {
    if (!rows.length) return;
    at = (at + d + rows.length) % rows.length;
    for (const r of list.querySelectorAll(".gt-row")) {
      const on = +r.dataset.i === at;
      r.classList.toggle("on", on);
      r.setAttribute("aria-selected", String(on));
      if (on) r.scrollIntoView({ block: "nearest" });
    }
  }

  function run(i) {
    const r = rows[i];
    if (!r) return;
    close();
    try { r.run(); } catch (e) { console.error("[goto]", e); }
  }

  function make() {
    wrap = document.createElement("div");
    wrap.className = "gt-wrap";
    wrap.innerHTML =
      `<div class="gt" role="dialog" aria-modal="true" aria-label="Go to">` +
        `<div class="gt-in">${ic("search")}` +
          `<input type="text" placeholder="Go to a widget, or type what you want to do" autocomplete="off" spellcheck="false" ` +
            `role="combobox" aria-expanded="true" aria-controls="gtList">` +
          `<kbd>Esc</kbd></div>` +
        `<div class="gt-list" id="gtList" role="listbox"></div>` +
        `<div class="gt-foot"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>↵</kbd> open</span>` +
          `<span><kbd>${KEY}</kbd> anywhere</span></div>` +
      `</div>`;
    document.body.appendChild(wrap);
    input = wrap.querySelector("input");
    list = wrap.querySelector(".gt-list");
    input.addEventListener("input", () => { at = 0; paint(); });
    input.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (e.key === "Enter") { e.preventDefault(); run(at); }
      else if (e.key === "Escape") { e.preventDefault(); close(); }
      else if ((e.metaKey || e.ctrlKey) && e.code === "KeyK") { e.preventDefault(); close(); }
    });
    list.addEventListener("pointermove", (e) => {
      const r = e.target.closest(".gt-row");
      if (!r || +r.dataset.i === at) return;
      at = +r.dataset.i;
      for (const x of list.querySelectorAll(".gt-row")) x.classList.toggle("on", x === r);
    });
    list.addEventListener("click", (e) => {
      const r = e.target.closest(".gt-row");
      if (r) run(+r.dataset.i);
    });
    wrap.addEventListener("pointerdown", (e) => { if (e.target === wrap) close(); });
  }

  function open() {
    if (!wrap) make();
    if (wrap.classList.contains("open")) return close();
    Dock.closeMenu();
    if (Dock.state().hub) Dock.hub(false);
    opener = document.activeElement;
    input.value = "";
    at = 0;
    paint();
    wrap.classList.add("open");
    btn && btn.classList.add("on");
    requestAnimationFrame(() => input.focus());
  }
  function close() {
    if (!wrap || !wrap.classList.contains("open")) return;
    wrap.classList.remove("open");
    btn && btn.classList.remove("on");
    if (opener && opener.focus && document.contains(opener)) try { opener.focus({ preventScroll: true }); } catch { }
  }

  /* The header button, beside Widgets. It says its own shortcut, so the
   * keyboard path is learnt by using the mouse one. */
  let btn = null;
  function header() {
    const anchor = document.getElementById("dockBtn");
    if (!anchor || document.getElementById("gotoBtn")) return;
    anchor.insertAdjacentHTML("beforebegin",
      `<button class="btn dk-hbtn gt-btn" id="gotoBtn" type="button" title="Go to a widget or action (${KEY})" ` +
      `aria-label="Go to">${ic("search")}<span>Go to</span><kbd>${KEY}</kbd></button>`);
    btn = document.getElementById("gotoBtn");
    btn.addEventListener("click", (e) => { e.stopPropagation(); open(); });
  }

  if (typeof Shortcuts !== "undefined" && Shortcuts.on) Shortcuts.on("goto", open);
  // the shortcuts dispatcher ignores chords while typing; Ctrl K should work
  // from the chat box too, the way every Go to box on the web does
  addEventListener("keydown", (e) => {
    if (!(e.metaKey || e.ctrlKey) || e.code !== "KeyK" || e.altKey || e.shiftKey) return;
    const t = e.target;
    if (!(t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable))) return;
    if (wrap && wrap.contains(t)) return;
    e.preventDefault();
    open();
  }, true);

  // No header button: the header is kept to what is about the chart. Go to is
  // Ctrl K (⌘K), listed on the shortcuts sheet, and in every widget's reach.
  window.GoTo = { open, close };
})();
