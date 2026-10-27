/* Pivot Plays — the app (plays.html).
 *
 * Home is a conversation: say a trade, watch Pivot work through its steps
 * (Catalyst's research list), and get the play back as its canvas, then keep
 * talking to change it. Strategies is every play as a tile and each play's
 * own page (its canvas beside its price and activity). Inbox is every fire
 * written up as one alert. The paper book and the day book are not drawn
 * here: they are the app's Portfolio page.
 *
 * The chat is the same /chat the chart uses, told it is on the Plays page
 * (`context.surface = "plays"`), so it knows there is no chart and which
 * plays already exist. Conversations are kept on the server (/plays/chats)
 * with their cards as references, so a thread opened on another device, or
 * days later, draws each play as it is now; this browser keeps a copy so the
 * page opens before the server answers.
 */
"use strict";

(() => {
  const { esc, ic, call } = Kit;
  const $ = (id) => document.getElementById(id);
  const KEY = "pivot:plays:convs", MAP = "pivot:plays:where", HIDE = "pivot:plays:hidden";
  const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) || d; } catch { return d; } };
  const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* full */ } };

  // Framed in the Pivot shell (its Execution tab), a chart link switches the
  // shell to its Chart tab rather than opening a second window, and the Inbox
  // is the shell's bell (beside the avatar), not a tab here
  const framed = window.parent !== window;
  // Plays live only inside Pivot: its Execution tab frames this page. Opened
  // on its own, the page sends the person there rather than running a second
  // copy with its own chrome; off the app's origin it says where to go.
  if (!framed) {
    if (location.pathname.startsWith("/chart-app/")) { location.replace("/#plays"); return; }
    $("main").innerHTML = `<div class="signin"><h2>Plays open inside Pivot</h2><p>Use the Execution tab in the Pivot app.</p></div>`;
    return;
  }
  if (framed) {
    document.addEventListener("click", (e) => {
      const a = e.target.closest && e.target.closest('a[target="pivot-chart"]');
      if (!a) return;
      e.preventDefault();
      const sym = new URL(a.href, location.href).searchParams.get("symbol") || "";
      window.parent.postMessage({ type: "plays:open-chart", symbol: sym }, location.origin);
    });
  }

  let convs = load(KEY, []), where = load(MAP, {}), hidden = new Set(load(HIDE, []));
  let data = { plays: [], events: [], securities: [] };
  let cur = null, busy = false, route = { page: "home" };
  const main = $("main");

  /* ── routing ──────────────────────────────────────────────────────────── */
  function parse() {
    const h = location.hash.replace(/^#\/?/, "");
    const [a, b] = h.split("/");
    if (a === "c" && b) return { page: "home", conv: b };
    if (a === "s" && b) return { page: "strategy", id: +b };
    if (["strategies", "inbox"].includes(a)) return { page: a };
    return { page: "home" };
  }
  const go = (hash) => { if (location.hash !== hash) location.hash = hash; else render(); };
  window.addEventListener("hashchange", render);

  function paintStatus() {
    const waiting = (data.events || []).filter((e) => e.kind === "proposed" && e.status === "open").length;
    const n = waiting || (data.events || []).filter((e) => !e.seen && !hidden.has(e.id)).length;
    // framed, the shell's bell carries the count
    if (framed) window.parent.postMessage({ type: "plays:inbox-count", count: n }, location.origin);
    const h = data.halt, bar = $("haltbar");
    bar.hidden = !h;
    if (h) bar.innerHTML = `${ic("ban")}<span><b>Trading is halted.</b> ${esc(h.text.replace(/^trading is halted /, ""))}. Exits still run.</span>` +
      `<button type="button" class="btn sm" data-lift>Lift it</button>`;
  }

  function render() {
    route = parse();
    paintStatus();
    if (!Auth.user) return signIn();
    if (route.page === "home") return home(route.conv);
    if (route.page === "strategies") return strategies();
    if (route.page === "strategy") return strategyPage(route.id);
    if (route.page === "inbox") return inbox();
  }

  /* ── review: the trader's own trades ──────────────────────────────────── */
  const ASKS = [
    ["chart", "What do my trades say I should change?"],
    ["shield", "Stop my trading for the day after two losses in a row"],
    ["ban", "Halt new intraday trades once I am down ₹5,000 on the day"],
    ["clock", "Stop me opening more than four intraday trades a day"],
  ];
  async function loadHomeReview() {
    const section = $("homeReview");
    if (!section) return;
    const content = section.querySelector("[data-review-content]");
    try {
      const r = await call("/plays/review");
      if (!section.isConnected) return;
      content.replaceChildren(Kit.review(r));
    } catch (err) {
      if (section.isConnected) content.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
    }
  }

  async function refresh() {
    if (!Auth.user) return;
    try { data = await call("/plays"); } catch { return; }
    paintStatus();
    if (route.page === "strategies" && !document.querySelector(".field:focus")) strategies(true);
    if (route.page === "inbox") inbox(true);
  }

  /* ── home: the conversation ───────────────────────────────────────────── */
  const IDEAS = [
    ["bell", "Tell me when HDFC Bank closes below its 50-day average"],
    ["file", "If Reliance files an order win on NSE, propose buying ₹10,000 of it"],
    ["layers", "Build a basket of the five largest defence stocks by market cap and tell me why each made it"],
    ["bolt", "Go 3× long BTC-PERP with ₹20,000 margin when funding turns negative, stop 3%"],
    ["chart", "Short RELIANCE intraday if it breaks the first 15 minutes' low, trail 0.8%"],
    ["target", "Sell the NIFTY ATM straddle at 9:20 with a 25% stop on each leg"],
    ["scale", "Rebalance my paper portfolio to equal weight and keep it that way"],
  ];

  function composerHTML(id, placeholder, ctx) {
    return `<form class="composer" data-form="${id}"><textarea rows="1" placeholder="${esc(placeholder)}" data-input></textarea>` +
      `<div class="bar">${ctx || ""}<span class="grow"></span>` +
      (window.webkitSpeechRecognition || window.SpeechRecognition ? `<button type="button" class="mic" data-mic title="Speak">${ic("mic")}</button>` : "") +
      `<button type="submit" class="send" disabled aria-label="Send">${ic("up")}</button></div></form>`;
  }

  function recentHTML() {
    const recent = convs.slice(0, 6);
    return recent.length ? `<div style="margin:0 0 60px"><div class="panel-h" style="margin-left:0">${ic("list")}<span>Recent conversations</span></div>` +
      `<div class="grid">${recent.map((c) => `<button type="button" class="sc" data-conv="${esc(c.id)}"><div class="top2"><b>${esc(c.title)}</b></div>` +
      `<div class="desc">${esc(Array.isArray(c.turns) ? lastReply(c) : c.last || "")}</div></button>`).join("")}</div></div>` : "";
  }

  // The home is drawn before the server answers; what the answers change (the
  // logos in the showcase, the recent list) is patched in place, so a draft
  // already in the composer is never wiped.
  function repaintHome() {
    if (cur || !main.querySelector(".hero h1")) return;
    const sc = main.querySelector(".showcase");
    if (sc) sc.outerHTML = showcase();
    const rc = $("homeRecent");
    if (rc) rc.innerHTML = recentHTML();
  }

  const homeActions = (showBack = false) => '<div class="home-actions">' +
    (showBack ? '<button type="button" class="btn" data-go="home"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="m12 5-7 7 7 7M5 12h14"/></svg>Back to Plays</button><span class="grow"></span>' : '') +
    '<button type="button" class="btn" data-go="strategies">Strategies</button></div>';

  // Placeholders in the shape of what is coming; styles are .sk in plays.html.
  const SKEL = {
    page: `<div class="page" aria-busy="true" role="status" aria-label="Loading"><div class="sk sk-title"></div><div class="sk sk-sub"></div>` +
      `${'<div class="sk sk-row"></div>'.repeat(4)}</div>`,
    thread: `<div class="home" aria-busy="true"><div class="sk-thread" role="status" aria-label="Opening the conversation"><div class="sk sk-bubble"></div>` +
      `<div class="sk sk-line" style="width:92%"></div><div class="sk sk-line" style="width:86%"></div><div class="sk sk-line" style="width:64%"></div>` +
      `<div class="sk sk-curve"></div></div></div>`,
    review: `<div role="status" aria-label="Reading your trades"><div class="sk-tiles">${'<div class="sk"></div>'.repeat(6)}</div><div class="sk sk-curve"></div></div>`,
  };

  async function home(convId) {
    cur = convId ? convs.find((c) => c.id === convId) || null : null;
    if (convId && (!cur || !Array.isArray(cur.turns))) {
      main.innerHTML = SKEL.thread;
      try {
        const d = await call(`/plays/chats/${encodeURIComponent(convId)}`);
        cur = { ...d.chat, updated: (d.chat.updated || 0) * 1000 };
        convs = [cur, ...convs.filter((c) => c.id !== cur.id)];
      } catch { cur = null; }
    }
    if (convId && !cur) return go("#/");
    if (!cur) {
      main.innerHTML = `<div class="home">${homeActions()}<div class="hero"><h1>Turn ideas into <mark>plays</mark><br/>that watch the market.</h1>` +
        `<p>Say a trade the way you would to a friend. Pivot reads the market, builds the rule, shows how often it would have fired, and runs it on paper.</p>` +
        composerHTML("new", "Describe a trade, a watch or a rule") +
        `<div class="ideas">${IDEAS.map(([i, t]) => `<button type="button" class="idea" data-idea="${esc(t)}">${ic(i)}${esc(t)}</button>`).join("")}</div></div>` +
        showcase() +
        `<div id="homeRecent">${recentHTML()}</div>` +
        `<section id="homeReview" aria-labelledby="homeReviewTitle"><h2 class="h1" id="homeReviewTitle">Review</h2>` +
        `<div data-review-content>${SKEL.review}</div>` +
        `<div class="ideas" style="margin:18px 0 60px">${ASKS.map(([i, t]) => `<button type="button" class="idea" data-ask="${esc(t)}">${ic(i)}${esc(t)}</button>`).join("")}</div></section></div>`;
      wireComposer(main.querySelector("[data-form]"));
      main.querySelector("[data-input]").focus();
      void loadHomeReview();
      return;
    }
    main.innerHTML = `<div class="home">${homeActions(true)}<div class="thread" id="thread"></div><div class="dock">` +
      composerHTML("conv", cur.focus ? "Ask for a change to this play" : "Ask for a change, or describe another play", focusChip()) +
      `<div class="fine">Every order is simulated on paper. Nothing is sent to a broker. This is analysis, not financial advice.</div></div></div>`;
    const th = $("thread");
    cur.turns.forEach((t) => th.appendChild(turnEl(t)));
    wireComposer(main.querySelector("[data-form]"));
    window.scrollTo(0, document.body.scrollHeight);
    main.querySelector("[data-input]").focus();
  }

  function focusChip() {
    if (!cur || !cur.focus) return "";
    const p = (data.plays || []).find((x) => x.id === cur.focus);
    return `<span class="ctx">${ic("flow")}Editing <b>${esc(p ? p.title : `play ${cur.focus}`)}</b><button type="button" data-unfocus aria-label="Stop editing">${ic("x")}</button></span>`;
  }

  const lastReply = (c) => { const a = [...c.turns].reverse().find((t) => t.role === "assistant" && t.content); return a ? a.content.replace(/\[\[panel[^\]]*\]\]/g, "").replace(/[*#|`]/g, "").slice(0, 160) : ""; };

  function showcase() {
    const row = (l, t, extra = "") => `<div style="display:flex;align-items:center;gap:8px;padding:7px 2px;border-bottom:1px solid #eee">${l}<span style="flex:1">${t}</span>${extra}</div>`;
    return `<div class="showcase">` +
      `<button type="button" class="show a" data-idea="Ping me when Reliance files an order win on NSE and it is above its 20-day average">` +
      `<div class="ask">Ping me when a filing and the chart line up</div><div class="demo">` +
      row(Kit.logo("RELIANCE", "sm"), "<b>NSE filing</b> matches “order”", `<span class="pill met">Met</span>`) +
      row(Kit.ic("chart", "mark-ic"), "Above its 20-day average", `<span class="pill met">Met</span>`) +
      row(Kit.ic("bell", "mark-ic"), "Alert lands in your Inbox, with the quote") +
      `</div><div class="cap"><b>Agents that work all day.</b> Filings, headlines and charts are read the moment they change.</div></button>` +
      `<button type="button" class="show b" data-idea="Build a defence basket: the five largest by market cap, weighted by market cap, trading at least ₹100 crore a day. Explain each pick, then buy ₹50,000 of it">` +
      `<div class="ask">Build a basket by a rule you can check</div><div class="demo">` +
      row(Kit.ic("layers", "mark-ic"), "Filter: <b>Aerospace &amp; Defense</b>") +
      row(Kit.ic("list", "mark-ic"), "Rank by <b>market cap</b>, keep the top 5") +
      row(Kit.ic("scale", "mark-ic"), "Weight by market cap, <b>40%</b> cap per name") +
      row(Kit.ic("checkc", "mark-ic"), "Every member shown with the numbers that put it there") +
      `</div><div class="cap"><b>Your own securities.</b> Same rule, same day, same basket. Chart it, trade it, change a weight yourself.</div></button>` +
      `<button type="button" class="show c" data-idea="Short RELIANCE intraday for ₹50,000 if a 5 minute candle closes below the first 15 minutes' low, trail it 0.8% and get out by 3:10">` +
      `<div class="ask">Short Reliance if it breaks the opening range</div><div class="demo">` +
      row(`<span class="chip sig">${Kit.ic("signal")}5m</span>`, "Close below the 09:15 to 09:30 low") +
      row(`<span class="chip sell">Short</span>`, `${Kit.logo("RELIANCE", "sm")} RELIANCE, intraday`) +
      row(Kit.ic("exit", "mark-ic"), "Trail 0.8%, out by 15:10") +
      `</div><div class="cap"><b>Day trading, handled.</b> Stops, trails, leg rules and square-off run on their own.</div></button></div>`;
  }

  /* one turn of the thread */
  function turnEl(t) {
    const w = document.createElement("div");
    if (t.role === "user") {
      w.className = "msg-u";
      w.innerHTML = `<div>${esc(t.content)}</div>`;
      return w;
    }
    w.className = "msg-a";
    if ((t.steps || []).length) w.appendChild(stepsEl(t.steps, true));
    w.appendChild(replyEl(t.content, t.cards));
    if (t.meta) w.insertAdjacentHTML("beforeend", `<div class="meta">${esc(t.meta)}</div>`);
    return w;
  }

  const STEP = {
    play: ["flow", "Building the triggers and trade rules"], measure: ["sparkle", "Working out the measure"],
    read_chart: ["chart", "Reading the chart"], market_data: ["scale", "Pulling market data"],
    search: ["globe", "Searching the web"], query: ["file", "Looking up fundamentals"],
    screen: ["grid", "Screening the market"], journal: ["wallet", "Reading your portfolio"],
    workspace: ["layers", "Checking the workspace"], draw: ["chart", "Marking the chart"],
  };
  function stepText(s) {
    const [, base] = STEP[s.name] || ["sparkle", "Working"];
    const h = String(s.hint || "").trim();
    if (s.name === "measure" && /^security/.test(h)) return `Building the custom security ${h.replace(/^security\s*/, "")}`.trim();
    if (s.name === "play" && h) return `${/^edit/.test(h) ? "Changing" : "Building"} ${h.replace(/^(compile|edit)\s*/, "") || "the play"}`;
    if (s.name === "search" && h) return `Searching for “${h}”`;
    if (s.name === "read_chart" && h) return `Reading ${h.split(" ")[0]}'s chart`;
    if (s.name === "market_data" && h) return `Pulling market data for ${h.split(" ")[0]}`;
    return base;
  }
  function stepsEl(steps, done) {
    const ul = document.createElement("ul");
    ul.className = "steps";
    ul.innerHTML = steps.map((s, i) => {
      const live = !done && i === steps.length - 1;
      const sym = Kit.symIn(s.hint);
      return `<li class="${live ? "now" : ""}">${live ? `<span class="spin"></span>` : ic((STEP[s.name] || ["sparkle"])[0])}<span>${esc(stepText(s))}</span>${sym ? Kit.logo(sym, "xs") : ""}</li>`;
    }).join("");
    return ul;
  }

  /** A card the server just built is the newest copy of its play or
   *  security: it replaces the one from the last /plays read, so a play
   *  edited this turn is never drawn as it was before the edit. */
  function fresh(c) {
    if (c.kind === "play" && c.id && c.conditions) {
      data.plays = [c, ...(data.plays || []).filter((x) => x.id !== c.id)];
    } else if (c.kind === "custom_security" && c.symbol && c.formula) {
      data.securities = [c, ...(data.securities || []).filter((x) => x.symbol !== c.symbol)];
    }
  }

  /** A stored card (or a reference to one) as it is now: the play or the
   *  security from the latest /plays read, else a line saying it is gone. */
  function live(c) {
    if (!c) return null;
    if (c.kind === "review" || c.kind === "scan") return c;
    if (c.kind === "play") {
      const p = (data.plays || []).find((x) => x.id === c.id);
      return p || (c.conditions ? c : { kind: "gone", title: c.title || `Play ${c.id}` });
    }
    if (c.kind === "custom_security") {
      const s = (data.securities || []).find((x) => x.symbol === c.symbol);
      return s || (c.formula ? c : { kind: "gone", title: c.symbol });
    }
    return null;
  }

  function cardEl(c) {
    if (c.kind === "gone") {
      const el = document.createElement("div");
      el.className = "gone";
      el.innerHTML = `${ic("trash")}<span>${esc(c.title)} was deleted.</span>`;
      return el;
    }
    if (c.kind === "play") return playFolder(c);
    if (c.kind === "custom_security") return securityCard(c);
    if (c.kind === "review") return Kit.review(c);
    if (c.kind === "scan") return Kit.scanCard(c);
    return null;
  }

  /** A play in the thread is drawn whole. On a phone it arrives as its
   *  folder instead: tapping it unfolds the full card, and "Fold" puts it
   *  back. Which plays are open is kept here, so a repaint while the reply
   *  streams in does not close one. */
  const phone = window.matchMedia("(max-width: 760px)");
  phone.addEventListener("change", () => { if (route.page === "home" && cur) home(cur.id); });
  const unfolded = new Set();
  function playFolder(c) {
    if (!phone.matches) return Kit.strategy(c);
    const box = document.createElement("div");
    box.className = "pfold";
    const paint = () => {
      if (unfolded.has(c.id)) {
        box.innerHTML = `<button type="button" class="fold-bar" data-fold>${ic("chevron")}<span>Fold</span></button>`;
        box.appendChild(Kit.strategy(c));
      } else box.replaceChildren(Kit.folder(c, { toggle: true }));
    };
    box.addEventListener("click", (e) => {
      const hit = unfolded.has(c.id) ? e.target.closest("[data-fold]") : e.target.closest(".folder");
      if (!hit) return;
      if (unfolded.has(c.id)) unfolded.delete(c.id); else unfolded.add(c.id);
      paint();
      if (!unfolded.has(c.id)) box.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
    paint();
    return box;
  }

  function securityCard(c) {
    const el = document.createElement("div");
    el.className = "strat";
    const basket = c.type === "basket";
    el.innerHTML = `<div class="strat-head"><div style="flex:1;min-width:0"><div class="row" style="gap:8px;margin-bottom:3px"><h3>${esc(c.title)}</h3>` +
      `<span class="pill">${esc(c.symbol)}</span></div><p>${esc(basket ? "A basket" : c.type === "ratio" ? "A ratio" : "A spread")}: ${esc(c.formula)}` +
      (basket && c.anchor ? `. Set to 100 on ${esc(Kit.dayY(c.anchor))}, holding those weights.` : ".") + `</p></div>` +
      `<div class="logos">${(c.members || []).slice(0, 5).map((m) => Kit.logo(m.symbol, "lg")).join("")}</div></div>` +
      `<div style="padding:0 12px 4px"><div class="pnl"><div class="ph"></div></div></div>` +
      (c.why || c.method ? `<div class="sec-why">${c.why ? `<p class="thesis"><b>Why</b>${esc(c.why)}</p>` : ""}` +
        (c.method ? `<p class="method">${ic("list")}<span><b>How it was picked</b>${esc(c.method.text)}${c.method.as_of ? `, on data to ${esc(Kit.dayY(c.method.as_of))}` : ""}.` +
          `${c.method.edited ? " Weights since changed by you." : ""}</span></p>` : "") + `</div>` : "") +
      (basket ? `<div style="padding:10px 18px 4px"><div class="alloc">${(c.members || []).map((m, i) => `<i style="width:${m.weight * 100}%;background:${Kit.COLORS[i % Kit.COLORS.length]}"></i>`).join("")}</div>` +
        `<div class="members">${(c.members || []).map((m, i) => `<div class="mem"><span class="sw" style="background:${Kit.COLORS[i % Kit.COLORS.length]}"></span>${Kit.logo(m.symbol)}` +
          `<div class="grow"><div class="nm"><b>${esc(m.symbol)}</b><span>${esc(Kit.name(m.symbol) || m.industry || "")}</span></div>${m.why ? `<div class="why">${esc(m.why)}</div>` : ""}</div>` +
          `${Kit.ed(m.symbol, (m.weight * 100).toFixed(1), `<b>${esc(Kit.num(m.weight * 100, 1))}%</b>`)}</div>`).join("")}</div></div>` :
        `<div class="members" style="padding:6px 18px">${(c.members || []).map((m) => `<div class="mem">${Kit.logo(m.symbol)}<div class="grow"><div class="nm"><b>${esc(m.symbol)}</b><span>${esc(Kit.name(m.symbol))}</span></div>${m.why ? `<div class="why">${esc(m.why)}</div>` : ""}</div></div>`).join("")}</div>`) +
      `<div class="strat-foot"><div class="facts">${["1d", "1m", "1y"].filter((k) => (c.change || {})[k] != null).map((k) =>
        `<span>${{ "1d": "Day", "1m": "Month", "1y": "Year" }[k]} <b class="${c.change[k] >= 0 ? "up" : "down"}">${esc(Kit.signed(c.change[k]))}</b></span>`).join("")}</div>` +
      `<a class="btn sm" href="./?symbol=${encodeURIComponent(c.symbol)}&interval=1D" target="pivot-chart">${ic("chart")}Open chart</a></div>`;
    Kit.priceBlock(el.querySelector(".ph"), c.symbol, { tfs: ["1D", "1W", "1M", "1Y", "ALL"], tf: "1Y", height: 120, width: 640, area: true });
    el.addEventListener("click", (e) => {
      const f = e.target.closest("[data-field]");
      if (!f || f.querySelector("input")) return;
      Kit.editInline(f, async (v) => {
        const got = await call("/plays/securities", { symbol: c.symbol, weights: { [f.dataset.field]: v } });
        data.securities = (data.securities || []).map((x) => x.symbol === c.symbol ? got.security : x);
        el.replaceWith(securityCard(got.security));
        Kit.toast("Weights changed. The value is recalculated from its anchor day.");
      });
    });
    return el;
  }

  /** The reply's prose with its cards: each [[panel]] marker takes the next
   *  card; any card left over follows the text. */
  function replyEl(text, cards) {
    const el = document.createElement("div");
    el.className = "prose";
    const shown = (cards || []).map(live).filter(Boolean);
    const parts = String(text || "").split(/\n?\s*\[\[panel(?::[^\]]*)?\]\]\s*\n?/);
    let k = 0;
    const put = () => { const c = cardEl(shown[k++]); if (c) { const s = document.createElement("div"); s.className = "card-slot"; s.appendChild(c); el.appendChild(s); } };
    parts.forEach((p, i) => {
      if (p.trim()) el.insertAdjacentHTML("beforeend", Kit.md(p));
      if (i < parts.length - 1 && k < shown.length) put();
    });
    while (k < shown.length) put();
    return el;
  }

  let saveT = 0;
  function persist() {
    if (!cur) return;
    cur.updated = Date.now();
    convs = [cur, ...convs.filter((c) => c.id !== cur.id)].slice(0, 80);
    save(KEY, convs.filter((c) => Array.isArray(c.turns)).slice(0, 30));
    const c = cur;
    clearTimeout(saveT);
    saveT = setTimeout(() => upload(c), 400);
  }
  async function upload(c) {
    const turns = (c.turns || []).filter((t) => !t.local);
    if (!turns.length || !Auth.user) return;
    try {
      await call("/plays/chats", { id: c.id, title: c.title, focus: c.focus || null,
        turns: turns.map((t) => ({ role: t.role, content: t.content, steps: t.steps, meta: t.meta, cards: t.cards })) });
      c.synced = true;
    } catch { /* kept in this browser; tried again on the next turn */ }
  }
  /** The server's list, with anything only this browser has sent up once. */
  async function loadConvs() {
    let list = [];
    try { list = (await call("/plays/chats")).chats || []; } catch { return; }
    const have = new Set(list.map((c) => c.id));
    const mine = convs.filter((c) => Array.isArray(c.turns) && !have.has(c.id));
    await Promise.all(mine.map(upload));
    const local = new Map(convs.map((c) => [c.id, c]));
    convs = [...list.map((c) => { const l = local.get(c.id); return l && Array.isArray(l.turns) ? { ...l, last: c.last } : { ...c, updated: c.updated * 1000 }; }),
             ...mine].sort((a, b) => b.updated - a.updated);
  }

  async function send(text) {
    text = String(text || "").trim();
    if (!text || busy) return;
    if (!cur) {
      cur = { id: `c${Date.now().toString(36)}`, title: text.slice(0, 70), updated: Date.now(), turns: [] };
      persist();
      history.replaceState(null, "", `#/c/${cur.id}`);
      route = parse();
      home(cur.id);
    }
    cur.turns.push({ role: "user", content: text });
    persist();
    const th = $("thread");
    th.appendChild(turnEl(cur.turns[cur.turns.length - 1]));
    const w = document.createElement("div");
    w.className = "msg-a";
    const steps = [{ name: "_", hint: "" }];
    let stepsBox = stepsEl([{ name: "_think" }], false);
    stepsBox.querySelector("li span:last-child").textContent = "Thinking";
    const body = document.createElement("div");
    w.append(stepsBox, body);
    th.appendChild(w);
    window.scrollTo(0, document.body.scrollHeight);
    busy = true;
    main.querySelector(".send").disabled = true;
    steps.length = 0;
    const t0 = performance.now();
    let text2 = "", cards = [], done = null, raf = 0, started = false;
    const paint = () => {
      raf = 0;
      const stick = window.innerHeight + window.scrollY >= document.body.scrollHeight - 140;
      body.replaceChildren(replyEl(text2, cards));
      if (stick) window.scrollTo(0, document.body.scrollHeight);
    };
    const later = () => { if (!raf) raf = requestAnimationFrame(paint); };
    const paintSteps = (fin) => { if (!steps.length) return; const n = stepsEl(steps, fin); stepsBox.replaceWith(n); stepsBox = n; };
    try {
      const wire = cur.turns.filter((t) => !t.local).map((t) => ({ role: t.role, content: t.content }));
      const res = await fetch(`${Kit.API}/chat`, {
        method: "POST",
        headers: Auth.headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ messages: wire, stream: true, harness: "v2", plays: true, chat_id: cur.id,
                               context: { surface: "plays", ...(cur.focus ? { focus_play: cur.focus } : {}) } }),
      });
      if (!res.ok) {
        if (res.status === 401) window.dispatchEvent(new Event("plays:unauthorized"));
        let m = `request failed (${res.status})`;
        try { const e = await res.json(); if (e.error) m = e.error; } catch { /* not json */ }
        throw new Error(m);
      }
      const reader = res.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done: fin } = await reader.read();
        if (fin) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() || "";
        for (const p of parts) {
          const line = p.split("\n").find((x) => x.startsWith("data:"));
          if (!line) continue;
          let ev;
          try { ev = JSON.parse(line.slice(5)); } catch { continue; }
          if (ev.type === "tool_start") { steps.push({ name: ev.name, hint: ev.hint || "" }); paintSteps(false); }
          else if (ev.type === "delta") {
            if (!started) { started = true; if (steps.length) paintSteps(true); else { stepsBox.remove(); } }
            text2 += ev.text || ""; later();
          } else if (ev.type === "retract") { text2 = ev.text || ""; later(); }
          else if (ev.type === "card" && ev.card) { fresh(ev.card); cards.push(ev.card); later(); }
          else if (ev.type === "done") done = ev;
        }
        if (done) break;
      }
      if (done && done.error) throw new Error(done.error);
      text2 = (done && done.text) || text2;
      if (!cards.length && done && done.cards) cards = done.cards;
      const t = { role: "assistant", content: text2, cards, steps: steps.slice(), meta: `${((performance.now() - t0) / 1000).toFixed(1)}s` };
      cur.turns.push(t);
      for (const c of cards) if (c.kind === "play" && c.id) { where[c.id] = cur.id; if (!cur.focus) cur.focusOf = c.id; }
      save(MAP, where);
      persist();
      w.replaceWith(turnEl(t));
    } catch (err) {
      stepsBox.remove();
      body.innerHTML = `<div class="err">${esc(err.message || "The answer did not arrive.")} Send it again.</div>`;
      cur.turns.pop();
      persist();
      const inp = main.querySelector("[data-input]");
      if (inp) { inp.value = text; fit(inp); }
    } finally {
      busy = false;
      const s = main.querySelector(".send"), inp = main.querySelector("[data-input]");
      if (s && inp) s.disabled = !inp.value.trim();
      refresh();
    }
  }

  /* ── composers ────────────────────────────────────────────────────────── */
  const fit = (t) => { t.style.height = "auto"; t.style.height = `${Math.min(t.scrollHeight, 220)}px`; };
  function wireComposer(form, onSend) {
    if (!form) return;
    const input = form.querySelector("[data-input]"), btn = form.querySelector(".send");
    input.addEventListener("input", () => { fit(input); btn.disabled = busy || !input.value.trim(); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
    });
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const t = input.value;
      if (!t.trim()) return;
      input.value = ""; fit(input); btn.disabled = true;
      (onSend || send)(t);
    });
    const mic = form.querySelector("[data-mic]");
    if (mic) {
      let rec = null;
      mic.addEventListener("click", () => {
        if (rec) { rec.stop(); return; }
        const R = window.SpeechRecognition || window.webkitSpeechRecognition;
        rec = new R();
        rec.lang = "en-IN"; rec.interimResults = true;
        const before = input.value;
        rec.onresult = (e) => { input.value = (before ? before + " " : "") + [...e.results].map((r) => r[0].transcript).join(" "); fit(input); btn.disabled = !input.value.trim(); };
        rec.onend = () => { rec = null; mic.classList.remove("on"); };
        mic.classList.add("on");
        rec.start();
      });
    }
  }

  /* ── strategies ───────────────────────────────────────────────────────── */
  let sQuery = "", sState = "all", sSort = "new";
  function strategyFilter(select, label) {
    const wrap = document.createElement("div");
    wrap.className = "strategy-filter";
    select.before(wrap);
    wrap.append(select);
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "field strategy-filter-trigger";
    trigger.setAttribute("aria-label", label);
    trigger.setAttribute("aria-haspopup", "menu");
    const menu = document.createElement("div");
    menu.className = "strategy-filter-menu";
    menu.id = select.id + "-menu";
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", label);
    trigger.setAttribute("aria-controls", menu.id);
    const close = () => { menu.hidden = true; trigger.setAttribute("aria-expanded", "false"); };
    const paint = () => {
      trigger.innerHTML = esc(select.selectedOptions[0].text) + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
      menu.replaceChildren();
      for (const option of select.options) {
        const button = document.createElement("button");
        button.type = "button";
        button.setAttribute("role", "menuitemradio");
        button.setAttribute("aria-checked", String(option.selected));
        button.innerHTML = '<span>' + esc(option.text) + '</span>' + (option.selected ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>' : '');
        button.onclick = () => { select.value = option.value; select.dispatchEvent(new Event("change", { bubbles: true })); paint(); close(); trigger.focus(); };
        menu.append(button);
      }
    };
    const open = () => { closeStrategyFilters(); menu.hidden = false; trigger.setAttribute("aria-expanded", "true"); };
    trigger.onclick = () => menu.hidden ? open() : close();
    wrap.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { close(); trigger.focus(); }
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault();
        if (menu.hidden) open();
        const items = [...menu.querySelectorAll("button")];
        const index = items.indexOf(document.activeElement);
        const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowUp" ? -1 : 1) + items.length) % items.length;
        items[next].focus();
      }
    });
    wrap.addEventListener("focusout", (event) => { if (!wrap.contains(event.relatedTarget)) close(); });
    wrap.append(trigger, menu);
    paint(); close();
  }
  function closeStrategyFilters(target) {
    document.querySelectorAll(".strategy-filter").forEach((wrap) => {
      if (target && wrap.contains(target)) return;
      wrap.querySelector(".strategy-filter-menu").hidden = true;
      wrap.querySelector(".strategy-filter-trigger").setAttribute("aria-expanded", "false");
    });
  }
  document.addEventListener("click", (event) => closeStrategyFilters(event.target));

  function strategies(keep) {
    if (!keep || !main.querySelector("#sgrid")) {
      main.innerHTML = `<div class="page"><div class="page-title"><button type="button" class="btn ghost back-button" data-go="home" aria-label="Back to Plays"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="m12 5-7 7 7 7M5 12h14"/></svg></button><h1 class="h1">Strategies.</h1></div><p class="sub">Every play: what it watches, what it would have done, and what it did.</p>` +
        `<div class="toolbar"><input class="field q" id="sq" type="search" placeholder="Search strategies" value="${esc(sQuery)}" />` +
        `<select class="field" id="ss"><option value="all">All statuses</option><option value="armed">Active</option><option value="paused">Paused</option><option value="done">Completed</option></select>` +
        `<select class="field" id="so"><option value="new">Newest first</option><option value="fired">Most fired</option><option value="az">A to Z</option></select>` +
        `<span class="grow"></span><button type="button" class="btn dark" data-go="home" aria-label="New play">${ic("plus")}<span class="lbl">New play</span></button></div><div id="sgrid"></div>` +
        `<div class="panel" id="charges" style="margin-top:16px;margin-bottom:60px" hidden></div></div>`;
      $("ss").value = sState; $("so").value = sSort;
      $("sq").addEventListener("input", (e) => { sQuery = e.target.value; paintGrid(); });
      $("ss").addEventListener("change", (e) => { sState = e.target.value; paintGrid(); });
      $("so").addEventListener("change", (e) => { sSort = e.target.value; paintGrid(); });
      strategyFilter($("ss"), "Filter by status");
      strategyFilter($("so"), "Sort strategies");
      charges($("charges"));
    }
    paintGrid();
  }
  function paintGrid() {
    const q = sQuery.trim().toLowerCase();
    let rows = (data.plays || []).filter((p) => (sState === "all" || p.state === sState) &&
      (!q || `${p.title} ${p.summary} ${p.symbol}`.toLowerCase().includes(q)));
    rows = rows.slice().sort(sSort === "fired" ? (a, b) => (b.fires || 0) - (a.fires || 0) :
      sSort === "az" ? (a, b) => a.title.localeCompare(b.title) : (a, b) => (b.created || b.id) - (a.created || a.id));
    if (!rows.length) {
      $("sgrid").innerHTML = `<div class="empty">${(data.plays || []).length ? "Nothing matches that." : "No plays yet. Say one on Home and it appears here."}</div>`;
      return;
    }
    const g = document.createElement("div");
    g.className = "grid folders";
    rows.forEach((p) => g.appendChild(Kit.folder(p)));
    $("sgrid").replaceChildren(g);
  }

  /* ── one strategy ─────────────────────────────────────────────────────── */
  let ptab = "activity";
  async function strategyPage(id) {
    main.innerHTML = SKEL.page;
    let d;
    try { d = await call(`/plays/${id}`); } catch (err) {
      main.innerHTML = `<div class="page"><div class="empty">${esc(err.message)}</div></div>`; return;
    }
    const c = d.play;
    const pick = (x) => x && x !== "*" ? x : null;
    const chartSym = c.universe ? (c.universe.holding[0] || c.universe.met_now[0] || c.symbol)
      : pick((c.do || {}).symbol) || pick((c.do || {}).underlying) || c.symbol;
    main.innerHTML = `<div class="page"><div class="crumbs"><button type="button" data-go="strategies">Strategies</button><span>/</span><span>${esc(c.title)}</span></div>` +
      `<div class="detail"><div><div class="panel"><div class="panel-h">${ic("flow")}<span>Workflow</span><span class="grow"></span>` +
      `<span id="venue"></span><a class="btn sm ghost" href="./?symbol=${encodeURIComponent(chartSym)}&interval=1D" target="pivot-chart">${ic("chart")}Chart</a></div><div id="golive"></div><div id="wf"></div></div>` +
      `<div class="ask-bar">${composerHTML("ask", `Ask for a change to ${c.title}`)}</div></div>` +
      `<div><div class="panel"><div class="panel-h">${Kit.logo(chartSym, "sm")}<span>${esc(chartSym)}</span><span class="grow"></span>` +
      `<span class="muted" style="font-size:12px">${(c.history || {}).fire_ts && c.history.fire_ts.length ? "dots mark past fires" : ""}</span></div>` +
      `<div class="pnl"><div id="px"></div></div></div>` +
      `<div class="panel" style="margin-top:14px"><div class="subtabs"><button type="button" data-ptab="activity" class="${ptab === "activity" ? "on" : ""}">${ic("bell")}Activity</button>` +
      `<button type="button" data-ptab="holdings" class="${ptab === "holdings" ? "on" : ""}">${ic("pie")}Holdings</button></div><div id="ptab"></div></div></div></div></div>`;
    $("wf").appendChild(Kit.strategy(c, { open: false }));
    if (c.action && c.state !== "done") venueSwitch(c);
    Kit.priceBlock($("px"), chartSym, { tfs: ["1D", "1W", "1M", "3M", "1Y", "ALL"], tf: "3M", height: 170, width: 330, area: true,
      markers: (c.history || {}).fire_ts, price: (c.history || {}).price });
    const paintP = async () => {
      main.querySelectorAll("[data-ptab]").forEach((b) => b.classList.toggle("on", b.dataset.ptab === ptab));
      const box = $("ptab");
      if (ptab === "activity") {
        const evs = d.events || [];
        box.innerHTML = evs.length ? evs.map(Kit.alertCard).join("") : `<div class="muted" style="padding:10px 4px;line-height:1.5">Nothing yet. When it fires, what it saw and what it did land here.</div>`;
      } else {
        const held = Object.entries(c.held || {}).filter(([, q]) => q);
        if (!held.length) { box.innerHTML = `<div class="muted" style="padding:10px 4px">This play holds nothing.</div>`; return; }
        let book = [];
        try { book = await call("/paper/holdings"); } catch { /* the rows still show quantities */ }
        const by = Object.fromEntries((book || []).map((h) => [h.symbol, h]));
        box.innerHTML = held.map(([s, q]) => {
          const h = by[s];
          const val = h ? (+h.last_price * q) : null;
          return `<div class="hold">${Kit.logo(s)}<div class="grow"><div>${esc(s)}</div><div class="s">${esc(Kit.num(q, 4))} shares${h ? ` · ${esc(Kit.num(h.last_price))}` : ""}</div></div>` +
            `<div class="v"><div>${val != null ? esc(Kit.inr(val)) : ""}</div>${h ? `<div class="s ${h.unrealized_pnl >= 0 ? "up" : "down"}">${esc(Kit.signed(h.unrealized_pct))}</div>` : ""}</div></div>`;
        }).join("");
      }
    };
    main.querySelectorAll("[data-ptab]").forEach((b) => b.addEventListener("click", () => { ptab = b.dataset.ptab; paintP(); }));
    paintP();
    wireComposer(main.querySelector('[data-form="ask"]'), (t) => {
      const conv = (where[id] && convs.find((x) => x.id === where[id] && Array.isArray(x.turns))) ||
        convs.find((x) => x.focus === id && Array.isArray(x.turns));
      cur = conv || { id: `c${Date.now().toString(36)}`, title: c.title, updated: Date.now(), focus: id, turns: [
        { role: "assistant", local: true, content: "Here it is. Tell me what to change.\n\n[[panel]]", cards: [c] }] };
      if (!conv) { where[id] = cur.id; save(MAP, where); }
      cur.focus = id;
      persist();
      location.hash = `#/c/${cur.id}`;
      setTimeout(() => send(t), 0);
    });
  }

  /* ── inbox ────────────────────────────────────────────────────────────── */
  let ifilter = "all";
  function inbox(keep) {
    if (!keep || !$("ilist")) {
      main.innerHTML = `<div class="page"><div class="inbox"><h1 class="h1">Inbox</h1><p class="sub">Every time a play triggers: what it saw, the value that decided it, and what it did.</p>` +
        `<div class="toolbar"><div class="seg" id="iseg">${[["all", "All"], ["wait", "Waiting for you"], ["orders", "Orders"], ["alerts", "Alerts"]].map(([k, l]) =>
          `<button type="button" data-if="${k}" class="${ifilter === k ? "on" : ""}">${l}</button>`).join("")}</div><span class="grow"></span>` +
        `<button type="button" class="btn sm ghost" id="markRead">${ic("check")}Mark all read</button></div><div id="ilist"></div></div></div>`;
      $("iseg").addEventListener("click", (e) => { const b = e.target.closest("[data-if]"); if (b) { ifilter = b.dataset.if; inbox(true); } });
      $("markRead").addEventListener("click", async () => { await call("/plays/seen", {}).catch(() => {}); refresh(); });
    }
    $("iseg").querySelectorAll("[data-if]").forEach((b) => b.classList.toggle("on", b.dataset.if === ifilter));
    const evs = (data.events || []).filter((e) => !hidden.has(e.id) || (e.kind === "proposed" && e.status === "open")).filter((e) =>
      ifilter === "all" ? true : ifilter === "wait" ? e.kind === "proposed" && e.status === "open" :
      ifilter === "orders" ? ["filled", "proposed", "rejected", "exited"].includes(e.kind) : e.kind === "fired" || e.kind === "note");
    $("ilist").innerHTML = evs.length ? evs.map(Kit.alertCard).join("") : `<div class="empty">Nothing here. When a play triggers, it lands in this list.</div>`;
    if ((data.events || []).some((e) => !e.seen)) setTimeout(() => call("/plays/seen", {}).then(() => { data.events.forEach((e) => { e.seen = true; }); paintStatus(); }).catch(() => {}), 1500);
  }

  /* ── what Pivot charges ───────────────────────────────────────────────── */
  const rs = (p) => Kit.inr(p / 100, p % 100 ? 2 : 0);
  const STATE_WORD = { ACTIVE: ["Ready for live", "live"], RESTRICTED: ["New entries paused", "bad"], NO_METHOD: ["No money in the wallet", "wait"] };

  async function charges(host) {
    let b;
    try { b = await call("/plays/billing"); } catch { return; }
    const m = b.month, st = b.statement, bl = b.builds;
    const left = bl.starter_left + bl.month_left;
    const tile = (k, v, s) => `<div style="background:var(--panel);border-radius:10px;padding:10px"><div class="muted" style="font-size:12px">${k}</div>` +
      `<div class="num" style="font-size:16px;font-weight:500;margin-top:2px">${v}</div>${s ? `<div class="muted" style="font-size:12px;margin-top:2px">${s}</div>` : ""}</div>`;
    const sw = STATE_WORD[b.state] || [b.state, ""];
    host.hidden = false;
    host.innerHTML = `<div class="panel-h">${ic("wallet")}<span>Pivot charges</span><span class="grow"></span>` +
      (b.live_open ? `<span class="pill ${sw[1]}">${esc(sw[0])}</span>` : "") + `</div>` +
      `<p class="muted" style="font-size:13px;line-height:1.5;margin:0 0 12px">Research and paper trading are free. A live play pays ₹10 a trade it places ` +
      `(₹5 or 0.1% for delivery, whichever is less), and never more than ${esc(rs(m.cap_paise))} in a month. Exits always run.` +
      (b.live_open ? "" : " Live plays are not open yet, so nothing here is charged.") + `</p>` +
      `<div class="kpis">${(b.live_open ? [tile("Wallet", esc(rs(b.wallet_paise))), tile("This month", esc(rs(m.charged_paise)), `of ${esc(rs(m.cap_paise))} · ${m.trades} ${m.trades === 1 ? "trade" : "trades"}`)] : []).join("")}` +
      (bl.enforced ? tile("Play builds left", String(left), bl.unlocked ? "this month" : "arm a play on paper for 10 a month") : "") + `</div>` +
      (b.live_open ? `<div style="height:6px;border-radius:3px;background:var(--panel);margin:12px 0 4px;overflow:hidden"><div style="height:100%;width:${Math.min(100, m.charged_paise / m.cap_paise * 100).toFixed(1)}%;background:var(--ink)"></div></div>` : "") +
      (b.live_open && b.topup_open ? `<div class="row" style="gap:8px;margin-top:12px;flex-wrap:wrap"><span class="muted" style="font-size:13px">Add to wallet</span>` +
        b.topups_paise.map((p) => `<button type="button" class="btn sm" data-topup="${p}">${esc(rs(p))}</button>`).join("") + `</div>` : "") +
      (st.trades.length ? `<table class="table" style="margin-top:12px"><thead><tr><th>When</th><th>Play</th><th class="wide">Kind</th><th class="r">Charge</th></tr></thead><tbody>` +
        st.trades.slice(0, 20).map((t) => `<tr><td class="muted">${esc(new Date(t.at * 1000).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }))}</td>` +
          `<td>${esc(t.play || `Play ${t.play_id}`)}</td><td class="wide">${t.segment === "DELIVERY" ? "Delivery" : "Intraday, F&amp;O or crypto"}</td>` +
          `<td class="r">${esc(rs(t.charge_paise))}${t.waived_paise ? ` <span class="muted pct">${esc(rs(t.waived_paise))} waived</span>` : ""}</td></tr>`).join("") +
        `</tbody></table><div class="muted" style="font-size:12px;margin-top:8px">${esc(rs(st.total_paise))} this month: ${esc(rs(st.base_paise))} plus ${esc(rs(st.gst_paise))} GST` +
        (st.waived_paise ? `. ${esc(rs(st.waived_paise))} waived by the monthly maximum.` : ".") + `</div>` : "");
    host.querySelectorAll("[data-topup]").forEach((x) => x.addEventListener("click", () => topup(+x.dataset.topup, host, x)));
  }

  function razorpay() {
    if (window.Razorpay) return Promise.resolve();
    return new Promise((ok, no) => {
      const s = Object.assign(document.createElement("script"), { src: "https://checkout.razorpay.com/v1/checkout.js", onload: ok, onerror: () => no(new Error("the payment window did not load")) });
      document.head.appendChild(s);
    });
  }

  async function topup(paise, host, btn) {
    btn.disabled = true;
    try {
      const [o] = await Promise.all([call("/plays/billing/topup", { amount_paise: paise }), razorpay()]);
      new window.Razorpay({
        key: o.key_id, order_id: o.order_id, amount: o.amount_paise, currency: "INR", name: "Pivot", description: "Wallet top-up",
        handler: async (r) => {
          try { await call("/plays/billing/topup/verify", r); Kit.toast(`Added ${rs(paise)} to your wallet`); } catch (err) { Kit.toast(err.message); }
          charges(host);
        },
        modal: { ondismiss: () => { btn.disabled = false; } },
      }).open();
    } catch (err) { btn.disabled = false; Kit.toast(err.message); }
  }

  /* a play's venue: paper is free; live shows its cost before it switches */
  async function venueSwitch(c) {
    let b;
    try { b = await call("/plays/billing"); } catch { return; }
    if (!b.live_open && c.venue !== "live") return;
    const box = $("venue");
    if (!box) return;
    box.innerHTML = `<div class="seg" style="margin-right:8px">${[["paper", "Paper"], ["live", "Live"]].map(([k, l]) =>
      `<button type="button" data-venue="${k}" class="${(c.venue || "paper") === k ? "on" : ""}">${l}</button>`).join("")}</div>`;
    box.addEventListener("click", async (e) => {
      const x = e.target.closest("[data-venue]");
      if (!x || x.dataset.venue === (c.venue || "paper")) return;
      if (x.dataset.venue === "paper") {
        try { await call(`/plays/${c.id}`, { action: "paper" }); strategyPage(c.id); } catch (err) { Kit.toast(err.message); }
        return;
      }
      let est;
      try { est = await call(`/plays/billing/estimate/${c.id}`); } catch (err) { Kit.toast(err.message); return; }
      const line = (k, v) => `<div class="row" style="justify-content:space-between;padding:3px 0"><span class="muted">${k}</span><span class="num">${v}</span></div>`;
      $("golive").innerHTML = `<div style="background:var(--panel);border-radius:10px;padding:12px 14px;margin:0 0 12px;font-size:13px">` +
        `<div style="font-weight:500;margin-bottom:6px">Estimated cost per trade</div>` +
        line(`${esc(est.broker)} brokerage`, esc(rs(est.brokerage_paise))) + line("GST on brokerage", esc(rs(est.brokerage_gst_paise))) +
        line("Taxes (STT and the rest)", "depends on the order size") +
        line("Pivot", `${est.segment === "DELIVERY" ? "up to " : ""}${esc(rs(est.pivot_paise))} <span class="muted">(${esc(rs(est.cap_paise))} a month at most · ${esc(rs(est.used_paise))} used)</span>`) +
        `<p class="muted" style="margin:8px 0 10px;line-height:1.5">Pivot charges only when this play trades. Exits always run, even with unpaid dues. ` +
        `In this build a live play still fills in the paper book; no broker order is placed.</p>` +
        `<div class="row" style="gap:8px"><button type="button" class="btn sm" id="goLive">Run live</button><button type="button" class="btn sm ghost" id="noLive">Cancel</button></div></div>`;
      $("noLive").onclick = () => { $("golive").innerHTML = ""; };
      $("goLive").onclick = async () => {
        try { await call(`/plays/${c.id}`, { action: "live" }); strategyPage(c.id); } catch (err) { $("golive").innerHTML = ""; Kit.toast(err.message); }
      };
    });
  }

  /* ── sign in ──────────────────────────────────────────────────────────── */
  // No form of its own: Pivot has one sign-in (the app's /login, Google
  // included) and the shell around this frame holds its session, the same
  // token auth.js reads. Signed out here is signed out of Pivot, so the way
  // in is the app's page, which brings the person back to this tab. A held
  // token whose check got no answer is not a sign-out and is not shown one.
  //
  // The prompt itself is the shell's (its sign-in panel, raised through
  // PIVOT_SIGNIN), the same one every other tab shows; this page draws no
  // sign-in of its own, only a line saying why it is empty. It is raised once
  // per sign-out, not on every redraw; the button raises it again.
  let asked = false;
  function signIn() {
    if (Auth.unsure) {
      main.innerHTML = `<div class="signin"><h2>Reaching your account</h2><p>Pivot did not answer just now. Your plays are safe on the server.</p>` +
        `<button type="button" class="btn dark" data-reauth>Try again</button></div>`;
      return;
    }
    main.innerHTML = `<div class="page"><div class="empty">You are signed out of Pivot. Your plays keep running on the server.` +
      `<div style="margin-top:14px"><button type="button" class="btn dark" data-signin>Sign in</button></div></div></div>`;
    if (!asked) { asked = true; window.PIVOT_SIGNIN("tab:plays"); }
  }

  // Ask the server who this is, the way auth.js does at boot: an unanswered
  // check is asked again (1 s, 3 s, 7 s) before anything is decided.
  async function whoami() {
    let me = await Auth.resume();
    for (const wait of [1000, 3000, 7000]) {
      if (me || !Auth.unsure) break;
      await new Promise((r) => setTimeout(r, wait));
      me = await Auth.resume();
    }
    return me;
  }

  async function boot() {
    // The plain home is static plus this browser's own chats: with a session
    // held, it is drawn at once rather than after four round trips.
    const plain = () => { const r = parse(); return r.page === "home" && !r.conv; };
    if (Auth.token && plain()) home();
    const me = await whoami();
    if (!me) return render();
    const ready = Promise.all([refresh(), loadConvs()]);
    const logos = Kit.loadLogos();
    if (plain()) {
      if (!main.querySelector(".hero h1")) render();
      logos.then(repaintHome);
      ready.then(repaintHome);
      return;
    }
    main.innerHTML = parse().conv ? SKEL.thread : SKEL.page;
    await Promise.all([ready, logos]);
    render();
  }

  // The session is the shell's. Signing in, out, or as someone else there (or
  // in another tab) changes the shared token, and this page starts over on
  // the new one rather than showing one account's plays under another's.
  window.addEventListener("storage", (e) => {
    if (e.key !== null && e.key !== Auth.KEY) return;
    let now = null;
    try { now = localStorage.getItem(Auth.KEY); } catch { /* denied */ }
    if (now !== Auth.token) location.reload();
  });

  // A refused request means the session ended on the server (expired, signed
  // out elsewhere). Confirm it before showing the sign-in, never on a guess.
  let rechecking = null;
  window.addEventListener("plays:unauthorized", () => {
    if (rechecking || !Auth.user) return;
    rechecking = whoami().then((me) => { if (!me) render(); }).finally(() => { rechecking = null; });
  });

  /* ── global clicks ────────────────────────────────────────────────────── */
  document.addEventListener("click", async (e) => {
    const g = e.target.closest("[data-go]");
    if (g) { e.preventDefault(); go(g.dataset.go === "home" ? "#/" : `#/${g.dataset.go}`); return; }
    const op = e.target.closest("[data-open]");
    if (op && op.dataset.open) { go(`#/s/${op.dataset.open}`); return; }
    const cv = e.target.closest("[data-conv]");
    if (cv) { go(`#/c/${cv.dataset.conv}`); return; }
    const ask = e.target.closest("[data-ask]");
    if (ask) { cur = null; history.replaceState(null, "", "#/"); route = parse(); paintStatus(); send(ask.dataset.ask); return; }
    if (e.target.closest("[data-lift]")) {
      try { await call("/plays/halt", { action: "lift" }); Kit.toast("Lifted. New entries can open again."); await refresh(); }
      catch (err) { Kit.toast(err.message); }
      return;
    }
    const idea = e.target.closest("[data-idea]");
    if (idea) {
      const inp = main.querySelector("[data-input]");
      if (inp) { inp.value = idea.dataset.idea; fit(inp); inp.focus(); main.querySelector(".send").disabled = false; window.scrollTo({ top: 0, behavior: "smooth" }); }
      return;
    }
    if (e.target.closest("[data-unfocus]")) { if (cur) { cur.focus = null; persist(); home(cur.id); } return; }
    const ea = e.target.closest("[data-evact]");
    if (ea) {
      const art = ea.closest("[data-ev]");
      ea.disabled = true;
      try { await call(`/plays/events/${art.dataset.ev}`, { action: ea.dataset.evact }); Kit.toast(ea.dataset.evact === "confirm" ? "Placed on paper" : "Dismissed"); await refresh(); if (route.page === "strategy") render(); }
      catch (err) { ea.disabled = false; Kit.toast(err.message); }
      return;
    }
    const x = e.target.closest("[data-x]");
    if (x) {
      const id = +x.dataset.x, ev = (data.events || []).find((v) => v.id === id);
      if (ev && ev.kind === "proposed" && ev.status === "open") {
        try { await call(`/plays/events/${id}`, { action: "dismiss" }); } catch (err) { Kit.toast(err.message); return; }
      }
      hidden.add(id); save(HIDE, [...hidden].slice(-500));
      x.closest(".alert").remove();
      refresh();
      return;
    }
    if (e.target.closest("[data-signin]")) { window.PIVOT_SIGNIN("tab:plays"); return; }
    const ra = e.target.closest("[data-reauth]");
    if (ra) { ra.disabled = true; main.innerHTML = `<div class="page"><div class="muted">Reaching your account…</div></div>`; boot(); }
  });
  window.addEventListener("plays:changed", () => refresh());

  setInterval(() => { if (!document.hidden) refresh(); }, 60_000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });

  Kit.loadLogos();
  boot();
})();
