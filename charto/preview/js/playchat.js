/* Pivot Plays — the app (plays.html).
 *
 * Home is a conversation: say a trade, watch Pivot work through its steps
 * (Catalyst's research list), and get the play back as its canvas, then keep
 * talking to change it. Strategies is every play as a tile and each play's
 * own page (its canvas beside its price and activity). Inbox is every fire
 * written up as one alert. Portfolio is the paper book.
 *
 * The chat is the same /chat the chart uses, told it is on the Plays page
 * (`context.surface = "plays"`), so it knows there is no chart and which
 * plays already exist. Conversations live in this browser; plays live on
 * the server, and each remembers the conversation that built it.
 */
"use strict";

(() => {
  const { esc, ic, call } = Kit;
  const $ = (id) => document.getElementById(id);
  const KEY = "pivot:plays:convs", MAP = "pivot:plays:where", HIDE = "pivot:plays:hidden";
  const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) || d; } catch { return d; } };
  const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* full */ } };

  let convs = load(KEY, []), where = load(MAP, {}), hidden = new Set(load(HIDE, []));
  let data = { plays: [], events: [], securities: [], book: { positions: [] } };
  let cur = null, busy = false, route = { page: "home" };
  const main = $("main");

  /* ── routing ──────────────────────────────────────────────────────────── */
  function parse() {
    const h = location.hash.replace(/^#\/?/, "");
    const [a, b] = h.split("/");
    if (a === "c" && b) return { page: "home", conv: b };
    if (a === "s" && b) return { page: "strategy", id: +b };
    if (["strategies", "inbox", "portfolio"].includes(a)) return { page: a };
    return { page: "home" };
  }
  const go = (hash) => { if (location.hash !== hash) location.hash = hash; else render(); };
  window.addEventListener("hashchange", render);

  function paintTabs() {
    const p = route.page === "strategy" ? "strategies" : route.page;
    document.querySelectorAll("#tabs [data-go]").forEach((t) => t.classList.toggle("on", t.dataset.go === p));
    const waiting = (data.events || []).filter((e) => e.kind === "proposed" && e.status === "open").length;
    const n = waiting || (data.events || []).filter((e) => !e.seen && !hidden.has(e.id)).length;
    $("inboxCount").hidden = !n;
    $("inboxCount").textContent = n;
  }

  function render() {
    route = parse();
    paintTabs();
    if (!Auth.user) return signIn();
    if (route.page === "home") return home(route.conv);
    if (route.page === "strategies") return strategies();
    if (route.page === "strategy") return strategyPage(route.id);
    if (route.page === "inbox") return inbox();
    if (route.page === "portfolio") return portfolio();
  }

  async function refresh() {
    if (!Auth.user) return;
    try { data = await call("/plays"); } catch { return; }
    paintTabs();
    if (route.page === "strategies" && !document.querySelector(".field:focus")) strategies(true);
    if (route.page === "inbox") inbox(true);
  }

  /* ── home: the conversation ───────────────────────────────────────────── */
  const IDEAS = [
    ["bell", "Tell me when HDFC Bank closes below its 50-day average"],
    ["file", "If Reliance files an order win on NSE, propose buying ₹10,000 of it"],
    ["layers", "Make me a defence basket and alert me when it breaks its 50-DMA"],
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

  function home(convId) {
    cur = convId ? convs.find((c) => c.id === convId) || null : null;
    if (convId && !cur) return go("#/");
    if (!cur) {
      const recent = convs.slice(0, 6);
      main.innerHTML = `<div class="home"><div class="hero"><h1>Turn ideas into <mark>plays</mark><br/>that watch the market.</h1>` +
        `<p>Say a trade the way you would to a friend. Pivot reads the market, builds the rule, shows how often it would have fired, and runs it on paper.</p>` +
        composerHTML("new", "Describe a trade, a watch or a rule") +
        `<div class="ideas">${IDEAS.map(([i, t]) => `<button type="button" class="idea" data-idea="${esc(t)}">${ic(i)}${esc(t)}</button>`).join("")}</div></div>` +
        showcase() +
        (recent.length ? `<div style="margin:0 0 60px"><div class="panel-h" style="margin-left:0">${ic("list")}<span>Recent conversations</span></div>` +
          `<div class="grid">${recent.map((c) => `<button type="button" class="sc" data-conv="${esc(c.id)}"><div class="top2"><b>${esc(c.title)}</b></div>` +
          `<div class="desc">${esc(lastReply(c))}</div></button>`).join("")}</div></div>` : "") + `</div>`;
      wireComposer(main.querySelector("[data-form]"));
      main.querySelector("[data-input]").focus();
      return;
    }
    main.innerHTML = `<div class="home"><div class="thread" id="thread"></div><div class="dock">` +
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
      `<button type="button" class="show b" data-idea="Make me a defence basket of HAL, BEL, BDL and Mazagon Dock and buy ₹50,000 of it">` +
      `<div class="ask">Make me a defence basket and buy it</div><div class="demo">` +
      `<div class="alloc" style="height:12px">${["#2563eb", "#f0570f", "#16a34a", "#7c4dff"].map((c) => `<i style="width:25%;background:${c}"></i>`).join("")}</div>` +
      ["HAL", "BEL", "BDL", "MAZDOCK"].map((s) => row(Kit.logo(s, "sm"), s, `<b>25%</b>`)).join("") +
      `</div><div class="cap"><b>Your own securities.</b> A basket, a ratio or a spread becomes a symbol you can chart and trade.</div></button>` +
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
      return `<li class="${live ? "now" : ""}">${live ? `<span class="spin"></span>` : ic((STEP[s.name] || ["sparkle"])[0])}<span>${esc(stepText(s))}</span></li>`;
    }).join("");
    return ul;
  }

  function cardEl(c) {
    if (c.kind === "play") {
      const el = Kit.strategy(c);
      return el;
    }
    if (c.kind === "custom_security") return securityCard(c);
    return null;
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
      (basket ? `<div style="padding:10px 18px 4px"><div class="alloc">${(c.members || []).map((m, i) => `<i style="width:${m.weight * 100}%;background:${Kit.COLORS[i % Kit.COLORS.length]}"></i>`).join("")}</div>` +
        `<div class="alloc-legend">${(c.members || []).map((m, i) => `<div><span class="sw" style="background:${Kit.COLORS[i % Kit.COLORS.length]}"></span>${Kit.logo(m.symbol, "sm")}<span>${esc(m.symbol)}</span><b>${esc(Kit.num(m.weight * 100, 1))}%</b></div>`).join("")}</div></div>` : "") +
      `<div class="strat-foot"><div class="facts">${["1d", "1m", "1y"].filter((k) => (c.change || {})[k] != null).map((k) =>
        `<span>${{ "1d": "Day", "1m": "Month", "1y": "Year" }[k]} <b class="${c.change[k] >= 0 ? "up" : "down"}">${esc(Kit.signed(c.change[k]))}</b></span>`).join("")}</div>` +
      `<a class="btn sm" href="./?symbol=${encodeURIComponent(c.symbol)}&interval=1D" target="pivot-chart">${ic("chart")}Open chart</a></div>`;
    Kit.priceBlock(el.querySelector(".ph"), c.symbol, { tfs: ["1D", "1W", "1M", "1Y", "ALL"], tf: "1Y", height: 120, width: 640, area: true });
    return el;
  }

  /** The reply's prose with its cards: each [[panel]] marker takes the next
   *  card; any card left over follows the text. */
  function replyEl(text, cards) {
    const el = document.createElement("div");
    el.className = "prose";
    const shown = (cards || []).filter((c) => c.kind === "play" || c.kind === "custom_security");
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

  function persist() {
    if (!cur) return;
    cur.updated = Date.now();
    convs = [cur, ...convs.filter((c) => c.id !== cur.id)].slice(0, 80);
    save(KEY, convs);
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
          else if (ev.type === "card" && ev.card) { cards.push(ev.card); later(); }
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
  function strategies(keep) {
    if (!keep || !main.querySelector("#sgrid")) {
      main.innerHTML = `<div class="page"><h1 class="h1">Strategies.</h1><p class="sub">Every play: what it watches, what it would have done, and what it did.</p>` +
        `<div class="toolbar"><input class="field" id="sq" placeholder="Search strategies" style="width:260px" value="${esc(sQuery)}" />` +
        `<select class="field" id="ss"><option value="all">All statuses</option><option value="armed">Armed</option><option value="paused">Paused</option><option value="done">Completed</option></select>` +
        `<select class="field" id="so"><option value="new">Newest first</option><option value="fired">Most fired</option><option value="az">A to Z</option></select>` +
        `<span class="grow"></span><button type="button" class="btn dark" data-go="home">${ic("sparkle")}New play</button></div><div id="sgrid"></div></div>`;
      $("ss").value = sState; $("so").value = sSort;
      $("sq").addEventListener("input", (e) => { sQuery = e.target.value; paintGrid(); });
      $("ss").addEventListener("change", (e) => { sState = e.target.value; paintGrid(); });
      $("so").addEventListener("change", (e) => { sSort = e.target.value; paintGrid(); });
    }
    paintGrid();
  }
  function paintGrid() {
    const q = sQuery.trim().toLowerCase();
    let rows = (data.plays || []).filter((p) => (sState === "all" || p.state === sState) &&
      (!q || `${p.title} ${p.summary} ${p.symbol}`.toLowerCase().includes(q)));
    rows = rows.slice().sort(sSort === "fired" ? (a, b) => (b.fires || 0) - (a.fires || 0) :
      sSort === "az" ? (a, b) => a.title.localeCompare(b.title) : (a, b) => (b.created || b.id) - (a.created || a.id));
    $("sgrid").innerHTML = rows.length ? `<div class="grid">${rows.map(Kit.tile).join("")}</div>`
      : `<div class="empty">${(data.plays || []).length ? "Nothing matches that." : "No plays yet. Say one on Home and it appears here."}</div>`;
  }

  /* ── one strategy ─────────────────────────────────────────────────────── */
  let ptab = "activity";
  async function strategyPage(id) {
    main.innerHTML = `<div class="page"><div class="muted">Loading…</div></div>`;
    let d;
    try { d = await call(`/plays/${id}`); } catch (err) {
      main.innerHTML = `<div class="page"><div class="empty">${esc(err.message)}</div></div>`; return;
    }
    const c = d.play;
    const chartSym = (c.do || {}).symbol || (c.do || {}).underlying || c.symbol;
    main.innerHTML = `<div class="page"><div class="crumbs"><button type="button" data-go="strategies">Strategies</button><span>/</span><span>${esc(c.title)}</span></div>` +
      `<div class="detail"><div><div class="panel"><div class="panel-h">${ic("flow")}<span>Workflow</span><span class="grow"></span>` +
      `<a class="btn sm ghost" href="./?symbol=${encodeURIComponent(chartSym)}&interval=1D" target="pivot-chart">${ic("chart")}Chart</a></div><div id="wf"></div></div>` +
      `<div class="ask-bar">${composerHTML("ask", `Ask for a change to ${c.title}`)}</div></div>` +
      `<div><div class="panel"><div class="panel-h">${Kit.logo(chartSym, "sm")}<span>${esc(chartSym)}</span><span class="grow"></span>` +
      `<span class="muted" style="font-size:12px">${(c.history || {}).fire_ts && c.history.fire_ts.length ? "dots mark past fires" : ""}</span></div>` +
      `<div class="pnl"><div id="px"></div></div></div>` +
      `<div class="panel" style="margin-top:14px"><div class="subtabs"><button type="button" data-ptab="activity" class="${ptab === "activity" ? "on" : ""}">${ic("bell")}Activity</button>` +
      `<button type="button" data-ptab="holdings" class="${ptab === "holdings" ? "on" : ""}">${ic("pie")}Holdings</button></div><div id="ptab"></div></div></div></div></div>`;
    $("wf").appendChild(Kit.strategy(c, { open: false }));
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
      const conv = where[id] && convs.find((x) => x.id === where[id]);
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
    if ((data.events || []).some((e) => !e.seen)) setTimeout(() => call("/plays/seen", {}).then(() => { data.events.forEach((e) => { e.seen = true; }); paintTabs(); }).catch(() => {}), 1500);
  }

  /* ── portfolio ────────────────────────────────────────────────────────── */
  let pftab = "holdings";
  async function portfolio() {
    main.innerHTML = `<div class="page"><div class="muted">Loading…</div></div>`;
    let sum = {}, holds = [], nav = [], fills = [];
    try {
      [sum, holds, nav, fills] = await Promise.all([call("/paper/summary"), call("/paper/holdings"), call("/paper/nav").catch(() => []),
        call("/paper/fills?limit=40").catch(() => [])]);
    } catch (err) { main.innerHTML = `<div class="page"><div class="empty">${esc(err.message)}</div></div>`; return; }
    const total = (holds || []).reduce((a, h) => a + (+h.market_value || 0), 0);
    main.innerHTML = `<div class="page"><h1 class="h1">Portfolio</h1><p class="sub">The paper book your plays trade in. Simulated, with estimated costs.</p><div class="pf">` +
      `<div class="panel"><div class="panel-h">${ic("wallet")}<span>Value</span></div><div class="pnl">` +
      `<div class="row" style="align-items:baseline;gap:10px"><span class="bignum">${Kit.bigMoney(sum.nav)}</span>` +
      `<span class="pill ${sum.total_pnl >= 0 ? "up" : "down"}">${esc(Kit.signed(sum.total_pnl_pct))}</span></div>` +
      `<div id="navc" style="margin-top:8px"></div></div>` +
      `<div class="grid" style="grid-template-columns:repeat(4,1fr);margin-top:12px;gap:10px">${[["Cash", sum.cash_available], ["Invested", sum.invested], ["Today", sum.day_pnl], ["All time", sum.total_pnl]].map(([k, v]) =>
        `<div style="background:var(--panel);border-radius:10px;padding:10px"><div class="muted" style="font-size:12px">${k}</div><div class="num ${k === "Today" || k === "All time" ? (v >= 0 ? "up" : "down") : ""}" style="font-size:16px;font-weight:500;margin-top:2px">${esc(Kit.inr(v))}</div></div>`).join("")}</div></div>` +
      `<div class="panel"><div class="panel-h">${ic("pie")}<span>Allocation</span></div><div class="donut-wrap" id="donut"></div></div></div>` +
      `<div class="panel" style="margin-top:16px"><div class="subtabs">${[["holdings", "Holdings", "pie"], ["day", "Day book", "bolt"], ["tx", "Transactions", "list"]].map(([k, l, i]) =>
        `<button type="button" data-pf="${k}" class="${pftab === k ? "on" : ""}">${ic(i)}${l}</button>`).join("")}</div><div id="pft"></div></div></div>`;
    const pts = (nav || []).map((n) => ({ t: Date.parse(n.as_of_date) / 1000, v: +n.nav }));
    if (pts.length >= 2) Kit.lineChart($("navc"), pts, { height: 150, width: 620, area: true, yfmt: (v) => Kit.inr(v) });
    else $("navc").innerHTML = `<div class="muted" style="font-size:13px;padding:24px 0">The value line starts after the first day of trading.</div>`;
    donut($("donut"), holds, total, sum.cash_available);
    const paint = () => {
      main.querySelectorAll("[data-pf]").forEach((b) => b.classList.toggle("on", b.dataset.pf === pftab));
      const box = $("pft");
      if (pftab === "holdings") {
        box.innerHTML = holds.length ? `<table class="table"><thead><tr><th>Name</th><th class="r">Qty</th><th class="r">Avg</th><th class="r">Last</th><th class="r">Value</th><th class="r">P&amp;L</th></tr></thead><tbody>` +
          holds.map((h) => `<tr><td><span class="row">${Kit.logo(h.symbol)}<span>${esc(h.symbol)}</span></span></td><td class="r">${esc(Kit.num(h.quantity, 4))}</td>` +
            `<td class="r">${esc(Kit.num(h.avg_cost))}</td><td class="r">${esc(Kit.num(h.last_price))}</td><td class="r">${esc(Kit.inr(h.market_value))}</td>` +
            `<td class="r ${h.unrealized_pnl >= 0 ? "up" : "down"}">${esc(Kit.inr(h.unrealized_pnl))} <span class="muted">${esc(Kit.signed(h.unrealized_pct))}</span></td></tr>`).join("") + `</tbody></table>`
          : `<div class="muted" style="padding:12px 4px">No delivery holdings.</div>`;
      } else if (pftab === "day") {
        const pos = (data.book || {}).positions || [];
        box.innerHTML = pos.length ? `<table class="table"><thead><tr><th>Position</th><th class="r">Qty</th><th class="r">Entry</th><th class="r">Now / out</th><th>Stops</th><th class="r">P&amp;L</th><th></th></tr></thead><tbody>` +
          pos.map((p) => {
            const nm = p.kind === "option" ? `${p.underlying} ${Kit.num(p.strike)} ${p.opt}` : p.kind === "future" ? `${p.underlying} future` : p.symbol;
            const open = p.state === "open", pnl = open ? p.open_pnl : p.pnl;
            return `<tr><td><span class="row">${Kit.logo(p.underlying || p.symbol)}<span><span class="chip ${p.side === "short" ? "sell" : "buy"}">${p.side === "short" ? "Short" : "Long"}</span> ${esc(nm)}</span></span>` +
              `<div class="muted" style="font-size:12px;margin-top:2px">${esc(p.play || "")}${p.product === "MIS" ? " · intraday" : ""}</div></td>` +
              `<td class="r">${esc(Kit.num(p.qty))}</td><td class="r">${esc(Kit.num(p.entry_px))}</td><td class="r">${esc(Kit.num(open ? p.mark_px : p.exit_px))}${open && p.stale ? ' <span class="pill wait">last close</span>' : ""}</td>` +
              `<td>${[p.stop_px != null ? `stop ${Kit.num(p.stop_px)}` : "", p.target_px != null ? `target ${Kit.num(p.target_px)}` : ""].filter(Boolean).map(esc).join(" · ") || (open ? "" : esc(p.exit_why || ""))}</td>` +
              `<td class="r ${pnl >= 0 ? "up" : "down"}">${esc(Kit.inr(pnl))}</td><td class="r">${open ? `<button type="button" class="btn sm" data-close="${esc(p.id)}">Close</button>` : ""}</td></tr>`;
          }).join("") + `</tbody></table>` : `<div class="muted" style="padding:12px 4px">No intraday, short or derivative positions.</div>`;
      } else {
        box.innerHTML = fills.length ? `<table class="table"><thead><tr><th>When</th><th>Name</th><th>Side</th><th class="r">Qty</th><th class="r">Price</th><th class="r">Value</th></tr></thead><tbody>` +
          fills.map((f) => `<tr><td class="muted">${esc(new Date(f.filled_at).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }))}</td>` +
            `<td><span class="row">${Kit.logo(f.symbol, "sm")}${esc(f.symbol)}</span></td><td><span class="chip ${f.side === "BUY" ? "buy" : "sell"}">${f.side === "BUY" ? "Buy" : "Sell"}</span></td>` +
            `<td class="r">${esc(Kit.num(f.quantity, 4))}</td><td class="r">${esc(Kit.num(f.fill_price))}</td><td class="r">${esc(Kit.inr(f.gross_value))}</td></tr>`).join("") + `</tbody></table>`
          : `<div class="muted" style="padding:12px 4px">No trades yet.</div>`;
      }
    };
    main.querySelectorAll("[data-pf]").forEach((b) => b.addEventListener("click", () => { pftab = b.dataset.pf; paint(); }));
    main.addEventListener("click", async (e) => {
      const x = e.target.closest("[data-close]");
      if (!x) return;
      x.disabled = true;
      try { await call(`/plays/positions/${x.dataset.close}`, {}); await refresh(); paint(); } catch (err) { x.disabled = false; Kit.toast(err.message); }
    });
    paint();
  }

  function donut(host, holds, total, cash) {
    const rows = (holds || []).filter((h) => +h.market_value > 0).sort((a, b) => b.market_value - a.market_value);
    const all = total + Math.max(0, +cash || 0);
    if (!all) { host.innerHTML = `<div class="muted">Nothing held yet.</div>`; return; }
    const segs = [...rows.map((h, i) => ({ s: h.symbol, v: +h.market_value, c: Kit.COLORS[i % Kit.COLORS.length] })),
      ...(cash > 0 ? [{ s: "Cash", v: +cash, c: "#e2e2e2" }] : [])];
    const R = 64, C = 2 * Math.PI * R;
    let off = 0;
    const arcs = segs.map((g) => {
      const len = g.v / all * C;
      const a = `<circle r="${R}" cx="84" cy="84" fill="none" stroke="${g.c}" stroke-width="20" stroke-dasharray="${Math.max(0, len - 2)} ${C}" stroke-dashoffset="${-off}" transform="rotate(-90 84 84)"/>`;
      off += len;
      return a;
    }).join("");
    const top = segs[0];
    host.innerHTML = `<div class="donut"><svg viewBox="0 0 168 168" width="168" height="168">${arcs}</svg>` +
      `<div class="c"><div><b>${esc(Kit.num(top.v / all * 100, 1))}%</b>${esc(top.s)}</div></div></div>` +
      `<div style="flex:1;display:flex;flex-direction:column;gap:7px;font-size:13px">${segs.slice(0, 7).map((g) =>
        `<div class="row" style="gap:8px"><span style="width:9px;height:9px;border-radius:3px;background:${g.c}"></span>${g.s === "Cash" ? "" : Kit.logo(g.s, "sm")}<span style="flex:1">${esc(g.s)}</span>` +
        `<b class="num" style="font-weight:500">${esc(Kit.inr(g.v))}</b></div>`).join("")}</div>`;
  }

  /* ── sign in ──────────────────────────────────────────────────────────── */
  function signIn() {
    main.innerHTML = `<form class="signin" id="sf"><h2>Sign in to Pivot</h2><p>Plays run on the server, so they keep watching with this tab closed.</p>` +
      `<input class="field" type="email" name="email" placeholder="Email" autocomplete="username" required />` +
      `<input class="field" type="password" name="password" placeholder="Password" autocomplete="current-password" required />` +
      `<button class="btn dark" type="submit">Sign in</button><div class="e" id="se"></div></form>`;
    $("sf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      try {
        const res = await fetch(`${Kit.API}/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: f.get("email"), password: f.get("password") }) });
        const d = await res.json().catch(() => ({}));
        if (!res.ok || !d.token) throw new Error(d.error || "That email and password did not match.");
        localStorage.setItem("charto:auth:token", d.token);
        location.reload();
      } catch (err) { $("se").textContent = err.message; }
    });
  }

  /* ── global clicks ────────────────────────────────────────────────────── */
  document.addEventListener("click", async (e) => {
    const g = e.target.closest("[data-go]");
    if (g) { e.preventDefault(); go(g.dataset.go === "home" ? "#/" : `#/${g.dataset.go}`); return; }
    const op = e.target.closest("[data-open]");
    if (op && op.dataset.open) { go(`#/s/${op.dataset.open}`); return; }
    const cv = e.target.closest("[data-conv]");
    if (cv) { go(`#/c/${cv.dataset.conv}`); return; }
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
    const av = e.target.closest("#avatar");
    const menu = document.querySelector(".menu");
    if (menu && !e.target.closest(".menu")) menu.remove();
    if (av && !menu) {
      const m = document.createElement("div");
      m.className = "menu";
      m.innerHTML = `<div class="who">${esc((Auth.user || {}).email || "Signed out")}</div><a href="./" target="pivot-chart">Open the chart</a>` +
        (Auth.user ? `<button type="button" id="so2">Sign out</button>` : "");
      document.body.appendChild(m);
      const so = m.querySelector("#so2");
      if (so) so.addEventListener("click", async () => { await Auth.logout(); location.reload(); });
    }
  });
  window.addEventListener("plays:changed", () => refresh());

  /* the search: "/" to focus; a play's name opens it, anything else is asked */
  const sIn = $("searchInput");
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); sIn.focus(); }
  });
  sIn.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const q = sIn.value.trim();
    if (!q) return;
    const hit = (data.plays || []).find((p) => p.title.toLowerCase().includes(q.toLowerCase()));
    sIn.value = ""; sIn.blur();
    if (hit) { go(`#/s/${hit.id}`); return; }
    cur = null; go("#/");
    setTimeout(() => send(q), 30);
  });

  setInterval(() => { if (!document.hidden) refresh(); }, 60_000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });

  (async () => {
    Kit.loadLogos();
    const me = await Auth.resume();
    $("avatar").textContent = me && me.email ? me.email[0].toUpperCase() : "P";
    if (me) { await Promise.all([refresh(), Kit.loadLogos()]); }
    render();
  })();
})();
