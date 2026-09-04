/* Charto preview — chat pane.
 *
 * Deliberately NOT a messenger. The thread reads as a document: your turn is
 * a quiet block, the model's turn is bare prose at full measure. Every
 * control (chart context, context inspector, clear) lives in the composer,
 * so the pane has no header chrome competing with the chart.
 *
 * Talks to the dataserver's /chat proxy (the same Azure Foundry endpoint
 * Pivot uses, deployment gpt-5.6-luna, medium reasoning).
 */
"use strict";

(function () {
  // same-origin behind a proxy, explicit port in local dev (see main.js)
  const LOCAL_DEV = ["localhost", "127.0.0.1"].includes(location.hostname);
  const API = LOCAL_DEV ? "http://127.0.0.1:5174" : "";
  /* Execution mode is a LAPTOP-ONLY surface for now.
   *
   * It builds and simulates a strategy well and then forgets it: there is no
   * saved-strategy list behind the deployed box, so a visitor can compose a
   * rule, be told it was saved, and find nothing afterwards. Shipping that to
   * anyone who opens the site is worse than not offering it yet.
   *
   * The half that is finished — Research — is what the site answers with, so
   * the switch STAYS, both halves visible and the same size. A control that
   * vanished in production would make the mode itself undiscoverable and
   * leave the remaining half looking like a lone unexplained label. It is
   * inert and says why on hover instead, which is the honest version of the
   * same screen. One flag, read in setChatMode below, so the click, the
   * arrow keys and the phone menu are all governed by a single rule. */
  const EXECUTION_ENABLED = LOCAL_DEV;
  const el = (id) => document.getElementById(id);
  const msgsEl = el("chatMsgs"), threadEl = el("thread"), input = el("chatInput"),
        sendBtn = el("chatSend"), panel = el("chatPanel");

  // One source for the thread: the wire payload maps out of this, and this is
  // what persists — so a restored conversation and a live one can't diverge.
  // `meta` is display-only (latency, tokens, tools) and never reaches the model.
  //
  // Mutated in PLACE, never rebound: every closure below holds this one array,
  // so opening a past conversation refills it rather than replacing it.
  const turns = [];   // [{role, content, ts?, image?, drawing?, meta?, acts?}]
  // A stored "execution" must not survive into a build that cannot run it:
  // the mode is remembered per browser, so anyone who used it on a laptop and
  // then opened the deployed site would land in the half that is switched off.
  let chatMode = EXECUTION_ENABLED
    && Store.get("chatmode", "chat") === "execution" ? "execution" : "chat";
  const wireHistory = () => turns.map((t) => ({
    role: t.role, content: t.content,
    // The chart this turn was asked on. A conversation survives a symbol
    // change by design, so a thread can hold six turns of NIFTY above one
    // GOLD question — and without this the transcript never said so, leaving
    // the model to answer the new chart out of the old chart's prose.
    ...(t.symbol ? { symbol: t.symbol } : {}),
    ...(t.image ? { image: t.image } : {}),
    ...(t.drawing ? { drawing: t.drawing } : {}),
    ...(t.journal ? { journal: t.journal } : {}),
  }));

  /* ── the archive ─────────────────────────────────────────────────────────
   * A conversation is kept, not overwritten. Starting a new one files the
   * old one away instead of erasing it, and the history overlay reads that
   * file back — the same move Claude Code's resume list makes.
   *
   * Bounded on both axes, because localStorage is ~5MB and Store.set swallows
   * a quota error: it would stop saving silently rather than loudly. Per
   * conversation, the last KEEP_TURNS turns; across the archive, MAX_CHATS
   * conversations, oldest-touched dropped first. Screenshots are the heavy
   * part — the OPEN conversation keeps only its newest one (the same policy
   * the server applies to what the model sees) and a filed one keeps none.
   */
  const KEEP_TURNS = 60, MAX_CHATS = 40;
  const newId = () =>
    `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  const blankChat = () => ({ id: newId(), created: Date.now(), updated: Date.now(), turns: [] });

  let chats = Store.get("chats", null);      // [{id, created, updated, turns}]
  let activeId = Store.get("chatid", null);
  if (!Array.isArray(chats)) {
    // migrate the single-thread key this replaces — an existing conversation
    // becomes the first record rather than being dropped on upgrade
    const old = Store.get("chat", []);
    chats = old.length
      ? [{ id: newId(), created: Date.now(), updated: Date.now(), turns: old }] : [];
    Store.del("chat");
    activeId = chats.length ? chats[0].id : null;
  }
  chats = chats.filter((c) => c && c.id && Array.isArray(c.turns));
  if (!chats.some((c) => c.id === activeId)) activeId = null;
  if (!activeId) { const c = blankChat(); chats.unshift(c); activeId = c.id; }

  const active = () => chats.find((c) => c.id === activeId) || chats[0];
  turns.push(...(active().turns || []));

  /** Newest touched first — the order both the archive and the list use. */
  const byRecent = (a, b) => (b.updated || 0) - (a.updated || 0);

  function persistChats() {
    const open = active();          // held across the trim: the conversation
    // A conversation nothing was ever said in is not a record of anything —
    // a "New conversation" you then walked away from must not leave a row.
    chats = chats.filter((c) => c.turns.length || c === open);
    chats.sort(byRecent);           // on screen can never be the one dropped
    if (chats.length > MAX_CHATS) chats.length = MAX_CHATS;
    if (!chats.includes(open)) chats.push(open);
    Store.set("chats", chats.map((c) => {
      if (c.id !== activeId) {
        return { ...c, turns: c.turns.map((t) => (t.image ? { ...t, image: undefined } : t)) };
      }
      const lastImg = c.turns.map((t) => !!t.image).lastIndexOf(true);
      return { ...c, turns: c.turns.map((t, i) =>
        t.image && i !== lastImg ? { ...t, image: undefined } : t) };
    }));
    Store.set("chatid", activeId);
    mirrorChats();
  }

  /* ── the copy a LATER session can be asked about ──────────────────────────
   *
   * localStorage is where a conversation lives while you are having it, and
   * it is the wrong place to answer "what did we decide about ITC last
   * week" from: it dies with the browser profile and it is invisible to the
   * model, which runs on the server. So a signed-in user's archive is
   * mirrored to their account, where recall_conversations can read it.
   *
   * Signed out, nothing is sent and nothing is stored — which is also why
   * that tool answers "not signed in" rather than empty-handed.
   *
   * Debounced and fire-and-forget: this rides on every persist, and a
   * conversation must never wait on it, nor fail because of it.
   */
  let mirrorTimer = null;
  function mirrorChats() {
    if (typeof Auth === "undefined" || !Auth.token) return;
    clearTimeout(mirrorTimer);
    mirrorTimer = setTimeout(async () => {
      try {
        await fetch(`${API}/conversations`, {
          method: "POST",
          headers: Auth.headers({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            chats: chats.map((c) => ({
              id: c.id, created: c.created, updated: c.updated,
              symbols: [...new Set(c.turns.map((t) => t.symbol).filter(Boolean))],
              // text only — an image never leaves the browser for this
              turns: c.turns.map((t) => ({ role: t.role, content: t.content })),
            })),
          }),
        });
      } catch { /* the conversation is safe locally; a mirror can wait */ }
    }, 4000);
  }

  /* How many replies deep a tool's PANEL is still worth storing.
   *
   * A conversation is sixty turns and the archive holds forty of them, so
   * anything filed per-turn is multiplied by 2,400 before it reaches
   * localStorage. A pattern sweep's card is ~4KB — the whole archive would be
   * measured in megabytes, and the quota does not fail loudly: Store.set
   * swallows it, so the symptom would be a thread that silently stopped
   * saving at all. The text is what a conversation IS; a panel is the working
   * surface of the reply that made it. So the newest few keep theirs and the
   * rest scroll back to their prose, degrading exactly the way the follow-up
   * suggestions already do. */
  const KEEP_CARDS = 4;

  const saveTurns = () => {
    const rec = active();
    let left = KEEP_CARDS;
    // Newest first, and never in place: `turns` is the live thread, and
    // stripping a card off one of those objects would blank the panel the
    // user is looking at.
    rec.turns = turns.slice(-KEEP_TURNS).reverse().map((t) => {
      if (!t.cards) return t;
      if (left > 0) { left--; return t; }
      const { cards, ...rest } = t;   // eslint-disable-line no-unused-vars
      return rest;
    }).reverse();
    rec.updated = Date.now();
    persistChats();
  };

  // Write the archive back at boot rather than waiting for the next message.
  // A conversation migrated off the old single-thread key exists only in
  // memory until something is saved, and the old key has just been deleted —
  // a reload before you next spoke would have lost the thread outright.
  persistChats();
  let pending = false, requestAbort = null;
  let pendingImage = null;   // a captured screenshot waiting to ride the next send
  let pendingDraw = null;    // the drawing this message is about, by ref
  let pendingJournal = null; // an exact journal record, attached deliberately

  function journalTagInner(j, removable) {
    const t = j.trade || j;
    const result = t.net_pnl == null ? "Open" : `${t.net_pnl >= 0 ? "+" : ""}${Number(t.net_pnl).toLocaleString("en-IN")}`;
    return `<span class="draw-tag-mark">${Icons.svg("fileText", "sm")}</span>`
      + `<span class="draw-tag-copy"><strong>${drawEsc(t.symbol || "Journal trade")}</strong>`
      + `<small>${drawEsc(`${t.side || ""} · ${result} · Trade #${t.id || "new"}`)}</small></span>`
      + (removable ? `<button type="button" class="x" data-unjournal="1" aria-label="Remove journal attachment">${Icons.svg("x", "xs")}</button>` : "");
  }
  function setJournalTag(j) {
    pendingJournal = j;
    let row = el("journalTagRow");
    if (!row) { row = document.createElement("div"); row.id = "journalTagRow"; row.className = "draw-tag-row"; el("drawTagRow").after(row); }
    row.style.display = j ? "" : "none";
    row.innerHTML = j ? `<span class="draw-tag journal-tag">${journalTagInner(j, true)}</span>` : "";
  }
  document.addEventListener("charto:journal-chat", (e) => {
    setJournalTag(e.detail); panel.classList.remove("hidden"); el("splitter").classList.remove("hidden");
    el("chatToggle").classList.add("on"); input.placeholder = "Ask about this trade…"; input.focus();
  });
  document.addEventListener("click", (e) => { if (e.target.closest("[data-unjournal]")) { setJournalTag(null); input.placeholder = "Ask about this chart…"; } });

  // ── drawing tag ───────────────────────────────────────
  // Selecting a shape offers it as the subject of the next question, the same
  // way pinning a candle does. The message then carries the drawing's REF, so
  // the tools resolve exact geometry instead of the model guessing which
  // shape "this" meant and retyping its coordinates.
  const drawEsc = (v) => String(v == null ? "" : v)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  // The ratio family carries its own glyph — the rail's, so a Gann fan
  // attached to a question looks like the button it was drawn with.
  const RATIO_ICONS = ["fibExtension", "fibChannel", "fibTimeZone",
    "fibSpeedFan", "fibTimeExtension", "fibCircles", "fibSpiral", "fibArcs",
    "fibWedge", "pitchfan", "gannBox", "gannSquare", "gannSquareFixed",
    "gannFan"];
  const drawIcon = (type) => (RATIO_ICONS.includes(type) ? type : ({
    level: "hline", hline: "hline", vline: "vline",
    zone: "rect", box: "rect", rect: "rect",
    segment: "trend", poly: "disjointChannel", trend: "trend",
    fib: "fib", position: "position", channel: "channel",
  }[type] || "trend"));
  function drawTagInner(d, removable) {
    // Detector labels often carry measurements after a middle dot. Those
    // facts belong in the chart card; the attachment needs only identity.
    const rawName = String(d.label || d.type || "Drawing").split("·")[0].trim();
    const name = rawName ? rawName[0].toUpperCase() + rawName.slice(1) : "Drawing";
    // A user's drawing wears its REF — "D3" is the handle they see on the
    // chart and the one the chat writes back. A chat-drawn annotation has no
    // such handle: its id is a scene key minted by the detector, and printing
    // that under the name would be showing plumbing where the drawing shows
    // an address. The id still travels on the wire; it is just not something
    // to read.
    const meta = [d.origin === "chat" ? "Chart analysis" : "Drawing",
                  d.origin === "chat" ? "" : d.ref,
                  d.on ? `on ${d.on}`
                       : (d.pane && d.pane !== "price" ? `on ${d.pane}` : "")]
      .filter(Boolean).join(" · ");
    return `<span class="draw-tag-mark">${Icons.svg(drawIcon(d.type), "sm")}</span>`
      + `<span class="draw-tag-copy"><strong>${drawEsc(name)}</strong>`
      + `<small>${drawEsc(meta)}</small></span>`
      + (removable
        ? `<button type="button" class="x" data-untag="1" aria-label="Remove drawing attachment" title="Remove attachment">${Icons.svg("x", "xs")}</button>`
        : "");
  }
  function setDrawTag(d) {
    pendingDraw = d;
    const row = el("drawTagRow");
    row.style.display = d ? "" : "none";
    if (!d) return;
    row.innerHTML = `<span class="draw-tag">${drawTagInner(d, true)}</span>`;
  }
  // Only an explicit "Ask in chat" on the drawing's card tags it — selecting
  // a shape to drag or edit must never attach it to the conversation.
  document.addEventListener("charto:draw-tag", (e) => {
    if (e.detail) reveal();     // same reason as charto:compose — see reveal()
    setDrawTag(e.detail);
    input.focus();
  });
  el("drawTagRow").addEventListener("click", (e) => {
    if (e.target.closest("[data-untag]")) setDrawTag(null);
  });

  // ── screenshot attach flow ────────────────────────────
  // The camera button captures the CHART (all panes, no chat, no shell) and
  // offers it here; nothing is sent until the user attaches and sends.
  function setAttachment(uri) {
    pendingImage = uri;
    el("attachRow").style.display = uri ? "" : "none";
    if (uri) el("attachThumb").src = uri;
  }
  document.addEventListener("charto:screenshot", (e) => {
    el("shotPopImg").src = e.detail.uri;
    el("shotPop").style.display = "";
    el("shotAttach").onclick = () => {
      setAttachment(e.detail.uri);
      el("shotPop").style.display = "none";
      input.focus();
    };
    el("shotDismiss").onclick = () => { el("shotPop").style.display = "none"; };
  });
  el("attachRemove").addEventListener("click", () => setAttachment(null));
  // pasting an image into the composer attaches it the same way
  input.addEventListener("paste", (e) => {
    const item = [...(e.clipboardData?.items || [])].find((x) => x.type.startsWith("image/"));
    if (!item) return;
    e.preventDefault();
    const r = new FileReader();
    r.onload = () => setAttachment(r.result);
    r.readAsDataURL(item.getAsFile());
  });

  // ── markdown ──────────────────────────────────────────
  // Small on purpose: headings, lists, code, emphasis, rules. Escape first,
  // so nothing the model emits can inject markup.
  const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

  function inline(t) {
    return t
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
      // models escape markdown punctuation ("\*09:15") — unescape last, so the
      // escaped char can't be re-read as emphasis
      .replace(/\\([*_`|#[\]])/g, "$1");
  }

  const TABLE_ROW = (t) => t.startsWith("|") && t.endsWith("|") && t.length > 2;
  const TABLE_SEP = (t) => /^\|[\s:|-]+\|$/.test(t) && t.includes("-");
  const CELLS = (t) => t.replace(/^\||\|$/g, "").split("|").map((s) => s.trim());

  /** A pipe table, or — if the separator row is missing — the raw lines back. */
  function renderTable(lines) {
    if (lines.length < 2 || !TABLE_SEP(lines[1])) {
      return lines.map((l) => `<p>${inline(l)}</p>`).join("");
    }
    const align = CELLS(lines[1]).map((s) =>
      s.endsWith(":") ? (s.startsWith(":") ? "center" : "right") : "left");
    const cell = (tag) => (c, i) =>
      `<${tag} style="text-align:${align[i] || "left"}">${inline(c)}</${tag}>`;
    const head = CELLS(lines[0]).map(cell("th")).join("");
    const body = lines.slice(2)
      .map((l) => `<tr>${CELLS(l).map(cell("td")).join("")}</tr>`).join("");
    return `<div class="tablewrap"><table><thead><tr>${head}</tr></thead>` +
           `<tbody>${body}</tbody></table></div>`;
  }

  /** Render a run of list rows, honouring indentation (one nested level per
   *  indent step). Returns [html, indexOfFirstUnconsumedRow]. */
  function renderList(rows, start) {
    const base = rows[start].indent, tag = rows[start].tag;
    let html = `<${tag}>`, i = start;
    while (i < rows.length) {
      const r = rows[i];
      if (r.indent < base) break;
      if (r.indent > base) {
        const [sub, next] = renderList(rows, i);
        html = html.endsWith("</li>")
          ? html.slice(0, -5) + sub + "</li>"
          : html + `<li>${sub}</li>`;
        i = next;
        continue;
      }
      if (r.tag !== tag) break;
      html += `<li>${inline(r.text)}</li>`;
      i++;
    }
    return [html + `</${tag}>`, i];
  }

  function md(src) {
    const lines = esc(String(src || "")).replace(/\r/g, "").split("\n");
    const out = [];
    let rows = [], para = [], code = null, tbl = [], quote = [];

    const flushPara = () => {
      if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para = []; }
    };
    const flushList = () => {
      let i = 0;
      while (i < rows.length) { const [html, next] = renderList(rows, i); out.push(html); i = next; }
      rows = [];
    };
    const flushTable = () => {
      if (tbl.length) { out.push(renderTable(tbl)); tbl = []; }
    };
    // Blockquotes were the one block syntax with no case here, so `> line`
    // fell through to a paragraph and printed its own marker — and a TWO-line
    // quote printed a second `>` in the middle of the sentence, because
    // flushPara joins its lines with a space. The model reaches for one
    // whenever it wants to set a conclusion apart, so this was raw markdown
    // in the middle of finished prose.
    const flushQuote = () => {
      if (quote.length) {
        out.push(`<blockquote>${inline(quote.join(" "))}</blockquote>`);
        quote = [];
      }
    };
    const pushRow = (tag, indent, text) => { flushPara(); rows.push({ tag, indent, text }); };

    for (const raw of lines) {
      const line = raw.trimEnd();
      const t = line.trim();

      if (code !== null) {
        if (t.startsWith("```")) { out.push(`<pre><code>${code.join("\n")}</code></pre>`); code = null; }
        else code.push(line);
        continue;
      }
      if (t.startsWith("```")) { flushPara(); flushList(); flushTable(); code = []; continue; }
      if (!t) { flushPara(); flushList(); flushTable(); flushQuote(); continue; }

      // pipe table — buffered whole, because the alignment row decides the shape
      if (TABLE_ROW(t)) { flushPara(); flushList(); tbl.push(t); continue; }
      flushTable();   // any other line closes an open table

      // `&gt;`, not `>`: md() escapes the whole source on line one, so by the
      // time the block loop sees a quote its marker is already entity-encoded.
      // Matching the bare character here silently never fired.
      const bq = t.match(/^&gt;\s?(.*)$/);
      if (bq) { flushPara(); flushList(); quote.push(bq[1]); continue; }
      flushQuote();   // and any other line closes an open quote

      const h = t.match(/^(#{1,3})\s+(.*)$/);
      if (h) { flushPara(); flushList(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); continue; }
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) { flushPara(); flushList(); out.push("<hr/>"); continue; }

      const indent = (raw.match(/^\s*/) || [""])[0].replace(/\t/g, "  ").length;
      const ul = t.match(/^[-*•]\s+(.*)$/);
      if (ul) { pushRow("ul", indent, ul[1]); continue; }
      const ol = t.match(/^\d+[.)]\s+(.*)$/);
      if (ol) { pushRow("ol", indent, ol[1]); continue; }

      // an indented non-bullet line continues the open list item
      if (rows.length && indent >= 2) { rows[rows.length - 1].text += " " + t; continue; }

      flushList();
      para.push(t);
    }
    if (code !== null) out.push(`<pre><code>${code.join("\n")}</code></pre>`);
    flushPara(); flushList(); flushTable(); flushQuote();
    return out.join("");
  }

  window.__chartoMd = md;   // sandbox debug hook, same spirit as window.__charto

  // ── thread ────────────────────────────────────────────
  function clearEmpty() {
    const e = msgsEl.querySelector(".chat-empty");
    if (e) e.remove();
  }

  /* ── copy ────────────────────────────────────────────────────────────────
   * A prompt and a reply are both worth lifting out of the thread, so both
   * carry the same control — same glyph, same tick, same place under the
   * turn. The reply's copy has always been there; the prompt's had not, and
   * re-typing what you asked in order to ask it elsewhere is the kind of
   * friction that makes a thread feel read-only.
   *
   * The Clipboard API needs a secure context. 127.0.0.1 is one, a phone
   * hitting the dev server on the LAN over plain http is NOT — so the old
   * textarea path stays as a fallback rather than the button doing nothing
   * on exactly the device where retyping is worst.
   */
  async function toClipboard(text) {
    if (!text) return false;
    try { await navigator.clipboard.writeText(text); return true; }
    catch { /* blocked or insecure context — fall through */ }
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;pointer-events:none";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    } catch { return false; }
  }

  /** A footnote-row control: one glyph, a title, a click. The geometry lives
   *  in `.turn-meta .act` so a prompt's Retry and a reply's Copy can never
   *  end up two different sizes. */
  function actBtn(icon, label, onClick) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "act";
    b.title = label;
    b.setAttribute("aria-label", label);
    b.innerHTML = Icons.svg(icon, "xs");
    b.addEventListener("click", onClick);
    return b;
  }

  /** The copy button used by both kinds of turn. `get` is called at CLICK
   *  time so a streaming reply copies what it finally said. */
  function copyBtn(get, label) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "act copy";
    b.title = label;
    b.setAttribute("aria-label", label);
    b.innerHTML = Icons.svg("copy", "xs");
    b.addEventListener("click", async () => {
      if (!(await toClipboard(typeof get === "function" ? get() : get))) return;
      b.innerHTML = Icons.svg("check", "xs");
      b.title = "Copied";
      b.classList.add("done");
      setTimeout(() => {
        b.innerHTML = Icons.svg("copy", "xs");
        b.title = label;
        b.classList.remove("done");
      }, 1200);
    });
    return b;
  }

  /* When a prompt was sent. A timestamp on every message is noise in a thread
   * you are reading, so it lives in the same hover-revealed row as the
   * actions — "when did I ask this?" only has to be one hover away.
   *
   * The DAY, not the clock: this is Pivot's format ("06 Aug"), and it is the
   * right one. Inside a conversation you are reading top to bottom, the
   * minute a question was typed answers nothing; which day it was is the
   * only part you might have lost track of. The full moment stays on the
   * title, one hover deeper, for the rare time it matters. */
  function fmtWhen(ts) {
    try {
      return new Date(ts).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
    } catch { return ""; }
  }

  /** The prompt's footnote row, exactly as Pivot draws it: when it was sent,
   *  a Retry that asks the same thing again, and a copy of what you typed.
   *  Nothing to show for a legacy turn that has neither.
   *
   *  Retry SENDS rather than refilling the composer — re-asking is one click
   *  in Pivot and has to be one here, and it appends a fresh turn rather than
   *  rewriting the old one, so the thread stays a record of what was asked. */
  function userMeta(text, ts) {
    if (!ts && !text) return null;
    const meta = document.createElement("div");
    meta.className = "turn-meta";
    if (ts) {
      const when = document.createElement("span");
      when.className = "when";
      when.textContent = fmtWhen(ts);
      when.title = new Date(ts).toLocaleString();   // the exact moment, in full
      meta.appendChild(when);
    }
    if (text) {
      meta.appendChild(actBtn("rotateCw", "Retry", () => send(text)));
      meta.appendChild(copyBtn(text, "Copy prompt"));
    }
    return meta;
  }

  function addUserTurn(text, image, drawing, ts, journal) {
    clearEmpty();
    const turn = document.createElement("div");
    turn.className = "turn user";
    const b = document.createElement("div");
    b.className = "bubble";
    if (drawing) {
      // the tag stays visible in the thread: scrolling back must show WHICH
      // shape a past answer was about, not just that one was tagged
      const tg = document.createElement("div");
      tg.className = "bubble-tag";
      tg.innerHTML = drawTagInner(drawing, false);
      b.appendChild(tg);
    }
    if (journal) {
      const tg = document.createElement("div"); tg.className = "bubble-tag";
      tg.innerHTML = journalTagInner(journal, false); b.appendChild(tg);
    }
    if (image) {
      const img = document.createElement("img");
      img.className = "shot";
      img.src = image;
      b.appendChild(img);
    }
    if (text) {
      const t = document.createElement("div");
      t.textContent = text;
      b.appendChild(t);
    }
    turn.appendChild(b);
    const meta = userMeta(text, ts);
    if (meta) turn.appendChild(meta);
    msgsEl.appendChild(turn);
    toBottom();
    return turn;
  }

  /* ── the wait ────────────────────────────────────────────────────────────
   *
   * Pivot's waiting indicator, in Charto's words. A breathing dot labelled
   * "Thinking…" said only that something was happening — and on a turn that
   * reads six tools before it writes a word, "something" is the least useful
   * thing it could say. This is the shape Pivot settled on instead: a
   * timeline that GROWS, one plain word per step, so the pane accumulates a
   * record of the work rather than repainting a spinner.
   *
   * The words are Charto's, because the work is Charto's: Pivot queries
   * fundamentals and searches news, this reads a chart. Two sources feed the
   * list and they are different in kind —
   *
   *   · the SCRIPT below is representative. It walks forward on a slow
   *     cadence and holds on its last line rather than looping, because a
   *     list that cycles is a spinner with extra steps.
   *   · a real tool call overrides it with what actually happened. The
   *     server emits `tool` the moment one lands, so genuine work shows
   *     through the moment there is genuine work to show.
   *
   * Nothing here claims a tool ran that did not. A scripted line names a
   * KIND of work ("Reading the chart") and never a number, a level or a
   * result — the same rule the replies themselves are held to.
   */
  const STEP_SCRIPT = [
    { word: "Thinking", detail: "" },
    { word: "Reading", detail: "the chart" },
    { word: "Scanning", detail: "price history" },
    { word: "Measuring", detail: "levels" },
    { word: "Checking", detail: "indicators" },
    { word: "Weighing", detail: "the evidence" },
  ];

  /** A lead word + its supporting words for a tool that actually ran. Keyed
   *  off the dataserver's own tool names (data/dataserver.py, TOOLS). */
  function toolStep(name) {
    const n = String(name || "").toLowerCase();
    if (/open_chart/.test(n)) return { word: "Opening", detail: "the chart" };
    if (/draw_shape/.test(n)) return { word: "Drawing", detail: "on the chart" };
    if (/trendline/.test(n)) return { word: "Fitting", detail: "trendlines" };
    if (/pattern/.test(n)) return { word: "Scanning", detail: "patterns" };
    if (/diverg/.test(n)) return { word: "Comparing", detail: "momentum" };
    if (/gap/.test(n)) return { word: "Finding", detail: "gaps" };
    if (/level|anchor/.test(n)) return { word: "Measuring", detail: "levels" };
    if (/volume_profile/.test(n)) return { word: "Building", detail: "the volume profile" };
    if (/evaluate/.test(n)) return { word: "Testing", detail: "the idea" };
    if (/plan_position/.test(n)) return { word: "Planning", detail: "the trade" };
    if (/indicator/.test(n)) return { word: "Computing", detail: "indicators" };
    if (/bars/.test(n)) return { word: "Reading", detail: "price history" };
    if (/news|explain_move/.test(n)) return { word: "Searching", detail: "news" };
    if (/flow|deal/.test(n)) return { word: "Reading", detail: "the flows" };
    if (/results/.test(n)) return { word: "Reading", detail: "the results" };
    if (/screen/.test(n)) return { word: "Screening", detail: "the market" };
    if (/compare|peer/.test(n)) return { word: "Comparing", detail: "peers" };
    return { word: "Working", detail: "" };
  }

  /** Mount the indicator into `host` and return its three controls. Owns two
   *  intervals, so `stop()` is not optional — a turn that finished with them
   *  still running would tick a counter on an answer that already landed. */
  function createWait(host) {
    const t0 = performance.now();
    host.innerHTML = '<div class="wait-head">'
      + '<span class="wait-ticker" aria-hidden="true">'
      + '<span class="wait-bar"></span><span class="wait-bar"></span><span class="wait-bar"></span>'
      + '</span><span class="wait-secs" aria-live="off">0s</span></div>'
      + '<div class="wait-steps"></div>';
    const rows = host.querySelector(".wait-steps");
    const secs = host.querySelector(".wait-secs");
    const seen = new Set();
    let last = null, cursor = 1;

    function push(step) {
      // never repeat the line already at the bottom — a second "Reading price
      // history" is a row that says nothing the row above it did not
      if (last && last.word === step.word && last.detail === step.detail) return;
      const prev = rows.lastElementChild;
      if (prev) prev.querySelector(".chat-step-label").classList.remove("shimmer");
      const row = document.createElement("div");
      row.className = "chat-step";
      row.innerHTML = '<span class="chat-step-dot" aria-hidden="true"></span>'
        + '<span class="chat-step-label shimmer">'
        + `<span class="chat-step-word">${esc(step.word)}</span>`
        + (step.detail ? ` ${esc(step.detail)}` : "") + "</span>";
      rows.appendChild(row);
      last = step;
      if (atBottom()) toBottom();
    }
    push(STEP_SCRIPT[0]);

    // The two intervals are started and stopped together, and more than once:
    // the wait stands down while the answer is streaming and comes back if
    // the turn goes back to work. Ticking a counter behind a hidden element
    // is a timer nobody reads.
    let clock = 0, walk = 0;
    function run() {
      if (clock) return;
      clock = setInterval(() => {
        secs.textContent = Math.max(0, Math.round((performance.now() - t0) / 1000)) + "s";
      }, 250);
      walk = setInterval(() => {
        if (cursor >= STEP_SCRIPT.length) return;   // hold on the last line
        push(STEP_SCRIPT[cursor++]);
      }, 2600);
    }
    function halt() {
      clearInterval(clock); clearInterval(walk);
      clock = walk = 0;
    }
    run();

    return {
      /** A tool landed. Once per tool: a turn that reads three intervals of
       *  bars did one kind of work, not three. A tool AFTER the answer began
       *  means the turn narrated and then went back to work, so the wait
       *  comes back with it. */
      tool(name) {
        if (seen.has(name)) return;
        seen.add(name);
        // Cancel a close in flight as well as reopening a finished one, or a
        // wait that came back would be hidden 200ms later by the old timer.
        if (host.dataset.closing) {
          delete host.dataset.closing;
          host.classList.remove("wait-out");
          host.style.maxHeight = "";
        }
        if (host.hidden) { host.hidden = false; run(); }
        push(toolStep(name));
      },
      /** The answer is arriving. Two live animations at once read as two
       *  things happening at once, so the wait steps aside the moment there
       *  is text — the caret in the prose is the only thing still moving.
       *  Idempotent: every delta calls it, only the first one does work. */
      writing() {
        if (host.hidden || host.dataset.closing) return;
        halt();
        // WRAP UP, don't vanish. `hidden` alone took the block out of flow in
        // one frame, so the answer's first line jumped up by the height of
        // however many step rows had accumulated — a jolt landing at the exact
        // moment the reader's eye goes looking for the first word. The steps
        // fade and the box collapses into the space the text is about to take,
        // which reads as a handover instead of a disappearance.
        host.dataset.closing = "1";
        host.style.maxHeight = host.scrollHeight + "px";
        requestAnimationFrame(() => host.classList.add("wait-out"));
        setTimeout(() => {
          // `tool()` can bring the wait BACK — a turn that narrated and then
          // went to work again. If that happened while this was closing, the
          // close must not fire on the newly reopened box.
          if (host.dataset.closing !== "1") return;
          host.hidden = true;
          host.classList.remove("wait-out");
          host.style.maxHeight = "";
          delete host.dataset.closing;
        }, 200);
      },
      stop() { halt(); host.remove(); },
    };
  }

  /** An assistant turn. `live` is false when repainting a finished thread —
   *  the wait belongs to a turn that is being waited FOR, and starting its
   *  intervals only to stop them a line later is work for nobody. */
  function addAssistantTurn(live = true) {
    clearEmpty();
    const turn = document.createElement("div");
    turn.className = "turn assistant";
    turn.innerHTML = (live ? '<div class="wait"></div>' : "") + '<div class="prose"></div>';
    if (live) turn.__wait = createWait(turn.querySelector(".wait"));
    msgsEl.appendChild(turn);
    toBottom();
    return turn;
  }

  /** Take the wait down. Idempotent — both the success and the failure path
   *  call it, and a turn that never had one is not an error. */
  function endWait(turn) {
    if (!turn.__wait) return;
    turn.__wait.stop();
    turn.__wait = null;
  }

  /** Consume the SSE turn, painting the answer as it arrives.
   *
   *  Markdown is re-rendered on every flush rather than appended as text: a
   *  half-written table or list is still valid markdown, and re-rendering the
   *  whole buffer is cheap next to the model's own pace. Flushing is rAF-gated
   *  so a fast stream cannot re-parse the buffer hundreds of times a second.
   */
  function readStream(res, turn, sink) {
    // Resolves the moment the ANSWER's `done` lands, and keeps draining after
    // it: the three follow-ups ride the tail of this same stream (see
    // _suggest_events in dataserver.py), and the caller must not wait on them
    // to file the turn, apply the scene patch or move the workspace. So the
    // promise is settled from inside the loop rather than by returning.
    let settle, fail, settled = false, gotSuggest = false;
    const answered = new Promise((ok, no) => { settle = ok; fail = no; });
    (async () => {
    const prose = turn.querySelector(".prose");
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";        // raw SSE bytes not yet split into events
    let text = "";       // the answer so far
    let done = null;
    const tools = [];
    let raf = 0;

    // A trailing `**` or `` ` `` is an emphasis marker whose partner has not
    // arrived yet. Rendering it raw makes every bolded number flash its
    // asterisks mid-stream, so the dangling opener is held back for the one
    // frame it takes to complete. Only ever applied to the live buffer — the
    // finished text is rendered untouched.
    const tidy = (s) => {
      if ((s.match(/\*\*/g) || []).length % 2) s = s.replace(/\*\*(?![\s\S]*\*\*)/, "");
      if ((s.match(/`/g) || []).length % 2) s = s.replace(/`(?![\s\S]*`)/, "");
      return s.replace(/(^|\s)\*(\S*)$/, "$1$2");
    };

    /* ── how this paints, and why it changed ──────────────────────────────
     *
     * It used to be one line: `prose.innerHTML = md(text)` on every rAF. Two
     * costs compound there, and both grow with the reply.
     *
     * The parse is O(text), so frame 400 of a long answer re-parses four
     * hundred lines to add one word — the render gets slower exactly as the
     * reply gets longer, which is why the jank always arrived at the END.
     * Worse, replacing innerHTML DESTROYS and rebuilds every node under it:
     * full relayout of the whole reply, a fresh paint, any text selection
     * inside it dropped, and a panel mounted in the prose torn out sixty
     * times a second.
     *
     * Two changes, which are what production streaming UIs do:
     *
     * 1 · INCREMENTAL RENDER. Markdown is a block language, so everything
     *     before the last blank line can never change again. That prefix is
     *     parsed ONCE into `.md-stable` and then left alone; only the trailing
     *     block re-renders. Per-frame work stops growing with the answer.
     *     The one thing this cannot do is carry block context across the
     *     split — a list broken at a blank line becomes two lists — which
     *     costs nothing, because `done` re-renders the whole text exactly
     *     (finishTurn). Stream approximately, finish precisely.
     *
     * 2 · A SMOOTHING BUFFER. Azure does not deliver evenly: it arrives in
     *     bursts of a few hundred characters separated by dead air, so the
     *     text used to LURCH — a paragraph at once, a pause, another lurch.
     *     `text` is now what has ARRIVED and `shown` is what has been
     *     revealed, and the gap drains at a rate set by its own size (about
     *     six frames' worth, ~100ms) rather than all at once. Bursts become a
     *     steady crawl; the buffer can never fall behind, because a bigger
     *     backlog drains proportionally faster. This is the same
     *     decouple-the-network-from-the-eye trick as the AI SDK's
     *     `smoothStream`.
     */
    let shown = "";          // revealed to the reader
    let stableLen = 0;       // how much of it is already IN .md-stable
    let stableMarks = 0;     // panel markers consumed by that prefix
    prose.innerHTML = '<div class="md-stable"></div><div class="md-tail"></div>';
    const stableEl = prose.querySelector(".md-stable");
    const tailEl = prose.querySelector(".md-tail");

    /** Where the finished prefix ends: the last blank line, unless a code
     *  fence is open — inside one, nothing is settled yet. */
    const splitAt = (s) => {
      if ((s.match(/^```/gm) || []).length % 2) return -1;
      return s.lastIndexOf("\n\n");
    };

    const render = () => {
      const wasAtBottom = atBottom();
      const cut = splitAt(shown);
      if (cut > stableLen) {
        // APPEND the newly-settled blocks; never rebuild what is already
        // there. Re-rendering the whole stable prefix each time it grew was
        // measurably wrong, not just wasteful: every settled block's DOM was
        // destroyed and recreated, and a panel mounted inside one went with
        // it. Instrumented on a single turn, one panel was torn out and
        // re-inserted THIRTEEN times — thirteen fades, and thirteen chances
        // to lose whatever state the panel was holding.
        const chunk = shown.slice(stableLen, cut);
        stableEl.insertAdjacentHTML("beforeend", proseHtml(chunk, stableMarks));
        stableMarks += countPanelMarks(chunk);
        stableLen = cut;
        fillCardSlots(turn);          // only the frame a slot can appear in
      }
      tailEl.innerHTML = proseHtml(tidy(shown.slice(stableLen)), stableMarks)
        + '<span class="caret"></span>';
      if (wasAtBottom) toBottom();
    };

    // The pump runs only while there is a backlog, and stops itself when the
    // reader has caught up — an idle turn costs no frames at all.
    const tick = () => {
      raf = 0;
      const backlog = text.length - shown.length;
      if (backlog <= 0) return;
      // Reveal proportionally: ~1/6th of whatever is waiting, so a burst
      // drains in about a tenth of a second and a trickle stays a trickle.
      // The floor of 2 keeps the slowest case moving; the ceiling stops a
      // paste-sized chunk from landing as one frame of lurch.
      const step = Math.max(2, Math.min(Math.ceil(backlog / 6), 90));
      shown = text.slice(0, shown.length + step);
      render();
      pump();
    };
    const pump = () => { if (!raf) raf = requestAnimationFrame(tick); };
    /** Everything, now — for `done`, where waiting would only add lag. */
    const paintAll = () => {
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      shown = text;
      render();
    };
    const paint = pump;

    for (;;) {
      const { value, done: fin } = await reader.read();
      if (fin) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop() || "";                 // keep the trailing partial event
      for (const p of parts) {
        const line = p.split("\n").find((x) => x.startsWith("data:"));
        if (!line) continue;
        let ev;
        try { ev = JSON.parse(line.slice(5)); } catch { continue; }
        if (ev.type === "delta") {
          // every delta, not just the first: a round that narrated before
          // calling a tool brought the wait back, and the round after it has
          // to send it away again
          if (turn.__wait) turn.__wait.writing();
          text += ev.text;
          turn.__streamText = text;
          paint();
        } else if (ev.type === "tool") {
          tools.push(ev.name);
          // a landed tool is the only progress signal a multi-round turn has —
          // it becomes its own line on the wait's timeline
          if (turn.__wait) turn.__wait.tool(ev.name);
        } else if (ev.type === "card") {
          // A tool's result panel, in as soon as it exists. Guarded because a
          // card that fails to render must cost nothing: the answer behind it
          // is still on its way.
          try {
            addCard(turn, ev.card);
            // Keep the OBJECT, not just the element. `done` carries the same
            // cards again as freshly parsed JSON, and filing those would put a
            // different object in the transcript from the one on screen — so
            // anything a panel writes onto itself later (a backtest run from a
            // draft card) would mutate a record nobody saves.
            (turn.__cardRecs || (turn.__cardRecs = [])).push(ev.card);
          } catch (e) { console.warn("[charto] card failed", e); }
        } else if (ev.type === "done") {
          // Show whatever the smoothing buffer was still holding, then stop.
          // Without the cancel, a queued frame lands AFTER finishTurn has
          // written the final markdown and puts the caret back on a reply
          // that is already complete.
          paintAll();
          if (raf) { cancelAnimationFrame(raf); raf = 0; }
          // the streamed text is the source of truth; `done.text` is the same string
          done = ev;
          done.text = done.text || text;
          settled = true;
          settle(done);          // the turn is answered; the tail is follow-ups
        } else if (ev.type === "suggest_delta") {
          if (sink) sink.delta(ev.text);
        } else if (ev.type === "suggest_done") {
          gotSuggest = true;
          if (sink) sink.done(ev.suggestions);
        }
      }
    }
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    if (!settled) throw new Error("stream ended without a result");
    // Ended before the follow-ups did — the row must not sit there half-written
    if (sink && !gotSuggest) sink.drop();
    })().catch((e) => {
      // Before the answer, the caller owns the failure. After it, the turn is
      // already on screen and a dead tail costs only the suggestions.
      if (!settled) fail(e);
      else if (sink) sink.drop();
    });
    return answered;
  }

  // ── company logos ────────────────────────────────────────
  // A reply's table rows are per-company, so the company's mark belongs next
  // to its NAME — the ticker column stays plain text. Nothing is linked: the
  // page is reached from the search dropdown. This runs on the FINISHED turn
  // over text nodes, never over the markdown string: a regex on HTML would
  // eventually match inside a tag it wrote.
  let SYMS = null, TO_SYM = null, SYM_RE = null, LOGOS = {}, NAME_KEYS = null;
  // one universe cache for the whole app — the pill, the legends, the pane
  // pickers and this marker all read the same payload
  Universe.load().then((d) => {
    SYMS = new Set(d.symbols || []);
    LOGOS = d.logos || {};
    TO_SYM = new Map(SYMS.size ? [...SYMS].map((s) => [s, s]) : []);
    // A reply names companies either way — "TCS" or "Caplin Labs". Only the
    // NAME spellings carry a logo; a bare ticker is left exactly as written.
    NAME_KEYS = new Set();
    // `alias` carries the plain asset name behind a venue-qualified listing
    // ("Bitcoin" for "Bitcoin / USDT (Bybit)") — the spelling a reply actually
    // uses. Last in the list so a real company name always wins a collision.
    for (const src of [d.names || {}, d.long || {}, d.alias || {}]) {
      for (const [sym, name] of Object.entries(src)) {
        if (name && name.length > 3 && !TO_SYM.has(name)) {
          TO_SYM.set(name, sym);
          NAME_KEYS.add(name);
        }
      }
    }
    const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const alts = [...TO_SYM.keys()].sort((a, b) => b.length - a.length).map(esc);
    SYM_RE = new RegExp(`(?<![\\w&-])(${alts.join("|")})(?![\\w&-])`, "g");
    // a restored session renders before this fetch lands — mark it now that
    // the map exists, or a reopened conversation would lose every logo
    document.querySelectorAll(".prose").forEach((p) => {
      try { linkCompanies(p); } catch (e) { console.warn("[charto] mark failed", e); }
    });
  }).catch((e) => { console.warn("[charto] symbols fetch failed", e); SYMS = new Set(); });

  /** Put each company's logo before its NAME — in TABLES only. Prose names the
   *  same company several times a paragraph, and a mark on each one is
   *  decoration; a table row is already a per-company row. Tickers and
   *  companies with no known logo are left untouched. */
  function linkCompanies(root) {
    if (!SYM_RE || !TO_SYM || !TO_SYM.size) return;
    const jobs = [];
    for (const table of root.querySelectorAll("table")) {
      const walk = document.createTreeWalker(table, NodeFilter.SHOW_TEXT, {
        acceptNode: (n) => n.parentElement.closest("a, code, pre")
          ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
      });
      for (let n = walk.nextNode(); n; n = walk.nextNode()) {
        if (SYM_RE.test(n.nodeValue)) jobs.push(n);
        SYM_RE.lastIndex = 0;
      }
    }
    for (const node of jobs) {
      const frag = document.createDocumentFragment();
      let last = 0, m;
      SYM_RE.lastIndex = 0;
      while ((m = SYM_RE.exec(node.nodeValue))) {
        const sym = TO_SYM.get(m[0]);
        // a ticker is not a name, and a company with no stored logo has
        // nothing to add — both are left as the model wrote them
        if (!sym || !NAME_KEYS.has(m[0]) || !LOGOS[sym]) continue;
        if (m.index > last) {
          frag.appendChild(document.createTextNode(
            node.nodeValue.slice(last, m.index)));
        }
        const img = document.createElement("img");
        img.className = "co-logo";
        img.src = LOGOS[sym];
        img.alt = "";
        img.loading = "lazy";
        img.onerror = () => img.remove();   // a dead logo must not leave a box
        frag.appendChild(img);
        frag.appendChild(document.createTextNode(m[0]));
        last = m.index + m[0].length;
      }
      if (!frag.childNodes.length) continue;
      if (last < node.nodeValue.length) {
        frag.appendChild(document.createTextNode(node.nodeValue.slice(last)));
      }
      node.parentNode.replaceChild(frag, node);
    }
  }


  /* ── what this turn put on the chart ─────────────────────────────────────
   *
   * The footer used to read "computed via get_indicator" — a backend
   * function name, and a misleading count: one get_indicator call can plot
   * three moving averages, and it read as a single thing.
   *
   * The scene patch is already the honest ledger, because it is literally
   * the list of objects that landed on the chart. One entry per VISUAL
   * thing: three indicators are three, a level and a zone are two, a
   * volume profile is one. Names are the ones the chart itself shows, so
   * nothing here leaks a tool id.
   */
  const SHAPE_WORD = {
    segment: "Trendline", zone: "Zone", box: "Box", vline: "Vertical line",
    point: "Point", poly: "Shape", fib: "Fib retracement",
    position: "Trade plan", markers: "Markers", vprofile: "Volume profile",
    level: "Level",
  };

  function chartActions(patch) {
    const out = [];
    const items = (patch || []).filter((a) => a && a.kind);
    // A clear that is FOLLOWED by drawings is draw_mode:"replace" doing its
    // bookkeeping, not something the reader saw happen — counting it made
    // three levels and a profile read as eight actions. A clear on its own
    // is a real removal and does count.
    const onlyClears = items.every(
      (a) => a.kind === "clear" || a.kind === "clear_levels");
    for (const a of items) {
      if (a.kind === "clear" || a.kind === "clear_levels") {
        if (onlyClears) out.push("Cleared the chart");
        continue;
      }
      if (a.kind === "indicator_remove") {
        out.push(`Removed ${String(a.name || "an indicator").toUpperCase()}`);
        continue;
      }
      if (a.kind === "indicator") {
        const nm = String(a.name || "").split("@")[0].toUpperCase();
        out.push(`${nm}${a.period ? " " + a.period : ""}`.trim() || "Indicator");
        continue;
      }
      // PARKED IS NOT ON THE CHART. A mark that arrives hidden went to the
      // Layers panel, not to the price scale — get_levels draws two a side
      // and parks the rest. Counting them here made the footer read "4 on
      // chart" beside two visible bands, which is the same chart/text
      // divergence the reply itself was just taught to avoid. The Layers
      // panel is where the parked ones are counted, and it counts them right.
      if (a.hidden) continue;
      // A catalogued tool names itself off the rail's own label, so the
      // footer says "Gann fan" rather than the generic word — and a tool
      // added to the catalogue tomorrow is named here without a second entry.
      const word = (a.kind === "drawing" && Tools.SPECS[a.tool]
                    && Tools.SPECS[a.tool].label)
        || SHAPE_WORD[a.kind] || "Drawing";
      // a level's own label is the price, which is more use than the word
      out.push(a.label ? `${word} · ${a.label}` : word);
    }
    return out;
  }

  /* Provenance the footer no longer carries. The token count was a number
   * about the BILL, not about the answer — it sat in the same row as the
   * latency and the chart actions, both of which say something about what
   * you just read, and it never did. New turns stop recording it (see
   * send()); a conversation saved before that still has the string in its
   * meta, so it is dropped on the way to the screen rather than left to
   * reappear on every reload of an old thread. */
  const TOKENS_RE = /^[\d,]+\s*in\s*\/\s*[\d,]+\s*out$/i;

  /* ── the panels a tool prints ────────────────────────────────────────────
   *
   * A card sits ABOVE the prose that reads it, because that is the order the
   * turn happened in: the scan ran, then the model said what it found. The
   * live path gets each card the moment its tool lands (an SSE `card` event),
   * so the panel is already in place before the first word arrives rather
   * than shoving a half-read paragraph down the pane at the end.
   *
   * cards.js owns what one looks like; this owns only where it goes. A kind
   * this build cannot render returns null and nothing is inserted — the reply
   * is unaffected, which is the whole point of keeping them separable.
   */
  /* A panel is BUILT here and mounted later — nothing appears on mounting it
   * twice, and where it goes is not known yet.
   *
   * It used to mount immediately, above the prose, on the theory that a tool
   * which took nine seconds should show its answer the moment it lands.
   * Measured end to end, that theory cost more than it bought. A real turn:
   *
   *     0.0s  thinking animation starts
   *     6.9s  PANEL lands above the prose — while the animation still runs
   *    12.2s  first text, under the panel; the animation finally goes
   *    12.6s  panel JUMPS down into the middle of the text
   *
   * Two bad things and one worse one. The panel sat beside a live "thinking"
   * indicator for five seconds, which says two contradictory things at once.
   * Then text arrived UNDER a panel it was supposed to introduce. Then the
   * whole block reflowed as the panel relocated — the "sudden pop".
   *
   * Held instead, the panel is mounted once, in its final place. The wait for
   * it is not five seconds: the model writes its marker right after the
   * opening sentence, and measured on the same turn the slot appeared 0.4s
   * after the first character. */
  function addCard(turn, card) {
    if (typeof Cards === "undefined") return;
    const box = Cards.render(card);
    if (!box) return;
    (turn.__cardEls || (turn.__cardEls = [])).push(box);
  }

  /** Put a panel on screen, once, wherever it belongs. `fade` is the only
   *  animation: a panel is 300px of measurements arriving in one frame, and
   *  without it the reply visibly lurches. */
  function mountCard(box, parent, beforeNode, animate = true) {
    const wasAtBottom = atBottom();
    // `animate` is false for the final exact re-render, which wipes the prose
    // and re-mounts every panel into fresh slots. The panel is not ARRIVING
    // there — it is already on screen and has been for seconds — so fading it
    // in again is a flicker on a finished reply.
    if (animate) box.classList.add("card-in");
    if (beforeNode) parent.insertBefore(box, beforeNode);
    else parent.appendChild(box);
    if (animate) requestAnimationFrame(() => box.classList.remove("card-in"));
    if (wasAtBottom) toBottom();
  }

  /** Panels the reply never claimed with a marker go where they always went:
   *  above the prose. Called at the END of the turn, not during it, so an
   *  unplaced panel never appears next to a running thinking animation. */
  function mountUnplacedCards(turn) {
    const prose = turn.querySelector(".prose");
    for (const box of turn.__cardEls || []) {
      if (!box.isConnected) mountCard(box, turn, prose);
    }
  }

  /* ── panels inside the prose ──────────────────────────────────────────────
   *
   * The model writes `[[panel]]` on its own line where a panel belongs. That
   * marker survives md() untouched (it escapes < > & and nothing else), so it
   * is swapped for an empty slot AFTER the markdown is built rather than
   * before — a regex over the source would have to know what is inside a code
   * fence, and a regex over finished HTML that rewrote text would eventually
   * match inside a tag it had just written.
   *
   * Nth marker = Nth panel, in the order the tools produced them. A marker
   * with no panel behind it renders as nothing at all rather than as a gap:
   * the model is writing this from a note in a tool result, and a stray one
   * must never leave a hole in the reply.
   */
  const PANEL_RE = /\[\[panel(?::(\d+))?\]\]/gi;
  const stripPanelMarks = (s) => String(s || "").replace(PANEL_RE, "").trim();

  /** md(), then the markers turned into slots. One place, so the streaming
   *  render and the final render cannot disagree about where a panel goes. */
  const countPanelMarks = (s) => (String(s || "").match(PANEL_RE) || []).length;

  function proseHtml(src, base) {
    // `base` is how many markers came BEFORE this fragment. The streaming
    // render parses each newly-settled chunk on its own (see render()), so
    // without a running offset the second chunk's first marker would claim
    // panel 0 all over again.
    let n = (base | 0) - 1;
    const withIds = String(src || "").replace(PANEL_RE, (_m, explicit) => {
      n += 1;
      const i = explicit != null ? Number(explicit) - 1 : n;
      return `⟦PANELSLOT${i}⟧`;
    });
    return md(withIds).replace(
      /<p>\s*⟦PANELSLOT(\d+)⟧\s*<\/p>|⟦PANELSLOT(\d+)⟧/g,
      (_m, a, b) => `<div class="card-slot" data-slot="${a ?? b}"></div>`);
  }

  /** Move each landed panel into its slot. Called after every render that can
   *  have produced one; moving a node that is already in the right slot is
   *  skipped, so this is a no-op on every frame but the one that matters. */
  function fillCardSlots(turn, animate = true) {
    const els = turn.__cardEls || [];
    for (const slot of turn.querySelectorAll(".card-slot")) {
      // A slot still inside the tail is re-rendered every frame, and mounting
      // there would tear the panel out and put it back sixty times a second.
      // It waits the few frames until the line it sits on is finished.
      if (slot.closest(".md-tail")) continue;
      const el = els[Number(slot.dataset.slot)];
      if (!el || slot.contains(el)) continue;
      mountCard(el, slot, null, animate);
    }
  }

  /** Fill an assistant turn with the final answer + its provenance footer. */
  function finishTurn(turn, text, bits, acts, cards) {
    endWait(turn);
    // Only on a repaint: a live turn already has its panels, put there by the
    // stream. Painting them again here would double every card in the thread.
    // Registration, not mounting — and guarded on the REGISTER, not on the
    // DOM: a live turn has held its panels without putting any of them on
    // screen, so `querySelector('.scan')` is null there and this would build
    // every panel a second time.
    if (cards && !(turn.__cardEls && turn.__cardEls.length)) {
      for (const c of cards) addCard(turn, c);
    }
    const prose = turn.querySelector(".prose");
    // The exact render, over the whole text: the streaming pass splits blocks
    // at blank lines to stay cheap, and this is where that approximation is
    // paid off. Same builder as the stream, so a panel cannot land in one
    // place while writing and another once finished.
    prose.innerHTML = proseHtml(text, 0);
    // A panel already on screen is re-seated silently; only one arriving for
    // the first time here (a repainted thread, or a reply with no marker)
    // gets the fade.
    fillCardSlots(turn, !turn.__streamText);
    mountUnplacedCards(turn);
    linkCompanies(prose);
    const meta = document.createElement("div");
    meta.className = "turn-meta";
    // The marker is placement, not prose — nobody wants `[[panel]]` in a
    // paste. It is stripped from the copy and from nothing else.
    const copy = copyBtn(stripPanelMarks(text), "Copy reply");
    const label = document.createElement("span");
    label.textContent = bits
      .filter((b) => b && !TOKENS_RE.test(String(b).trim()))
      .join("  ·  ");
    meta.append(copy, label);
    // The chart-actions disclosure rides in the same row, in the same type —
    // it is provenance, the same as the latency and the token count.
    const acts2 = acts || [];
    if (acts2.length) {
      const tog = document.createElement("button");
      tog.className = "acts-toggle";
      tog.innerHTML = `<span>${acts2.length} on chart</span>`
        + Icons.svg("chevronDown", "xs");
      const list = document.createElement("div");
      list.className = "acts-list";
      list.innerHTML = acts2.map((t) =>
        `<span class="act-row">${esc(t)}</span>`).join("");
      tog.addEventListener("click", () => {
        const open = meta.classList.toggle("acts-open");
        tog.setAttribute("aria-expanded", String(open));
      });
      meta.append(tog);
      place(turn, meta);
      place(turn, list);
      toBottom();
      return;
    }
    place(turn, meta);
    toBottom();
  }

  /** Append to `turn`, but always ABOVE the follow-up row.
   *
   *  The follow-ups now ride the answer's own stream rather than a request
   *  made after it, so the row can exist before this footer does. Appending
   *  blindly then put the latency and the copy button UNDER the three
   *  questions. Ordering by insertion point rather than by who happens to
   *  arrive first is what makes that independent of timing. */
  function place(turn, node) {
    const box = turn.querySelector(".suggest");
    if (box) turn.insertBefore(node, box); else turn.appendChild(node);
  }

  /* ── three things worth asking next ───────────────────────────────────
   *
   * They arrive on the TAIL of the answer's own stream, after its `done` — not
   * in a request of their own. So a slow or dead suggest still costs the answer
   * nothing (everything the turn does has already happened by then), and the
   * backend still ends every failure path with an empty list rather than an
   * error: there is no failure here for the client to handle.
   *
   * Only the NEWEST turn carries a row. Leaving them under older replies
   * would offer questions the conversation has already moved past, and stack
   * a control every few hundred pixels down a thread you are trying to read.
   */
  function clearSuggest() {
    msgsEl.querySelectorAll(".suggest").forEach((n) => n.remove());
    // The saved copy goes with the row. An offer is spent the moment
    // something else is asked, and one that outlived its turn in storage
    // would come back on the next reload under a reply nobody is reading.
    let dropped = false;
    for (const t of turns) if (t.sugg) { delete t.sugg; dropped = true; }
    if (dropped) saveTurns();
  }

  /** The row under `turn`, made if it isn't there yet. */
  function suggestBox(turn) {
    let box = turn.querySelector(".suggest");
    if (!box) {
      box = document.createElement("div");
      box.className = "suggest";
      turn.appendChild(box);
    }
    return box;
  }

  /** Fill `box` with `lines`. The ONE place a suggestion becomes a button —
   *  the live stream and a restored thread both come through here, so the two
   *  cannot drift. Rows are reused rather than rebuilt: a list still arriving
   *  a line at a time must not replace the buttons already under the pointer. */
  function paintSuggest(box, lines) {
    lines.slice(0, 3).forEach((q, i) => {
      while (box.children.length <= i) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "suggest-row";
        b.addEventListener("click", () => {
          const asked = b.textContent.trim();
          if (asked) { clearSuggest(); send(asked); }
        });
        box.appendChild(b);
      }
      const b = box.children[i];
      if (b.textContent !== q) { b.textContent = q; b.title = q; }
    });
  }

  /** The server's suggest_clean, verbatim. Both ends apply it — this one to
   *  the half-written line on screen, that one to the final list — and if
   *  they disagreed every suggestion would twitch as the stream settled. */
  const cleanSuggest = (s) => s
    .replace(/^\s*(?:[-*•]|\d+[.)])\s*/, "")
    .trim().replace(/^["']|["']$/g, "").trim();

  /** Where the follow-ups land, fed by the answer's own stream.
   *
   *  This was a second POST to /suggest. It reads the same on screen — the
   *  list still fills in a line at a time — but it is no longer a request:
   *  the events arrive on the tail of /chat, after the answer's `done`. That
   *  removed a hop, and more importantly a ROUTE: a new endpoint is invisible
   *  in production until nginx's allowlist learns it, and /suggest spent its
   *  whole life answering the HTML page on the VM while working perfectly on
   *  localhost. Nothing riding /chat can go missing that way.
   *
   *  The box is made on the first delta, not up front, so a turn whose
   *  follow-ups never arrive never grows an empty row.
   */
  function suggestSink(turn) {
    clearSuggest();
    let box = null, acc = "", picks = null, rec = null;
    const boxOf = () => box || (box = suggestBox(turn));
    // Only the newest turn carries a row. Anything that displaces it — a new
    // question, a cleared thread, a switched conversation — appends past it,
    // and that is the honest test for "this offer is spent".
    const dead = () => !turn.isConnected || turn !== msgsEl.lastElementChild
                    || (box && !box.isConnected);
    // The record the row belongs to is filed by send() a moment after the
    // answer's `done` — the same moment these begin arriving. So the two are
    // joined by whichever lands second rather than by assuming an order.
    const persist = () => {
      if (!rec || !picks || rec.role !== "assistant") return;
      rec.sugg = picks;
      saveTurns();
    };
    return {
      attach(r) { rec = r; persist(); },
      delta(t) {
        if (dead()) return;
        acc += t || "";
        paintSuggest(boxOf(), acc.split("\n").map(cleanSuggest).filter(Boolean));
        toBottom();
      },
      done(list) {
        const three = list || [];
        if (three.length !== 3 || dead()) return this.drop();
        const b = boxOf();
        paintSuggest(b, three);
        while (b.children.length > 3) b.lastChild.remove();
        // File them with the turn. The row is part of the reply, not a
        // decoration on top of it — a reload repaints the thread from `turns`,
        // and unsaved questions would vanish with it.
        picks = three;
        persist();
        toBottom(true);
      },
      drop() { if (box) { box.remove(); box = null; } },
    };
  }

  /* A card that changed itself. `cards.js` owns the panels and this file owns
   * the transcript, so a backtest run from a draft card mutates the record it
   * was rendered from and says so here — the alternative is cards.js reaching
   * into `turns`, which would give two files an opinion about what a thread
   * is. The record is already the same object; this only makes it durable. */
  document.addEventListener("charto:card-updated", () => saveTurns());

  function failTurn(turn, msg) {
    endWait(turn);
    turn.classList.add("error");
    // A panel the stream got as far as inserting goes with the turn. The
    // reply that would have read it never arrived, the drawings it points at
    // were never applied — a scene patch lands only on `done` — and the turn
    // itself is dropped from the record, so leaving the card would strand a
    // scan under an error, pointing at annotations that are not on the chart.
    turn.querySelectorAll(".scan").forEach((n) => n.remove());
    turn.querySelector(".prose").textContent = `Couldn't reach the model — ${msg}`;
    toBottom();
  }

  const atBottom = () =>
    threadEl.scrollHeight - threadEl.scrollTop - threadEl.clientHeight < 120;
  // The thread is styled `scroll-behavior: smooth`, so assigning scrollTop
  // starts an ANIMATION. Auto-follow must not animate: each new chunk of a
  // reply retargets the in-flight scroll and it never lands, leaving the tail
  // stranded. So follow instantly and save the glide for the user's own jump.
  function toBottom(smooth = false) {
    threadEl.scrollTo({ top: threadEl.scrollHeight,
                        behavior: smooth ? "smooth" : "instant" });
    // The composer also grows after we scroll — a pin chip lands, the textarea
    // auto-grows, the meta row appears — and every pixel it gains is a pixel
    // the thread loses. Re-pin once layout has settled.
    requestAnimationFrame(() => {
      threadEl.scrollTo({ top: threadEl.scrollHeight, behavior: "instant" });
    });
  }

  // Same race, triggered from outside a turn (pinning a candle while reading).
  // Only follows when the user was already at the bottom, so it never yanks
  // them away from scrollback.
  const composerEl = document.querySelector(".composer-wrap");
  if (composerEl && window.ResizeObserver) {
    new ResizeObserver(() => { if (atBottom()) toBottom(); }).observe(composerEl);
  }

  el("toBottom").innerHTML = Icons.svg("arrowDown", "sm");
  el("toBottom").addEventListener("click", () => toBottom(true));
  threadEl.addEventListener("scroll", () => {
    el("toBottom").classList.toggle("show", !atBottom() && msgsEl.children.length > 0);
  });

  /* ── the twelve openings ───────────────────────────────────────────────
   * An empty chat box is the hardest screen in the product: it can do about
   * ninety things and shows none of them, so the first question is usually a
   * guess at what it understands. These are the twelve it is best at, drawn
   * rather than listed.
   *
   * `q` is a real prompt, phrased the way a person would type it — not a
   * command and not a tool name. Clicking one FILLS the composer and focuses
   * it; it does not send. The point is to teach the vocabulary and leave the
   * user holding the sentence, so the second question can be their own.
   * (It is also the honest default while a turn costs what it currently
   * costs — a mis-click should not spend a minute of somebody's time.)
   *
   * `{sym}` is the chart's own instrument, so the sentence reads as a
   * question about what is actually on screen.
   */
  const TEMPLATES = [
    { icon: "levels", label: "Levels",
      q: "Mark the support and resistance on {sym} and tell me how many times each has been touched." },
    { icon: "patterns", label: "Patterns",
      q: "What chart patterns are forming on {sym} right now? Mark them." },
    { icon: "trendlines", label: "Trends",
      q: "Draw the trend lines that matter on {sym} and say which one is still intact." },
    { icon: "indicators", label: "Indicators",
      q: "Add RSI and a 50-period moving average to {sym}, and read them together." },
    // The one tile that DOES something before it types: a screenshot prompt
    // with no screenshot attached is a question about nothing, so the tile
    // fires the capture too. `act` runs the same menu item the camera button
    // runs — clicking the real control rather than reaching past it, which
    // is the rule the keyboard shortcuts already follow.
    { icon: "screenshot", label: "Screenshot", act: "shot",
      q: "Read the screenshot of my chart and tell me what stands out — structure, levels, anything unusual." },
    { icon: "whyMoved", label: "Why it moved",
      q: "Explain {sym}'s last big move — what happened, and how far did it travel?" },
    { icon: "planTrade", label: "Plan",
      q: "Plan a position on {sym}: entry, stop, target and the R:R, with the levels you used." },
    { icon: "evidence", label: "Evidence",
      q: "Take the most recent pattern on {sym} and show me its historical record against a control." },
    { icon: "screen", label: "Screen",
      q: "Find other stocks setting up the same way {sym} is." },
    { icon: "compare", label: "Compare",
      q: "Compare {sym} against its sector peers over the last six months." },
    { icon: "alert", label: "Alert",
      q: "Alert me when {sym} closes above its nearest resistance." },
    { icon: "earnings", label: "Earnings",
      q: "How has {sym} reacted to its last few results? Mark them on the chart." },
  ];

  /* Execution mode's own openings. NOT the research tiles reworded — a
   * builder's blank page is a different blank page. Each one is a complete,
   * buildable rule with every value the tools need already in it (a size, a
   * threshold, a period), because a template that lands the user in a
   * clarifying question has taught them the mode asks questions rather than
   * that it builds things.
   *
   * Ordered the way the work actually goes: state a rule, give it an exit,
   * size it, schedule it, then test it and look at it. */
  const EXEC_TEMPLATES = [
    // Two openers that name only a NATURE. Every other tile here is a fully
    // specified rule, which quietly taught the opposite of what the builder
    // can do: that you have to arrive knowing the indicator, the lookback and
    // the threshold. Most people arrive with an adjective.
    { icon: "lock", label: "Defensive",
      q: "Build me something defensive on {sym} — protect the downside even if the upside is small." },
    { icon: "opRise", label: "Momentum",
      q: "Build me a momentum strategy on {sym}." },
    { icon: "indicators", label: "Dip buy",
      q: "Buy 10 {sym} when RSI(14) falls below 30." },
    { icon: "cross", label: "Crossover",
      q: "Buy 25 {sym} when the 20-day EMA crosses above the 50-day EMA." },
    { icon: "levels", label: "Breakout",
      q: "Buy 15 {sym} when it closes above the highest high of the last 20 days." },
    { icon: "trendlines", label: "Trend filter",
      q: "Buy 10 {sym} when RSI(14) is under 35 and price is above the 200-day SMA." },
    { icon: "volumeProfile", label: "On volume",
      q: "Buy 20 {sym} when volume is more than 1.5 times its 20-day average and the bar closes up." },
    { icon: "exit", label: "Add an exit",
      q: "Buy 10 {sym} when RSI(14) drops below 30, and sell when unrealised profit reaches 8%." },
    { icon: "planTrade", label: "Stop loss",
      q: "Buy 10 {sym} on a close above the 50-day SMA, with a 3% stop from my entry." },
    { icon: "trail", label: "Trailing exit",
      q: "Buy 10 {sym} when RSI(14) is below 30 and exit when drawdown from the peak reaches 4%." },
    { icon: "schedule", label: "Schedule",
      q: "Every Friday at 10:00, buy 5 {sym}." },
    { icon: "compare", label: "Relative",
      q: "Buy 15 {sym} when its 20-day return beats NIFTY's 20-day return." },
    { icon: "evidence", label: "Backtest",
      q: "Backtest buying {sym} when RSI(14) is under 35 and selling when it is over 65, over the last two years with 1 lakh." },
    { icon: "squareoff", label: "Square off",
      q: "Square off all my intraday positions at 15:15 every trading day." },
  ];

  function templateGrid() {
    // the thread's own esc() leaves quotes alone, which is fine for text
    // nodes and wrong for an attribute — these prompts are going into one
    const attr = (s) => String(s).replace(/[&<>"]/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const cards = (chatMode === "execution" ? EXEC_TEMPLATES : TEMPLATES).map((t) => {
      const q = t.q.replace(/\{sym\}/g, Sym.name);
      const act = t.act ? ` data-act="${attr(t.act)}"` : "";
      // `Icons.tile` THROWS on a name it doesn't carry, and the tiles are a
      // separate curated family from the 24px glyphs — so a name borrowed
      // from the wrong map took the whole tray down with it, and with it
      // paintChatMode, which is what actually sets the mode on the panel. A
      // missing illustration is worth one blank square, never a dead panel.
      let art = "";
      try { art = Icons.tile(t.icon); }
      catch (e) { console.warn("[charto] no tile for", t.icon); }
      return `<button type="button" class="tpl-card" data-q="${attr(q)}"${act}>`
        + `<span class="tpl-box">${art}</span>`
        + `<span class="tpl-name">${attr(t.label)}</span></button>`;
    }).join("");
    return `<div class="tpl-head">${chatMode === "execution"
        ? "START BUILDING WITH" : "START ASKING WITH"}</div>`
      + `<div class="tpl-grid">${cards}</div>`;
  }

  /* The tray belongs to the prompt bar, not to the thread, so it is mounted
   * once and shown or hidden — rebuilding it on every render would throw
   * away the DOM under a pointer that is hovering it. The group only wears
   * its border and tint while the tray is up; with the tray down the bar
   * has to look exactly as it always did. */
  /* ── the placeholder types ──────────────────────────────────────────
   * An empty bar with one frozen line of grey text says the box exists.
   * A bar that is quietly writing questions says what the box is FOR, and
   * it does it in the user's own reading rhythm rather than asking them to
   * scan a list. Same job as the twelve tiles, in the one place the eye is
   * already resting.
   *
   * It is an INTRODUCTION, so it belongs to an EMPTY thread and nothing else.
   * Two things end it for the rest of the session: a keystroke in the bar, or
   * the conversation having any turns at all.
   *
   * The turn check is the one that was missing, and it left the effect
   * running in the two places it reads worst. A RESTORED thread starts the
   * cycle from scratch on load — twenty turns of answers above a bar quietly
   * suggesting "Mark the levels that actually held", as though nothing had
   * been asked. And a follow-up chip fills the composer and sends WITHOUT a
   * keystroke, so `input` never fires and the typing survived a whole
   * conversation. A placeholder writing openers underneath someone who has
   * been talking for ten minutes is the bar arguing with a person who has
   * moved on. Focus alone only pauses it; clicking in and out is not the
   * same as using it.
   *
   * It never runs at all under prefers-reduced-motion.
   */
  const PLACEHOLDER = "Ask about this chart…";
  const EXECUTION_PLACEHOLDER = "Describe the strategy you want to build…";
  const TYPED = [
    "What's the trend here right now?",
    "Mark the levels that actually held",
    "Why did it move like that?",
    "Is a pattern forming?",
    "Where would a stop go?",
    "How does it compare to its peers?",
  ];
  (function typedPlaceholder() {
    const slow = window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (slow) return;
    if (turns.length) return;      // a thread was restored: never start
    let i = 0, ch = 0, dir = 1, timer = null, done = false;
    function retire() {           // used once; the bar is static from here on
      if (done) return;
      done = true;
      clearTimeout(timer);
      input.placeholder = chatMode === "execution" ? EXECUTION_PLACEHOLDER : PLACEHOLDER;
      input.classList.remove("ph-typing");
    }
    function step() {
      if (done) return;
      if (chatMode === "execution") { retire(); return; }
      // The conversation started, however it started — typed, clicked off a
      // template tile, or sent from a follow-up chip.
      if (turns.length) { retire(); return; }
      // A value in the box is the end of it, whoever put it there — the
      // template tiles fill the composer without firing `input`, so the
      // check has to be on the value and not only on the event.
      if (input.value) { retire(); return; }
      if (document.activeElement === input) {   // focused but still empty: hold
        input.placeholder = chatMode === "execution" ? EXECUTION_PLACEHOLDER : PLACEHOLDER;
        input.classList.remove("ph-typing");
        timer = setTimeout(step, 900);
        return;
      }
      input.classList.add("ph-typing");
      const full = TYPED[i % TYPED.length];
      ch += dir;
      input.placeholder = full.slice(0, ch) + (dir > 0 && ch < full.length ? "▌" : "");
      // A finished question has to be READ, not glimpsed. The hold is the
      // whole point of the effect; the typing is just how it arrives.
      let wait = dir > 0 ? 42 : 22;
      if (dir > 0 && ch >= full.length) { dir = -1; wait = 3900; }
      else if (dir < 0 && ch <= 0) { dir = 1; i++; wait = 700; }
      timer = setTimeout(step, wait);
    }
    // the bar is the first thing on screen; let it be still for a beat
    timer = setTimeout(step, 1400);
    input.addEventListener("input", retire);
    input.addEventListener("focus", () => {
      if (done) return;
      input.placeholder = chatMode === "execution" ? EXECUTION_PLACEHOLDER : PLACEHOLDER;
      input.classList.remove("ph-typing");
    });
  })();

  const trayEl = el("tplTray");
  const groupEl = el("askGroup");
  let trayBuilt = null;   // which MODE the mounted tray was built for
  /* The refraction is attached only while the tray is up, and torn down
   * after. It costs a live SVG filter and a ResizeObserver, and with the
   * tray down the group is an ordinary composer that must look exactly as
   * it always did — a backdrop-filter left running would quietly frost the
   * one control the user types into for the rest of the session. */
  let glass = null;
  function showTray(on) {
    on = !!on;
    // `trayBuilt` used to be a boolean and the tray was chat-only, so the
    // grid was built once and never again. With two modes the cache key has
    // to be the MODE — otherwise flipping the switch on an empty thread
    // leaves the other mode's openings sitting under the new placeholder.
    if (on && trayBuilt !== chatMode) {
      trayEl.innerHTML = templateGrid();
      trayBuilt = chatMode;
    }
    trayEl.hidden = !on;
    groupEl.classList.toggle("has-tray", !!on);
    if (on && !glass && window.liquidGlass) {
      // gentler than the module's defaults: this is a panel you read tiles
      // off, not a lens. Enough bend at the rim to catch the glow behind it,
      // not enough to smear a 52px drawing.
      glass = liquidGlass(trayEl, { scale: -64, chroma: 4, blur: 5,
                                     saturate: 1.35, fallbackBlur: 14 });
    } else if (!on && glass) {
      glass.destroy();
      glass = null;
    }
  }



  /* One listener on the thread, not twelve on the cards: the empty state is
   * rebuilt from scratch on every clear, and per-card handlers would have to
   * be re-bound each time or silently stop working on the second new chat. */
  trayEl.addEventListener("click", (e) => {
    const card = e.target.closest && e.target.closest(".tpl-card");
    if (!card) return;
    if (card.dataset.act === "shot") {
      // The camera button's own "Full chart" item — same path, one owner.
      // It ends at charto's review popover (Attach / Dismiss) rather than
      // attaching outright, and that is left alone on purpose: the capture
      // is confirmed in exactly one place no matter who asked for it.
      const item = document.querySelector('#shotMenu [data-shot="full"]');
      if (item) item.click();
    }
    const input = el("chatInput");
    input.value = card.dataset.q || "";
    for (const c of trayEl.querySelectorAll(".tpl-card")) c.classList.remove("is-on");
    card.classList.add("is-on");
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
    input.dispatchEvent(new Event("input", { bubbles: true }));  // regrow the box
  });

  /** The mark is a claim about what is in the bar, so it only stands while
   *  that is still true — edit the sentence or clear it and the tile lets
   *  go. Anything else leaves a tile looking armed over a prompt that is
   *  no longer its own. */
  function clearTileIfEdited() {
    const on = trayEl.querySelector(".tpl-card.is-on");
    if (on && input.value !== on.dataset.q) on.classList.remove("is-on");
  }
  input.addEventListener("input", clearTileIfEdited);

  /** Paint `turns` from scratch. Same builders as a live turn, so a reloaded
   *  conversation — or one reopened from the history — is pixel-identical to
   *  the one that just happened. */
  function renderThread() {
    msgsEl.innerHTML = "";
    showTray(!turns.length);
    if (!turns.length) {
      el("toBottom").classList.remove("show");
      return;
    }
    for (const t of turns) {
      if (t.role === "user") { addUserTurn(t.content, t.image, t.drawing, t.ts, t.journal); continue; }
      const turn = addAssistantTurn(false);
      finishTurn(turn, t.content, t.meta || [], t.acts || [], t.cards || []);
      // The questions offered under that reply come back with it. Only the
      // newest turn can be carrying any — clearSuggest drops the rest the
      // moment something else is asked.
      if (t.sugg && t.sugg.length) paintSuggest(suggestBox(turn), t.sugg);
    }
    toBottom();
  }
  // Unconditional: renderThread's empty branch IS the template grid, and
  // guarding it on turns.length meant a fresh session kept the static
  // markup from index.html and the twelve openings never appeared.
  renderThread();

  // ── send ──────────────────────────────────────────────
  /** `again` is a prompt being re-asked from a past turn's Retry. It leaves
   *  the composer alone — the half-written question sitting in it is not
   *  what you clicked — and carries no attachment: a screenshot and a tagged
   *  drawing were pinned for the message they went with, and silently
   *  re-attaching them to a re-ask would change the question. */
  async function send(again) {
    const retry = typeof again === "string";
    const text = retry ? again.trim() : input.value.trim();
    if ((!text && !pendingImage) || pending) return;
    clearSuggest();          // the offer is spent the moment anything is asked
    const image = retry ? null : pendingImage;
    const drawing = retry ? null : pendingDraw;
    const journal = retry ? null : pendingJournal;
    if (!retry) {
      setAttachment(null);
      setDrawTag(null);
      setJournalTag(null);
      // Mode-aware, not the literal. Sending a message used to reset the box
      // to the research prompt, so the builder asked you to "ask about this
      // chart" the moment you finished building something on it.
      input.placeholder = chatMode === "execution"
        ? EXECUTION_PLACEHOLDER : PLACEHOLDER;
      input.value = "";
      autoGrow();
    }
    pending = true;
    requestAbort = new AbortController();
    sendBtn.classList.add("stopping");
    sendBtn.title = "Stop response";
    sendBtn.setAttribute("aria-label", "Stop response");
    sendBtn.innerHTML = '<span class="stop-glyph" aria-hidden="true"></span>';

    const ts = Date.now();
    turns.push({ role: "user", content: text, ts,
                 ...(image ? { image } : {}), ...(drawing ? { drawing } : {}),
                 ...(journal ? { journal } : {}) });
    showTray(false);          // first question asked — the openings fold away
    const onTile = trayEl.querySelector(".tpl-card.is-on");
    if (onTile) onTile.classList.remove("is-on");
    addUserTurn(text, image, drawing, ts, journal);
    const turn = addAssistantTurn();
    const t0 = performance.now();

    try {
      // Snapshot the charts at send time — what you were looking at when you
      // asked. The chip names panes, so the envelope is built from those panes
      // rather than from whatever happens to be selected now, and what came
      // back is recorded: a layout change can retire a chosen pane, and the
      // fallback has to be visible rather than silent.
      let context = window.__charto
        ? window.__charto.getChartContext(chosen) : null;
      if (journal) context = Object.assign({}, context || {}, { journal });
      // Stamp the turn with what was on screen when it was asked. Only the
      // mirrored archive uses it, so a later session can find "that ITC
      // conversation" without reading every word of every one.
      if (context && context.symbol) turns[turns.length - 1].symbol = context.symbol;
      if (context && context.symbol) {
        // A chart still loading its bars has no envelope, so it is not in the
        // conversation — and the chip must stop implying it is.
        sent = new Set((context.charts || [context]).map((c) => c.symbol));
        const open = new Map(openCharts().map((c) => [c.pane, c.symbol]));
        const kept = chosen.filter((p) => sent.has(open.get(p)));
        if (kept.length && kept.length !== chosen.length) chosen = kept;
        paintCtxFlag();
      }
      const res = await fetch(`${API}/chat`, {
        method: "POST",
        // The turn rides the account's token. The server reads WHO is asking
        // off these headers, and the tools that own something — the alerts a
        // rule is armed under, the archive recall_conversations searches — are
        // scoped by it. Sent without one, every such tool answered "you need
        // an account" to a user who was looking at their own name in the
        // corner. `Auth.headers` adds nothing when signed out, which is the
        // honest state for a browser that has no session.
        headers: typeof Auth !== "undefined"
          ? Auth.headers({ "Content-Type": "application/json" })
          : { "Content-Type": "application/json" },
        // chat_id is what lets recall_conversations EXCLUDE this conversation
        // from a search of the earlier ones — its turns are already in
        // `messages`, and finding them twice would read as two occasions.
        body: JSON.stringify({ messages: wireHistory(), context, stream: true,
                               chat_id: activeId, mode: chatMode }),
        signal: requestAbort.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      // The follow-ups arrive on the tail of this same stream, so their sink
      // is handed to the reader up front. It resolves on the ANSWER, not on
      // them — everything below runs at the same moment it always did.
      const sink = suggestSink(turn);
      const d = await readStream(res, turn, sink);
      if (d.error) throw new Error(d.error);

      // File the reply BEFORE touching the workspace. `open_chart` with
      // replace on the main chart navigates the page (that is how an
      // instrument becomes the main chart), and a navigation between here and
      // the save below took the answer with it — the reload came back to a
      // thread missing the turn that had just asked for the chart.
      const secs = ((performance.now() - t0) / 1000).toFixed(1);
      // The footer names the model ONLY when the answer did not come from the
      // usual one — the server sends `model` on a fallback and omits it
      // otherwise. An outage upstream otherwise reads as the product quietly
      // getting worse, which is the one explanation that is not true.
      const meta = [`${secs}s`, ...(d.model ? [`${d.model} · fallback`] : [])];
      const acts = chartActions(d.scene_patch);
      // The panels are filed with the reply, not re-fetched: reopening a
      // conversation has to bring back the scan the answer was written about,
      // and the tool that produced it is not going to be run again.
      // The ones the stream rendered win, because they are the objects the
      // user is looking at. `d.cards` is the fallback for the non-streaming
      // path, which never emits a card event and so has nothing rendered yet.
      const cards = (turn.__cardRecs && turn.__cardRecs.length)
        ? turn.__cardRecs : (d.cards || []);
      turns.push({ role: "assistant", content: d.text, meta, acts,
                   ...(cards.length ? { cards } : {}) });
      saveTurns();
      // The row's record now exists. If the follow-ups already landed they are
      // written into it here; if they land later they find it waiting.
      sink.attach(turns[turns.length - 1]);

      // Move the workspace BEFORE drawing on it: a scene op can be aimed at a
      // chart this same turn opened, and applying the patch first would draw
      // it onto whatever pane happened to be there.
      if (d.view_ops && d.view_ops.length) {
        // An alert armed in conversation is a row in the widget and a line on
        // the price axis, and this tab holds its own copy of both — so a turn
        // that touched the watcher re-reads it. Once per turn however many
        // alerts the turn changed: three edits are one refresh.
        let alertsStale = false;
        for (const op of d.view_ops) {
          if (op.kind === "alerts_changed") { alertsStale = true; continue; }
          if (op.kind !== "open_chart" || !window.__charto?.panes) continue;
          try {
            window.__charto.panes.openChart(op.symbol, op.interval, op.replace);
          } catch (e) {
            console.warn("[charto] open_chart failed", op, e);
          }
        }
        // After the opens: an alert on a symbol this same turn put on screen
        // has no line to draw until that chart exists.
        //
        // `typeof`, not `window.Alerts`: js/alerts.js declares its module as a
        // top-level `const`, which never becomes a window property — and this
        // file loads BEFORE it, so the name only has to exist by the time a
        // turn comes back, not now.
        if (alertsStale && typeof Alerts !== "undefined") {
          Promise.resolve(Alerts.load()).catch((e) =>
            console.warn("[charto] alert refresh failed", e));
        }
      }

      // Apply anything the model chose to draw. Guarded like the open above:
      // the answer is already written and filed, and a patch that throws must
      // cost the drawing, never the reply.
      if (d.scene_patch && d.scene_patch.length && window.__charto) {
        try {
          window.__charto.scene.apply(d.scene_patch);
        } catch (e) {
          console.warn("[charto] scene patch failed", e);
        }
      }

      // The footer carries how long it took, and nothing else. The token count
      // used to ride here too; it is a fact about the bill rather than about
      // the answer, and the row it sits in is otherwise entirely about what
      // you just read.
      // `cards` is the fallback, not the norm: the stream already inserted
      // them, and finishTurn skips any turn that has one. It matters for the
      // non-streaming path, which has no card event to insert from.
      finishTurn(turn, d.text, meta, acts, cards);
    } catch (e) {
      if (e && e.name === "AbortError") {
        const partial = turn.__streamText || "Response interrupted.";
        turns.push({ role: "assistant", content: partial, meta: ["Interrupted"], acts: [] });
        saveTurns();
        finishTurn(turn, partial, ["Interrupted"], [], []);
      } else {
        turns.pop();   // keep the thread consistent with what the model saw
        saveTurns();
        failTurn(turn, e.message || String(e));
      }
    } finally {
      pending = false;
      requestAbort = null;
      sendBtn.classList.remove("stopping");
      sendBtn.title = "Send (Enter)";
      sendBtn.setAttribute("aria-label", "Send message");
      sendBtn.innerHTML = Icons.svg("arrowUp", "sm");
      input.focus();
    }
  }

  // ── composer ──────────────────────────────────────────
  sendBtn.innerHTML = Icons.svg("arrowUp", "sm");
  sendBtn.addEventListener("click", (e) => {
    if (!pending || !requestAbort) return;
    e.preventDefault();
    requestAbort.abort();
  });
  el("chatNew").innerHTML = Icons.svg("plus", "sm");
  el("chatHistoryBtn").innerHTML = Icons.svg("clock", "sm");
  const mobileMenuBtn = el("mobileChatMenuBtn");
  const mobileMenu = el("mobileChatMenu");
  const mobileModeItems = [...mobileMenu.querySelectorAll("[data-mobile-chat-mode]")];
  mobileMenuBtn.innerHTML = Icons.svg("plus", "sm");
  /* The mode switch. Both halves are always visible and always the same size,
   * so the state is the position of the lift, not a label you have to read.
   * `aria-checked` carries the state for assistive tech AND is what the CSS
   * paints off, so there is exactly one place the truth lives. */
  const modeSwitch = el("chatModeSwitch");
  const modeSegs = [...modeSwitch.querySelectorAll("[data-chat-mode]")];
  /* The switched-off half says so where the hand already is.
   *
   * `aria-disabled`, never the `disabled` attribute: a disabled button takes
   * no pointer events at all, which means no hover, which means the browser
   * never shows the title — the control would go quiet and grey and never
   * explain itself. This marks it for assistive tech and for the stylesheet,
   * and the gate in setChatMode is what actually refuses the switch. */
  if (!EXECUTION_ENABLED) {
    const soon = (n) => {
      n.setAttribute("aria-disabled", "true");
      n.title = "Coming soon";
    };
    for (const seg of modeSegs) if (seg.dataset.chatMode === "execution") soon(seg);
    for (const it of mobileModeItems) {
      if (it.dataset.mobileChatMode === "execution") soon(it);
    }
  }
  function paintChatMode() {
    const execution = chatMode === "execution";
    input.placeholder = execution ? EXECUTION_PLACEHOLDER : PLACEHOLDER;
    panel.dataset.chatMode = chatMode;
    for (const seg of modeSegs) {
      const on = seg.dataset.chatMode === chatMode;
      seg.setAttribute("aria-checked", String(on));
      // Only the selected segment is a tab stop — a radio group is ONE stop
      // and the arrows move within it, so Tab doesn't have to walk past a
      // control the user isn't changing.
      seg.tabIndex = on ? 0 : -1;
    }
    for (const item of mobileModeItems) {
      item.setAttribute("aria-checked", String(item.dataset.mobileChatMode === chatMode));
    }
    showTray(!turns.length);
  }
  function setChatMode(next, { focus = false } = {}) {
    next = next === "execution" ? "execution" : "chat";
    // The one gate. Every path into the mode — the segment, the arrow keys,
    // the phone menu — comes through here, so switching it off is one line
    // rather than three that have to agree.
    if (next === "execution" && !EXECUTION_ENABLED) return;
    if (next === chatMode) return;
    chatMode = next;
    Store.set("chatmode", chatMode);
    paintChatMode();
    if (focus) modeSegs.find((s) => s.dataset.chatMode === chatMode)?.focus();
  }
  modeSwitch.addEventListener("click", (e) => {
    const seg = e.target.closest("[data-chat-mode]");
    if (!seg) return;
    setChatMode(seg.dataset.chatMode);
    input.focus();
  });
  modeSwitch.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight"
        && e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
    e.preventDefault();
    const next = chatMode === "execution" ? "chat" : "execution";
    // An unavailable half still TAKES FOCUS. Refusing the key outright would
    // leave a keyboard reader with a group that appears to hold one option:
    // the arrow would do nothing, nothing would be announced, and the mode
    // would be invisible rather than merely off. Focus moves, the label and
    // its disabled state are read, and only the switch itself is declined —
    // which is what a radio group with an unavailable option is supposed to
    // do. (Roving tabindex, so `.focus()` on the -1 segment is the point.)
    if (next === "execution" && !EXECUTION_ENABLED) {
      modeSegs.find((s) => s.dataset.chatMode === "execution")?.focus();
      return;
    }
    setChatMode(next, { focus: true });
  });
  function closeMobileMenu() {
    mobileMenu.classList.remove("open");
    mobileMenuBtn.setAttribute("aria-expanded", "false");
  }
  mobileMenuBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = mobileMenu.classList.toggle("open");
    mobileMenuBtn.setAttribute("aria-expanded", String(open));
  });
  mobileMenu.addEventListener("click", (e) => {
    const item = e.target.closest("[data-mobile-chat-mode]");
    if (!item) return;
    setChatMode(item.dataset.mobileChatMode);
    closeMobileMenu();
    input.focus();
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".mobile-chat-menu-wrap")) closeMobileMenu();
  });
  paintChatMode();
  // Voice: the transcript is APPENDED to the draft and never sent. Speaking
  // is a way of adding to a half-typed question, and a mis-heard word should
  // be edited before it is asked, not after it is answered.
  if (typeof Voice !== "undefined") {
    Voice.attach(el("micBtn"), (text) => {
      const cur = input.value.trim();
      input.value = cur ? `${cur} ${text}` : text;
      autoGrow();
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }, { api: API, toast: (m) => (typeof Alerts !== "undefined"
                                  ? Alerts.toast(m) : console.warn(m)) });
  }
  el("histClose").innerHTML = Icons.svg("x", "sm");

  function autoGrow() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 190) + "px";
  }
  input.addEventListener("input", autoGrow);

  // ── pinned bars: clicked candles shown above the composer ──
  // They only ground the next message; they never send one on their own.
  //
  // A chip has to answer three things on sight, or it is just a number
  // floating above the composer: WHICH bar (its size and its timestamp),
  // WHAT it did (close, and the move across the bar), and WHY it is sitting
  // there (the row's caption). The glyph is the bar drawn to scale — body
  // and wicks in their real proportions — so the chip is recognisable as the
  // candle you clicked before you read a word of it.
  const pinRow = el("pinRow");
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const IV_LABEL = { "1m": "1-min", "5m": "5-min", "15m": "15-min", "30m": "30-min",
                     "1h": "1-hour", "1d": "Daily", "1w": "Weekly", "1mo": "Monthly" };
  const DAILY_IV = new Set(["1d", "1w", "1mo"]);
  // Routed through Sym, not hardcoded en-IN: the same pin chip renders a
  // Bitcoin candle, where "12,34,567.5" is the wrong grouping and the wrong
  // currency. Sym owns locale and symbol per instrument.
  const num = (n) => Sym.num(n, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function pinChip(p) {
    // Chart time is IST-shifted, so the UTC getters render IST wall clock.
    const t = new Date(p.time * 1000), pad = (n) => String(n).padStart(2, "0");
    const iv = p.interval || "";
    // Year always: the store reaches back to 2015, so a bare "28 Jul" is
    // ambiguous by a decade the moment you scroll back.
    const date = `${t.getUTCDate()} ${MON[t.getUTCMonth()]} ${t.getUTCFullYear()}`;
    const when = DAILY_IV.has(iv) ? date : `${date}, ${pad(t.getUTCHours())}:${pad(t.getUTCMinutes())}`;
    const size = IV_LABEL[iv] ? `${IV_LABEL[iv]} candle` : "candle";

    const up = p.close >= p.open;
    const pct = p.open ? (p.close - p.open) / p.open * 100 : 0;
    const move = `${pct >= 0 ? "+" : "−"}${Math.abs(pct).toFixed(2)}%`;

    // Body position and height as a share of the high-low span: the chip's
    // miniature is the same shape as the bar on the chart.
    const span = Math.max(p.high - p.low, 1e-9);
    const top = (p.high - Math.max(p.open, p.close)) / span * 100;
    const body = Math.max(Math.abs(p.close - p.open) / span * 100, 7);

    const full = [
      `${size} · ${when}`,
      `O ${num(p.open)}   H ${num(p.high)}   L ${num(p.low)}   C ${num(p.close)}`,
      p.volume ? `V ${Sym.num(p.volume)}` : "",
      "Click to find it on the chart · × to unpin",
    ].filter(Boolean).join("\n");

    return `<span class="pin ${up ? "up" : "down"}" data-find="${p.time}" `
      + `role="button" tabindex="0" title="${full}" aria-label="Pinned ${size}, ${when}, close ${num(p.close)}, ${move}">`
      + `<span class="pin-bar" aria-hidden="true"><i class="wick"></i>`
      + `<i class="body" style="top:${top.toFixed(1)}%;height:${body.toFixed(1)}%"></i></span>`
      + `<span class="pin-txt">`
      + `<span class="pin-top">${Sym.price(p.close, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}<b class="pin-move">${move}</b></span>`
      + `<span class="pin-sub">${size} · ${when}</span>`
      + `</span>`
      + `<span class="x" data-unpin="${p.time}" title="Unpin this candle">${Icons.svg("x", "xs")}</span>`
      + `</span>`;
  }

  document.addEventListener("charto:pins", (e) => {
    const n = e.detail.length;
    // The caption is the whole point of the row: a pin is context for the
    // NEXT message, not an action, and nothing else on screen says that.
    pinRow.innerHTML = n
      ? `<span class="pins-lead">${Icons.svg("pin", "xs")}`
        + `${n} candle${n === 1 ? "" : "s"} pinned — sent with your next message</span>`
        + e.detail.map(pinChip).join("")
      : "";
    if (n) { reveal(); input.focus(); }   // see reveal() — a pin you can't see
  });
  pinRow.addEventListener("click", (e) => {
    const un = e.target.closest("[data-unpin]");
    if (un) {
      document.dispatchEvent(new CustomEvent("charto:unpin", { detail: Number(un.dataset.unpin) }));
      return;
    }
    const find = e.target.closest("[data-find]");
    if (find) {
      document.dispatchEvent(new CustomEvent("charto:reveal-pin", { detail: Number(find.dataset.find) }));
    }
  });
  pinRow.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const chip = e.target.closest("[data-find]");
    if (!chip) return;
    e.preventDefault();
    document.dispatchEvent(new CustomEvent("charto:reveal-pin", { detail: Number(chip.dataset.find) }));
  });

  /** The panel, if it is not already up. A tag that lands in a composer the
   *  reader cannot see has done nothing they can tell — and both callers below
   *  exist to put something IN that composer, so opening it is the point of
   *  the gesture rather than a side effect of it. */
  function reveal() {
    if (panel.classList.contains("hidden")) chatToggle.click();
  }

  // Seed the composer from elsewhere in the app — text only, never a send.
  document.addEventListener("charto:compose", (e) => {
    reveal();
    input.value = (input.value ? input.value.replace(/\s*$/, " ") : "") + e.detail;
    input.focus();
    autoGrow();
  });

  el("chatForm").addEventListener("submit", (e) => { e.preventDefault(); send(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
    e.stopPropagation(); // don't let Delete/Escape hit the drawing layer
  });

  // The subject chip controls which visible charts are included in context.
  const ctxFlag = el("ctxFlag");

  /* ── what the conversation is about ──────────────────────────────────────
   *
   * The chip names the CHARTS the model is reading, not the fact that it is
   * reading something: "sees chart" told you the switch was on and nothing
   * about which chart. It follows the pane you last clicked, so on a split the
   * chat and the selection can never disagree — unless you have chosen the set
   * yourself in the menu, which pins it (a deliberate choice outranks a click
   * somewhere else on screen).
   *
   * The menu offers exactly the charts that are OPEN. Not the universe: a
   * ticker with no pane has no visible chart to talk about, and the whole
   * premise here is that the conversation is about what is on screen. Opening
   * a chart is a layout away.
   *
   * Whether the charts ride along is carried by the chip's own ink — attached
   * reads at full strength, detached goes faint and the tooltip says so.
   */
  let chosen = [0];                   // pane indices, in the order they were added
  let pinned = false;                 // set by choosing in this menu
  let sent = null;                    // what the last turn actually carried

  /** The open charts, primary first. Before main.js has finished booting there
   *  is exactly one and it is the page's own symbol. */
  const openCharts = () => (window.__charto && window.__charto.charts)
    ? window.__charto.charts()
    : [{ pane: 0, symbol: Sym.name, interval: "", primary: true }];

  /** The chosen panes as chart records, dropping any the layout has retired. */
  function chosenCharts() {
    const open = openCharts();
    const byPane = new Map(open.map((c) => [c.pane, c]));
    const list = chosen.map((p) => byPane.get(p)).filter(Boolean);
    return list.length ? list : [open[0]];
  }

  function paintCtxFlag() {
    const list = chosenCharts();
    ctxFlag.classList.toggle("multi", list.length > 1);
    /* One chart is named. SEVERAL are just their marks: the row would run to
     * three or four names beside a text box that is itself the width of the
     * pane, and the names are one click away in the menu. No interval either —
     * it belongs to the chart, and the chip answers "about what", not
     * "at what resolution". */
    ctxFlag.innerHTML = list.length === 1
      ? Universe.logoHTML(list[0].symbol, "co-logo")
        + `<span class="sym">${list[0].symbol}</span>`
      : list.map((c) => Universe.logoHTML(c.symbol, "co-logo")
          || `<span class="sym">${c.symbol}</span>`).join("");
    const names = list.map((c) => `${c.symbol} ${c.interval || ""}`.trim()).join(" · ");
    ctxFlag.title = `The model reads ${list.length > 1 ? "these charts" : "this chart"} — ${names}`
      + " · click to choose what is in context";
  }

  /** The menu behind the chip: every open chart, ticked or not. Ticking one
   *  puts it in the turn; unticking takes it out. The last one cannot be
   *  removed because every message always includes at least one chart. */
  function openSubjectMenu() {
    const open = openCharts();
    const picked = new Set(chosenCharts().map((c) => c.pane));
    const rows = [
      `<div class="head">Charts in this conversation</div>`,
      ...open.map((c) => {
        const on = picked.has(c.pane);
        return `<div class="item ${on ? "on" : ""}" data-pane="${c.pane}">`
          + `<span class="lead">${Universe.logoHTML(c.symbol, "co-logo")}`
          + `${c.symbol}<span class="co-name">${c.interval || ""}`
          + `${c.primary ? " · main" : ""}</span></span>`
          + (on ? Icons.svg("check", "xs") : "") + `</div>`;
      }),
      pinned ? `<div class="sep"></div><div class="item" data-pane="follow">`
        + `<span class="lead">${Icons.svg("check", "sm")}`
        + `Your choice — click to follow the selected chart again</span></div>` : "",
      open.length === 1
        ? `<div class="pick-note">Split the layout to put a second chart on `
          + `screen; anything open can join the conversation.</div>`
        : `<div class="pick-note">Every ticked chart is sent with each message, `
          + `and the model can read any of them.</div>`,
    ].join("");

    const pop = document.createElement("div");
    pop.className = "dropdown floating subj-menu open";
    pop.innerHTML = rows;
    document.body.appendChild(pop);
    const r = ctxFlag.getBoundingClientRect();
    pop.style.left = Math.max(8, r.left) + "px";
    pop.style.bottom = (innerHeight - r.top + 8) + "px";

    const close = () => { pop.remove(); document.removeEventListener("mousedown", out, true); };
    const out = (e) => { if (!pop.contains(e.target) && !ctxFlag.contains(e.target)) close(); };
    setTimeout(() => document.addEventListener("mousedown", out, true), 0);

    pop.addEventListener("click", (e) => {
      const it = e.target.closest("[data-pane]");
      if (!it) return;
      e.stopPropagation();
      if (it.dataset.pane === "follow") {
        pinned = false;
        chosen = [(window.__chartoActivePane || 0)];
        close(); paintCtxFlag();
        return;
      }
      const p = Number(it.dataset.pane);
      const i = chosen.indexOf(p);
      if (i >= 0) {
        if (chosen.length === 1) return;   // never leave the turn with nothing
        chosen.splice(i, 1);
      } else {
        chosen.push(p);
      }
      pinned = true;                        // an explicit set outranks a click
      paintCtxFlag();
      close(); openSubjectMenu();           // stays open for a second choice
    });
  }

  // The selected chart is the subject — the same signal that re-aims the
  // toolbar. A pinned set ignores it.
  document.addEventListener("charto:pane-active", (e) => {
    window.__chartoActivePane = e.detail.pane;
    if (pinned) return;
    chosen = [e.detail.pane];
    paintCtxFlag();
  });
  // A layout change retires panes; the chip must stop naming charts that are
  // no longer on screen.
  document.addEventListener("charto:panes-changed", () => {
    const open = new Set(openCharts().map((c) => c.pane));
    const keep = chosen.filter((p) => open.has(p));
    chosen = keep.length ? keep : [0];
    paintCtxFlag();
  });
  Universe.load().then(paintCtxFlag);   // the mark lands after the fetch

  /* ── conversation history ────────────────────────────────────────────────
   *
   * The overlay is Claude Code's resume list, in this pane: every past
   * conversation as one row — what it was about, how long it ran, when it
   * was last touched — and opening one puts it back in the thread.
   *
   * The row's TITLE is the first thing you asked, verbatim. A generated
   * summary would be a second model call and a second thing that can be
   * wrong; the question you typed is what you will recognise the
   * conversation by, because it is the reason you started it.
   *
   * Nothing here moves the chart. A conversation is a record of what was
   * said, not a workspace snapshot — reopening one must not silently redraw
   * levels onto the chart you are looking at now.
   */
  const histEl = el("chatHistory"), histList = el("histList"),
        histSearch = el("histSearch");

  /* The OPEN conversation's record is only written on save, so the list reads
   * the live array for it — otherwise a row could describe the thread as it
   * was one message ago, in the one place the reader can see the thread. */
  const turnsOf = (c) => (c.id === activeId ? turns : (c.turns || []));

  function chatTitle(c) {
    const rows = turnsOf(c);
    const first = rows.find((t) => t.role === "user" && t.content);
    const s = String(first ? first.content : "").replace(/\s+/g, " ").trim();
    if (s) return s.length > 90 ? s.slice(0, 89) + "…" : s;
    return rows.some((t) => t.image) ? "Screenshot" : "Empty conversation";
  }

  /** How long ago, at the coarseness that is actually useful in a list. */
  function relTime(ts) {
    const s = Math.max(0, (Date.now() - (ts || 0)) / 1000);
    if (s < 90) return "just now";
    if (s < 3600) return `${Math.round(s / 60)}m ago`;
    if (s < 86400) return `${Math.round(s / 3600)}h ago`;
    if (s < 604800) return `${Math.round(s / 86400)}d ago`;
    return new Date(ts).toLocaleDateString([], { day: "numeric", month: "short" });
  }

  function renderHistory() {
    const q = histSearch.value.trim().toLowerCase();
    // Search reads the whole conversation, not just its title: you remember a
    // thread by something that was SAID in it as often as by how it opened.
    // The conversation you are IN is a row — unless nothing has been said in
    // it yet, when "Empty conversation · Current" is a line about the screen
    // you are already looking at.
    const list = chats
      .filter((c) => turnsOf(c).length)
      .filter((c) => !q || chatTitle(c).toLowerCase().includes(q)
        || turnsOf(c).some((t) => String(t.content || "").toLowerCase().includes(q)))
      .slice()
      .sort(byRecent);

    const rows = list.map((c) => {
      const n = turnsOf(c).length;
      // Which chart it was about. Conversations are no longer filed per
      // symbol — one thread can move from RELIANCE to TCS and back, which is
      // how people actually talk — so the row has to say what it was about
      // instead of the panel implying it by only showing one company's.
      const syms = [...new Set(turnsOf(c).map((t) => t.symbol).filter(Boolean))];
      const sub = [
        c.id === activeId ? "Current" : "",
        syms.slice(0, 2).join(", ") + (syms.length > 2 ? ` +${syms.length - 2}` : ""),
        `${n} message${n === 1 ? "" : "s"}`,
        relTime(c.updated),
      ].filter(Boolean).join(" · ");
      return `<div class="hist-row${c.id === activeId ? " on" : ""}" data-open="${c.id}"`
        + ` role="button" tabindex="0">`
        + `<span class="hist-title">${esc(chatTitle(c))}</span>`
        + `<span class="hist-sub">${sub}</span>`
        + `<span class="x" data-del="${c.id}" role="button" tabindex="0"`
        + ` title="Delete this conversation">${Icons.svg("x", "xs")}</span>`
        + `</div>`;
    }).join("");

    // An in-flight reply belongs to the conversation that asked for it, so
    // the list says why it is inert rather than quietly ignoring a click.
    histList.innerHTML = (pending
      ? `<div class="hist-note">Waiting for the current reply to finish…</div>` : "")
      + (rows || `<div class="hist-empty">`
        + (q ? "Nothing matches that." : "No conversations yet.") + `</div>`);
  }

  function openHistory() {
    histSearch.value = "";
    renderHistory();
    histEl.classList.add("open");
    // a phone has no hover and no room to spare — don't summon the keyboard
    if (!matchMedia("(hover: none)").matches) histSearch.focus();
  }
  const closeHistory = () => histEl.classList.remove("open");

  function newConversation() {
    if (pending) return;
    closeHistory();
    if (!turns.length) { input.focus(); return; }   // already a fresh one
    saveTurns();                                     // file the open one away
    const c = blankChat();
    chats.unshift(c);
    activeId = c.id;
    turns.length = 0;
    persistChats();
    renderThread();
    input.focus();
  }

  function openConversation(id) {
    if (pending) return;
    if (id === activeId) { closeHistory(); return; }
    const rec = chats.find((c) => c.id === id);
    if (!rec) return;
    saveTurns();              // flush what is open before leaving it
    activeId = id;
    turns.length = 0;
    turns.push(...(rec.turns || []));
    persistChats();           // remember which one is open across a reload
    renderThread();
    closeHistory();
  }

  function deleteConversation(id) {
    if (pending && id === activeId) return;
    const i = chats.findIndex((c) => c.id === id);
    if (i < 0) return;
    chats.splice(i, 1);
    if (id === activeId) {
      const c = blankChat();
      chats.unshift(c);
      activeId = c.id;
      turns.length = 0;
      renderThread();
    }
    persistChats();
    renderHistory();
  }

  histList.addEventListener("click", (e) => {
    const del = e.target.closest("[data-del]");
    if (del) { e.stopPropagation(); deleteConversation(del.dataset.del); return; }
    const row = e.target.closest("[data-open]");
    if (row) openConversation(row.dataset.open);
  });
  histList.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const del = e.target.closest("[data-del]");
    const row = e.target.closest("[data-open]");
    if (!del && !row) return;
    e.preventDefault();
    if (del) deleteConversation(del.dataset.del);
    else openConversation(row.dataset.open);
  });
  histSearch.addEventListener("input", renderHistory);
  // The field swallows keys so typing "d" in it can't arm a drawing tool —
  // but Escape has to close the overlay from the one place you are most
  // likely to press it, so it is handled here rather than left to bubble.
  histSearch.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeHistory();
    e.stopPropagation();
  });
  el("histClose").addEventListener("click", closeHistory);
  el("histNew").addEventListener("click", newConversation);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && histEl.classList.contains("open")) closeHistory();
  });

  el("chatNew").addEventListener("click", newConversation);
  el("chatHistoryBtn").addEventListener("click", openHistory);
  el("mobileChatNew").addEventListener("click", () => {
    closeMobileMenu();
    newConversation();
  });
  el("mobileChatHistory").addEventListener("click", () => {
    closeMobileMenu();
    openHistory();
  });
  // The chip chooses which visible chart or charts the model reads.
  ctxFlag.addEventListener("click", (e) => { e.stopPropagation(); openSubjectMenu(); });
  paintCtxFlag();

  // ── resizable split: chart | chat ─────────────────────
  const splitter = el("splitter"), main = document.querySelector(".main");
  const splitReadout = el("splitReadout");
  const WKEY = "charto_chat_width";
  const MIN_CHAT = 340, MIN_CHART = 420;
  /* The width a first visit opens at, and the one the reset gesture returns
   * to — a third of the row, so the chart keeps the two thirds it is the
   * subject of. Named once because three places have to agree on it. */
  const DEF_W = 0.30;

  /* Below the breakpoint the shell is a COLUMN: the chat sits under the
   * chart at full width, and a horizontal split has nothing to divide. The
   * saved desktop width is an inline style, so it beat the stylesheet and
   * pinned a phone's chat panel to 340px with the chart's own width beside
   * it — the split survived the layout that removed it. */
  const stacked = () => window.matchMedia("(max-width: 820px)").matches;

  const HKEY = "charto_chat_height";
  const MIN_CHAT_H = 160, MIN_CHART_H = 200;

  /** Everything on this row that is NEITHER chart nor conversation: the
   *  divider itself, the widget bar on the outer edge, and a widget panel if
   *  one is open. The clamp used to read `total - MIN_CHART` and hand the
   *  remainder to the chat, which quietly spent the chart's floor on that
   *  chrome — dragging all the way over left a 373px chart under a 420px
   *  promise, and 71px of it with the watchlist out. Measured rather than
   *  named: it is three widths that each move on their own. */
  const chartsEl = document.querySelector(".charts");
  const rowChrome = () =>
    Math.max(0, main.clientWidth - panel.offsetWidth - chartsEl.clientWidth);

  /* The stacked phone layout also spends height on the mobile toolbar and
   * horizontal splitter. Subtract those rows before clamping a saved chat
   * height, otherwise chart + chat + chrome can exceed the viewport. */
  const columnChrome = () => [...main.children].reduce((sum, node) => {
    if (node === panel || node === chartsEl) return sum;
    const style = getComputedStyle(node);
    return style.display === "none" ? sum : sum + node.getBoundingClientRect().height;
  }, 0);

  /** What the divider currently says about itself — the share of the row the
   *  conversation holds, which is the number that survives a window resize,
   *  and the pixels the layout actually stores. Written into the readout the
   *  drag shows AND into the separator's ARIA value, because a control you
   *  can drive from the keyboard has to announce where it now is. Which axis
   *  it measures follows the shell: stacked, this divider drags height. */
  function paintSplit() {
    const vert = !stacked();
    const total = vert ? main.clientWidth : main.clientHeight;
    const size = vert ? panel.offsetWidth : panel.offsetHeight;
    const pct = total ? Math.round((size / total) * 100) : 0;
    splitter.setAttribute("aria-orientation", vert ? "vertical" : "horizontal");
    splitter.setAttribute("aria-valuenow", String(pct));
    splitter.setAttribute("aria-valuetext",
      `Chat ${pct}% of the ${vert ? "width" : "height"}, ${size} pixels`);
    if (splitReadout) splitReadout.textContent = `${pct}% · ${size}px`;
  }

  /* A keyboard nudge has no drag to show the readout during, so it borrows
   * it for a moment. Same feedback, same element, no second design. */
  let peekT = null;
  function peekSplit() {
    splitter.classList.add("peek");
    clearTimeout(peekT);
    peekT = setTimeout(() => splitter.classList.remove("peek"), 1100);
  }

  /** Stacked layout: the same divider drags HEIGHT. Kept on its own key so
   *  a phone split and a desktop split do not overwrite each other every
   *  time the window crosses the breakpoint. */
  function setChatHeight(px, persist = true) {
    const total = main.clientHeight;
    const ceiling = Math.max(MIN_CHAT_H, total - MIN_CHART_H - columnChrome());
    const h = Math.round(Math.max(MIN_CHAT_H, Math.min(px, ceiling)));
    panel.style.height = h + "px";
    if (persist) localStorage.setItem(HKEY, String(h));
    paintSplit();
  }

  function setChatWidth(px, persist = true) {
    if (stacked()) {
      panel.style.width = "";
      const savedH = parseInt(localStorage.getItem(HKEY) || "0", 10);
      if (savedH) setChatHeight(savedH, false);
      else paintSplit();
      return;
    }
    panel.style.height = "";
    const total = main.clientWidth;
    const ceiling = total - MIN_CHART - rowChrome();
    // a narrow window can put the ceiling under the floor; the chat's own
    // minimum wins there, exactly as it did before the chrome was counted
    const w = Math.round(Math.max(MIN_CHAT, Math.min(px, ceiling)));
    panel.style.width = w + "px";
    if (persist) localStorage.setItem(WKEY, String(w));
    paintSplit();
  }
  function applySavedWidth() {
    const saved = parseInt(localStorage.getItem(WKEY) || "0", 10);
    setChatWidth(saved || main.clientWidth * DEF_W, false);
  }
  requestAnimationFrame(applySavedWidth);
  // rotating a phone, or dragging a desktop window across the breakpoint,
  // has to re-decide this — the width is only meaningful on one side of it
  window.matchMedia("(max-width: 820px)").addEventListener("change", applySavedWidth);

  /* A window that SHRINKS could leave the chat wider than MIN_CHART allows:
   * the clamp only ever ran when the divider moved. Re-feeding the current
   * size through it costs nothing. Deliberately not persisted — a window
   * dragged narrow for a minute must not overwrite the width you chose. And
   * only when the size is ours to re-state: with no inline value the panel is
   * on the stylesheet's viewport-relative height, and pinning that to pixels
   * here would freeze it. */
  window.addEventListener("resize", () => {
    if (stacked()) {
      if (panel.style.height) setChatHeight(panel.offsetHeight, false);
      else paintSplit();
    } else if (panel.style.width) {
      setChatWidth(panel.offsetWidth, false);
    } else {
      paintSplit();
    }
  });

  let dragging = false;
  splitter.addEventListener("mousedown", (e) => {
    dragging = true;
    splitter.classList.add("dragging");
    document.body.style.cursor = "col-resize";
    // keep the drag off the chart's own pan/draw handlers
    el("chart").style.pointerEvents = "none";
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const r = main.getBoundingClientRect();
    if (stacked()) setChatHeight(r.bottom - e.clientY);
    else setChatWidth(r.right - e.clientX);
  });
  /* The divider has to be draggable by a finger too, or the phone split is
   * decorative. Two things the mouse path does not need:
   *
   *  · the GRAB OFFSET. This used to set the panel straight from the touch
   *    y, which centres the divider under the finger on the first move —
   *    invisible on a 1px line, a visible jump now that the phone divider
   *    is a 20px bar you aim at. Where you took hold of it is where it
   *    stays.
   *  · a start and an end. Touch has no hover, so `dragging` is the only
   *    feedback that the bar is live, and nothing else would ever clear it.
   */
  let grab = null;
  splitter.addEventListener("touchstart", (e) => {
    const t = e.touches[0];
    if (!t) return;
    const s = splitter.getBoundingClientRect();
    grab = stacked() ? t.clientY - s.top : t.clientX - s.left;
    splitter.classList.add("dragging");
    el("chart").style.pointerEvents = "none";   // as the mouse path does
    e.preventDefault();
  }, { passive: false });
  splitter.addEventListener("touchmove", (e) => {
    const t = e.touches[0];
    if (!t) return;
    const r = main.getBoundingClientRect();
    const off = grab === null ? 0 : grab;
    if (stacked()) setChatHeight(r.bottom - (t.clientY - off) - splitter.offsetHeight);
    else setChatWidth(r.right - (t.clientX - off) - splitter.offsetWidth);
    e.preventDefault();
  }, { passive: false });
  const endTouch = () => {
    if (grab === null) return;
    grab = null;
    splitter.classList.remove("dragging");
    el("chart").style.pointerEvents = "";
  };
  splitter.addEventListener("touchend", endTouch);
  splitter.addEventListener("touchcancel", endTouch);
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    splitter.classList.remove("dragging");
    document.body.style.cursor = "";
    el("chart").style.pointerEvents = "";
  });
  // Reset. setChatWidth is a no-op when the shell is a COLUMN — it restores
  // the saved height instead — so a stacked layout has to name its own
  // default, or the divider's one documented gesture does nothing on the
  // only layout where the divider is a visible control.
  splitter.addEventListener("dblclick", () => {
    if (stacked()) setChatHeight(main.clientHeight * 0.46);
    else setChatWidth(main.clientWidth * DEF_W);
    peekSplit();   // the jump is instant; the number is what says it landed
  });

  /* ── the same control, from the keyboard ──────────────────────────────
   * The divider is a real `separator`, so it takes the keys one takes. A
   * drag is a gesture not everyone can hold, and until now it was the only
   * way to size the conversation at all: the arrows are that same control at
   * 16px a press (64 with Shift), Home/End run it to its two stops, and
   * Enter is the double-click's reset. The direction follows the axis — on a
   * stacked shell the divider drags height, so up/down is what it answers.
   * setChatWidth is a no-op when stacked (it restores the saved height), so
   * each branch calls its own setter rather than trusting that redirect. */
  const NUDGE = 16, NUDGE_BIG = 64, FAR = 1e5;
  splitter.addEventListener("keydown", (e) => {
    const vert = !stacked();
    const grow = vert ? "ArrowLeft" : "ArrowUp";     // more conversation
    const shrink = vert ? "ArrowRight" : "ArrowDown";  // more chart
    const cur = vert ? panel.offsetWidth : panel.offsetHeight;
    const step = e.shiftKey ? NUDGE_BIG : NUDGE;
    let next;
    if (e.key === grow) next = cur + step;
    else if (e.key === shrink) next = cur - step;
    else if (e.key === "Home") next = FAR;           // clamped to the chart's floor
    else if (e.key === "End") next = 0;              // clamped to the chat's own
    else if (e.key === "Enter" || e.key === " ") {
      next = (vert ? main.clientWidth * DEF_W : main.clientHeight * 0.46);
    } else return;
    e.preventDefault();
    if (vert) setChatWidth(next); else setChatHeight(next);
    peekSplit();
  });

  const chatToggle = el("chatToggle");
  chatToggle.addEventListener("click", () => {
    const hidden = panel.classList.toggle("hidden");
    splitter.classList.toggle("hidden", hidden);
    chatToggle.classList.toggle("on", !hidden);
  });
  chatToggle.classList.add("on");

  /* The three things a saved LAYOUT needs from the conversation: which one is
   * open (it is stored with the layout), and the two ways of changing that.
   * Deliberately thin — a layout does not own the thread, it remembers which
   * thread it was had in, and reuses the same two functions the history
   * panel drives so there is one path in and out of a conversation. */
  window.Chat = {
    activeId: () => activeId,
    newChat: newConversation,
    openChat: openConversation,
    /* Ask something from elsewhere in the app — the chart's context menu is
     * the first caller.
     *
     * It goes through the COMPOSER, not through send(text): send's string
     * argument is the retry path, which deliberately drops the pending
     * attachments (see there). A question about a drawing tags the drawing
     * first and then asks, so dropping them would send "is D3 any good?"
     * with no D3 attached — the exact guessing the ref exists to end.
     *
     * Setting the value and calling send() with no argument is what the
     * user's own Enter does, so there is one send path, and the question is
     * visible in the box for the instant before it goes. */
    ask(text) {
      const q = String(text || "").trim();
      if (!q) return;
      if (panel.classList.contains("hidden")) chatToggle.click();
      input.value = q;
      autoGrow();
      send();
    },
    /* Put a question in the box and STOP. `ask` sends; this one hands the
     * user a draft to edit first.
     *
     * The difference matters for where it is called from: the Strategies page
     * "Edit with chat" arrives as a page LOAD, and a page load that spends an
     * LLM turn before the reader has read anything is a side effect nobody
     * asked for. Composing costs nothing and the send is still one Enter. */
    compose(text) {
      const q = String(text || "").trim();
      if (!q) return;
      if (panel.classList.contains("hidden")) chatToggle.click();
      input.value = q;
      autoGrow();
      input.focus();
      try { input.setSelectionRange(q.length, q.length); } catch { }
    },
  };

  /* ?ask= — the way the portfolio and strategy pages come back to the chart.
   *
   * They are a different app on the same origin, so they cannot call into this
   * one; a query parameter is the whole interface, and it lands as a draft
   * rather than a sent turn for the reason above. Stripped from the address
   * bar once read, so a refresh does not re-open it. */
  try {
    const q = new URLSearchParams(location.search).get("ask");
    if (q) {
      window.Chat.compose(q);
      const url = new URL(location.href);
      url.searchParams.delete("ask");
      history.replaceState(null, "", url.pathname + url.search + url.hash);
    }
  } catch { }
})();
