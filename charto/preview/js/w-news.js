/* Charto preview — the News widget.
 *
 * Market headlines from Indian business desks' public RSS feeds, read by the
 * data server (data/webfeeds.py, a fixed list — the page never names a URL).
 * Each headline keeps its source, its time and its link; nothing is
 * rewritten or scored. "This symbol" narrows the list to headlines that
 * name the company the chart is on. A headline opens at its publisher, in a
 * new tab — the article belongs to them.
 */
"use strict";

(() => {
  if (typeof Dock === "undefined") return;
  const { esc, ic, json, ago, empty, skel } = WKit;
  const POLL_MS = 5 * 60_000;
  // the server's fixed list (data/webfeeds.py) — the page never names a URL
  const SOURCES = [["et-markets", "Economic Times"], ["et-stocks", "ET Stocks"], ["mint-markets", "Mint"],
                   ["bl-markets", "BusinessLine"], ["cnbc-market", "CNBC-TV18"]];
  const DEFAULT_SOURCES = ["et-markets", "mint-markets", "bl-markets", "cnbc-market"];

  function mount(host, ctx) {
    let timer = 0, data = null, seq = 0;
    host.innerHTML =
      `<div class="nw-head">` +
        `<div class="dk-seg nw-scope"><button type="button" data-sc="all">All markets</button><button type="button" data-sc="sym"></button></div>` +
        `<span class="sh-gap"></span>` +
        `<button type="button" class="sh-btn i" data-nw="src" title="Sources">${ic("columns")}</button>` +
        `<button type="button" class="sh-btn i" data-nw="refresh" title="Refresh">${ic("rotateCw")}</button>` +
      `</div>` +
      `<div class="nw-search">${Icons.field ? Icons.field('<input type="search" placeholder="Filter headlines" autocomplete="off" spellcheck="false">') : '<input type="search" placeholder="Filter headlines">'}</div>` +
      `<div class="side-body nw-body"></div>` +
      `<div class="nw-foot"></div>`;
    const $ = (s) => host.querySelector(s);
    const body = $(".nw-body");
    const input = host.querySelector(".nw-search input");

    /** The words a headline would use for this company — its name, not its ticker. */
    function nameWords() {
      const s = ctx.symbol();
      const label = typeof Universe !== "undefined" && Universe.label ? Universe.label(s) : s;
      const w = String(label || s).replace(/\b(ltd|limited|india|industries|corporation|company|co)\b\.?/gi, "").trim().split(/\s+/)[0];
      return w && w.length > 2 ? w : s;
    }

    async function load(force) {
      const my = ++seq;
      if (!data || force) body.innerHTML = skel(7, "news");
      const scope = ctx.cfg.scope || "all";
      const q = [scope === "sym" ? nameWords() : "", input.value.trim()].filter(Boolean).join(" ");
      const src = (ctx.cfg.sources || []).join(",");
      try {
        data = await json(`/feeds?limit=80${q ? `&q=${encodeURIComponent(q)}` : ""}${src ? `&sources=${src}` : ""}`);
        if (my !== seq) return;
        paint();
      } catch (e) {
        if (my !== seq) return;
        body.innerHTML = empty("news", `Headlines could not be fetched: ${esc(e.message)}`);
      }
    }

    function paint() {
      for (const b of host.querySelectorAll("[data-sc]")) b.classList.toggle("on", b.dataset.sc === (ctx.cfg.scope || "all"));
      host.querySelector('[data-sc="sym"]').textContent = ctx.symbol();
      if (!data) return;
      // muted words: a headline naming any of them is left out
      const mute = String(ctx.cfg.mute || "").split(",").map((w) => w.trim().toLowerCase()).filter(Boolean);
      const items = (data.items || []).filter((it) => !mute.some((w) => (it.title + " " + (it.summary || "")).toLowerCase().includes(w)));
      host.classList.toggle("nw-compact", ctx.cfg.density === "compact");
      body.innerHTML = items.length ? items.map((it, i) =>
        `<a class="nw-item" href="${esc(it.link)}" target="_blank" rel="noopener noreferrer" data-i="${i}">` +
          (it.image && ctx.cfg.images !== false ? `<span class="nw-img"><img src="${esc(it.image)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.parentNode.remove()"></span>` : "") +
          `<span class="nw-txt"><b>${esc(it.title)}</b>` +
          (ctx.cfg.summaries !== false && it.summary ? `<span class="nw-sum">${esc(it.summary)}</span>` : "") +
          `<span class="nw-meta">${esc(it.source)} · ${esc(ago(it.ts))}</span></span>` +
          `<button type="button" class="nw-ask" data-ask="${data.items.indexOf(it)}" title="Ask in chat about this">${ic("chat")}</button>` +
        `</a>`).join("")
        : empty("news", (ctx.cfg.scope === "sym" ? `No recent headline names ${esc(nameWords())}.` : "No headlines match."));
      const ok = (data.sources || []).filter((s) => s.ok).map((s) => s.name);
      const down = (data.sources || []).filter((s) => !s.ok).map((s) => s.name);
      $(".nw-foot").innerHTML = `<span>${esc(ok.join(", "))}${down.length ? ` · ${esc(down.join(", "))} did not answer` : ""}</span>` +
        `<span>${items.length} headline${items.length === 1 ? "" : "s"}</span>`;
    }

    host.addEventListener("click", (e) => {
      // a headline can open in the Browser widget's reading view instead
      const link = e.target.closest("a.nw-item");
      if (link && !e.target.closest("[data-ask]") && ctx.cfg.openIn === "reader" && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        return ctx.send("browser", { url: link.href, reader: true });
      }
      const askB = e.target.closest("[data-ask]");
      if (askB) {
        e.preventDefault(); e.stopPropagation();
        const it = data.items[+askB.dataset.ask];
        return ctx.compose(`This headline from ${it.source}: "${it.title}". What does it mean for the stocks involved, and did the chart react?`);
      }
      const sc = e.target.closest("[data-sc]");
      if (sc) { ctx.setCfg({ scope: sc.dataset.sc }); return load(true); }
      const b = e.target.closest("[data-nw]");
      if (!b) return;
      e.stopPropagation();
      if (b.dataset.nw === "refresh") return load(true);
      if (b.dataset.nw === "src" && data) {
        const on = new Set(ctx.cfg.sources && ctx.cfg.sources.length ? ctx.cfg.sources : data.sources.map((s) => s.id));
        return ctx.menu(b, [{ head: "Sources" }, ...data.catalog.map((c) => ({ id: c.id, label: c.name, on: on.has(c.id) }))],
          (id) => {
            on.has(id) ? on.delete(id) : on.add(id);
            if (!on.size) return true;
            ctx.setCfg({ sources: [...on] }); load(true);
          });
      }
    });
    let qT = 0;
    input.addEventListener("input", () => { clearTimeout(qT); qT = setTimeout(() => load(true), 350); });
    input.addEventListener("keydown", (e) => e.stopPropagation());

    return {
      show() {
        load(!data);
        clearInterval(timer);
        timer = setInterval(() => { if (document.visibilityState === "visible") load(false); }, Number(ctx.cfg.refresh) || POLL_MS);
      },
      hide() { clearInterval(timer); },
      config(cfg, patch) {
        if ("sources" in patch || "symbol" in patch || "link" in patch || "pin" in patch) return load(true);
        if ("refresh" in patch && timer) { clearInterval(timer); timer = setInterval(() => { if (document.visibilityState === "visible") load(false); }, Number(ctx.cfg.refresh) || POLL_MS); }
        paint();
      },
      ask: () => data && data.items.length
        ? `Here are the latest market headlines:\n${data.items.slice(0, 10).map((it) => `- ${it.title} (${it.source})`).join("\n")}\n\nWhich of these matter for Indian markets today, and why?` : "",
    };
  }

  Dock.register({
    type: "news", title: "News", icon: "news", hue: "orange", group: "Research",
    desc: "Live headlines from Indian market desks", zone: "right", minW: 280, mount,
    linkable: true,
    settings: [
      { section: "Sources" },
      { key: "sources", label: "Desks", kind: "chips", min: 1, def: DEFAULT_SOURCES, options: SOURCES.map(([v, label]) => ({ v, label })),
        hint: "Business Standard and Moneycontrol refuse readers that are not browsers" },
      { key: "mute", label: "Hide headlines that mention", kind: "text", def: "", placeholder: "Words, separated by commas" },
      { key: "refresh", label: "Check for new headlines", def: 300000,
        options: [{ v: 120000, label: "2 min" }, { v: 300000, label: "5 min" }, { v: 900000, label: "15 min" }] },
      { section: "Display" },
      { key: "images", label: "Pictures", kind: "toggle", def: true },
      { key: "summaries", label: "Summaries", kind: "toggle", def: true },
      { key: "density", label: "Rows", def: "comfortable", options: [{ v: "comfortable", label: "Comfortable" }, { v: "compact", label: "Compact" }] },
      { key: "openIn", label: "Open a headline in", def: "tab", hint: "The reading view needs no other tab",
        options: [{ v: "tab", label: "New tab" }, { v: "reader", label: "Browser widget" }] },
    ],
  });
})();
