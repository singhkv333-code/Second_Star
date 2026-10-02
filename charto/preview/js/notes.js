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

  const KEY = (id) => `note:${id}`;
  const scopeKey = (ctx) => ctx.cfg.scope === "general" ? "general" : ctx.pageSymbol();

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

    function load() {
      current = KEY(scopeKey(ctx));
      const v = Store.get(current, null);
      ed.innerHTML = v && v.html ? clean(v.html) : "";
      ed.dataset.ph = ctx.cfg.scope === "general"
        ? "A general note. Ideas, a plan for the week, anything."
        : `Your notes on ${ctx.pageSymbol()}. They come back whenever this chart does.`;
      paintFoot(v && v.at ? `Saved ${when(v.at)}` : "");
      for (const b of host.querySelectorAll("[data-scope]")) {
        b.classList.toggle("on", b.dataset.scope === (ctx.cfg.scope || "chart"));
      }
      host.querySelector('[data-scope="chart"]').textContent = ctx.pageSymbol();
      ctx.setTitle(ctx.cfg.scope === "general" ? "General" : ctx.pageSymbol());
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
      const c = window.__chartoLast ? window.__chartoLast() : { symbol: ctx.pageSymbol() };
      const t = new Date().toLocaleString("en-IN", { day: "2-digit", month: "short",
        hour: "2-digit", minute: "2-digit", hour12: false });
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
        if (text) ctx.compose(`Here are my notes on ${ctx.cfg.scope === "general" ? "the market" : ctx.pageSymbol()}:\n\n${text}\n\nWhat would you check on the chart to test them?`);
      }
    });

    // another tab editing the same note
    addEventListener("storage", (e) => {
      if (e.key === "charto:" + current && document.activeElement !== ed) load();
    });

    return {
      show() { load(); },
      hide() { if (saveT) save(); },
      config() { },
      ask: () => {
        const text = ed.innerText.trim();
        return text ? `Here are my notes:\n\n${text}\n\nWhat would you check on the chart to test them?` : "";
      },
    };
  }

  Dock.register({
    type: "notes", title: "Notes", icon: "note", shortcut: "notes",
    key: "Alt N", desc: "Write beside the chart, stamped with its price",
    zone: "right", single: true, mount,
  });
})();
