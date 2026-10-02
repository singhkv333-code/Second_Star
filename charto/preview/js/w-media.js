/* Charto preview — third-party widgets: live business TV and the economic
 * calendar.
 *
 * TV. The channels' own live streams on YouTube, embedded with YouTube's
 * privacy-enhanced player (youtube-nocookie.com), muted until you unmute.
 * The data server names the broadcast a channel is showing (/live-video); a
 * channel that is off air says so, and every tile carries a link to watch
 * the channel on YouTube itself. Open several for a wall of screens.
 *
 * Calendar. TradingView's free economic-calendar widget — their data, their
 * attribution, which stays visible as their terms ask. Filtered to India and
 * the United States by default; the filter is a setting.
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
      $(".tv-screen").innerHTML = `<iframe src="https://www.youtube-nocookie.com/embed/${encodeURIComponent(vid)}?autoplay=1&mute=1&rel=0&modestbranding=1" ` +
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
    return {
      show() { if (!playing) play(); },
      // a hidden tile stops playing: the frame is removed, not just covered
      hide() { playing = null; $(".tv-screen").innerHTML = ""; },
    };
  }

  Dock.register({
    type: "tv", title: "Live TV", icon: "tv", hue: "red", group: "Media", anim: "pulse",
    desc: "Business news channels, live", zone: "right", minW: 300, mount: tvMount,
    defaults: { ch: CHANNELS[0].id },
  });

  function calMount(host, ctx) {
    let built = null;
    function build() {
      const dark = document.documentElement.dataset.theme === "dark";
      const key = `${dark}|${ctx.cfg.countries}|${ctx.cfg.impact}`;
      if (built === key) return;
      built = key;
      host.innerHTML = `<div class="tradingview-widget-container cal-wrap"><div class="tradingview-widget-container__widget"></div>` +
        `<div class="cal-credit"><a href="https://www.tradingview.com/economic-calendar/" rel="noopener nofollow" target="_blank">Economic calendar by TradingView</a></div></div>`;
      const s = document.createElement("script");
      s.src = "https://s3.tradingview.com/external-embedding/embed-widget-events.js";
      s.async = true;
      s.textContent = JSON.stringify({
        colorTheme: dark ? "dark" : "light", isTransparent: true, width: "100%", height: "100%", locale: "en",
        importanceFilter: ctx.cfg.impact === "high" ? "1" : "0,1", countryFilter: ctx.cfg.countries || "in,us",
      });
      host.querySelector(".cal-wrap").appendChild(s);
    }
    if (typeof Theme !== "undefined" && Theme.onChange) Theme.onChange(() => { if (ctx.visible()) build(); });
    return {
      show: build,
      config() { built = null; build(); },
      ask: () => "What are the most important economic events for Indian markets this week, and how have markets usually reacted to them?",
    };
  }

  Dock.register({
    type: "calendar", title: "Calendar", icon: "calendar", hue: "azure", group: "Research",
    desc: "Economic events for India and the US", zone: "right", minW: 300, mount: calMount,
    defaults: { countries: "in,us", impact: "medium" },
    settings: [
      { key: "countries", label: "Countries", def: "in,us",
        options: [{ v: "in", label: "India" }, { v: "in,us", label: "India + US" }, { v: "in,us,eu,cn,jp,gb", label: "Major" }] },
      { key: "impact", label: "Importance", def: "medium", options: [{ v: "medium", label: "Medium+" }, { v: "high", label: "High" }] },
    ],
  });
})();
