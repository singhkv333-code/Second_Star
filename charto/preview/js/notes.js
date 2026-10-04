/* Charto preview — the Notes widget.
 *
 * A plain place to think beside the chart. Each note belongs either to the
 * instrument the chart is on (and follows it: open TCS and you see what you
 * wrote about TCS) or to no instrument at all, a general scratchpad. It saves
 * as you type, to this browser, and says so; it never claims a sync it does
 * not do.
 *
 * The one thing it adds to a text box is the STAMP: a line that records what
 * the chart was showing when you wrote — instrument, interval, last price,
 * time — read off the chart, so a note made in March can be checked against
 * what actually happened next.
 *
 * Text is edited as rich text but stored as sanitised HTML: only the handful
 * of tags the toolbar can make survive a paste or a reload. Nothing pasted can
 * bring a script, a style or an image along.
 */
"use strict";

(() => {
  if (typeof Dock === "undefined") return;
  const esc = (s) => String(s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const ic = (n) => Icons.svg(n, "xs");

  const ALLOWED = new Set(["P", "DIV", "BR", "B", "STRONG", "I", "EM", "U", "H3", "UL", "OL", "LI",
                           "SPAN", "BLOCKQUOTE"]);
  /** Rebuild the tree keeping only allowed tags and no attributes but the
   *  two the toolbar sets itself (a checklist item's state, a stamp's class). */
  function clean(html) {
    const src = new DOMParser().parseFromString(`<div>${html}</div>`, "text/html").body.firstChild;
    const out = document.createElement("div");
    const walk = (from, to) => {
      for (const n of from.childNodes) {
        if (n.nodeType === 3) { to.appendChild(document.createTextNode(n.textContent)); continue; }
        if (n.nodeType !== 1) continue;
        if (!ALLOWED.has(n.tagName)) { walk(n, to); continue; }
        const c = document.createElement(n.tagName === "STRONG" ? "B" : n.tagName === "EM" ? "I" : n.tagName);
        if (n.tagName === "LI" && n.dataset.done != null) c.dataset.done = n.dataset.done === "1" ? "1" : "0";
        if (n.tagName === "UL" && n.classList.contains("nt-check")) c.className = "nt-check";
        if (n.tagName === "SPAN" && n.classList.contains("nt-stamp")) { c.className = "nt-stamp"; c.contentEditable = "false"; }
        to.appendChild(c);
        walk(n, c);
      }
    };
    walk(src, out);
    return out.innerHTML;
  }

  // The first notes widget keeps the plain keys; every further copy is its
  // own page, so two notes side by side never write over each other.
  const KEY = (scope, ctx) => ctx.id === "notes" ? `note:${scope}` : `note:${scope}:${ctx.id.split(":")[1]}`;
  // a note is about the widget's symbol: the chart's, its link group's, or a pin
  const scopeKey = (ctx) => ctx.cfg.scope === "general" ? "general" : ctx.symbol();

  function mount(host, ctx) {
    if (!ctx.cfg.scope) ctx.setCfg({ scope: "chart" });
    let saveT = 0, current = null;

    host.innerHTML =
      `<div class="side-head nt-head">` +
        `<div class="dk-seg nt-scope" role="tablist">` +
          `<button type="button" data-scope="chart"></button>` +
          `<button type="button" data-scope="general">General</button>` +
        `</div><div class="spacer"></div>` +
        `<button type="button" class="side-act" data-n="ask" title="Send this note to the chat" aria-label="Send to chat">${ic("chat")}</button>` +
      `</div>` +
      `<div class="nt-bar" role="toolbar" aria-label="Formatting">` +
        `<button type="button" data-cmd="h3" title="Heading">${ic("heading")}</button>` +
        `<button type="button" data-cmd="bold" title="Bold (Ctrl B)">${ic("bold")}</button>` +
        `<button type="button" data-cmd="italic" title="Italic (Ctrl I)">${ic("italic")}</button>` +
        `<span class="sep"></span>` +
        `<button type="button" data-cmd="ul" title="Bulleted list">${ic("listBullet")}</button>` +
        `<button type="button" data-cmd="check" title="Checklist">${ic("listChecks")}</button>` +
        `<span class="sep"></span>` +
        `<button type="button" data-cmd="stamp" class="nt-stampbtn" title="Stamp what the chart shows now">${ic("stamp")}<span>Stamp</span></button>` +
      `</div>` +
      `<div class="nt-page side-body"><div class="nt-ed" contenteditable="true" spellcheck="true" ` +
        `role="textbox" aria-multiline="true"></div></div>` +
      `<div class="nt-foot"><span class="nt-state"></span><span class="nt-count"></span></div>`;
    const $ = (s) => host.querySelector(s);
    const ed = $(".nt-ed");

    function prefs() {
      const c = ctx.cfg;
      host.style.setProperty("--nt-size", { s: "13px", m: "14.5px", l: "16.5px" }[c.textSize || "m"]);
      host.classList.toggle("nt-nobar", c.toolbar === false);
      host.classList.toggle("nt-nocount", c.count === false);
      ed.spellcheck = c.spell !== false;
    }

    function load() {
      prefs();
      current = KEY(scopeKey(ctx), ctx);
      const v = Store.get(current, null);
      ed.innerHTML = v && v.html ? clean(v.html) : "";
      ed.dataset.ph = ctx.cfg.scope === "general"
        ? "A general note. Ideas, a plan for the week, anything."
        : `Your notes on ${ctx.symbol()}. They come back whenever this chart does.`;
      paintFoot(v && v.at ? `Saved ${when(v.at)}` : "");
      for (const b of host.querySelectorAll("[data-scope]")) {
        b.classList.toggle("on", b.dataset.scope === (ctx.cfg.scope || "chart"));
      }
      host.querySelector('[data-scope="chart"]').textContent = ctx.symbol();
      ctx.setTitle(ctx.cfg.scope === "general" ? "General" : ctx.symbol());
    }

    const when = (t) => {
      const d = new Date(t), now = new Date();
      return d.toDateString() === now.toDateString()
        ? d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false })
        : d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
    };
    function paintFoot(state) {
      const words = (ed.innerText.trim().match(/\S+/g) || []).length;
      $(".nt-state").textContent = state;
      $(".nt-count").textContent = words ? `${words} word${words === 1 ? "" : "s"}` : "";
    }

    function save() {
      clearTimeout(saveT);
      const html = clean(ed.innerHTML);
      if (!ed.innerText.trim()) Store.del(current);
      else Store.set(current, { html, at: Date.now() });
      paintFoot(ed.innerText.trim() ? "Saved on this device" : "");
    }
    ed.addEventListener("input", () => {
      paintFoot("Saving…");
      clearTimeout(saveT);
      saveT = setTimeout(save, 450);
    });
    ed.addEventListener("blur", () => { if (saveT) save(); });
    addEventListener("pagehide", () => { if (saveT) save(); });
    // keys typed here belong to the note — not to the chart's symbol search
    // or a drawing tool's chord
    ed.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { ed.blur(); return; }
      e.stopPropagation();
    });
    ed.addEventListener("paste", (e) => {
      e.preventDefault();
      const html = e.clipboardData.getData("text/html");
      const text = e.clipboardData.getData("text/plain");
      document.execCommand("insertHTML", false,
        html ? clean(html) : esc(text).replace(/\n/g, "<br>"));
    });
    // a checklist item is ticked by clicking its box (the ::before)
    ed.addEventListener("click", (e) => {
      const li = e.target.closest(".nt-check > li");
      if (!li || e.offsetX > 20) return;
      li.dataset.done = li.dataset.done === "1" ? "0" : "1";
      save();
    });

    function stamp() {
      const c = window.__chartoLast ? window.__chartoLast() : { symbol: ctx.symbol() };
      const t = ctx.cfg.stampTime === false ? new Date().toLocaleDateString("en-IN", { day: "2-digit", month: "short" })
        : new Date().toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
      const px = c.close != null ? Sym.of(c.symbol).num(c.close) : "no price loaded";
      ed.focus();
      document.execCommand("insertHTML", false,
        `<span class="nt-stamp" contenteditable="false">${esc(c.symbol)} · ${esc(c.interval || "")} · ` +
        `${esc(px)} · ${esc(t)}</span>&nbsp;`);
    }

    function cmd(c) {
      ed.focus();
      if (c === "bold" || c === "italic") document.execCommand(c);
      else if (c === "h3") {
        const block = document.queryCommandValue("formatBlock").toLowerCase();
        document.execCommand("formatBlock", false, block === "h3" ? "p" : "h3");
      } else if (c === "ul") document.execCommand("insertUnorderedList");
      else if (c === "check") {
        document.execCommand("insertUnorderedList");
        const sel = getSelection();
        const ul = sel.anchorNode && (sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentNode).closest("ul");
        if (ul && ed.contains(ul)) {
          ul.classList.add("nt-check");
          for (const li of ul.children) if (li.dataset.done == null) li.dataset.done = "0";
        }
      } else if (c === "stamp") stamp();
      save();
    }

    host.addEventListener("mousedown", (e) => {
      // toolbar presses must not take the caret out of the note
      if (e.target.closest(".nt-bar button")) e.preventDefault();
    });
    host.addEventListener("click", (e) => {
      const b = e.target.closest("[data-cmd]");
      if (b) return cmd(b.dataset.cmd);
      const sc = e.target.closest("[data-scope]");
      if (sc && sc.dataset.scope !== ctx.cfg.scope) {
        if (saveT) save();
        ctx.setCfg({ scope: sc.dataset.scope });
        return load();
      }
      if (e.target.closest('[data-n="ask"]')) {
        const text = ed.innerText.trim();
        if (text) ctx.ask(noteAsk(text));
      }
    });

    function noteAsk(text) {
      const on = ctx.cfg.scope === "general" ? "the market" : ctx.symbol();
      const words = text.split(/\s+/).filter(Boolean).length;
      return { sub: `On ${on} · ${words} word${words === 1 ? "" : "s"}`,
        context: `My notes on ${on}:\n${text.slice(0, 5000)}`,
        question: "What would you check on the chart to test these notes?" };
    }

    // another tab editing the same note
    addEventListener("storage", (e) => {
      if (e.key === "charto:" + current && document.activeElement !== ed) load();
    });

    return {
      /** Another widget files a line into a note: { symbol | general, html }.
       *  It lands at the end of that symbol's note (or the general one),
       *  whether or not that note is the one on screen. */
      receive(p) {
        if (!p || !p.html) return;
        if (saveT) save();
        const key = KEY(p.general ? "general" : String(p.symbol || scopeKey(ctx)).toUpperCase(), ctx);
        const v = Store.get(key, null);
        Store.set(key, { html: clean((v && v.html || "") + p.html), at: Date.now() });
        if (key === current) load();
        ctx.toast(p.general ? "Saved to your general note." : `Saved to your notes on ${String(p.symbol).toUpperCase()}.`);
      },
      show() { load(); },
      hide() { if (saveT) save(); },
      config(cfg, patch) {
        if ("symbol" in patch || "link" in patch || "pin" in patch) { if (saveT) save(); return load(); }
        prefs();
      },
      ask: () => {
        const text = ed.innerText.trim();
        return text ? noteAsk(text) : null;
      },
    };
  }

  Dock.register({
    type: "notes", title: "Notes", icon: "note", shortcut: "notes",
    key: "Alt N", desc: "Write beside the chart, stamped with its price",
    zone: "right", minW: 260, hue: "gold", group: "Tools", mount, linkable: true,
    settings: [
      { section: "Writing" },
      { key: "textSize", label: "Text size", def: "m", options: [{ v: "s", label: "Small" }, { v: "m", label: "Medium" }, { v: "l", label: "Large" }] },
      { key: "spell", label: "Check spelling", kind: "toggle", def: true },
      { key: "stampTime", label: "Stamps carry the time", kind: "toggle", def: true, hint: "Off: the date only" },
      { section: "Display" },
      { key: "toolbar", label: "Formatting bar", kind: "toggle", def: true },
      { key: "count", label: "Word count", kind: "toggle", def: true },
      { kind: "note", label: "Notes on a symbol come back whenever that symbol is open. They are kept in this browser." },
    ],
  });
})();
