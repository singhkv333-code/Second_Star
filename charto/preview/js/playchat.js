/* Pivot — the Plays page (plays.html): plays built and adjusted in conversation.
 *
 * A ChatGPT-shaped page: a rail with the trader's plays, securities, positions
 * and inbox, and one conversation in the middle. The same /chat endpoint the
 * chart uses answers it, with `context.surface = "plays"` so the model is told
 * there is no chart on screen and which plays already exist (plays.surface_block).
 * Every card is drawn by PlayUI (js/plays.js) from the stored row the server
 * returned, exactly as on the chart page.
 *
 * Conversations live in this browser (localStorage); the plays they built live
 * on the server. Each play remembers the conversation that built it, so opening
 * it from the rail returns to the place it was made, ready to be adjusted.
 */
"use strict";

(() => {
  const API = location.port === "5173" ? "http://127.0.0.1:5174" : "";
  const KEY = "pivot:plays:convs";
  const MAP = "pivot:plays:where";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) || d; } catch { return d; } };
  const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* full or blocked */ } };

  let convs = load(KEY, []);          // [{id, title, updated, focus, turns: [{role, content, cards}]}]
  let where = load(MAP, {});          // play id -> conversation id
  let cur = null;                     // the open conversation, or null for a new one
  let view = "chat";                  // chat | positions | inbox
  let data = { plays: [], securities: [], events: [], book: { positions: [] } };
  let busy = false;

  /* ── markdown: the subset the model writes ───────────────────────────── */
  function inline(s) {
    return s
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  }
  function md(src) {
    const lines = esc(src).replace(/\r/g, "").split("\n");
    const out = [];
    let para = [], list = null, tbl = [], code = null;
    const flushP = () => { if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para = []; } };
    const flushL = () => { if (list) { out.push(`<${list.tag}>${list.items.map((x) => `<li>${inline(x)}</li>`).join("")}</${list.tag}>`); list = null; } };
    const flushT = () => {
      if (!tbl.length) return;
      const rows = tbl.filter((r) => !/^\|?\s*:?-{2,}/.test(r)).map((r) =>
        r.replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim())));
      const [head, ...rest] = rows;
      out.push(`<table><thead><tr>${head.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>` +
        rest.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("") + `</tbody></table>`);
      tbl = [];
    };
    const flush = () => { flushP(); flushL(); flushT(); };
    for (const raw of lines) {
      const t = raw.trim();
      if (code !== null) {
        if (t.startsWith("```")) { out.push(`<pre><code>${code.join("\n")}</code></pre>`); code = null; }
        else code.push(raw);
        continue;
      }
      if (t.startsWith("```")) { flush(); code = []; continue; }
      if (!t) { flush(); continue; }
      if (t.startsWith("|")) { flushP(); flushL(); tbl.push(t); continue; }
      flushT();
      const h = t.match(/^(#{1,3})\s+(.*)$/);
      if (h) { flush(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); continue; }
      const q = t.match(/^&gt;\s?(.*)$/);
      if (q) { flush(); out.push(`<blockquote>${inline(q[1])}</blockquote>`); continue; }
      const ul = t.match(/^[-*•]\s+(.*)$/), ol = t.match(/^\d+[.)]\s+(.*)$/);
      if (ul || ol) {
        flushP();
        const tag = ul ? "ul" : "ol";
        if (!list || list.tag !== tag) { flushL(); list = { tag, items: [] }; }
        list.items.push((ul || ol)[1]);
        continue;
      }
      if (list && /^\s{2,}/.test(raw)) { list.items[list.items.length - 1] += " " + t; continue; }
      flushL();
      para.push(t);
    }
    if (code !== null) out.push(`<pre><code>${code.join("\n")}</code></pre>`);
    flush();
    return out.join("");
  }

  /* ── cards ────────────────────────────────────────────────────────────── */
  function cardEl(c) {
    const box = document.createElement("div");
    box.className = "pc-card";
    if (c.kind === "play") {
      box.innerHTML = PlayUI.html(c);
      PlayUI.wire(box, c, () => refresh());
    } else if (c.kind === "custom_security") {
      box.innerHTML = PlayUI.security(c);
    } else {
      return null;                     // chart panels belong to the chart page
    }
    return box;
  }

  /** The reply's prose with its cards: each [[panel]] marker takes the next
   *  card; any card left over follows the text. */
  function aiEl(text, cards) {
    const el = document.createElement("div");
    el.className = "pc-ai";
    const shown = (cards || []).filter((c) => c.kind === "play" || c.kind === "custom_security");
    const parts = String(text || "").split(/\n?\s*\[\[panel(?::[^\]]*)?\]\]\s*\n?/);
    let k = 0;
    parts.forEach((p, i) => {
      if (p.trim()) {
        const d = document.createElement("div");
        d.className = "md";
        d.innerHTML = md(p);
        el.appendChild(d);
      }
      if (i < parts.length - 1 && k < shown.length) {
        const c = cardEl(shown[k++]);
        if (c) el.appendChild(c);
      }
    });
    while (k < shown.length) {
      const c = cardEl(shown[k++]);
      if (c) el.appendChild(c);
    }
    return el;
  }

  /* ── the conversation ─────────────────────────────────────────────────── */
  const col = $("col"), body = $("body");
  const atBottom = () => body.scrollHeight - body.scrollTop - body.clientHeight < 80;
  const toBottom = () => { body.scrollTop = body.scrollHeight; };

  const EXAMPLES = [
    ["Alert", "Tell me when HDFC Bank closes below its 50-day average."],
    ["Basket", "Make me a defence basket and alert me when it breaks its 50-DMA."],
    ["Intraday", "Short RELIANCE for ₹50,000 if a 5 minute candle closes below the first 15 minutes' low, trail it 0.8%, out by 3:10."],
    ["Options", "Sell the NIFTY ATM straddle at 9:20 with a 25% stop on each leg, and move the other leg to cost if one stops."],
    ["Filings", "If Reliance files an order win on NSE, propose buying ₹10,000 of it while it trades above its 20-day average."],
    ["Rebalance", "Rebalance my paper portfolio to equal weight, and keep it that way every week."],
  ];

  function paintEmpty() {
    col.innerHTML = `<div class="pc-empty"><h2>What should Pivot watch for you?</h2>` +
      `<p>Say it the way you would to a trader beside you. Pivot builds it as a play, ` +
      `shows how often it would have fired, and runs it on paper. Ask for any change in the same conversation.</p>` +
      `<div class="pc-ex">${EXAMPLES.map(([h, q]) =>
        `<button type="button" data-ex="${esc(q)}"><b>${esc(h)}</b>${esc(q)}</button>`).join("")}</div></div>`;
  }

  function turnEl(t) {
    const w = document.createElement("div");
    w.className = "pc-turn";
    if (t.role === "user") {
      w.innerHTML = `<div class="pc-user"><div class="b">${esc(t.content)}</div></div>`;
    } else {
      w.appendChild(aiEl(t.content, t.cards));
      if (t.meta) {
        const m = document.createElement("div");
        m.className = "pc-meta";
        m.textContent = t.meta;
        w.appendChild(m);
      }
    }
    return w;
  }

  function paintConv() {
    $("barTitle").textContent = cur ? cur.title : "New play";
    $("foot").hidden = false;
    if (!cur || !cur.turns.length) return paintEmpty();
    col.innerHTML = "";
    cur.turns.forEach((t) => col.appendChild(turnEl(t)));
    toBottom();
  }

  function persist() {
    if (!cur) return;
    cur.updated = Date.now();
    convs = [cur, ...convs.filter((c) => c.id !== cur.id)].slice(0, 60);
    save(KEY, convs);
  }

  const TOOL_WORDS = {
    play: "Building the play", measure: "Working out the measure", read_chart: "Reading the chart",
    market_data: "Reading market data", search: "Searching", query: "Looking it up",
    screen: "Screening", journal: "Reading your portfolio", workspace: "Checking the workspace",
  };

  async function send(text) {
    text = String(text || "").trim();
    if (!text || busy) return;
    if (!Auth.user) return paintSignIn();
    view = "chat";
    if (!cur) {
      cur = { id: `pc${Date.now().toString(36)}`, title: text.slice(0, 60), updated: Date.now(), turns: [] };
    }
    if (!cur.turns.length) col.innerHTML = "";
    cur.turns.push({ role: "user", content: text });
    persist();
    paintRail();
    $("barTitle").textContent = cur.title;
    col.appendChild(turnEl(cur.turns[cur.turns.length - 1]));

    const w = document.createElement("div");
    w.className = "pc-turn";
    const status = document.createElement("div");
    status.className = "pc-status";
    status.innerHTML = `<span class="pc-spin" aria-hidden="true"></span><span>Thinking</span>`;
    const prose = document.createElement("div");
    w.append(status, prose);
    col.appendChild(w);
    toBottom();
    busy = true;
    $("send").disabled = true;

    const t0 = performance.now();
    let text2 = "", cards = [], done = null, raf = 0;
    const paint = () => {
      raf = 0;
      const stick = atBottom();
      prose.replaceChildren(aiEl(text2, cards));
      if (stick) toBottom();
    };
    const later = () => { if (!raf) raf = requestAnimationFrame(paint); };
    try {
      const wire = cur.turns.filter((t) => !t.local).map((t) => ({ role: t.role, content: t.content }));
      const res = await fetch(`${API}/chat`, {
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
      const reader = res.body.getReader();
      const dec = new TextDecoder();
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
          if (ev.type === "delta") { text2 += ev.text || ""; status.hidden = true; later(); }
          else if (ev.type === "retract") { text2 = ev.text || ""; later(); }
          else if (ev.type === "tool_start" || ev.type === "tool") {
            status.hidden = false;
            status.lastChild.textContent = TOOL_WORDS[ev.name] || "Working";
          } else if (ev.type === "card" && ev.card) { cards.push(ev.card); later(); }
          else if (ev.type === "done") { done = ev; }
        }
        if (done) break;
      }
      if (done && done.error) throw new Error(done.error);
      text2 = (done && done.text) || text2;
      if (!cards.length && done && done.cards) cards = done.cards;
      const meta = `${((performance.now() - t0) / 1000).toFixed(1)}s`;
      cur.turns.push({ role: "assistant", content: text2, cards, meta });
      for (const c of cards) if (c.kind === "play" && c.id) where[c.id] = cur.id;
      save(MAP, where);
      persist();
      w.replaceWith(turnEl(cur.turns[cur.turns.length - 1]));
    } catch (err) {
      status.remove();
      prose.innerHTML = `<div class="pc-err">${esc(err.message || "The answer did not arrive.")} Try again.</div>`;
      cur.turns.pop();              // the question was not answered; it can be sent again
      persist();
      $("input").value = text;
      fit();
    } finally {
      busy = false;
      $("send").disabled = !$("input").value.trim();
      refresh();
    }
  }

  /* ── the rail ─────────────────────────────────────────────────────────── */
  const STATE = { armed: "Watching", paused: "Paused", done: "Finished", draft: "Draft" };

  function paintRail() {
    const plays = data.plays || [];
    const live = plays.filter((p) => p.state === "armed" || p.state === "paused" || p.state === "draft");
    const old = plays.filter((p) => p.state === "done");
    const row = (p) => `<button type="button" class="pc-item${cur && cur.focusOf === p.id ? " on" : ""}" data-play="${p.id}">` +
      `<span class="t">${esc(p.title)}</span><span class="s">${esc(STATE[p.state] || p.state)}</span></button>`;
    const secs = data.securities || [];
    const recent = convs.slice(0, 15);
    $("railList").innerHTML =
      `<div class="pc-h">Plays</div>` +
      (live.length ? live.map(row).join("") : `<p class="pc-none">None running yet.</p>`) +
      (old.length ? `<div class="pc-h">Finished</div>${old.slice(0, 15).map(row).join("")}` : "") +
      (secs.length ? `<div class="pc-h">Your securities</div>` + secs.map((s) =>
        `<button type="button" class="pc-item" data-sec="${esc(s.symbol)}"><span class="t">${esc(s.title)}</span>` +
        `<span class="s">${esc(PlayUI.num(s.now))}</span></button>`).join("") : "") +
      (recent.length ? `<div class="pc-h">Conversations</div>` + recent.map((c) =>
        `<button type="button" class="pc-item${cur && cur.id === c.id && view === "chat" ? " on" : ""}" data-conv="${esc(c.id)}">` +
        `<span class="t">${esc(c.title)}</span></button>`).join("") : "");
    const waiting = (data.events || []).filter((e) => e.kind === "proposed" && e.status === "open").length;
    $("navInbox").textContent = waiting ? `${waiting} waiting` : (data.unseen ? `${data.unseen} new` : "");
    const pnl = (data.book || {}).day_pnl;
    $("navPnl").textContent = (data.book && (data.book.positions || []).some((p) => p.state === "open")) && pnl != null
      ? `${pnl < 0 ? "−" : "+"}₹${Math.abs(pnl).toLocaleString("en-IN", { maximumFractionDigits: 0 })}` : "";
    document.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("on", b.dataset.view === view));
  }

  function paintMe() {
    const u = Auth.user;
    $("me").innerHTML = u ? `<span>${esc(u.email || u.name || "Signed in")}</span><button type="button" id="signOut">Sign out</button>`
      : `<span>Signed out</span><button type="button" id="signIn">Sign in</button>`;
  }

  async function refresh() {
    if (!Auth.user) return;
    try {
      data = await PlayUI.call("/plays");
    } catch { return; }
    paintRail();
    if (view === "positions") paintPositions();
    if (view === "inbox") paintInbox(false);
  }

  /* ── positions and the inbox ──────────────────────────────────────────── */
  function paintPositions() {
    $("barTitle").textContent = "Positions";
    $("foot").hidden = true;
    col.innerHTML = PlayUI.positionsHTML(data.book || { positions: [] });
  }

  function paintInbox(markSeen = true) {
    $("barTitle").textContent = "Inbox";
    $("foot").hidden = true;
    const evs = data.events || [];
    col.innerHTML = evs.length ? `<div class="pc-list">${evs.map(PlayUI.eventHTML).join("")}</div>`
      : `<div class="side-empty"><p>Nothing yet. When a play triggers, what it saw and what it did land here.</p></div>`;
    if (markSeen && data.unseen) PlayUI.call("/plays/seen", {}).then(() => { data.unseen = 0; paintRail(); }).catch(() => {});
  }

  /* ── opening things ───────────────────────────────────────────────────── */
  function openConv(id) {
    cur = convs.find((c) => c.id === id) || null;
    view = "chat";
    paintConv();
    paintRail();
    closeRail();
  }

  /** A play from the rail: back to the conversation that built it, or a new
   *  one that starts from its card, pointed at it, ready for a change. */
  function openPlay(pid) {
    const conv = where[pid] && convs.find((c) => c.id === where[pid]);
    if (conv) return openConv(conv.id);
    const card = (data.plays || []).find((p) => p.id === pid);
    if (!card) return;
    cur = { id: `pc${Date.now().toString(36)}`, title: card.title, updated: Date.now(), focus: pid, focusOf: pid,
            turns: [{ role: "assistant", local: true, content: "Here it is. Tell me what to change.", cards: [card] }] };
    where[pid] = cur.id;
    save(MAP, where);
    persist();
    view = "chat";
    paintConv();
    paintRail();
    closeRail();
  }

  function paintSignIn() {
    $("barTitle").textContent = "Sign in";
    $("foot").hidden = true;
    col.innerHTML = `<form class="pc-sign" id="signForm"><h2>Sign in to build plays</h2>` +
      `<p>Plays run on the server, so they keep watching with this tab closed.</p>` +
      `<input type="email" name="email" placeholder="Email" autocomplete="username" required />` +
      `<input type="password" name="password" placeholder="Password" autocomplete="current-password" required />` +
      `<button type="submit">Sign in</button><div class="e" id="signErr"></div></form>`;
  }

  /* ── wiring ───────────────────────────────────────────────────────────── */
  const input = $("input");
  const fit = () => { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 220)}px`; };
  input.addEventListener("input", () => { fit(); $("send").disabled = busy || !input.value.trim(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("form").requestSubmit(); }
  });
  $("form").addEventListener("submit", (e) => {
    e.preventDefault();
    const t = input.value;
    input.value = "";
    fit();
    $("send").disabled = true;
    send(t);
  });

  const closeRail = () => $("pc").classList.remove("rail-open");
  $("menuBtn").addEventListener("click", () => $("pc").classList.toggle("rail-open"));
  $("pc").addEventListener("click", (e) => {
    if ($("pc").classList.contains("rail-open") && !e.target.closest("#rail") && !e.target.closest("#menuBtn")) closeRail();
  });

  $("newConv").addEventListener("click", () => {
    cur = null; view = "chat"; paintConv(); paintRail(); closeRail(); input.focus();
  });

  document.addEventListener("click", async (e) => {
    const ex = e.target.closest("[data-ex]");
    if (ex) { input.value = ex.dataset.ex; fit(); $("send").disabled = false; input.focus(); return; }
    const v = e.target.closest("[data-view]");
    if (v) {
      if (!Auth.user) return paintSignIn();
      view = v.dataset.view;
      view === "positions" ? paintPositions() : paintInbox();
      paintRail(); closeRail(); return;
    }
    const p = e.target.closest("[data-play]");
    if (p && p.closest("#rail")) { openPlay(+p.dataset.play); return; }
    const c = e.target.closest("[data-conv]");
    if (c) { openConv(c.dataset.conv); return; }
    const s = e.target.closest("[data-sec]") || e.target.closest("[data-py-open]");
    if (s) {
      // the chart lives on the chart page; a custom security is a symbol there
      window.open(`./?symbol=${encodeURIComponent(s.dataset.sec || s.dataset.pyOpen)}&interval=1D`, "pivot-chart");
      return;
    }
    const x = e.target.closest("[data-close-pos]");
    if (x) {
      x.disabled = true;
      try { await PlayUI.call(`/plays/positions/${x.dataset.closePos}`, {}); refresh(); }
      catch (err) { x.disabled = false; PlayUI.toast(err.message); }
      return;
    }
    if (e.target.id === "signOut") { await Auth.logout(); location.reload(); return; }
    if (e.target.id === "signIn") { paintSignIn(); return; }
  });

  document.addEventListener("submit", async (e) => {
    if (e.target.id !== "signForm") return;
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      const res = await fetch(`${API}/auth/login`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: f.get("email"), password: f.get("password") }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok || !d.token) throw new Error(d.error || "That email and password did not match.");
      localStorage.setItem("charto:auth:token", d.token);
      location.reload();
    } catch (err) {
      $("signErr").textContent = err.message;
    }
  });

  // the inbox's Confirm and Dismiss
  PlayUI.wireInbox(col, () => refresh());
  window.addEventListener("pivot:plays-changed", () => refresh());
  // a light refresh while the page is in view: a play can fire at any bar close
  setInterval(() => { if (!document.hidden) refresh(); }, 60_000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });

  Auth.onChange(() => { paintMe(); refresh(); });
  (async () => {
    paintMe();
    paintConv();
    paintRail();
    const me = await Auth.resume();
    paintMe();
    if (!me) paintSignIn();
    else { refresh(); input.focus(); }
  })();
})();
