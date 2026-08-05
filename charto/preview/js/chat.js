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
  const API = ["localhost", "127.0.0.1"].includes(location.hostname)
    ? "http://127.0.0.1:5174" : "";
  const el = (id) => document.getElementById(id);
  const msgsEl = el("chatMsgs"), threadEl = el("thread"), input = el("chatInput"),
        sendBtn = el("chatSend"), panel = el("chatPanel");

  // One source for the thread: the wire payload maps out of this, and this is
  // what persists — so a restored conversation and a live one can't diverge.
  // `meta` is display-only (latency, tokens, tools) and never reaches the model.
  const turns = Store.get("chat", []);   // [{role, content, image?, meta?}]
  const wireHistory = () => turns.map((t) => ({
    role: t.role, content: t.content,
    ...(t.image ? { image: t.image } : {}),
    ...(t.drawing ? { drawing: t.drawing } : {}),
  }));
  // Persist a bounded tail. A long session of table-heavy replies would
  // otherwise walk into the ~5MB localStorage ceiling, and Store.set swallows
  // quota errors — so it would stop saving silently rather than loudly.
  // Screenshots are the heavy part: persist only the NEWEST one (the same
  // policy the server applies to what the model sees).
  const KEEP_TURNS = 60;
  const saveTurns = () => {
    const tail = turns.slice(-KEEP_TURNS);
    const lastImg = tail.map((t) => !!t.image).lastIndexOf(true);
    Store.set("chat", tail.map((t, i) =>
      t.image && i !== lastImg ? { ...t, image: undefined } : t));
  };
  let pending = false;
  let ctxOn = true;     // chart-state envelope attached to each message
  let lastBlock = "";   // what the model was actually told, for the inspector
  let pendingImage = null;   // a captured screenshot waiting to ride the next send
  let pendingDraw = null;    // the drawing this message is about, by ref

  // ── drawing tag ───────────────────────────────────────
  // Selecting a shape offers it as the subject of the next question, the same
  // way pinning a candle does. The message then carries the drawing's REF, so
  // the tools resolve exact geometry instead of the model guessing which
  // shape "this" meant and retyping its coordinates.
  function setDrawTag(d) {
    pendingDraw = d;
    const row = el("drawTagRow");
    row.style.display = d ? "" : "none";
    if (!d) return;
    // A candle pin shows a candle. A drawing pin shows the DRAWING — its own
    // geometry in its own ink — because that is what makes it recognisable at
    // a glance, and it is the thing you just clicked on. The previous version
    // spelled out kind, span and touch count and read as a paragraph; a
    // picture and a name say it faster and take one line instead of two.
    const c = d.color || "currentColor";
    const S = (inner) => `<svg class="dg" viewBox="0 0 20 14" width="20"`
      + ` height="14" fill="none" stroke="${c}" stroke-width="1.6"`
      + ` stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;
    const GLYPH = {
      segment: () => S('<path d="M2 12L18 3"/>'),
      level: () => S('<path d="M2 7h16" stroke-dasharray="3 2.5"/>'),
      zone: () => S(`<rect x="2" y="4" width="16" height="6" fill="${c}"`
        + ` fill-opacity=".18" stroke-dasharray="3 2.5"/>`),
      box: () => S(`<rect x="2.5" y="3" width="15" height="8" fill="${c}" fill-opacity=".18"/>`),
      poly: () => S('<path d="M2 11l4-6 4 4 4-7 4 5"/>'),
      fib: () => S('<path d="M2 3h16M2 7h16M2 11h16" stroke-dasharray="3 2.5"/>'),
      vline: () => S('<path d="M10 2v10" stroke-dasharray="3 2.5"/>'),
      point: () => S(`<circle cx="10" cy="7" r="3.2" fill="${c}" fill-opacity=".3"/>`),
      vprofile: () => S('<path d="M3 3h7M3 7h13M3 11h5"/>'),
      position: () => S(`<rect x="2.5" y="3" width="15" height="8" fill="${c}"`
        + ` fill-opacity=".14"/><path d="M2.5 7h15"/>`),
    };
    const glyph = (GLYPH[d.kind] || GLYPH.segment)();
    row.innerHTML = `<span class="pins-lead">${Icons.svg("pin", "xs")}`
      + `1 drawing pinned — sent with your next message</span>`
      + `<span class="pin draw"><span class="dg-wrap">${glyph}</span>`
      + `<span class="pin-txt"><span class="pin-top">${d.label}</span></span>`
      + `<span class="x" data-untag="1" title="Don't ask about this">`
      + `${Icons.svg("x", "xs")}</span></span>`;
  }
  // Only an explicit "Ask in chat" on the drawing's card tags it — selecting
  // a shape to drag or edit must never attach it to the conversation.
  document.addEventListener("charto:draw-tag", (e) => {
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
    let rows = [], para = [], code = null, tbl = [];

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
      if (!t) { flushPara(); flushList(); flushTable(); continue; }

      // pipe table — buffered whole, because the alignment row decides the shape
      if (TABLE_ROW(t)) { flushPara(); flushList(); tbl.push(t); continue; }
      flushTable();   // any other line closes an open table

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
    flushPara(); flushList(); flushTable();
    return out.join("");
  }

  window.__chartoMd = md;   // sandbox debug hook, same spirit as window.__charto

  // ── thread ────────────────────────────────────────────
  function clearEmpty() {
    const e = msgsEl.querySelector(".chat-empty");
    if (e) e.remove();
  }

  function addUserTurn(text, image, drawing) {
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
      tg.textContent = `${drawing.ref} · ${String(drawing.label).toLowerCase()}`
        + (drawing.pane && drawing.pane !== "price" ? ` on ${drawing.pane}` : "");
      b.appendChild(tg);
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
    msgsEl.appendChild(turn);
    toBottom();
    return turn;
  }

  function addAssistantTurn() {
    clearEmpty();
    const turn = document.createElement("div");
    turn.className = "turn assistant";
    turn.innerHTML =
      '<div class="prose"><div class="thinking"><span class="pulse"></span>Thinking…</div></div>';
    msgsEl.appendChild(turn);
    toBottom();
    return turn;
  }

  /** Consume the SSE turn, painting the answer as it arrives.
   *
   *  Markdown is re-rendered on every flush rather than appended as text: a
   *  half-written table or list is still valid markdown, and re-rendering the
   *  whole buffer is cheap next to the model's own pace. Flushing is rAF-gated
   *  so a fast stream cannot re-parse the buffer hundreds of times a second.
   */
  async function readStream(res, turn) {
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

    const flush = () => {
      raf = 0;
      if (!text) return;
      const wasAtBottom = atBottom();
      prose.innerHTML = md(tidy(text)) + '<span class="caret"></span>';
      if (wasAtBottom) toBottom();
    };
    const paint = () => {
      if (!raf) raf = requestAnimationFrame(flush);
    };

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
        if (ev.type === "delta") { text += ev.text; paint(); }
        else if (ev.type === "tool") {
          tools.push(ev.name);
          // before the first token there is nothing else to show — say what
          // is actually happening rather than an indefinite "Thinking…"
          if (!text) {
            const t = turn.querySelector(".thinking");
            if (t) t.innerHTML = `<span class="pulse"></span>${[...new Set(tools)].join(" · ")}`;
          }
        } else if (ev.type === "done") { done = ev; }
      }
    }
    // Cancel any queued repaint. Without this the last frame lands AFTER
    // finishTurn has written the final markdown and puts the caret back on a
    // reply that is already complete.
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    if (!done) throw new Error("stream ended without a result");
    // the streamed text is the source of truth; `done.text` is the same string
    done.text = done.text || text;
    return done;
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
      const word = SHAPE_WORD[a.kind] || "Drawing";
      // a level's own label is the price, which is more use than the word
      out.push(a.label ? `${word} · ${a.label}` : word);
    }
    return out;
  }

  /** Fill an assistant turn with the final answer + its provenance footer. */
  function finishTurn(turn, text, bits, acts) {
    const prose = turn.querySelector(".prose");
    prose.innerHTML = md(text);
    linkCompanies(prose);
    const meta = document.createElement("div");
    meta.className = "turn-meta";
    const copy = document.createElement("button");
    copy.className = "copy"; copy.title = "Copy reply";
    copy.innerHTML = Icons.svg("copy", "xs");
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(text);
        copy.innerHTML = Icons.svg("check", "xs");
        setTimeout(() => { copy.innerHTML = Icons.svg("copy", "xs"); }, 1200);
      } catch { /* clipboard blocked — nothing useful to say */ }
    });
    const label = document.createElement("span");
    label.textContent = bits.filter(Boolean).join("  ·  ");
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
      turn.appendChild(meta);
      turn.appendChild(list);
      toBottom();
      return;
    }
    turn.appendChild(meta);
    toBottom();
  }

  function failTurn(turn, msg) {
    turn.classList.add("error");
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

  /** Repaint a persisted thread. Same builders as a live turn, so a reloaded
   *  conversation is pixel-identical to the one that just happened. */
  (function restoreThread() {
    if (!turns.length) return;
    for (const t of turns) {
      if (t.role === "user") { addUserTurn(t.content, t.image, t.drawing); continue; }
      finishTurn(addAssistantTurn(), t.content, t.meta || [], t.acts || []);
    }
    toBottom();
  })();

  // ── send ──────────────────────────────────────────────
  async function send() {
    const text = input.value.trim();
    if ((!text && !pendingImage) || pending) return;
    const image = pendingImage;
    const drawing = pendingDraw;
    setAttachment(null);
    setDrawTag(null);
    input.value = "";
    autoGrow();
    pending = true;
    sendBtn.disabled = true;

    turns.push({ role: "user", content: text,
                 ...(image ? { image } : {}), ...(drawing ? { drawing } : {}) });
    addUserTurn(text, image, drawing);
    const turn = addAssistantTurn();
    const t0 = performance.now();

    try {
      // Snapshot the charts at send time — what you were looking at when you
      // asked. The chip names panes, so the envelope is built from those panes
      // rather than from whatever happens to be selected now, and what came
      // back is recorded: a layout change can retire a chosen pane, and the
      // fallback has to be visible rather than silent.
      const context = ctxOn && window.__charto
        ? window.__charto.getChartContext(chosen) : null;
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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: wireHistory(), context, stream: true }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await readStream(res, turn);
      if (d.error) throw new Error(d.error);
      lastBlock = d.context_preview || "(no chart context sent)";

      // Move the workspace BEFORE drawing on it: a scene op can be aimed at a
      // chart this same turn opened, and applying the patch first would draw
      // it onto whatever pane happened to be there.
      if (d.view_ops && d.view_ops.length && window.__charto?.panes) {
        for (const op of d.view_ops) {
          if (op.kind !== "open_chart") continue;
          try {
            window.__charto.panes.openChart(op.symbol, op.interval, op.replace);
          } catch (e) {
            console.warn("[charto] open_chart failed", op, e);
          }
        }
      }

      // apply anything the model chose to draw
      if (d.scene_patch && d.scene_patch.length && window.__charto) {
        window.__charto.scene.apply(d.scene_patch);
      }

      const secs = ((performance.now() - t0) / 1000).toFixed(1);
      const u = d.usage || {};
      const meta = [
        `${secs}s`,
        u.input_tokens != null ? `${u.input_tokens.toLocaleString()} in / ${(u.output_tokens ?? 0).toLocaleString()} out` : null,
      ].filter(Boolean);
      const acts = chartActions(d.scene_patch);
      turns.push({ role: "assistant", content: d.text, meta, acts });
      saveTurns();
      finishTurn(turn, d.text, meta, acts);
    } catch (e) {
      turns.pop();   // keep the thread consistent with what the model saw
      saveTurns();
      failTurn(turn, e.message || String(e));
    } finally {
      pending = false;
      sendBtn.disabled = false;
      input.focus();
    }
  }

  // ── composer ──────────────────────────────────────────
  sendBtn.innerHTML = Icons.svg("arrowUp", "sm");
  el("plusBtn").innerHTML = Icons.svg("plus", "sm");
  el("ctxPeekClose").innerHTML = Icons.svg("x", "sm");

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
    if (n) input.focus();
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

  // Seed the composer from elsewhere in the app — text only, never a send.
  document.addEventListener("charto:compose", (e) => {
    input.value = (input.value ? input.value.replace(/\s*$/, " ") : "") + e.detail;
    input.focus();
    autoGrow();
  });

  el("chatForm").addEventListener("submit", (e) => { e.preventDefault(); send(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
    e.stopPropagation(); // don't let Delete/Escape hit the drawing layer
  });

  // ── the "+" menu: everything the old chat header used to hold ──
  const plusMenu = el("plusMenu"), ctxFlag = el("ctxFlag");

  function renderPlusMenu() {
    plusMenu.innerHTML = [
      `<div class="item ${ctxOn ? "on" : ""}" data-act="ctx">`,
      `<span class="lead">${Icons.svg(ctxOn ? "eye" : "eyeOff", "sm")}Let the model see the chart</span>`,
      ctxOn ? Icons.svg("check", "xs") : "",
      `</div>`,
      `<div class="item" data-act="peek"><span class="lead">${Icons.svg("fileText", "sm")}Inspect context sent</span></div>`,
      `<div class="sep"></div>`,
      `<div class="item danger" data-act="clear"><span class="lead">${Icons.svg("eraser", "sm")}Clear conversation</span></div>`,
    ].join("");
  }

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
    ctxFlag.classList.toggle("off", !ctxOn);
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
    ctxFlag.title = (ctxOn
      ? `The model reads ${list.length > 1 ? "these charts" : "this chart"} — ${names}`
      : "The charts are detached; the model reads none of them")
      + " · click to choose what is in context";
  }

  /** The menu behind the chip: every open chart, ticked or not. Ticking one
   *  puts it in the turn; unticking takes it out. The last one cannot be
   *  removed — a conversation about no chart is the detach switch, which
   *  lives in "+" and says so in its own words. */
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

  function clearConversation() {
    turns.length = 0;
    Store.del("chat");
    msgsEl.innerHTML = '<div class="chat-empty">Cleared.<br/><b>Fresh conversation.</b></div>';
    el("toBottom").classList.remove("show");
  }

  el("plusBtn").addEventListener("click", (e) => {
    e.stopPropagation();
    renderPlusMenu();
    if (window.__chartoCloseMenus) window.__chartoCloseMenus(plusMenu);
    plusMenu.classList.toggle("open");
  });
  plusMenu.addEventListener("click", (e) => {
    const it = e.target.closest("[data-act]");
    if (!it) return;
    e.stopPropagation();
    const act = it.dataset.act;
    if (act === "ctx") { ctxOn = !ctxOn; paintCtxFlag(); renderPlusMenu(); return; }
    plusMenu.classList.remove("open");
    if (act === "peek") {
      el("ctxPeekBody").textContent = lastBlock || "Nothing sent yet — ask something first.";
      el("ctxPeek").classList.add("open");
    }
    if (act === "clear") clearConversation();
  });
  // the chip opens the subject menu; the see-the-chart switch is in "+"
  ctxFlag.addEventListener("click", (e) => { e.stopPropagation(); openSubjectMenu(); });
  el("ctxPeekClose").addEventListener("click", () => el("ctxPeek").classList.remove("open"));
  paintCtxFlag();

  // ── resizable split: chart | chat ─────────────────────
  const splitter = el("splitter"), main = document.querySelector(".main");
  const WKEY = "charto_chat_width";
  const MIN_CHAT = 340, MIN_CHART = 420;

  /* Below the breakpoint the shell is a COLUMN: the chat sits under the
   * chart at full width, and a horizontal split has nothing to divide. The
   * saved desktop width is an inline style, so it beat the stylesheet and
   * pinned a phone's chat panel to 340px with the chart's own width beside
   * it — the split survived the layout that removed it. */
  const stacked = () => window.matchMedia("(max-width: 820px)").matches;

  const HKEY = "charto_chat_height";
  const MIN_CHAT_H = 160, MIN_CHART_H = 200;

  /** Stacked layout: the same divider drags HEIGHT. Kept on its own key so
   *  a phone split and a desktop split do not overwrite each other every
   *  time the window crosses the breakpoint. */
  function setChatHeight(px, persist = true) {
    const total = main.clientHeight;
    const h = Math.round(Math.max(MIN_CHAT_H, Math.min(px, total - MIN_CHART_H)));
    panel.style.height = h + "px";
    if (persist) localStorage.setItem(HKEY, String(h));
  }

  function setChatWidth(px, persist = true) {
    if (stacked()) {
      panel.style.width = "";
      const savedH = parseInt(localStorage.getItem(HKEY) || "0", 10);
      if (savedH) setChatHeight(savedH, false);
      return;
    }
    panel.style.height = "";
    const total = main.clientWidth;
    const w = Math.round(Math.max(MIN_CHAT, Math.min(px, total - MIN_CHART)));
    panel.style.width = w + "px";
    if (persist) localStorage.setItem(WKEY, String(w));
  }
  function applySavedWidth() {
    const saved = parseInt(localStorage.getItem(WKEY) || "0", 10);
    setChatWidth(saved || main.clientWidth * 0.44, false);
  }
  requestAnimationFrame(applySavedWidth);
  // rotating a phone, or dragging a desktop window across the breakpoint,
  // has to re-decide this — the width is only meaningful on one side of it
  window.matchMedia("(max-width: 820px)").addEventListener("change", applySavedWidth);

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
    else setChatWidth(main.clientWidth * 0.44);
  });

  const chatToggle = el("chatToggle");
  chatToggle.addEventListener("click", () => {
    const hidden = panel.classList.toggle("hidden");
    splitter.classList.toggle("hidden", hidden);
    chatToggle.classList.toggle("on", !hidden);
  });
  chatToggle.classList.add("on");
})();
