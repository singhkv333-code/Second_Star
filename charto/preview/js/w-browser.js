/* Charto preview — the Browser widget: the web, read beside the chart.
 *
 * A research browser, the way ChatGPT's is: you search, you get a list of
 * real pages, and a page opens as clean text rather than as the site.
 *
 *   Search    /web-search — the hosted web search, India-located. Only pages
 *             the search engine itself returned are listed; a summary is the
 *             page's own description where it has one. Wikipedia's best
 *             match (/wiki/search) rides above the results.
 *   Read      /reader — any page as its title, headings and paragraphs;
 *             /wiki/page — a Wikipedia article rebuilt from an allow-list,
 *             its links kept inside the widget. Nothing a site sends runs.
 *   Live      a page that allows framing can also be shown as itself
 *             (/frame-check first), sandboxed.
 */
"use strict";

(() => {
  if (typeof Dock === "undefined") return;
  const { esc, ic, json } = WKit;
  const SUGGEST = ["SEBI circulars this week", "RBI monetary policy", "NIFTY 50", "India CPI inflation", "Upcoming IPOs India"];
  const looksLikeUrl = (s) => /^(https?:\/\/)?[\w-]+(\.[\w-]+)+(:\d+)?(\/\S*)?$/i.test(String(s).trim());
  const WIKI = /^https?:\/\/(en|hi)\.(?:m\.)?wikipedia\.org\/wiki\/([^?#]+)/i;
  const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } };
  const favicon = (u) => { try { return `${new URL(u).origin}/favicon.ico`; } catch { return ""; } };

  function mount(host, ctx) {
    let hist = [], at = -1, cur = null, seq = 0;
    host.innerHTML =
      `<div class="br-bar">` +
        `<button type="button" class="sh-btn i" data-br="back" title="Back">${ic("arrowLeft")}</button>` +
        `<button type="button" class="sh-btn i" data-br="fwd" title="Forward">${ic("arrowRight")}</button>` +
        `<form class="br-url"><span class="br-lock">${ic("search")}</span>` +
          `<input name="u" placeholder="Search the web or enter an address" autocomplete="off" spellcheck="false"></form>` +
        `<button type="button" class="sh-btn i" data-br="home" title="New search">${ic("home")}</button>` +
      `</div>` +
      `<div class="br-view"></div>`;
    const $ = (s) => host.querySelector(s);
    const view = $(".br-view"), form = $(".br-url"), input = form.u;
    const cfg = () => ctx.cfg;

    function prefs() {
      host.style.setProperty("--br-size", { s: "13px", m: "14.5px", l: "16.5px" }[cfg().textSize || "m"]);
      host.classList.toggle("br-noimg", cfg().images === false);
    }

    function paintNav() {
      $('[data-br="back"]').disabled = at <= 0;
      $('[data-br="fwd"]').disabled = at >= hist.length - 1;
    }
    function nav(entry, push = true) {
      if (push) { hist = hist.slice(0, at + 1); hist.push(entry); at = hist.length - 1; }
      ctx.setCfg({ last: entry.kind === "home" ? null : entry });
      paintNav();
      if (entry.kind === "home") return home();
      if (entry.kind === "search") return search(entry.q);
      return page(entry.url, entry.live);
    }
    function go(raw) {
      const s = String(raw || "").trim();
      if (!s) return nav({ kind: "home" });
      if (looksLikeUrl(s)) return nav({ kind: "page", url: /^https?:\/\//i.test(s) ? s : "https://" + s });
      nav({ kind: "search", q: s });
    }

    function home() {
      cur = null; input.value = ""; ctx.setTitle("Browser");
      view.innerHTML = `<div class="br-home">` +
        `<form class="br-big">${ic("search")}<input name="q" placeholder="Search the web" autocomplete="off" spellcheck="false"></form>` +
        `<div class="br-sugs">${SUGGEST.map((q) => `<button type="button" class="br-sug" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>` +
        `<p class="br-note">Pages open as clean text, beside the chart. ${cfg().source === "wiki" ? "Searching Wikipedia only." : "Results come from a web search located in India."}</p></div>`;
      const f = view.querySelector(".br-big");
      f.q.addEventListener("keydown", (e) => e.stopPropagation());
      f.addEventListener("submit", (e) => { e.preventDefault(); go(f.q.value); });
      requestAnimationFrame(() => { if (ctx.visible()) f.q.focus({ preventScroll: true }); });
    }

    async function search(q) {
      const my = ++seq;
      cur = { kind: "search", q };
      input.value = q; ctx.setTitle(q);
      const wikiOnly = cfg().source === "wiki";
      view.innerHTML = `<div class="br-res"><div class="br-busy">${ic("search")}<span>${wikiOnly ? "Searching Wikipedia…" : "Searching the web…"}</span></div>${WKit.skel(8)}</div>`;
      const lang = cfg().lang || "en";
      const [wk, web] = await Promise.all([
        json(`/wiki/search?q=${encodeURIComponent(q)}&lang=${lang}`).catch(() => null),
        wikiOnly ? Promise.resolve(null) : json(`/web-search?q=${encodeURIComponent(q)}`).catch((e) => ({ results: [], error: e.message })),
      ]);
      if (my !== seq) return;
      const wr = (wk && wk.results) || [];
      let html = "";
      if (wikiOnly) {
        html = wr.length ? wr.map((r) => wikiRow(r, lang)).join("") : `<div class="br-none">Wikipedia has no article for “${esc(q)}”.</div>`;
      } else {
        // Wikipedia's best match rides on top only when it is about the query
        const STOP = new Set(["the", "and", "for", "new", "what", "how", "why", "with", "from", "india", "indian", "rules", "latest", "today", "news"]);
        const words = q.toLowerCase().split(/[^\p{L}\p{N}&]+/u).filter((w) => w.length > 2 && !STOP.has(w));
        const top = wr.find((r) => { const t = (r.title || "").toLowerCase(); return words.length && words.filter((w) => t.includes(w)).length >= Math.min(2, words.length); });
        if (top) html += `<div class="br-wiki" data-open="https://${lang}.wikipedia.org/wiki/${esc(encodeURIComponent(top.key || top.title))}">` +
          (top.thumb ? `<img src="${esc(top.thumb)}" alt="" referrerpolicy="no-referrer">` : "") +
          `<div><em>Wikipedia</em><b>${esc(top.title)}</b><span>${esc(top.description || "")}</span></div></div>`;
        const rs = (web && web.results) || [];
        html += rs.map((r) => `<div class="br-hit" data-open="${esc(r.url)}">` +
            `<div class="br-site"><img src="${esc(favicon(r.url))}" alt="" referrerpolicy="no-referrer" onerror="this.style.visibility='hidden'"><span>${esc(r.site || hostOf(r.url))}</span></div>` +
            `<b>${esc(r.title)}</b>` +
            (r.snippet ? `<p${r.described ? "" : ` title="A summary written by the search; open the page to read it"`}>${esc(r.snippet)}</p>` : "") +
          `</div>`).join("");
        if (!rs.length) html += `<div class="br-none">${esc((web && web.error) || "No pages came back for that.")}</div>`;
      }
      view.innerHTML = `<div class="br-res">${html}</div>`;
    }
    const wikiRow = (r, lang) => `<div class="br-hit" data-open="https://${lang}.wikipedia.org/wiki/${esc(encodeURIComponent(r.key || r.title))}">` +
      `<div class="br-site"><span>${lang}.wikipedia.org</span></div><b>${esc(r.title)}</b>` +
      `<p>${esc(r.description || "")}${r.description && r.excerpt ? " — " : ""}${esc(r.excerpt || "").replace(/\[\[(.*?)\]\]/g, "<mark>$1</mark>")}</p></div>`;

    async function page(url, live) {
      const my = ++seq;
      input.value = url;
      ctx.setTitle(hostOf(url));
      view.innerHTML = `<div class="br-wait">${WKit.skel(9)}</div>`;
      if (live || cfg().openAs === "live") {
        let chk;
        try { chk = await json(`/frame-check?url=${encodeURIComponent(url)}`); } catch (e) { chk = { embeddable: false, reason: e.message }; }
        if (my !== seq) return;
        if (chk.embeddable) return showLive(chk.final || url);
        if (live) ctx.toast(chk.reason || "This site does not allow itself to be shown here; showing it as text.");
      }
      const wm = url.match(WIKI);
      if (wm) {
        const d = await json(`/wiki/page?lang=${wm[1]}&title=${encodeURIComponent(decodeURIComponent(wm[2]).replace(/_/g, " "))}`).catch((e) => ({ error: e.message }));
        if (my !== seq) return;
        if (d.error) return fail(url, d.error);
        cur = { kind: "page", url: d.url || url, title: d.title, text: () => view.querySelector(".br-doc").innerText };
        ctx.setTitle(d.title);
        input.value = d.url || url;
        view.innerHTML = `<article class="br-doc wiki">${tools()}` +
          `<h1>${esc(d.title)}</h1>${d.description ? `<p class="br-desc">${esc(d.description)}</p>` : ""}` +
          (d.toc && d.toc.length > 2 ? `<details class="br-toc"><summary>Contents</summary>${d.toc.map((t) => `<a href="#" data-to="${esc(t.id)}">${esc(t.title)}</a>`).join("")}</details>` : "") +
          `<div class="br-wikibody">${d.html}</div><p class="br-lic">${esc(d.license)} · <a href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">wikipedia.org</a></p></article>`;
        view.scrollTop = 0;
        return;
      }
      const d = await json(`/reader?url=${encodeURIComponent(url)}`).catch((e) => ({ error: e.message }));
      if (my !== seq) return;
      if (d.error && !(d.blocks || []).length) return fail(url, d.error);
      cur = { kind: "page", url: d.final || url, title: d.title, text: () => view.querySelector(".br-doc").innerText };
      ctx.setTitle(d.title || hostOf(url));
      const when = d.published ? new Date(d.published) : null;
      view.innerHTML = `<article class="br-doc">${tools()}` +
        `<div class="br-site"><img src="${esc(favicon(d.final || url))}" alt="" referrerpolicy="no-referrer" onerror="this.style.visibility='hidden'"><span>${esc(d.site || hostOf(url))}</span>` +
          (when && !isNaN(when) ? `<span>· ${when.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}</span>` : "") + `</div>` +
        `<h1>${esc(d.title || hostOf(url))}</h1>` +
        (d.thin ? `<div class="br-thin"><p>${d.description ? esc(d.description) + " " : ""}</p>` +
          `<span>This site writes its text with scripts as the page loads, so it cannot be read as text here.</span>` +
          `<div><button type="button" class="sh-btn" data-br="live">${ic("globe")}<span>Try the live page</span></button>` +
          `<button type="button" class="sh-btn" data-br="out">${ic("externalLink")}<span>Open in a new tab</span></button></div></div>` : "") +
        (d.image ? `<img class="br-hero" src="${esc(d.image)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()">` : "") +
        (d.thin ? [] : d.blocks || []).map((b) => b.t === "h" ? `<h3>${esc(b.text)}</h3>` : b.t === "li" ? `<p class="li">${esc(b.text)}</p>`
          : b.t === "q" ? `<blockquote>${esc(b.text)}</blockquote>` : `<p>${esc(b.text)}</p>`).join("") +
        `<p class="br-lic">Shown as text by Pivot's reader. <a href="${esc(d.final || url)}" target="_blank" rel="noopener noreferrer">Open the original</a></p></article>`;
      view.scrollTop = 0;
    }
    const tools = () => `<div class="br-tools">` +
      `<button type="button" class="sh-btn" data-br="live" title="Show the site itself, where it allows it">${ic("globe")}<span>Live page</span></button>` +
      `<button type="button" class="sh-btn" data-br="note" title="Save to notes (the selection, or the page's address)">${ic("note")}<span>Save to notes</span></button>` +
      `<button type="button" class="sh-btn" data-br="ask" title="Ask the chat about this page">${ic("chat")}<span>Ask</span></button>` +
      `<span class="sh-gap"></span>` +
      `<button type="button" class="sh-btn i" data-br="out" title="Open in a new tab">${ic("externalLink")}</button></div>`;
    function showLive(u) {
      cur = { kind: "page", url: u, title: hostOf(u), live: true, text: () => "" };
      const f = document.createElement("iframe");
      f.className = "br-frame";
      f.src = u;
      f.referrerPolicy = "no-referrer";
      f.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox");
      f.setAttribute("allow", "fullscreen");
      view.innerHTML = `<div class="br-livebar"><span>${ic("globe")}${esc(hostOf(u))} · live</span><button type="button" class="sh-btn" data-br="text">Read as text</button></div>`;
      view.appendChild(f);
    }
    function fail(url, why) {
      cur = { kind: "page", url, title: hostOf(url), text: () => "" };
      view.innerHTML = `<div class="side-empty">${Icons.svg("globe")}<p>${esc(why)}</p>` +
        `<a class="dk-cta" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${ic("externalLink")}Open ${esc(hostOf(url))} in a new tab</a></div>`;
    }

    form.addEventListener("submit", (e) => { e.preventDefault(); go(input.value); input.blur(); });
    input.addEventListener("keydown", (e) => e.stopPropagation());
    input.addEventListener("focus", () => input.select());
    host.addEventListener("click", (e) => {
      const q = e.target.closest("[data-q]");
      if (q) return go(q.dataset.q);
      const w = e.target.closest("[data-wiki]");
      if (w) { e.preventDefault(); const lang = (cur && (cur.url.match(WIKI) || [])[1]) || cfg().lang || "en"; return nav({ kind: "page", url: `https://${lang}.wikipedia.org/wiki/${encodeURIComponent(w.dataset.wiki.replace(/ /g, "_"))}` }); }
      const an = e.target.closest("[data-anchor], [data-to]");
      if (an) {
        e.preventDefault();
        const id = an.dataset.to || "w-" + (an.getAttribute("href") || "").slice(1);
        const t = view.querySelector(`[id="${CSS.escape(id)}"]`);
        if (t) view.scrollTo({ top: t.offsetTop - 8, behavior: "smooth" });
        return;
      }
      const o = e.target.closest("[data-open]");
      if (o && !e.target.closest("a[target]")) return nav({ kind: "page", url: o.dataset.open });
      const b = e.target.closest("[data-br]");
      if (!b) return;
      const a = b.dataset.br;
      if (a === "back" && at > 0) return nav(hist[--at], false);
      if (a === "fwd" && at < hist.length - 1) return nav(hist[++at], false);
      if (a === "home") return nav({ kind: "home" });
      if (!cur || cur.kind !== "page") return;
      if (a === "out") return open(cur.url, "_blank", "noopener,noreferrer");
      if (a === "live") return nav({ kind: "page", url: cur.url, live: true });
      if (a === "text") return nav({ kind: "page", url: cur.url });
      if (a === "note") {
        const sel = String(getSelection() || "").trim();
        const body = sel && view.contains(getSelection().anchorNode) ? `<blockquote>${esc(sel.slice(0, 2000))}</blockquote>` : "";
        return ctx.send("notes", { quiet: true, general: true, html: `<p><b>${esc(cur.title || hostOf(cur.url))}</b> — <a href="${esc(cur.url)}">${esc(hostOf(cur.url))}</a></p>${body}` });
      }
      if (a === "ask") {
        const t = cur.text ? cur.text().replace(/\s+\n/g, "\n").slice(0, 3000) : "";
        return ctx.compose(`I'm reading "${cur.title || cur.url}" (${cur.url}).${t ? `\n\n${t}\n\n` : " "}What in it matters for an investor or trader in India? Quote it where you rely on it.`);
      }
    });

    prefs();
    let booted = false;
    return {
      show() {
        if (booted) return;
        booted = true;
        const l = cfg().last || (cfg().url ? { kind: "page", url: cfg().url } : null);
        nav(l || { kind: "home" });
      },
      config(c, patch) { prefs(); if ("source" in patch && cur && cur.kind === "search") search(cur.q); },
      receive(p) { if (p && p.url) nav({ kind: "page", url: p.url, live: !p.reader && cfg().openAs === "live" }); else if (p && p.q) go(p.q); },
      ask: () => cur && cur.kind === "page" ? `I'm reading ${cur.title || cur.url} (${cur.url}). Summarise what matters on it for a trader in India.` : "",
    };
  }

  Dock.register({
    type: "browser", title: "Browser", icon: "globe", hue: "cyan", group: "Tools",
    desc: "Search the web and read pages beside the chart", zone: "right", minW: 320, mount,
    settings: [
      { section: "Search" },
      { key: "source", label: "Search", kind: "seg", def: "web", options: [{ v: "web", label: "The web" }, { v: "wiki", label: "Wikipedia only" }] },
      { key: "lang", label: "Wikipedia in", kind: "seg", def: "en", options: [{ v: "en", label: "English" }, { v: "hi", label: "Hindi" }] },
      { section: "Pages" },
      { key: "openAs", label: "Open pages as", kind: "seg", def: "text", options: [{ v: "text", label: "Clean text" }, { v: "live", label: "The site itself" }],
        hint: "Most sites refuse to be shown inside another app; those open as text" },
      { key: "textSize", label: "Text size", kind: "seg", def: "m", options: [{ v: "s", label: "Small" }, { v: "m", label: "Medium" }, { v: "l", label: "Large" }] },
      { key: "images", label: "Pictures", kind: "toggle", def: true },
      { kind: "note", label: "Searches go through Pivot's server; the pages you open are fetched there and shown as text, so nothing from them runs here." },
    ],
  });
})();
