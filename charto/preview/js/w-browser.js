/* Charto preview — the Browser widget: the web, beside the chart.
 *
 *   Search    /web-search — a search engine API (Brave, or our own SearXNG;
 *             charto/data/websearch.py). Real titles and snippets, paging,
 *             a News tab and a time filter. Wikipedia's best match rides on
 *             top when it is about the query.
 *   Browse    a page opens in a real Chromium on Pivot's side (charto/browser):
 *             this widget paints the frames it streams and sends back the
 *             mouse, wheel and keys. Scripts, logins and every site that
 *             refuses to be framed work as they do in a desktop browser, and
 *             nothing from the site runs in this page.
 *   Text      the same page as clean text (/reader, /wiki/page) — the
 *             fallback when the live browser is unavailable, and a choice.
 *   PDFs      open in the Documents widget (/fetch-file), from a result or
 *             from a link clicked in the live page.
 */
"use strict";

(() => {
  if (typeof Dock === "undefined") return;
  const { esc, ic, json, API } = WKit;
  // the start page: the sources a trader in India reads, by kind; a tile
  // scopes the search to that site ("site:" on the web search)
  const SITES = [
    ["Markets & regulators", [["NSE", "www.nseindia.com"], ["BSE", "www.bseindia.com"], ["SEBI", "www.sebi.gov.in"],
      ["RBI", "www.rbi.org.in"], ["MOSPI", "www.mospi.gov.in"], ["PIB", "pib.gov.in"]]],
    ["News", [["Economic Times", "economictimes.indiatimes.com"], ["Mint", "www.livemint.com"], ["Business Standard", "www.business-standard.com"],
      ["BusinessLine", "www.thehindubusinessline.com"], ["Reuters", "www.reuters.com"], ["Bloomberg", "www.bloomberg.com"]]],
    ["Reference", [["Wikipedia", "en.wikipedia.org"], ["Investopedia", "www.investopedia.com"]]],
  ];
  const ICON_HOST = { "en.wikipedia.org": "www.wikipedia.org" };
  const FRESH = [["", "Any time"], ["day", "Past day"], ["week", "Past week"], ["month", "Past month"], ["year", "Past year"]];
  const SOURCE = { brave: "Brave Search", searxng: "SearXNG", model: "the model's web search" };
  const domainOf = (h) => h.replace(/^www\./, "");
  let icons = null;                 // host → data URL, fetched once per page
  const loadIcons = () => icons || (icons = json(`/site-icons?hosts=${SITES.flatMap(([, l]) => l.map(([, h]) => ICON_HOST[h] || h)).join(",")}`)
    .then((d) => d.icons || {}).catch(() => ({})));
  const looksLikeUrl = (s) => /^(https?:\/\/)?[\w-]+(\.[\w-]+)+(:\d+)?(\/\S*)?$/i.test(String(s).trim());
  const WIKI = /^https?:\/\/(en|hi)\.(?:m\.)?wikipedia\.org\/wiki\/([^?#]+)/i;
  const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } };
  const favicon = (u) => { try { return `${new URL(u).origin}/favicon.ico`; } catch { return ""; } };
  const isPdf = (u) => { try { return /\.pdf$/i.test(new URL(u).pathname); } catch { return false; } };
  const mode = (c) => (c.openAs === "text" ? "text" : "browser");   // "live" (old) reads as browser
  const WHY = {
    signin: "Sign in to browse live pages; this one is shown as text.",
    unavailable: "The live browser is not running on this server; this page is shown as text.",
    busy: "The live browser is full right now; this page is shown as text.",
    down: "The live browser did not answer; this page is shown as text.",
    ticket: "The live browser did not accept this session; this page is shown as text.",
  };
  /* Sites that turn away every browser running in a data centre — their bot
   * managers score the address itself, so no server-side browser gets in.
   * Seeded with the ones seen refusing ours, then learned: a site that
   * answers the live browser 401/403/429 is remembered for a week and opens
   * in the user's OWN browser, in a window sized over this widget. */
  const REFUSED_KEY = "charto:br:refused", WEEK = 7 * 864e5;
  const SEEN_REFUSING = ["moneycontrol.com", "nseindia.com", "investing.com", "bloomberg.com"];
  const refusedMap = () => { try { return JSON.parse(localStorage.getItem(REFUSED_KEY) || "{}") || {}; } catch { return {}; } };
  const isRefused = (u) => {
    const h = hostOf(u), m = refusedMap();
    return SEEN_REFUSING.some((x) => h === x || h.endsWith("." + x)) || (!!m[h] && Date.now() - m[h] < WEEK);
  };
  const markRefused = (u) => { try { const m = refusedMap(); m[hostOf(u)] = Date.now(); localStorage.setItem(REFUSED_KEY, JSON.stringify(m)); } catch { } };
  /* A site that allows framing opens as itself, in an iframe: the real page,
   * native text and scrolling, loaded by the user's own browser. Asked once
   * per host (/frame-check reads its X-Frame-Options and CSP), bounded so a
   * slow answer never holds a page up. */
  const frames = new Map();
  function frameable(url) {
    if (!/^https:/i.test(url)) return Promise.resolve(false);
    const h = hostOf(url);
    if (!frames.has(h)) {
      frames.set(h, Promise.race([
        json(`/frame-check?url=${encodeURIComponent(url)}`)
          .then((d) => !!(d && d.embeddable && (!d.status || d.status < 400))).catch(() => false),
        new Promise((r) => setTimeout(() => r(false), 2500)),
      ]));
    }
    return frames.get(h);
  }

  function mount(host, ctx) {
    let hist = [], at = -1, cur = null, seq = 0;
    host.innerHTML =
      `<div class="br-bar">` +
        `<button type="button" class="sh-btn i" data-br="back" title="Back">${ic("arrowLeft")}</button>` +
        `<button type="button" class="sh-btn i" data-br="fwd" title="Forward">${ic("arrowRight")}</button>` +
        `<button type="button" class="sh-btn i" data-br="reload" title="Reload" hidden>${ic("rotateCw")}</button>` +
        `<form class="br-url"><span class="br-lock">${ic("search")}</span>` +
          `<input name="u" placeholder="Search the web or enter an address" autocomplete="off" spellcheck="false"></form>` +
        `<button type="button" class="sh-btn i" data-br="more" title="Page tools" hidden>${ic("more")}</button>` +
        `<button type="button" class="sh-btn i" data-br="home" title="New search">${ic("home")}</button>` +
      `</div>` +
      `<div class="br-prog"></div>` +
      `<div class="br-view"></div>` +
      `<div class="br-stage" hidden><div class="br-flash" hidden></div><canvas></canvas>` +
        `<textarea class="br-kb" aria-label="Keyboard input for the page" autocomplete="off" autocapitalize="off" spellcheck="false"></textarea>` +
        `<div class="br-over" hidden></div></div>` +
      `<div class="br-frame" hidden></div>`;
    const $ = (s) => host.querySelector(s);
    const view = $(".br-view"), form = $(".br-url"), input = form.u;
    const stage = $(".br-stage"), canvas = stage.querySelector("canvas"), kb = $(".br-kb"), flash = $(".br-flash"), over = $(".br-over");
    const g2 = canvas.getContext("2d");
    const frameEl = $(".br-frame");
    let outWin = null, outT = 0;   // the window opened over this widget, and its watch
    const cfg = () => ctx.cfg;

    function prefs() {
      host.style.setProperty("--br-size", { s: "13px", m: "14.5px", l: "16.5px" }[cfg().textSize || "m"]);
      host.classList.toggle("br-noimg", cfg().images === false);
    }

    /* ── the live browser ─────────────────────────────────────────────────
     * One remote page per widget. The socket closes when the widget is
     * hidden and reattaches (same sid) when it is shown again; the server
     * keeps the page for 90 s in between. */
    const rb = { ws: null, sid: null, st: {}, live: false, want: null, size: [0, 0] };

    function wsUrl(path) {
      if (/^wss?:\/\//.test(path)) return path;
      return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;
    }
    function stageSize() {
      const r = stage.getBoundingClientRect();
      return [Math.max(200, Math.round(r.width)), Math.max(150, Math.round(r.height))];
    }
    async function connect(firstUrl) {
      let t;
      try {
        const r = await fetch(API + "/browser/ticket", { method: "POST", headers: typeof Auth !== "undefined" ? Auth.headers() : {} });
        t = await r.json();
      } catch { return { ok: false, reason: "down" }; }
      if (!t || !t.ok) return { ok: false, reason: (t && t.reason) || "down" };
      const [w, h] = stageSize();
      rb.size = [w, h];
      const dpr = cfg().sharp === "standard" ? 1 : Math.min(2, window.devicePixelRatio || 1);
      const qs = new URLSearchParams({ ticket: t.ticket, w, h, dpr: String(dpr) });
      if (rb.sid) qs.set("sid", rb.sid);
      if (firstUrl) qs.set("url", firstUrl);
      return new Promise((res) => {
        let done = false;
        const settle = (v) => { if (!done) { done = true; res(v); } };
        const ws = new WebSocket(`${wsUrl(t.ws)}?${qs}`);
        ws.binaryType = "blob";
        rb.ws = ws;
        const timer = setTimeout(() => { settle({ ok: false, reason: "down" }); try { ws.close(); } catch {} }, 15000);
        ws.onmessage = (ev) => {
          if (typeof ev.data !== "string") return frame(ev.data);
          let m; try { m = JSON.parse(ev.data); } catch { return; }
          if (m.t === "hello") { rb.sid = m.sid; clearTimeout(timer); settle({ ok: true }); return; }
          if (m.t === "busy") { clearTimeout(timer); settle({ ok: false, reason: "busy" }); return; }
          onMsg(m);
        };
        ws.onclose = (ev) => {
          clearTimeout(timer);
          if (rb.ws === ws) rb.ws = null;
          if (!done) return settle({ ok: false, reason: ev.code === 4003 ? "ticket" : "down" });
          if (ev.code === 4000) return;                       // replaced by a newer socket
          if (rb.live && ev.code !== 4001 && !rb.hidden) ended("The connection to the live browser dropped.");
        };
      });
    }
    const send = (m) => { if (rb.ws && rb.ws.readyState === 1) rb.ws.send(JSON.stringify(m)); };

    // frames: decode the newest, drop any that arrive while one is decoding
    let decoding = false, nextBlob = null;
    function frame(blob) {
      if (decoding) { nextBlob = blob; return; }
      decoding = true;
      createImageBitmap(blob).then((bmp) => {
        if (canvas.width !== bmp.width || canvas.height !== bmp.height) { canvas.width = bmp.width; canvas.height = bmp.height; }
        g2.drawImage(bmp, 0, 0);
        bmp.close();
        stage.classList.add("on");
      }).catch(() => {}).finally(() => {
        decoding = false;
        if (nextBlob) { const b = nextBlob; nextBlob = null; frame(b); }
      });
    }

    function onMsg(m) {
      if (m.t === "state") {
        rb.st = m;
        $(".br-prog").classList.toggle("on", !!m.loading && rb.live);
        if (!rb.live) return;
        if (m.url && /^https?:/.test(m.url)) {
          if (document.activeElement !== input) input.value = m.url;
          lockIcon(m.url);
          cur = { kind: "page", url: m.url, title: m.title || hostOf(m.url), live: true, text: () => "" };
          if (hist[at] && hist[at].kind === "page") hist[at] = { kind: "page", url: m.url };
          ctx.setCfg({ last: { kind: "page", url: m.url } });
        }
        ctx.setTitle(m.title || hostOf(m.url || ""));
        reloadBtn(m.loading);
        paintNav();
      } else if (m.t === "cursor") {
        if (/^[a-z-]+$/.test(m.c || "")) canvas.style.cursor = m.c;
      } else if (m.t === "pdf") {
        note(`Opened ${esc(m.name || "the PDF")} in Documents.`);
        ctx.send("docs", { url: m.url, name: m.name });
      } else if (m.t === "clip") {
        if (navigator.clipboard) navigator.clipboard.writeText(m.s).catch(() => {});
      } else if (m.t === "notice") {
        note(esc(m.msg));
      } else if (m.t === "refused") {
        // the site turned our browser away: remember it, free the remote
        // page, and offer the user's own browser over the widget instead
        markRefused(m.url);
        if (rb.ws) { const w = rb.ws; rb.ws = null; try { w.close(1000, "refused"); } catch { } }
        rb.sid = null;
        ++seq;
        beside(m.url, false, `${hostOf(m.url)} turns away browsers that run on servers${m.code ? ` (it answered ${m.code})` : ""}.`);
      } else if (m.t === "bye") {
        ended({ idle: "The live page closed after 15 minutes without use.", time: "The live page reached its two-hour limit.",
          crash: "The live page stopped unexpectedly.", replaced: "This page was closed to open another.",
          capacity: "The live page was closed to make room." }[m.reason] || "The live page closed.");
      }
    }

    let flashT = 0;
    function note(html, actions = "", ms = 6000) {
      flash.innerHTML = `<span>${html}</span>${actions}<button type="button" class="sh-btn i" data-br="unflash" aria-label="Dismiss">${ic("x")}</button>`;
      flash.hidden = false;
      clearTimeout(flashT);
      if (ms) flashT = setTimeout(() => { flash.hidden = true; }, ms);
    }
    function ended(why) {
      rb.sid = null;
      over.innerHTML = `<div>${Icons.svg("globe")}<p>${esc(why)}</p><button type="button" class="dk-cta" data-br="resume">${ic("rotateCw")}Open it again</button></div>`;
      over.hidden = false;
    }
    function lockIcon(u) {
      form.querySelector(".br-lock").innerHTML = rb.live ? ic(/^https:/.test(u) ? "lock" : "unlock") : ic("search");
    }
    function reloadBtn(loading) {
      const b = $('[data-br="reload"]');
      b.hidden = !(rb.live || (cur && cur.frame));
      b.innerHTML = loading ? ic("x") : ic("rotateCw");
      b.title = loading ? "Stop" : "Reload";
      b.dataset.loading = loading ? "1" : "";
    }

    function setLive(on) {
      rb.live = on;
      stage.hidden = !on;
      view.hidden = on;
      frameEl.hidden = true;
      if (frameEl.firstChild) frameEl.innerHTML = "";
      $('[data-br="more"]').hidden = !(on || (cur && cur.kind === "page"));
      if (!on) { $(".br-prog").classList.remove("on"); flash.hidden = true; }
      reloadBtn(on && rb.st.loading);
      lockIcon(input.value);
    }

    async function showRemote(url, my) {
      flash.hidden = true; over.hidden = true;
      setLive(true);
      input.value = url;
      lockIcon(url);
      ctx.setTitle(hostOf(url));
      if (rb.ws && rb.ws.readyState === 1) {
        if (rb.st.url !== url) send({ t: "nav", url });
        return true;
      }
      stage.classList.remove("on");
      $(".br-prog").classList.add("on");
      const r = await connect(url);
      if (my !== seq) return true;
      if (!r.ok) {
        setLive(false);
        await readPage(url, my, WHY[r.reason] || WHY.down);
        return false;
      }
      kb.focus({ preventScroll: true });
      return true;
    }

    /* input: pointer → mouse events in page pixels; wheel coalesced per
     * frame; touch drags become wheel scrolls; keys go through a hidden
     * textarea so paste and the on-screen keyboard work too */
    const mods = (e) => (e.altKey ? 1 : 0) | (e.ctrlKey || e.metaKey ? 2 : 0) | (e.shiftKey ? 8 : 0);
    function pt(e) {
      const r = canvas.getBoundingClientRect();
      return [(e.clientX - r.left) * (rb.size[0] / r.width), (e.clientY - r.top) * (rb.size[1] / r.height)];
    }
    let lastDown = { t: 0, x: 0, y: 0, n: 0 }, moveQ = null, wheelQ = null, raf = 0, touch = null;
    const flush = () => {
      raf = 0;
      if (moveQ) { send(moveQ); moveQ = null; }
      if (wheelQ) { send(wheelQ); wheelQ = null; }
    };
    const later = () => { if (!raf) raf = requestAnimationFrame(flush); };
    canvas.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      kb.focus({ preventScroll: true });
      const [x, y] = pt(e);
      if (e.pointerType === "touch") { touch = { x: e.clientX, y: e.clientY, px: x, py: y, moved: false }; return; }
      canvas.setPointerCapture(e.pointerId);
      const now = performance.now();
      const n = now - lastDown.t < 400 && Math.hypot(x - lastDown.x, y - lastDown.y) < 6 ? lastDown.n + 1 : 1;
      lastDown = { t: now, x, y, n };
      flush();
      send({ t: "mouse", e: "down", x, y, b: e.button, bs: e.buttons, n, m: mods(e) });
    });
    canvas.addEventListener("pointermove", (e) => {
      if (e.pointerType === "touch" && touch) {
        const dx = e.clientX - touch.x, dy = e.clientY - touch.y;
        if (!touch.moved && Math.hypot(dx, dy) < 6) return;
        touch.moved = true;
        touch.x = e.clientX; touch.y = e.clientY;
        const [x, y] = pt(e);
        wheelQ = { t: "wheel", x, y, dx: -dx + ((wheelQ && wheelQ.dx) || 0), dy: -dy + ((wheelQ && wheelQ.dy) || 0), m: 0 };
        return later();
      }
      const [x, y] = pt(e);
      moveQ = { t: "mouse", e: "move", x, y, bs: e.buttons, b: 0, m: mods(e) };
      later();
    });
    canvas.addEventListener("pointerup", (e) => {
      const [x, y] = pt(e);
      if (e.pointerType === "touch") {
        if (touch && !touch.moved) {
          send({ t: "mouse", e: "down", x, y, b: 0, bs: 1, n: 1, m: 0 });
          send({ t: "mouse", e: "up", x, y, b: 0, bs: 0, n: 1, m: 0 });
        }
        touch = null;
        return;
      }
      flush();
      send({ t: "mouse", e: "up", x, y, b: e.button, bs: e.buttons, n: lastDown.n, m: mods(e) });
    });
    canvas.addEventListener("contextmenu", (e) => e.preventDefault());
    canvas.addEventListener("wheel", (e) => {
      e.preventDefault();
      const k = e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? rb.size[1] : 1;
      const [x, y] = pt(e);
      wheelQ = { t: "wheel", x, y, dx: e.deltaX * k + ((wheelQ && wheelQ.dx) || 0), dy: e.deltaY * k + ((wheelQ && wheelQ.dy) || 0), m: mods(e) };
      later();
    }, { passive: false });
    const keyMsg = (e, down) => ({
      t: "key", e: down ? "down" : "up", key: e.key, code: e.code, kc: e.keyCode || 0, m: mods(e),
      loc: e.location || 0, rep: e.repeat,
      text: down && !e.ctrlKey && !e.metaKey ? (e.key.length === 1 ? e.key : e.key === "Enter" ? "\r" : "") : "",
    });
    kb.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.isComposing) return;
      const cmd = e.ctrlKey || e.metaKey;
      if (cmd && e.key.toLowerCase() === "v") return;                     // the paste event carries it
      if (cmd && e.key.toLowerCase() === "l") { e.preventDefault(); input.focus(); return; }
      if (cmd && e.key.toLowerCase() === "c") { send({ t: "copy" }); return; }
      e.preventDefault();
      send(keyMsg(e, true));
    });
    kb.addEventListener("keyup", (e) => { e.stopPropagation(); if (!e.isComposing) send(keyMsg(e, false)); });
    kb.addEventListener("paste", (e) => {
      e.preventDefault();
      const s = e.clipboardData && e.clipboardData.getData("text");
      if (s) send({ t: "text", s });
    });
    kb.addEventListener("compositionend", (e) => { if (e.data) send({ t: "text", s: e.data }); kb.value = ""; });
    kb.addEventListener("input", (e) => { if (!e.isComposing) kb.value = ""; });
    let sizeT = 0;
    new ResizeObserver(() => {
      if (!rb.live) return;
      clearTimeout(sizeT);
      sizeT = setTimeout(() => {
        const [w, h] = stageSize();
        if (w === rb.size[0] && h === rb.size[1]) return;
        rb.size = [w, h];
        send({ t: "size", w, h });
      }, 160);
    }).observe(stage);

    /* ── widget history: home, results and pages; inside a live page the
     * remote page's own history comes first */
    function paintNav() {
      $('[data-br="back"]').disabled = !(at > 0 || (rb.live && rb.st.back));
      $('[data-br="fwd"]').disabled = !(at < hist.length - 1 || (rb.live && rb.st.fwd));
    }
    function nav(entry, push = true) {
      if (push) { hist = hist.slice(0, at + 1); hist.push(entry); at = hist.length - 1; }
      ctx.setCfg({ last: entry.kind === "home" ? null : entry });
      paintNav();
      if (entry.kind === "home") return home();
      if (entry.kind === "search") return search(entry);
      return page(entry.url, entry.text);
    }
    function go(raw) {
      const s = String(raw || "").trim();
      if (!s) return nav({ kind: "home" });
      if (looksLikeUrl(s)) {
        const url = /^https?:\/\//i.test(s) ? s : "https://" + s;
        // typed while a live page is open: stay in it, as a browser would
        if (rb.live && rb.ws && !isPdf(url)) { send({ t: "nav", url }); kb.focus({ preventScroll: true }); return; }
        return nav({ kind: "page", url });
      }
      const m = s.match(/^site:(\S+)\s+(.+)$/i);
      if (m) return nav({ kind: "search", q: m[2], site: m[1] });
      nav({ kind: "search", q: s, ...(scope && cur == null ? { site: scope[1] } : {}) });
    }

    let scope = null;                 // [name, host] while the search is narrowed to one site
    function paintScope(f) {
      const chip = f.querySelector(".br-scope");
      chip.hidden = !scope;
      if (scope) chip.innerHTML = `<span>${esc(scope[0])}</span><button type="button" data-br="unscope" aria-label="Search the whole web">${ic("x")}</button>`;
      f.q.placeholder = scope ? `Search ${domainOf(scope[1])}` : "Search the web";
    }
    function home() {
      setLive(false);
      cur = null; input.value = ""; ctx.setTitle("Browser");
      $('[data-br="more"]').hidden = true;
      view.innerHTML = `<div class="br-home">` +
        `<form class="br-big">${ic("search")}<span class="br-scope" hidden></span><input name="q" autocomplete="off" spellcheck="false"></form>` +
        SITES.map(([sec, list]) => `<section class="br-sec"><h6>${esc(sec)}</h6><div class="br-sites">` +
          list.map(([name, h]) => `<div class="br-site-t${scope && scope[1] === h ? " on" : ""}" data-scope="${esc(h)}" data-name="${esc(name)}" role="button" tabindex="0" title="Search ${esc(domainOf(h))}">` +
            `<span class="br-ico" data-host="${esc(ICON_HOST[h] || h)}"><i>${esc(name[0])}</i></span><b>${esc(name)}</b>` +
            `<button type="button" class="br-ext" data-go="https://${esc(h)}/" title="Open ${esc(domainOf(h))}">${ic("arrowUpRight")}</button></div>`).join("") +
          `</div></section>`).join("") +
        `<p class="br-note">Pages open right here: a site that allows it loads as itself, others run in a live browser on Pivot's side, and the few that refuse both open in your own browser over this widget. Pick a site to search only it.</p></div>`;
      const f = view.querySelector(".br-big");
      paintScope(f);
      f.q.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Backspace" && !f.q.value && scope) { scope = null; paintScope(f); paintTiles(); } });
      f.addEventListener("submit", (e) => { e.preventDefault(); go(f.q.value); });
      loadIcons().then((m) => {
        for (const el of view.querySelectorAll(".br-ico[data-host]")) {
          const src = m[el.dataset.host];
          if (src) el.innerHTML = `<img src="${esc(src)}" alt="">`;
        }
      });
      requestAnimationFrame(() => { if (ctx.visible()) f.q.focus({ preventScroll: true }); });
    }
    const paintTiles = () => { for (const t of view.querySelectorAll("[data-scope]")) t.classList.toggle("on", !!scope && scope[1] === t.dataset.scope); };

    const hitRow = (r) => `<div class="br-hit" data-open="${esc(r.url)}"${r.pdf ? ' data-pdf="1"' : ""}>` +
      `<div class="br-site"><img src="${esc(favicon(r.url))}" alt="" referrerpolicy="no-referrer" onerror="this.style.visibility='hidden'"><span>${esc(r.site || hostOf(r.url))}</span>` +
      (r.published ? `<span class="br-when">· ${esc(when(r.published))}</span>` : "") + `</div>` +
      `<b>${r.pdf ? `<span class="br-pdf" title="A PDF; opens in Documents">${ic("doc")}</span>` : ""}${esc(r.title)}</b>` +
      (r.snippet ? `<p>${esc(r.snippet)}</p>` : "") +
      `</div>`;
    function when(s) {
      const d = new Date(s);
      if (isNaN(d)) return String(s).slice(0, 24);
      const h = (Date.now() - d) / 36e5;
      if (h < 1) return "just now";
      if (h < 24) return `${Math.floor(h)}h ago`;
      if (h < 24 * 7) return `${Math.floor(h / 24)}d ago`;
      return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
    }

    async function search(e) {
      const my = ++seq;
      setLive(false);
      const { q, site } = e;
      const tab = e.tab === "news" ? "news" : "web", fresh = e.fresh || "";
      cur = { kind: "search", q, site };
      $('[data-br="more"]').hidden = true;
      input.value = site ? `site:${domainOf(site)} ${q}` : q; ctx.setTitle(q);
      const wikiOnly = cfg().source === "wiki" || /wikipedia\.org$/.test(site || "");
      const filters = wikiOnly ? "" : `<div class="br-filt">` +
        [["web", "All"], ["news", "News"]].map(([v, l]) => `<button type="button" class="br-tab${tab === v ? " on" : ""}" data-tab="${v}">${l}</button>`).join("") +
        `<span class="sh-gap"></span><button type="button" class="br-fresh" data-br="fresh">${ic("clock")}<span>${esc((FRESH.find(([v]) => v === fresh) || FRESH[0])[1])}</span>${ic("chevronDown")}</button></div>`;
      view.innerHTML = `${filters}<div class="br-res">${WKit.skel(8)}</div>`;
      view.scrollTop = 0;
      const lang = cfg().lang || "en";
      const full = site ? `site:${domainOf(site)} ${q}` : q;
      const [wk, web] = await Promise.all([
        tab === "web" ? json(`/wiki/search?q=${encodeURIComponent(q)}&lang=${lang}`).catch(() => null) : null,
        wikiOnly ? Promise.resolve(null) : json(`/web-search?q=${encodeURIComponent(full)}&kind=${tab}&fresh=${fresh}`).catch((er) => ({ results: [], error: er.message })),
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
        const top = site || fresh ? null : wr.find((r) => { const t = (r.title || "").toLowerCase(); return words.length && words.filter((w) => t.includes(w)).length >= Math.min(2, words.length); });
        if (top) html += `<div class="br-wiki" data-open="https://${lang}.wikipedia.org/wiki/${esc(encodeURIComponent(top.key || top.title))}">` +
          (top.thumb ? `<img src="${esc(top.thumb)}" alt="" referrerpolicy="no-referrer">` : "") +
          `<div><em>Wikipedia</em><b>${esc(top.title)}</b><span>${esc(top.description || "")}</span></div></div>`;
        const rs = (web && web.results) || [];
        html += rs.map(hitRow).join("");
        if (!rs.length) html += `<div class="br-none">${esc((web && web.error) || (site ? `No pages on ${domainOf(site)} came back for that.` : "No pages came back for that."))}` +
          (web && web.error ? `<div class="br-beside-acts"><button type="button" class="sh-btn" data-br="gsearch">${ic("search")}<span>Search Google over the widget</span></button></div>` : "") + `</div>`;
        if (rs.length) html += `<div class="br-foot">` + (web.more ? `<button type="button" class="sh-btn" data-br="page" data-p="${(web.page || 1) + 1}">${ic("chevronDown")}<span>More results</span></button>` : "") +
          `<span>Results from ${esc(SOURCE[web.source] || "the web")}${web.stale ? " (saved earlier; the search did not answer)" : ""}</span></div>`;
      }
      view.querySelector(".br-res").innerHTML = html;
      cur.web = web;
    }
    async function morePage(btn) {
      const p = +btn.dataset.p;
      btn.disabled = true;
      const full = cur.site ? `site:${domainOf(cur.site)} ${cur.q}` : cur.q;
      const e = hist[at] || {};
      const web = await json(`/web-search?q=${encodeURIComponent(full)}&kind=${e.tab || "web"}&fresh=${e.fresh || ""}&page=${p}`).catch(() => null);
      const foot = btn.closest(".br-foot");
      if (!web || !(web.results || []).length) { btn.remove(); return; }
      foot.insertAdjacentHTML("beforebegin", web.results.map(hitRow).join(""));
      if (web.more && p < 10) { btn.dataset.p = p + 1; btn.disabled = false; } else btn.remove();
    }
    const wikiRow = (r, lang) => `<div class="br-hit" data-open="https://${lang}.wikipedia.org/wiki/${esc(encodeURIComponent(r.key || r.title))}">` +
      `<div class="br-site"><span>${lang}.wikipedia.org</span></div><b>${esc(r.title)}</b>` +
      `<p>${esc(r.description || "")}${r.description && r.excerpt ? " — " : ""}${esc(r.excerpt || "").replace(/\[\[(.*?)\]\]/g, "<mark>$1</mark>")}</p></div>`;

    async function page(url, asText) {
      const my = ++seq;
      if (isPdf(url)) return openPdf(url);
      if (asText || mode(cfg()) !== "browser") { setLive(false); return readPage(url, my); }
      // before any await: a window can only be opened inside the click
      if (isRefused(url)) return beside(url, true);
      if (await frameable(url)) { if (my === seq) showFrame(url); return; }
      if (my !== seq) return;
      return showRemote(url, my);
    }

    /* ── the real page, framed ───────────────────────────────────────────
     * Sandboxed: it may run its scripts and open links in new tabs, but it
     * can never navigate this page. Its own navigation is not visible here
     * (it is another origin), so the address bar keeps the page it opened. */
    function showFrame(url) {
      setLive(false);
      cur = { kind: "page", url, title: hostOf(url), text: () => "", frame: true };
      input.value = url; lockIcon(url); ctx.setTitle(hostOf(url));
      $('[data-br="more"]').hidden = false;
      view.hidden = true;
      frameEl.hidden = false;
      frameEl.innerHTML = `<iframe src="${esc(url)}" title="${esc(hostOf(url))}" ` +
        `sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads" ` +
        `referrerpolicy="strict-origin-when-cross-origin" allow="clipboard-write; fullscreen"></iframe>`;
      const f = frameEl.firstChild;
      $(".br-prog").classList.add("on");
      f.addEventListener("load", () => $(".br-prog").classList.remove("on"), { once: true });
      reloadBtn(false);
    }

    /* ── the user's own browser, over the widget ─────────────────────────
     * For a site that refuses every server-side browser. The window opens on
     * about:blank — still ours, so it can be sized and placed exactly over
     * the widget — then its tie back to this page is cut and only then is it
     * sent to the site. Once the site loads it is another origin's window:
     * it cannot be moved again, so "Snap to the widget" reopens it in place. */
    function dockBox() {
      const r = host.getBoundingClientRect();
      const top = Math.max(0, outerHeight - innerHeight), side = Math.max(0, (outerWidth - innerWidth) / 2);
      return { left: Math.round(screenX + side + r.left), top: Math.round(screenY + top + r.top),
               width: Math.max(320, Math.round(r.width)), height: Math.max(320, Math.round(r.height)) };
    }
    function openDocked(url) {
      const g = dockBox();
      const w = window.open("about:blank", `pivot-beside-${ctx.id}`,
        `popup=yes,left=${g.left},top=${g.top},width=${g.width},height=${g.height}`);
      if (!w) return false;
      try {
        w.resizeTo(g.width, g.height);      // outer box = the widget's box
        w.moveTo(g.left, g.top);
      } catch { }
      try { w.opener = null; } catch { }
      try { w.location.replace(url); } catch { w.location.href = url; }
      outWin = w;
      clearInterval(outT);
      outT = setInterval(() => {
        if (!outWin || !outWin.closed) return;
        clearInterval(outT); outWin = null;
        if (cur && cur.beside) paintBeside(cur.url, "offer", "The window was closed.");
      }, 1000);
      return true;
    }
    function beside(url, auto, why) {
      setLive(false);
      cur = { kind: "page", url, title: hostOf(url), text: () => "", beside: true };
      input.value = url; ctx.setTitle(hostOf(url));
      $('[data-br="more"]').hidden = false;
      reloadBtn(false);
      const opened = auto && openDocked(url);
      paintBeside(url, opened ? "open" : "offer", why || (auto && !opened
        ? "The browser blocked the window. Allow pop-ups for Pivot, or use a button below." : ""));
    }
    function paintBeside(url, state, why) {
      const h = hostOf(url);
      const lead = state === "open"
        ? `${esc(h)} is open in your own browser, in a window over this widget.`
        : `${esc(h)} turns away browsers that run on servers, so it opens in your own browser instead.`;
      view.hidden = false;
      view.innerHTML = `<div class="br-beside">` +
        `<img class="br-fav" src="${esc(favicon(url))}" alt="" referrerpolicy="no-referrer" onerror="this.style.visibility='hidden'">` +
        `<b>${esc(h)}</b><p>${lead}</p>` + (why && why !== lead ? `<p class="br-why-s">${esc(why)}</p>` : "") +
        `<div class="br-beside-acts">` +
          (state === "open"
            ? `<button type="button" class="dk-cta" data-br="dockfocus">${ic("arrowUpRight")}Bring it to the front</button>` +
              `<button type="button" class="sh-btn" data-br="docksnap">${ic("expand")}<span>Snap to the widget</span></button>` +
              `<button type="button" class="sh-btn" data-br="dockclose">${ic("x")}<span>Close the window</span></button>`
            : `<button type="button" class="dk-cta" data-br="dock">${ic("arrowUpRight")}Open over the widget</button>` +
              `<button type="button" class="sh-btn" data-br="out">${ic("externalLink")}<span>Open in a new tab</span></button>` +
              `<button type="button" class="sh-btn" data-br="text">${ic("fileText")}<span>Try it as text</span></button>`) +
        `</div></div>`;
    }
    function openPdf(url, name) {
      ctx.send("docs", { url, name });
      ctx.toast(`Opening ${name || hostOf(url) + "'s PDF"} in Documents.`);
      if (hist[at] && hist[at].kind === "page" && hist[at].url === url && at > 0) { at--; paintNav(); }
    }

    // the page as text: Wikipedia rebuilt from an allow-list, anything else
    // as its title, headings and paragraphs
    async function readPage(url, my, why) {
      input.value = url;
      ctx.setTitle(hostOf(url));
      view.innerHTML = `<div class="br-wait">${WKit.skel(9)}</div>`;
      const notice = why ? `<div class="br-why">${ic("info")}<span>${esc(why)}</span></div>` : "";
      const wm = url.match(WIKI);
      if (wm) {
        const d = await json(`/wiki/page?lang=${wm[1]}&title=${encodeURIComponent(decodeURIComponent(wm[2]).replace(/_/g, " "))}`).catch((e) => ({ error: e.message }));
        if (my !== seq) return;
        if (d.error) return fail(url, d.error, why);
        cur = { kind: "page", url: d.url || url, title: d.title, text: () => view.querySelector(".br-doc").innerText };
        $('[data-br="more"]').hidden = false;
        ctx.setTitle(d.title);
        input.value = d.url || url;
        view.innerHTML = `<article class="br-doc wiki">${notice}` +
          `<h1>${esc(d.title)}</h1>${d.description ? `<p class="br-desc">${esc(d.description)}</p>` : ""}` +
          (d.toc && d.toc.length > 2 ? `<details class="br-toc"><summary>Contents</summary>${d.toc.map((t) => `<a href="#" data-to="${esc(t.id)}">${esc(t.title)}</a>`).join("")}</details>` : "") +
          `<div class="br-wikibody">${d.html}</div><p class="br-lic">${esc(d.license)} · <a href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">wikipedia.org</a></p></article>`;
        view.scrollTop = 0;
        return;
      }
      const d = await json(`/reader?url=${encodeURIComponent(url)}`).catch((e) => ({ error: e.message }));
      if (my !== seq) return;
      if (d.pdf) return openPdf(d.final || url);
      if (d.error && !(d.blocks || []).length) return fail(url, d.error, why);
      cur = { kind: "page", url: d.final || url, title: d.title, text: () => view.querySelector(".br-doc").innerText };
      $('[data-br="more"]').hidden = false;
      ctx.setTitle(d.title || hostOf(url));
      const dt = d.published ? new Date(d.published) : null;
      view.innerHTML = `<article class="br-doc">${notice}` +
        `<div class="br-site"><img src="${esc(favicon(d.final || url))}" alt="" referrerpolicy="no-referrer" onerror="this.style.visibility='hidden'"><span>${esc(d.site || hostOf(url))}</span>` +
          (dt && !isNaN(dt) ? `<span>· ${dt.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}</span>` : "") + `</div>` +
        `<h1>${esc(d.title || hostOf(url))}</h1>` +
        (d.thin ? `<div class="br-thin"><p>${d.description ? esc(d.description) + " " : ""}</p>` +
          `<span>This site writes its text with scripts as the page loads, so it cannot be read as text.</span>` +
          `<div><button type="button" class="sh-btn" data-br="live">${ic("globe")}<span>Open it live</span></button>` +
          `<button type="button" class="sh-btn" data-br="out">${ic("externalLink")}<span>Open in a new tab</span></button></div></div>` : "") +
        (d.image ? `<img class="br-hero" src="${esc(d.image)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()">` : "") +
        (d.thin ? [] : d.blocks || []).map((b) => b.t === "h" ? `<h3>${esc(b.text)}</h3>` : b.t === "li" ? `<p class="li">${esc(b.text)}</p>`
          : b.t === "q" ? `<blockquote>${esc(b.text)}</blockquote>` : `<p>${esc(b.text)}</p>`).join("") +
        `<p class="br-lic">Shown as text by Pivot's reader. <a href="${esc(d.final || url)}" target="_blank" rel="noopener noreferrer">Open the original</a></p></article>`;
      view.scrollTop = 0;
    }
    // neither the live browser nor the reader could show it: the user's own
    // browser can, over the widget
    function fail(url, msg, why) {
      if (/\b(401|403|429)\b/.test(String(msg))) markRefused(url);
      beside(url, false, [msg, why].filter(Boolean).join(" "));
      if (why === WHY.signin && typeof window.CHARTO_AUTH_OPEN === "function") {
        view.querySelector(".br-beside-acts").insertAdjacentHTML("beforeend",
          `<button type="button" class="sh-btn" data-br="signin">${ic("user")}<span>Sign in to open pages live</span></button>`);
      }
    }

    function tools(anchor) {
      if (!cur || cur.kind !== "page") return;
      const items = [{ head: hostOf(cur.url) },
        rb.live ? { id: "text", label: "Read as text", hint: "Title, headings and paragraphs" } : { id: "live", label: "Open it live", hint: "In the live browser" },
        { id: "note", label: "Save to notes" },
        { id: "ask", label: "Ask the chat about this page" },
        { id: "copy", label: "Copy the address" },
        { id: "dock", label: "Open over the widget", hint: "In your own browser" },
        { id: "out", label: "Open in a new tab" }];
      ctx.menu(anchor, items, (id) => act(id));
    }
    async function act(a) {
      if (!cur || cur.kind !== "page") return;
      if (a === "out") return open(cur.url, "_blank", "noopener,noreferrer");
      if (a === "dock" || a === "docksnap") { if (!cur.beside) beside(cur.url, true); else if (openDocked(cur.url)) paintBeside(cur.url, "open"); else paintBeside(cur.url, "offer", "The browser blocked the window. Allow pop-ups for Pivot."); return; }
      if (a === "dockfocus") { if (outWin && !outWin.closed) outWin.focus(); else paintBeside(cur.url, "offer", "The window was closed."); return; }
      if (a === "dockclose") { if (outWin && !outWin.closed) outWin.close(); outWin = null; clearInterval(outT); return paintBeside(cur.url, "offer"); }
      if (a === "live") { hist[at] = { kind: "page", url: cur.url }; return page(cur.url); }
      if (a === "text") { const my = ++seq; setLive(false); hist[at] = { kind: "page", url: cur.url, text: true }; return readPage(cur.url, my); }
      if (a === "copy") { try { await navigator.clipboard.writeText(cur.url); ctx.toast("Address copied."); } catch {} return; }
      if (a === "note") {
        const sel = String(getSelection() || "").trim();
        const body = sel && view.contains(getSelection().anchorNode) ? `<blockquote>${esc(sel.slice(0, 2000))}</blockquote>` : "";
        return ctx.send("notes", { quiet: true, general: true, html: `<p><b>${esc(cur.title || hostOf(cur.url))}</b> — <a href="${esc(cur.url)}">${esc(hostOf(cur.url))}</a></p>${body}` });
      }
      if (a === "ask") {
        let t = cur.text ? cur.text().replace(/\s+\n/g, "\n").slice(0, 3000) : "";
        if (!t) {
          const d = await json(`/reader?url=${encodeURIComponent(cur.url)}`).catch(() => null);
          t = d && !d.thin ? (d.blocks || []).map((b) => b.text).join("\n").slice(0, 3000) : "";
        }
        return ctx.ask({ sub: `${hostOf(cur.url)} · ${cur.title || cur.url}`,
          context: `The page "${cur.title || cur.url}" (${cur.url}).${t ? `\nIts text:\n${t}` : " Its text could not be read."}`,
          question: "What in this page matters for an investor or trader in India? Quote it where you rely on it." });
      }
    }

    form.addEventListener("submit", (e) => { e.preventDefault(); go(input.value); input.blur(); });
    input.addEventListener("keydown", (e) => e.stopPropagation());
    input.addEventListener("focus", () => input.select());
    host.addEventListener("click", (e) => {
      const ext = e.target.closest("[data-go]");
      if (ext) { e.stopPropagation(); return nav({ kind: "page", url: ext.dataset.go }); }
      const sc = e.target.closest("[data-scope]");
      if (sc) {
        scope = scope && scope[1] === sc.dataset.scope ? null : [sc.dataset.name, sc.dataset.scope];
        const f = view.querySelector(".br-big");
        if (f) { paintScope(f); paintTiles(); f.q.focus(); }
        return;
      }
      if (e.target.closest('[data-br="unscope"]')) {
        e.stopPropagation(); scope = null;
        const f = view.querySelector(".br-big");
        if (f) { paintScope(f); paintTiles(); f.q.focus(); }
        return;
      }
      const tb = e.target.closest("[data-tab]");
      if (tb && cur && cur.kind === "search") return nav({ ...hist[at], tab: tb.dataset.tab });
      const w = e.target.closest("[data-wiki]");
      if (w) { e.preventDefault(); const lang = (cur && (cur.url.match(WIKI) || [])[1]) || cfg().lang || "en"; return nav({ kind: "page", url: `https://${lang}.wikipedia.org/wiki/${encodeURIComponent(w.dataset.wiki.replace(/ /g, "_"))}`, text: true }); }
      const an = e.target.closest("[data-anchor], [data-to]");
      if (an) {
        e.preventDefault();
        const id = an.dataset.to || "w-" + (an.getAttribute("href") || "").slice(1);
        const t = view.querySelector(`[id="${CSS.escape(id)}"]`);
        if (t) view.scrollTo({ top: t.offsetTop - 8, behavior: "smooth" });
        return;
      }
      const o = e.target.closest("[data-open]");
      if (o && !e.target.closest("a[target]")) {
        if (o.dataset.pdf) return openPdf(o.dataset.open);
        return nav({ kind: "page", url: o.dataset.open });
      }
      const b = e.target.closest("[data-br]");
      if (!b) return;
      const a = b.dataset.br;
      if (a === "back") {
        if (rb.live && rb.st.back) return send({ t: "back" });
        if (at > 0) return nav(hist[--at], false);
        return;
      }
      if (a === "fwd") {
        if (rb.live && rb.st.fwd) return send({ t: "fwd" });
        if (at < hist.length - 1) return nav(hist[++at], false);
        return;
      }
      if (a === "reload") {
        if (cur && cur.frame && frameEl.firstChild) { const f = frameEl.firstChild; f.src = f.src; return; }
        return send({ t: b.dataset.loading ? "stop" : "reload" });
      }
      if (a === "home") return nav({ kind: "home" });
      if (a === "unflash") { flash.hidden = true; return; }
      if (a === "signin") { window.CHARTO_AUTH_OPEN(); return; }
      if (a === "resume") { over.hidden = true; const u = (cur && cur.url) || rb.st.url; rb.ws = null; return u && showRemote(u, ++seq); }
      if (a === "page") return morePage(b);
      if (a === "gsearch" && cur && cur.kind === "search") {
        const q = cur.site ? `site:${domainOf(cur.site)} ${cur.q}` : cur.q;
        return beside(`https://www.google.com/search?q=${encodeURIComponent(q)}`, true);
      }
      if (a === "fresh") {
        e.stopPropagation();
        return ctx.menu(b, [{ head: "Published" }, ...FRESH.map(([v, l]) => ({ id: v || "any", label: l, on: (hist[at].fresh || "") === v }))],
          (id) => nav({ ...hist[at], fresh: id === "any" ? "" : id }));
      }
      if (a === "more") { e.stopPropagation(); return tools(b); }
      act(a);
    });

    prefs();
    let booted = false;
    return {
      show() {
        rb.hidden = false;
        if (!booted) {
          booted = true;
          const l = cfg().last || (cfg().url ? { kind: "page", url: cfg().url } : null);
          return nav(l || { kind: "home" });
        }
        // back from hidden: reattach the live page where it was
        if (rb.live && !rb.ws && cur && cur.url) showRemote(cur.url, ++seq);
      },
      hide() {
        rb.hidden = true;
        if (rb.ws) { const w = rb.ws; rb.ws = null; try { w.close(1000, "hidden"); } catch {} }
      },
      config(c, patch) {
        prefs();
        if ("source" in patch && cur && cur.kind === "search") nav(hist[at], false);
        if ("sharp" in patch && rb.ws) { const w = rb.ws; rb.ws = null; try { w.close(1000); } catch {} rb.sid = null; if (cur) showRemote(cur.url, ++seq); }
      },
      receive(p) { if (p && p.url) nav({ kind: "page", url: p.url, ...(p.reader ? { text: true } : {}) }); else if (p && p.q) go(p.q); },
      ask: () => cur && cur.kind === "page" ? { sub: `${hostOf(cur.url)} · ${cur.title || cur.url}`,
        context: `The page "${cur.title || cur.url}" (${cur.url})${cur.text && cur.text() ? `. Its text:\n${cur.text().replace(/\s+\n/g, "\n").slice(0, 3000)}` : "."}`,
        question: "Summarise what matters on this page for a trader in India." } : null,
    };
  }

  Dock.register({
    type: "browser", title: "Browser", icon: "globe", hue: "cyan", group: "Tools",
    desc: "Search the web and browse pages beside the chart", zone: "right", minW: 320, mount,
    settings: [
      { section: "Search" },
      { key: "source", label: "Search", kind: "seg", def: "web", options: [{ v: "web", label: "The web" }, { v: "wiki", label: "Wikipedia only" }] },
      { key: "lang", label: "Wikipedia in", kind: "seg", def: "en", options: [{ v: "en", label: "English" }, { v: "hi", label: "Hindi" }] },
      { section: "Pages" },
      { key: "openAs", label: "Open pages", kind: "seg", def: "browser", options: [{ v: "browser", label: "Live" }, { v: "text", label: "As text" }],
        hint: "Live pages run in a browser on Pivot's side and stream here" },
      { key: "sharp", label: "Live picture", kind: "seg", def: "sharp", options: [{ v: "sharp", label: "Sharp" }, { v: "standard", label: "Light" }],
        hint: "Light sends a smaller picture, for slow connections" },
      { key: "textSize", label: "Text size", kind: "seg", def: "m", options: [{ v: "s", label: "Small" }, { v: "m", label: "Medium" }, { v: "l", label: "Large" }] },
      { key: "images", label: "Pictures in text pages", kind: "toggle", def: true },
      { kind: "note", label: "Searches go through Pivot's servers. A site that allows framing loads directly in your browser, sandboxed so it cannot touch this page; otherwise the page runs in a private browser on Pivot's side, closed when you leave it. Sites that refuse both open in a window of your own browser." },
    ],
  });
})();
