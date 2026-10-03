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
   * the macro schedule. Grouped by day; each kind keeps one colour. */
  const KINDS = {
    macro:   { label: "Macro",     cls: "k-macro" },
    results: { label: "Results",   cls: "k-results" },
    board:   { label: "Board",     cls: "k-board" },
    action:  { label: "Ex-date",   cls: "k-action" },
  };
  const ACT = { dividend: "Dividend", bonus: "Bonus", split: "Split", rights: "Rights", buyback: "Buyback", action: "Action" };
  const FEED_OF = { macro: "macro", results: "board", board: "board", action: "actions" };

  function watchSyms() {
    try {
      const w = Store.get("watchlists", null);
      return [...new Set((w && w.lists || []).flatMap((l) => l.syms || []))];
    } catch { return []; }
  }
  const dayName = (iso, today) => {
    const d = new Date(iso + "T00:00:00+05:30");
    const diff = Math.round((d - new Date(today + "T00:00:00+05:30")) / 864e5);
    const rel = diff === 0 ? "Today" : diff === 1 ? "Tomorrow" : d.toLocaleDateString("en-IN", { weekday: "long", timeZone: "Asia/Kolkata" });
    return { rel, date: d.toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" }) };
  };

  function calMount(host, ctx) {
    host.innerHTML =
      `<div class="cl-bar"><div class="cl-kinds" role="tablist"></div><span class="sh-gap"></span>` +
        `<button type="button" class="sh-btn i" data-cl="refresh" title="Refresh">${ic("rotateCw")}</button></div>` +
      `<div class="cl-list"></div><div class="cl-foot"></div>`;
    const $ = (q) => host.querySelector(q);
    let data = null, busy = false, open = new Set();
    const cfg = () => ctx.cfg;
    const kinds = () => (Array.isArray(cfg().kinds) && cfg().kinds.length ? cfg().kinds : Object.keys(KINDS));

    function scopeSyms() {
      if (cfg().scope === "watch") return watchSyms();
      if (cfg().scope === "symbol") { const s = ctx.symbol(); return s ? [s] : []; }
      return null;
    }

    async function load(force) {
      if (busy) return;
      busy = true;
      if (!data) $(".cl-list").innerHTML = WKit.skel(10, "cl-skel");
      const feeds = [...new Set(kinds().map((k) => FEED_OF[k]))];
      const syms = scopeSyms();
      const q = new URLSearchParams({ days: String(cfg().days || 14), kinds: feeds.join(",") });
      if (syms) q.set("symbols", syms.join(",") || "-");
      try { data = await json(`/calendar?${q}`); }
      catch (e) { data = null; $(".cl-list").innerHTML = WKit.empty("calendar", "The calendar is unavailable right now."); $(".cl-foot").innerHTML = ""; busy = false; return; }
      busy = false;
      paint();
    }

    function visible() {
      if (!data) return [];
      const want = new Set(kinds()), f = cfg().filter || "all", reg = cfg().region || "all";
      return data.items.filter((it) => want.has(it.kind) && (f === "all" || it.kind === f)
        && !(it.kind === "macro" && reg !== "all" && it.region !== reg));
    }

    function paint() {
      const k = kinds(), f = cfg().filter || "all";
      const counts = {};
      for (const it of (data ? data.items : [])) counts[it.kind] = (counts[it.kind] || 0) + 1;
      $(".cl-kinds").innerHTML = [["all", "All"], ...k.map((x) => [x, KINDS[x].label])].map(([v, l]) =>
        `<button type="button" role="tab" class="cl-k ${v === "all" ? "" : KINDS[v].cls}${f === v ? " on" : ""}" data-f="${v}">` +
        `${v === "all" ? "" : "<i></i>"}${esc(l)}${v !== "all" && counts[v] ? `<span>${counts[v]}</span>` : ""}</button>`).join("");
      host.classList.toggle("cl-compact", cfg().density === "compact");
      if (!data) return;
      const rows = visible();
      const list = $(".cl-list");
      if (!rows.length) {
        list.innerHTML = WKit.empty("calendar", cfg().scope === "watch" ? "Nothing scheduled for your watchlist in this window."
          : cfg().scope === "symbol" ? `Nothing scheduled for ${esc(ctx.symbol() || "this symbol")} in this window.` : "Nothing scheduled in this window.");
      } else {
        const byDay = new Map();
        for (const it of rows) (byDay.get(it.date) || byDay.set(it.date, []).get(it.date)).push(it);
        list.innerHTML = [...byDay].map(([day, its]) => {
          const n = dayName(day, data.today);
          return `<section class="cl-day"><h4><b>${esc(n.rel)}</b><span>${esc(n.date)}</span><em>${its.length}</em></h4>` +
            its.map((it, i) => row(it, `${day}|${i}|${it.symbol || it.title}`)).join("") + `</section>`;
        }).join("");
      }
      const srcs = data.sources.map((s) => `<span class="cl-src${s.ok ? (s.stale ? " stale" : "") : " off"}" title="${s.ok ? (s.stale ? "Showing the last copy; the feed did not answer" : "Updated " + WKit.ago(s.as_of)) : "Unavailable right now"}"><i></i>${esc(s.name)}</span>`).join("");
      $(".cl-foot").innerHTML = srcs + (data.items.some((x) => x.approx) ? `<span class="cl-note">~ usual date, not yet published</span>` : "");
    }

    function row(it, key) {
      const K = KINDS[it.kind] || KINDS.board;
      const tag = it.kind === "action" ? ACT[it.action] || "Ex-date" : it.kind === "macro" ? `${K.label} · ${it.region}` : K.label;
      const sym = it.symbol ? `<button type="button" class="cl-sym" data-sym="${esc(it.symbol)}" title="Open ${esc(it.symbol)}">${esc(it.symbol)}</button>` : "";
      const when = it.time ? `<time>${esc(it.time)} IST</time>` : "";
      const name = it.kind === "macro" ? it.title : (it.title || it.symbol);
      const sub = it.kind === "results" ? "Financial results" : it.kind === "board" ? (it.purpose || "Board meeting")
        : it.kind === "macro" ? it.detail : it.detail;
      const long = cfg().details !== false && it.detail && it.kind !== "action" && it.kind !== "macro";
      const isOpen = open.has(key);
      return `<div class="cl-row ${K.cls}${isOpen ? " open" : ""}${long ? " more" : ""}" data-key="${esc(key)}">` +
        `<span class="cl-rail"></span>` +
        `<div class="cl-main"><div class="cl-t"><span class="cl-name" title="${esc(name)}">${esc(name)}</span>` +
          `${it.approx ? `<span class="cl-approx" title="The usual date; not yet published">~ date</span>` : ""}</div>` +
          `<div class="cl-sub">${sym}${when}<span>${esc(sub || "")}</span></div>` +
          (long && isOpen ? `<p class="cl-more">${esc(it.detail)}</p>` : "") + `</div>` +
        `<span class="cl-tag">${esc(tag)}</span></div>`;
    }

    host.addEventListener("click", (e) => {
      const s = e.target.closest("[data-sym]");
      if (s) { e.stopPropagation(); return ctx.pick(s.dataset.sym); }
      const f = e.target.closest("[data-f]");
      if (f) { ctx.setCfg({ filter: f.dataset.f }); return paint(); }
      const b = e.target.closest("[data-cl]");
      if (b && b.dataset.cl === "refresh") return load(true);
      const r = e.target.closest(".cl-row");
      if (r && r.classList.contains("more")) { const k = r.dataset.key; open.has(k) ? open.delete(k) : open.add(k); paint(); }
    });

    let timer = 0;
    return {
      show() {
        load(!data);
        clearInterval(timer);
        timer = setInterval(() => { if (document.visibilityState === "visible") load(false); }, 30 * 60e3);
      },
      hide() { clearInterval(timer); },
      config(c, patch) {
        if ("days" in patch || "kinds" in patch || "scope" in patch || "symbol" in patch || "link" in patch) return load(true);
        paint();
      },
      ask: () => {
        const rows = visible().slice(0, 25);
        return rows.length ? `These are on the calendar for the next ${cfg().days || 14} days:\n` +
          rows.map((it) => `- ${it.date}${it.time ? " " + it.time + " IST" : ""}: ${it.kind === "macro" ? it.title : `${it.symbol} — ${it.kind === "action" ? it.detail : it.purpose || it.kind}`}${it.approx ? " (usual date, not yet published)" : ""}`).join("\n") +
          `\n\nWhich of these matter most for Indian markets, and what has usually happened around them?` : "";
      },
    };
  }

  Dock.register({
    type: "calendar", title: "Calendar", icon: "calendar", hue: "azure", group: "Research",
    desc: "Results, ex-dates, board meetings and macro events", zone: "right", minW: 280, mount: calMount,
    linkable: true,
    defaults: { days: 14, scope: "all" },
    settings: [
      { section: "What to show" },
      { key: "kinds", label: "Events", kind: "chips", min: 1, def: ["macro", "results", "action", "board"],
        options: Object.entries(KINDS).map(([v, k]) => ({ v, label: k.label })) },
      { key: "scope", label: "Companies", kind: "seg", def: "all",
        options: [{ v: "all", label: "All NSE" }, { v: "watch", label: "Watchlist" }, { v: "symbol", label: "This symbol" }],
        hint: "This symbol follows the chart, or the tile's link group" },
      { key: "region", label: "Macro events", kind: "seg", def: "all",
        options: [{ v: "all", label: "India + US" }, { v: "IN", label: "India" }, { v: "US", label: "US" }] },
      { key: "days", label: "Look ahead", kind: "seg", def: 14,
        options: [{ v: 7, label: "1 week" }, { v: 14, label: "2 weeks" }, { v: 30, label: "1 month" }] },
      { section: "Display" },
      { key: "density", label: "Rows", kind: "seg", def: "comfortable", options: [{ v: "comfortable", label: "Comfortable" }, { v: "compact", label: "Compact" }] },
      { key: "details", label: "Expand a row for the full notice", kind: "toggle", def: true },
      { kind: "note", label: "Sources: NSE's event calendar and corporate actions, and Pivot's macro schedule (RBI, MOSPI, Fed, BLS). Refreshed every 30 minutes." },
    ],
  });
})();
