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
  const API = "http://127.0.0.1:5174";
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
    const on = d.pane && d.pane !== "price" ? ` · on ${d.pane}` : "";
    row.innerHTML = `<span class="draw-tag"><span class="ref">${d.ref}</span>`
      + `${d.label.toLowerCase()}${on}`
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

  /** Fill an assistant turn with the final answer + its provenance footer. */
  function finishTurn(turn, text, bits) {
    turn.querySelector(".prose").innerHTML = md(text);
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
      finishTurn(addAssistantTurn(), t.content, t.meta || []);
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
      // snapshot the chart at send time — what you were looking at when you asked
      const context = ctxOn && window.__charto ? window.__charto.getChartContext() : null;
      const res = await fetch(`${API}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: wireHistory(), context, stream: true }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await readStream(res, turn);
      if (d.error) throw new Error(d.error);
      lastBlock = d.context_preview || "(no chart context sent)";

      // apply anything the model chose to draw
      if (d.scene_patch && d.scene_patch.length && window.__charto) {
        window.__charto.scene.apply(d.scene_patch);
      }

      const secs = ((performance.now() - t0) / 1000).toFixed(1);
      const u = d.usage || {};
      const tools = (d.tools_used || []).map((t) => t.name + (t.ok ? "" : " (failed)"));
      const meta = [
        `${secs}s`,
        u.input_tokens != null ? `${u.input_tokens.toLocaleString()} in / ${(u.output_tokens ?? 0).toLocaleString()} out` : null,
        tools.length ? `computed via ${[...new Set(tools)].join(", ")}` : null,
      ].filter(Boolean);
      turns.push({ role: "assistant", content: d.text, meta });
      saveTurns();
      finishTurn(turn, d.text, meta);
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
  const pinRow = el("pinRow");
  document.addEventListener("charto:pins", (e) => {
    pinRow.innerHTML = e.detail.map((p) => {
      const t = new Date(p.time * 1000);
      const label = `${t.getUTCDate()} ${["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][t.getUTCMonth()]} `
        + `${String(t.getUTCHours()).padStart(2, "0")}:${String(t.getUTCMinutes()).padStart(2, "0")}`;
      return `<span class="pin"><span class="t">${label}</span>`
        + `${Number(p.close).toLocaleString("en-IN", { minimumFractionDigits: 2 })}`
        + `<span class="x" data-unpin="${p.time}">${Icons.svg("x", "xs")}</span></span>`;
    }).join("");
    if (e.detail.length) input.focus();
  });
  pinRow.addEventListener("click", (e) => {
    const t = e.target.closest("[data-unpin]")?.dataset.unpin;
    if (t) document.dispatchEvent(new CustomEvent("charto:unpin", { detail: Number(t) }));
  });

  // "Ask about this" on a provenance card seeds the composer, never sends
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

  function paintCtxFlag() {
    ctxFlag.classList.toggle("off", !ctxOn);
    ctxFlag.innerHTML = `<span class="dot"></span>${ctxOn ? "sees chart" : "chart hidden"}`;
    ctxFlag.title = ctxOn
      ? "The visible chart is attached to each message — click to detach"
      : "The model gets no chart state — click to attach";
  }

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
  ctxFlag.addEventListener("click", () => { ctxOn = !ctxOn; paintCtxFlag(); });
  el("ctxPeekClose").addEventListener("click", () => el("ctxPeek").classList.remove("open"));
  paintCtxFlag();

  // ── resizable split: chart | chat ─────────────────────
  const splitter = el("splitter"), main = document.querySelector(".main");
  const WKEY = "charto_chat_width";
  const MIN_CHAT = 340, MIN_CHART = 420;

  function setChatWidth(px, persist = true) {
    const total = main.clientWidth;
    const w = Math.round(Math.max(MIN_CHAT, Math.min(px, total - MIN_CHART)));
    panel.style.width = w + "px";
    if (persist) localStorage.setItem(WKEY, String(w));
  }
  requestAnimationFrame(() => {
    const saved = parseInt(localStorage.getItem(WKEY) || "0", 10);
    setChatWidth(saved || main.clientWidth * 0.44, false);
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
    if (dragging) setChatWidth(main.getBoundingClientRect().right - e.clientX);
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    splitter.classList.remove("dragging");
    document.body.style.cursor = "";
    el("chart").style.pointerEvents = "";
  });
  splitter.addEventListener("dblclick", () => setChatWidth(main.clientWidth * 0.44));

  const chatToggle = el("chatToggle");
  chatToggle.addEventListener("click", () => {
    const hidden = panel.classList.toggle("hidden");
    splitter.classList.toggle("hidden", hidden);
    chatToggle.classList.toggle("on", !hidden);
  });
  chatToggle.classList.add("on");
})();
