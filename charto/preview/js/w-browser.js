/* Charto preview — the Browser widget: a web page beside the chart.
 *
 * Most sites forbid being shown inside another app, and a browser draws a
 * forbidden frame as a blank box with no reason given. So before a page is
 * framed, the data server reads its headers (GET /frame-check — public
 * addresses only, headers only) and the widget either shows the page or
 * says plainly that the site does not allow it, with the button that opens
 * it in a real tab. The start page lists sites that DO allow it.
 *
 * Framed pages run sandboxed: scripts and forms, no access to this app.
 * History is the widget's own, since a cross-origin frame's is not readable.
 */
"use strict";

(() => {
  if (typeof Dock === "undefined") return;
  const { esc, ic, json } = WKit;
  // checked: each of these answers /frame-check with embeddable: true
  const START = [
    { t: "NSE India", u: "https://www.nseindia.com", d: "Exchange notices, indices, market data" },
    { t: "Moneycontrol", u: "https://www.moneycontrol.com", d: "Markets news and company pages" },
    { t: "Chittorgarh IPO", u: "https://www.chittorgarh.com", d: "IPO calendar, GMP reports, subscription" },
    { t: "World Government Bonds", u: "https://www.worldgovernmentbonds.com", d: "Sovereign yields and spreads" },
    { t: "Wikipedia · Nifty 50", u: "https://en.wikipedia.org/wiki/NIFTY_50", d: "Reference and history" },
  ];
  const looksLikeUrl = (s) => /^(https?:\/\/)?[\w-]+(\.[\w-]+)+(:\d+)?(\/\S*)?$/i.test(s.trim());

  function mount(host, ctx) {
    let hist = [], at = -1;
    host.innerHTML =
      `<div class="br-bar">` +
        `<button type="button" class="sh-btn i" data-br="back" title="Back">${ic("arrowLeft")}</button>` +
        `<button type="button" class="sh-btn i" data-br="fwd" title="Forward">${ic("arrowRight")}</button>` +
        `<button type="button" class="sh-btn i" data-br="reload" title="Reload">${ic("rotateCw")}</button>` +
        `<button type="button" class="sh-btn i" data-br="home" title="Start page">${ic("home")}</button>` +
        `<form class="br-url"><span class="br-lock">${ic("globe")}</span>` +
          `<input name="u" placeholder="Search Wikipedia or type an address" autocomplete="off" spellcheck="false"></form>` +
        `<button type="button" class="sh-btn i" data-br="out" title="Open in a new tab">${ic("externalLink")}</button>` +
      `</div>` +
      `<div class="br-view"></div>`;
    const $ = (s) => host.querySelector(s);
    const view = $(".br-view"), form = $(".br-url"), input = form.u;

    function start() {
      input.value = "";
      ctx.setTitle("Start");
      view.innerHTML = `<div class="br-start"><b>Open a page beside the chart</b>` +
        `<span>Many sites refuse to be shown inside other apps; these allow it.</span>` +
        `<div class="br-tiles">${START.map((s) => `<button type="button" class="br-tile" data-go="${esc(s.u)}">` +
          `<i class="br-mono">${esc(s.t[0])}</i>` +
          `<span><b>${esc(s.t)}</b><em>${esc(s.d)}</em></span></button>`).join("")}</div></div>`;
    }

    async function go(raw, push = true) {
      let u = String(raw || "").trim();
      if (!u) return start();
      if (!looksLikeUrl(u)) u = "https://en.wikipedia.org/w/index.php?search=" + encodeURIComponent(u);
      if (!/^https?:\/\//i.test(u)) u = "https://" + u;
      if (push) { hist = hist.slice(0, at + 1); hist.push(u); at = hist.length - 1; ctx.setCfg({ url: u }); }
      input.value = u;
      let host_ = u;
      try { host_ = new URL(u).hostname.replace(/^www\./, ""); } catch { }
      ctx.setTitle(host_);
      view.innerHTML = `<div class="br-wait">${WKit.skel(6)}</div>`;
      let chk;
      try { chk = await json(`/frame-check?url=${encodeURIComponent(u)}`); }
      catch (e) { chk = { embeddable: false, reason: e.message }; }
      if (input.value !== u) return;           // navigated away meanwhile
      if (!chk.embeddable) {
        view.innerHTML = `<div class="side-empty">${Icons.svg("globe")}<p>${esc(chk.reason || "This page cannot be shown here.")}</p>` +
          `<a class="dk-cta" href="${esc(u)}" target="_blank" rel="noopener noreferrer">${ic("externalLink")}Open ${esc(host_)} in a new tab</a></div>`;
        return;
      }
      const f = document.createElement("iframe");
      f.className = "br-frame";
      f.src = chk.final || u;
      f.referrerPolicy = "no-referrer";
      f.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox");
      f.setAttribute("allow", "fullscreen");
      f.loading = "lazy";
      view.innerHTML = "";
      view.appendChild(f);
    }

    form.addEventListener("submit", (e) => { e.preventDefault(); go(input.value); input.blur(); });
    input.addEventListener("keydown", (e) => e.stopPropagation());
    input.addEventListener("focus", () => input.select());
    host.addEventListener("click", (e) => {
      const t = e.target.closest("[data-go]");
      if (t) return go(t.dataset.go);
      const b = e.target.closest("[data-br]");
      if (!b) return;
      const a = b.dataset.br;
      if (a === "back" && at > 0) return go(hist[--at], false);
      if (a === "fwd" && at < hist.length - 1) return go(hist[++at], false);
      if (a === "reload") { const f = view.querySelector("iframe"); if (f) f.src = f.src; return; }
      if (a === "home") { ctx.setCfg({ url: null }); return start(); }
      if (a === "out" && input.value) return open(input.value, "_blank", "noopener,noreferrer");
    });

    let booted = false;
    return {
      show() { if (booted) return; booted = true; ctx.cfg.url ? go(ctx.cfg.url) : start(); },
      receive(p) { if (p && p.url) go(p.url); },
      ask: () => input.value ? `I'm reading ${input.value}. Summarise what matters on it for a trader in India.` : "",
    };
  }

  Dock.register({
    type: "browser", title: "Browser", icon: "globe", hue: "cyan", group: "Tools",
    desc: "A web page beside the chart", zone: "right", minW: 320, mount,
  });
})();
