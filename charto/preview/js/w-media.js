/* Charto preview — third-party widgets: live business TV and the economic
 * calendar.
 *
 * TV. The channels' own live streams on YouTube, embedded with YouTube's
 * privacy-enhanced player (youtube-nocookie.com), muted until you unmute.
 * The data server names the broadcast a channel is showing (/live-video); a
 * channel that is off air says so, and every tile carries a link to watch
 * the channel on YouTube itself. Open several for a wall of screens.
 *
 * Calendar. Our own feed (/calendar, calfeed.py): NSE's board meetings and
 * corporate-action ex-dates, and the macro schedule (RBI, MOSPI, Fed, BLS).
 * No third-party embed and no yfinance; each source's status is shown.
 */
"use strict";

(() => {
  if (typeof Dock === "undefined") return;
  const { esc, ic, json } = WKit;

  // channel ids verified against each channel's YouTube page
  const CHANNELS = [
    { id: "UCmRbHAgG2k2vDUvb3xsEunQ", name: "CNBC-TV18" },
    { id: "UCQIycDaLsBpMKjOCeaKUYVg", name: "CNBC Awaaz" },
    { id: "UC3uJIdRFTGgLWrUziaHbzrg", name: "NDTV Profit" },
    { id: "UCIALMKvObZNtJ6AmdCLP7Lg", name: "Bloomberg TV" },
    { id: "UCvJJ_dzjViJCoLf5uKUTwoA", name: "CNBC" },
  ];

  function tvMount(host, ctx) {
    host.innerHTML =
      `<div class="tv-bar"><div class="tv-chips"></div><span class="sh-gap"></span>` +
        `<a class="sh-btn i tv-out" target="_blank" rel="noopener noreferrer" title="Watch on YouTube">${ic("externalLink")}</a></div>` +
      `<div class="tv-screen"></div>`;
    const $ = (s) => host.querySelector(s);
    let playing = null;

    function chips() {
      $(".tv-chips").innerHTML = CHANNELS.map((c) =>
        `<button type="button" class="tv-chip${c.id === ctx.cfg.ch ? " on" : ""}" data-ch="${c.id}">${esc(c.name)}</button>`).join("") +
        `<button type="button" class="tv-chip" data-ch="custom" title="Any YouTube video or live stream">${ic("plus")}</button>`;
    }
    function frame(vid, title) {
      const q = `autoplay=1&mute=${ctx.cfg.muted === false ? 0 : 1}&rel=0&modestbranding=1${ctx.cfg.captions ? "&cc_load_policy=1" : ""}`;
      $(".tv-screen").innerHTML = `<iframe src="https://www.youtube-nocookie.com/embed/${encodeURIComponent(vid)}?${q}" ` +
        `title="${esc(title)}" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe>`;
    }
    async function play() {
      const ch = ctx.cfg.ch || CHANNELS[0].id;
      const c = CHANNELS.find((x) => x.id === ch);
      const vid = ctx.cfg.video;
      const key = vid || ch;
      chips();
      $(".tv-out").href = vid ? `https://www.youtube.com/watch?v=${encodeURIComponent(vid)}` : `https://www.youtube.com/channel/${ch}/live`;
      ctx.setTitle(vid ? "Video" : c ? c.name : "Live");
      if (playing === key) return;
      playing = key;
      if (vid) return frame(vid, "Video");
      // YouTube's embed-a-channel's-stream address no longer plays, so the
      // data server reads which broadcast the channel is showing right now
      $(".tv-screen").innerHTML = "";
      let r;
      try { r = await json(`/live-video?channel=${encodeURIComponent(ch)}`); }
      catch (e) { r = { video: null, error: e.message }; }
      if (playing !== key) return;
      if (r.video && r.live) return frame(r.video, c ? c.name : "Live");
      $(".tv-screen").innerHTML = `<div class="tv-off">${Icons.svg("tv")}<p>${esc(c ? c.name : "This channel")} ` +
        `${r.error ? `could not be reached: ${esc(r.error)}` : "is not broadcasting live right now."}</p>` +
        `<a class="dk-cta" href="https://www.youtube.com/channel/${ch}" target="_blank" rel="noopener noreferrer">${ic("externalLink")}Open the channel</a></div>`;
    }
    host.addEventListener("click", (e) => {
      const b = e.target.closest("[data-ch]");
      if (!b) return;
      e.stopPropagation();
      if (b.dataset.ch !== "custom") { ctx.setCfg({ ch: b.dataset.ch, video: null }); return play(); }
      const p = ctx.menu(b, `<div class="head">Play a YouTube link</div>` +
        `<form class="tv-form"><input name="u" placeholder="Paste a YouTube link" autocomplete="off"><button class="dk-cta" type="submit">Play</button></form>`, null);
      if (!p) return;
      const f = p.querySelector("form");
      f.u.focus();
      f.u.addEventListener("keydown", (ev) => ev.stopPropagation());
      f.addEventListener("submit", (ev) => {
        ev.preventDefault();
        const m = f.u.value.match(/(?:v=|youtu\.be\/|\/live\/|\/embed\/|shorts\/)([\w-]{11})/);
        if (!m) return ctx.toast("That is not a YouTube video link.");
        Dock.closeMenu();
        ctx.setCfg({ video: m[1] }); play();
      });
    });
    const prefs = () => host.classList.toggle("tv-nochips", ctx.cfg.chips === false);
    prefs();
    return {
      show() { if (!playing) play(); },
      config(cfg, patch) {
        prefs();
        // sound and captions are the player's own parameters: reload it
        if ("muted" in patch || "captions" in patch) { playing = null; play(); }
        if ("ch" in patch) { ctx.cfg.video = null; playing = null; play(); }
      },
      // a hidden tile stops playing: the frame is removed, not just covered
      hide() { playing = null; $(".tv-screen").innerHTML = ""; },
    };
  }

  Dock.register({
    type: "tv", title: "Live TV", icon: "tv", hue: "red", group: "Media", anim: "pulse",
    desc: "Business news channels, live", zone: "right", minW: 300, mount: tvMount,
    defaults: { ch: CHANNELS[0].id },
    settings: [
      { section: "Channel" },
      { key: "ch", label: "Plays", kind: "select", def: CHANNELS[0].id, options: CHANNELS.map((c) => ({ v: c.id, label: c.name })) },
      { key: "chips", label: "Channel buttons", kind: "toggle", def: true },
      { section: "Player" },
      { key: "muted", label: "Start muted", kind: "toggle", def: true, hint: "Browsers only autoplay video that starts muted" },
      { key: "captions", label: "Captions when the channel has them", kind: "toggle", def: false },
      { kind: "note", label: "A tile that is hidden stops playing; nothing streams in the background." },
    ],
  });

  /* Calendar — our own feed (/calendar): NSE board meetings and ex-dates, and
   * the macro schedule. Sections as TradingView's calendar has them; rows are
   * a logo, the company, and the one fact that matters for that kind. */
  const TABS = [
    ["all", "All"], ["results", "Earnings"], ["dividend", "Dividends"],
    ["action", "Splits & bonus"], ["board", "Board"], ["macro", "Economy"],
  ];
  const FEED_OF = { macro: "macro", results: "board", board: "board", dividend: "actions", action: "actions" };
  const ACT = { bonus: "Bonus", split: "Split", rights: "Rights", buyback: "Buyback", demerger: "Demerger", merger: "Merger", action: "Corporate action" };
  // flags for the two economies the macro schedule covers
  const FLAG = {
    IN: `<svg viewBox="0 0 30 30" class="cl-flag" aria-label="India"><clipPath id="clf-in"><circle cx="15" cy="15" r="15"/></clipPath><g clip-path="url(#clf-in)"><rect width="30" height="10" fill="#FF9933"/><rect y="10" width="30" height="10" fill="#fff"/><rect y="20" width="30" height="10" fill="#138808"/><circle cx="15" cy="15" r="3.2" fill="none" stroke="#000080" stroke-width=".9"/></g></svg>`,
    US: `<svg viewBox="0 0 30 30" class="cl-flag" aria-label="United States"><clipPath id="clf-us"><circle cx="15" cy="15" r="15"/></clipPath><g clip-path="url(#clf-us)"><rect width="30" height="30" fill="#fff"/>${[0, 2, 4, 6, 8, 10, 12].map((k) => `<rect y="${k * 2.31}" width="30" height="2.31" fill="#B22234"/>`).join("")}<rect width="14" height="16.2" fill="#3C3B6E"/></g></svg>`,
  };

  function watchSyms() {
    try {
      const w = Store.get("watchlists", null);
      return [...new Set((w && w.lists || []).flatMap((l) => l.syms || []))];
    } catch { return []; }
  }
  const IST = { timeZone: "Asia/Kolkata" };
  const dOf = (iso) => new Date(iso + "T00:00:00+05:30");
  const short = (iso) => dOf(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", ...IST });
  const dayHead = (iso, today) => {
    const diff = Math.round((dOf(iso) - dOf(today)) / 864e5);
    const wd = dOf(iso).toLocaleDateString("en-IN", { weekday: "short", ...IST });
    return { main: `${wd}, ${short(iso)}`, rel: diff === 0 ? "Today" : diff === 1 ? "Tomorrow" : "" };
  };
  const coName = (t) => String(t || "").replace(/\s+(Limited|Ltd\.?)$/i, "").trim();
  const money = (v) => "₹" + (Number.isInteger(v) ? v : v.toFixed(2));

  /** The one fact a row states, in words a trader reads at a glance. */
  function fact(it, all) {
    if (it.kind === "results") return { v: it.period || "Results", sub: all ? "Results" : (it.also || []).join(" · ") };
    if (it.kind === "dividend") return { v: it.amount != null ? money(it.amount) : "Dividend",
      sub: [all ? "Dividend" : it.dtype, it.record && it.record !== it.date ? `Record ${short(it.record)}` : ""].filter(Boolean).join(" · ") };
    if (it.kind === "action") return { v: it.ratio ? `${ACT[it.action] || "Action"} ${it.ratio}` : ACT[it.action] || "Corporate action",
      sub: it.record && it.record !== it.date ? `Record ${short(it.record)}` : "" };
    if (it.kind === "board") return { v: (it.also && it.also[0]) || "Board meeting", sub: all ? "Board meeting" : "" };
    return { v: it.time ? `${it.time}` : "", sub: it.approx ? "Expected" : "IST" };
  }
  /** One sentence for a note, a copy or the chat. */
  function sentence(it) {
    const f = fact(it, true);
    const nm = coName(it.title);
    const who = it.symbol ? (nm && nm.toUpperCase() !== it.symbol ? `${nm} (${it.symbol})` : it.symbol) : it.title;
    const when = `${dayHead(it.date, it.date).main}${it.time ? " " + it.time + " IST" : ""}`;
    const what = it.kind === "results" ? `board meets on results${it.period ? " for " + it.period : ""}`
      : it.kind === "dividend" ? `goes ex-dividend${it.amount != null ? ", " + money(it.amount) + " a share" : ""}`
      : it.kind === "action" ? `goes ex-${(ACT[it.action] || "action").toLowerCase()}${it.ratio ? " " + it.ratio : ""}`
      : it.kind === "board" ? `board meets: ${it.purpose || "other business"}`
      : (it.approx ? "expected (the usual date; not yet published)" : "scheduled");
    return `${when} — ${who} ${what}.${f.sub && /Record/.test(f.sub) ? " " + f.sub.replace(/.*(Record [^·]+).*/, "$1") + "." : ""}`;
  }
  function ics(it) {
    const d = it.date.replace(/-/g, "");
    let when;
    if (it.time) {
      const t = new Date(`${it.date}T${it.time}:00+05:30`).toISOString().replace(/[-:]/g, "").replace(/\.\d+/, "");
      const e = new Date(new Date(`${it.date}T${it.time}:00+05:30`).getTime() + 30 * 60e3).toISOString().replace(/[-:]/g, "").replace(/\.\d+/, "");
      when = `DTSTART:${t}\r\nDTEND:${e}`;
    } else {
      const nx = new Date(dOf(it.date).getTime() + 864e5).toISOString().slice(0, 10).replace(/-/g, "");
      when = `DTSTART;VALUE=DATE:${d}\r\nDTEND;VALUE=DATE:${nx}`;
    }
    const sum = (it.symbol ? `${it.symbol}: ` : "") + (it.kind === "macro" ? it.title : fact(it, true).sub + " " + fact(it, true).v);
    const body = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Pivot//Calendar//EN", "BEGIN:VEVENT",
      `UID:${d}-${(it.symbol || it.title).replace(/\W/g, "")}-${it.kind}@pivot`, `DTSTAMP:${new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+/, "")}`,
      when, `SUMMARY:${sum.replace(/[,;]/g, " ")}`, `DESCRIPTION:${sentence(it).replace(/[,;]/g, " ")} Source: ${it.source}.`, "END:VEVENT", "END:VCALENDAR"].join("\r\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([body], { type: "text/calendar" }));
    a.download = `${(it.symbol || it.title).replace(/\W+/g, "-")}-${it.date}.ics`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }

  function calMount(host, ctx) {
    host.innerHTML =
      `<div class="cl-bar"><div class="cl-tabs" role="tablist"></div>` +
        `<button type="button" class="sh-btn i" data-cl="refresh" title="Refresh">${ic("rotateCw")}</button></div>` +
      `<div class="cl-list"></div><div class="cl-foot"></div>`;
    const $ = (q) => host.querySelector(q);
    let data = null, busy = false, rows = [];
    const cfg = () => ctx.cfg;
    const tab = () => cfg().tab || "all";

    function scopeSyms() {
      if (cfg().scope === "watch") return watchSyms();
      if (cfg().scope === "symbol") { const s = ctx.symbol(); return s ? [s] : []; }
      return null;
    }

    async function load() {
      if (busy) return;
      busy = true;
      if (!data) $(".cl-list").innerHTML = WKit.skel(10, "cl-skel");
      const syms = scopeSyms();
      const q = new URLSearchParams({ days: String(cfg().days || 14), kinds: "macro,board,actions" });
      if (syms) q.set("symbols", syms.join(",") || "-");
      try { data = await json(`/calendar?${q}`); }
      catch (e) { data = null; $(".cl-list").innerHTML = WKit.empty("calendar", "The calendar is unavailable right now."); $(".cl-foot").innerHTML = ""; busy = false; return; }
      busy = false;
      if (typeof Universe !== "undefined" && Universe.load) Universe.load().then(() => paint());
      paint();
    }

    function visible() {
      if (!data) return [];
      const t = tab(), reg = cfg().region || "all";
      return data.items.filter((it) => (t === "all" || it.kind === t)
        && !(it.kind === "macro" && reg !== "all" && it.region !== reg));
    }

    function mark(it) {
      if (it.kind === "macro") return FLAG[it.region] || `<span class="cl-mono">${esc(it.region)}</span>`;
      const img = typeof Universe !== "undefined" ? Universe.logoHTML(it.symbol, "cl-logo") : "";
      return img ? img.replace('loading="lazy"', "") : `<span class="cl-mono">${esc((coName(it.title) || it.symbol || "?")[0])}</span>`;
    }

    function paint() {
      $(".cl-tabs").innerHTML = TABS.map(([v, l]) =>
        `<button type="button" role="tab" class="cl-tab${tab() === v ? " on" : ""}" data-t="${v}" aria-selected="${tab() === v}">${esc(l)}</button>`).join("");
      host.classList.toggle("cl-compact", cfg().density === "compact");
      if (!data) return;
      rows = visible();
      const list = $(".cl-list"), all = tab() === "all";
      if (!rows.length) {
        list.innerHTML = WKit.empty("calendar", cfg().scope === "watch" ? "Nothing scheduled for your watchlist in this window."
          : cfg().scope === "symbol" ? `Nothing scheduled for ${esc(ctx.symbol() || "this symbol")} in this window.` : "Nothing scheduled in this window.");
      } else {
        let last = null, html = "";
        rows.forEach((it, i) => {
          if (it.date !== last) {
            last = it.date;
            const h = dayHead(it.date, data.today);
            html += `<h4 class="cl-day"><b>${esc(h.main)}</b>${h.rel ? `<span>${h.rel}</span>` : ""}</h4>`;
          }
          const f = fact(it, all);
          const name = it.kind === "macro" ? it.title : coName(it.title) || it.symbol;
          html += `<div class="cl-row" data-i="${i}" title="${esc(sentence(it))}">` +
            `<span class="cl-mark">${mark(it)}</span>` +
            `<span class="cl-who"><b>${esc(name)}</b><em>${esc(it.kind === "macro" ? (it.region === "IN" ? "India" : "United States") : it.symbol)}</em></span>` +
            `<span class="cl-fact"><b>${esc(f.v)}</b>${f.sub ? `<em>${esc(f.sub)}</em>` : ""}</span>` +
            `<button type="button" class="cl-more" data-more="${i}" title="More" aria-label="More">${ic("more")}</button></div>`;
        });
        list.innerHTML = html;
      }
      const down = data.sources.filter((s) => !s.ok), stale = data.sources.filter((s) => s.stale);
      $(".cl-foot").innerHTML = `<span>NSE · RBI, MOSPI, Fed and BLS schedules</span>` +
        (down.length ? `<span class="cl-warn">${esc(down.map((s) => s.name).join(", "))} unavailable</span>`
          : stale.length ? `<span class="cl-warn">${esc(stale.map((s) => s.name).join(", "))}: last copy</span>` : "");
    }

    function menu(anchor, it) {
      const sym = it.symbol;
      ctx.menu(anchor, [
        ...(sym ? [{ id: "open", label: `Open ${sym}`, icon: "lineChart" },
                   { id: "watch", label: "Add to watchlist", icon: "star" }] : []),
        { id: "note", label: "Save to notes", icon: "note" },
        { id: "ics", label: "Add to my calendar", icon: "calendar" },
        { id: "ask", label: "Ask in chat", icon: "chat" },
        { id: "copy", label: "Copy", icon: "copy" },
      ], (id) => {
        if (id === "open") return ctx.pick(sym);
        if (id === "watch") {
          if (typeof Panels !== "undefined" && Panels.watch) { Panels.watch(sym); ctx.toast(`${sym} is on your watchlist.`); }
          return;
        }
        if (id === "note") {
          const h = dayHead(it.date, it.date).main;
          return ctx.send("notes", { symbol: sym, general: !sym,
            html: `<p><b>${esc(h)}</b> — ${esc(sentence(it).replace(/^[^—]+—\s*/, ""))} <i>(${esc(it.source)})</i></p>` });
        }
        if (id === "ics") return ics(it);
        if (id === "ask") return ctx.compose(it.kind === "macro"
          ? `${sentence(it)} What does it usually mean for Indian markets, and how have NIFTY and BANKNIFTY moved around past releases?`
          : `${sentence(it)} What should I look at on ${sym}'s chart and financials before this date, and how has the stock usually moved around it?`);
        if (id === "copy") { try { navigator.clipboard.writeText(sentence(it)); ctx.toast("Copied."); } catch { } }
      });
    }

    host.addEventListener("click", (e) => {
      const t = e.target.closest("[data-t]");
      if (t) { ctx.setCfg({ tab: t.dataset.t }); host.querySelector(".cl-list").scrollTop = 0; return paint(); }
      const b = e.target.closest("[data-cl]");
      if (b && b.dataset.cl === "refresh") return load();
      const m = e.target.closest("[data-more]");
      if (m) { e.stopPropagation(); return menu(m, rows[+m.dataset.more]); }
      const r = e.target.closest(".cl-row");
      if (r) { const it = rows[+r.dataset.i]; if (it && it.symbol) ctx.pick(it.symbol); else if (it) menu(r.querySelector(".cl-more"), it); }
    });
    host.addEventListener("contextmenu", (e) => {
      const r = e.target.closest(".cl-row");
      if (!r) return;
      e.preventDefault();
      menu({ x: e.clientX, y: e.clientY }, rows[+r.dataset.i]);
    });

    let timer = 0;
    return {
      show() {
        load();
        clearInterval(timer);
        timer = setInterval(() => { if (document.visibilityState === "visible") load(); }, 30 * 60e3);
      },
      hide() { clearInterval(timer); },
      config(c, patch) {
        if ("days" in patch || "scope" in patch || "symbol" in patch || "link" in patch) return load();
        paint();
      },
      ask: () => {
        const r = visible().slice(0, 25);
        return r.length ? `On the calendar for the next ${cfg().days || 14} days:\n` + r.map((it) => "- " + sentence(it)).join("\n") +
          `\n\nWhich of these matter most for Indian markets, and what has usually happened around them?` : "";
      },
    };
  }

  Dock.register({
    type: "calendar", title: "Calendar", icon: "calendar", hue: "azure", group: "Research",
    desc: "Earnings, dividends, splits and the economy", zone: "right", minW: 280, mount: calMount,
    linkable: true,
    defaults: { days: 14, scope: "all", tab: "all" },
    settings: [
      { section: "What to show" },
      { key: "scope", label: "Companies", kind: "seg", def: "all",
        options: [{ v: "all", label: "All NSE" }, { v: "watch", label: "Watchlist" }, { v: "symbol", label: "This symbol" }],
        hint: "This symbol follows the chart, or the tile's link group" },
      { key: "region", label: "Economy", kind: "seg", def: "all",
        options: [{ v: "all", label: "India + US" }, { v: "IN", label: "India" }, { v: "US", label: "US" }] },
      { key: "days", label: "Look ahead", kind: "seg", def: 14,
        options: [{ v: 7, label: "1 week" }, { v: 14, label: "2 weeks" }, { v: 30, label: "1 month" }] },
      { section: "Display" },
      { key: "density", label: "Rows", kind: "seg", def: "comfortable", options: [{ v: "comfortable", label: "Comfortable" }, { v: "compact", label: "Compact" }] },
      { kind: "note", label: "Click a company to open its chart; right-click, or ⋯, to save it to notes, add it to your calendar, or ask about it. Sources: NSE's event calendar and corporate actions, and Pivot's macro schedule. Refreshed every 30 minutes." },
    ],
  });
})();
