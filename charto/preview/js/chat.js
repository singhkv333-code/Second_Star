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
  //
  // Mutated in PLACE, never rebound: every closure below holds this one array,
  // so opening a past conversation refills it rather than replacing it.
  const turns = [];   // [{role, content, ts?, image?, drawing?, meta?, acts?}]
  const wireHistory = () => turns.map((t) => ({
    role: t.role, content: t.content,
    ...(t.image ? { image: t.image } : {}),
    ...(t.drawing ? { drawing: t.drawing } : {}),
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

  const saveTurns = () => {
    const rec = active();
    rec.turns = turns.slice(-KEEP_TURNS);
    rec.updated = Date.now();
    persistChats();
  };

  // Write the archive back at boot rather than waiting for the next message.
  // A conversation migrated off the old single-thread key exists only in
  // memory until something is saved, and the old key has just been deleted —
  // a reload before you next spoke would have lost the thread outright.
  persistChats();
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

  function addUserTurn(text, image, drawing, ts) {
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

    const clock = setInterval(() => {
      secs.textContent = Math.max(0, Math.round((performance.now() - t0) / 1000)) + "s";
    }, 250);
    const walk = setInterval(() => {
      if (cursor >= STEP_SCRIPT.length) return;   // hold on the last line
      push(STEP_SCRIPT[cursor++]);
    }, 2600);

    return {
      /** A tool landed. Once per tool: a turn that reads three intervals of
       *  bars did one kind of work, not three. */
      tool(name) {
        if (seen.has(name)) return;
        seen.add(name);
        push(toolStep(name));
      },
      /** The first token of the answer arrived. */
      writing() { push({ word: "Writing", detail: "the answer" }); },
      stop() { clearInterval(clock); clearInterval(walk); host.remove(); },
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
        if (ev.type === "delta") {
          if (!text && turn.__wait) turn.__wait.writing();
          text += ev.text;
          paint();
        } else if (ev.type === "tool") {
          tools.push(ev.name);
          // a landed tool is the only progress signal a multi-round turn has —
          // it becomes its own line on the wait's timeline
          if (turn.__wait) turn.__wait.tool(ev.name);
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

  /* Provenance the footer no longer carries. The token count was a number
   * about the BILL, not about the answer — it sat in the same row as the
   * latency and the chart actions, both of which say something about what
   * you just read, and it never did. New turns stop recording it (see
   * send()); a conversation saved before that still has the string in its
   * meta, so it is dropped on the way to the screen rather than left to
   * reappear on every reload of an old thread. */
  const TOKENS_RE = /^[\d,]+\s*in\s*\/\s*[\d,]+\s*out$/i;

  /** Fill an assistant turn with the final answer + its provenance footer. */
  function finishTurn(turn, text, bits, acts) {
    endWait(turn);
    const prose = turn.querySelector(".prose");
    prose.innerHTML = md(text);
    linkCompanies(prose);
    const meta = document.createElement("div");
    meta.className = "turn-meta";
    const copy = copyBtn(text, "Copy reply");
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
      turn.appendChild(meta);
      turn.appendChild(list);
      toBottom();
      return;
    }
    turn.appendChild(meta);
    toBottom();
  }

  /* ── three things worth asking next ───────────────────────────────────
   *
   * Fetched AFTER the turn is finished and painted, in a request of its own,
   * so a slow or dead suggest costs the answer nothing — the row simply never
   * appears. That is also why the backend answers 200 with an empty list
   * rather than an error: there is no failure here for the client to handle.
   *
   * Only the NEWEST turn carries a row. Leaving them under older replies
   * would offer questions the conversation has already moved past, and stack
   * a control every few hundred pixels down a thread you are trying to read.
   */
  function clearSuggest() {
    msgsEl.querySelectorAll(".suggest").forEach((n) => n.remove());
  }

  /** The server's suggest_clean, verbatim. Both ends apply it — this one to
   *  the half-written line on screen, that one to the final list — and if
   *  they disagreed every suggestion would twitch as the stream settled. */
  const cleanSuggest = (s) => s
    .replace(/^\s*(?:[-*•]|\d+[.)])\s*/, "")
    .trim().replace(/^["']|["']$/g, "").trim();

  async function suggestAfter(turn) {
    clearSuggest();
    const box = document.createElement("div");
    box.className = "suggest";
    turn.appendChild(box);
    // Stale before it was ever shown: by now the user may have asked
    // something else, switched chats, or cleared the thread.
    const dead = () => !turn.isConnected || !box.isConnected || pending;
    const rowAt = (i) => {
      while (box.children.length <= i) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "suggest-row";
        b.addEventListener("click", () => {
          const q = b.textContent.trim();
          if (q) { clearSuggest(); send(q); }
        });
        box.appendChild(b);
      }
      return box.children[i];
    };
    const paint = (lines) => {
      lines.slice(0, 3).forEach((q, i) => {
        const b = rowAt(i);
        if (b.textContent !== q) { b.textContent = q; b.title = q; }
      });
    };

    let acc = "";
    try {
      const r = await fetch(`${API}/suggest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: wireHistory() }),
      });
      if (!r.ok || !r.body) { box.remove(); return; }
      const rd = r.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await rd.read();
        if (done) break;
        if (dead()) { await rd.cancel(); box.remove(); return; }
        buf += dec.decode(value, { stream: true });
        // SSE frames are blank-line separated; a partial one waits its turn
        const frames = buf.split("\n\n");
        buf = frames.pop();
        for (const f of frames) {
          const line = f.split("\n").find((l) => l.startsWith("data:"));
          if (!line) continue;
          let ev;
          try { ev = JSON.parse(line.slice(5).trim()); } catch { continue; }
          if (ev.type === "delta") {
            acc += ev.text || "";
            paint(acc.split("\n").map(cleanSuggest).filter(Boolean));
            toBottom();
          } else if (ev.type === "done") {
            const picks = ev.suggestions || [];
            if (picks.length !== 3 || dead()) { box.remove(); return; }
            paint(picks);
            while (box.children.length > 3) box.lastChild.remove();
            toBottom(true);
            return;
          }
        }
      }
      box.remove();            // stream ended without a `done` — say nothing
    } catch {
      box.remove();
    }
  }

  function failTurn(turn, msg) {
    endWait(turn);
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

  const EMPTY_HTML = '<div class="chat-empty">Ask about what you\'re looking at.'
    + '<br/><b>The model sees the visible chart.</b></div>';

  /** Paint `turns` from scratch. Same builders as a live turn, so a reloaded
   *  conversation — or one reopened from the history — is pixel-identical to
   *  the one that just happened. */
  function renderThread() {
    msgsEl.innerHTML = "";
    if (!turns.length) {
      msgsEl.innerHTML = EMPTY_HTML;
      el("toBottom").classList.remove("show");
      return;
    }
    for (const t of turns) {
      if (t.role === "user") { addUserTurn(t.content, t.image, t.drawing, t.ts); continue; }
      finishTurn(addAssistantTurn(false), t.content, t.meta || [], t.acts || []);
    }
    toBottom();
  }
  if (turns.length) renderThread();

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
    if (!retry) {
      setAttachment(null);
      setDrawTag(null);
      input.value = "";
      autoGrow();
    }
    pending = true;
    sendBtn.disabled = true;

    const ts = Date.now();
    turns.push({ role: "user", content: text, ts,
                 ...(image ? { image } : {}), ...(drawing ? { drawing } : {}) });
    addUserTurn(text, image, drawing, ts);
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
        headers: { "Content-Type": "application/json" },
        // chat_id is what lets recall_conversations EXCLUDE this conversation
        // from a search of the earlier ones — its turns are already in
        // `messages`, and finding them twice would read as two occasions.
        body: JSON.stringify({ messages: wireHistory(), context, stream: true,
                               chat_id: activeId }),
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

      // How long it took, and nothing else. The token count used to ride
      // here too; it is a fact about the bill rather than about the answer,
      // and the row it sat in is otherwise entirely about what you just read.
      const secs = ((performance.now() - t0) / 1000).toFixed(1);
      const meta = [`${secs}s`];
      const acts = chartActions(d.scene_patch);
      turns.push({ role: "assistant", content: d.text, meta, acts });
      saveTurns();
      finishTurn(turn, d.text, meta, acts);
      suggestAfter(turn);    // deliberately not awaited — the turn is done
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
    const n = chats.filter((c) => turnsOf(c).length).length;
    plusMenu.innerHTML = [
      `<div class="item" data-act="new"><span class="lead">${Icons.svg("plus", "sm")}New conversation</span></div>`,
      `<div class="item" data-act="history"><span class="lead">${Icons.svg("clock", "sm")}Chat history</span>`,
      n ? `<span class="menu-count">${n}</span>` : "",
      `</div>`,
      `<div class="sep"></div>`,
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
      const sub = [
        c.id === activeId ? "Current" : "",
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

  /** Erase what is in the thread. The conversation's own record goes with it —
   *  "clear" has always meant gone, and leaving it in the history under a
   *  title you just erased would be the opposite of what the word says. */
  function clearConversation() {
    if (pending) return;
    turns.length = 0;
    const rec = active();
    rec.turns = [];
    rec.updated = Date.now();
    persistChats();
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
    if (act === "new") newConversation();
    if (act === "history") openHistory();
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

  /* The three things a saved LAYOUT needs from the conversation: which one is
   * open (it is stored with the layout), and the two ways of changing that.
   * Deliberately thin — a layout does not own the thread, it remembers which
   * thread it was had in, and reuses the same two functions the history
   * panel drives so there is one path in and out of a conversation. */
  window.Chat = {
    activeId: () => activeId,
    newChat: newConversation,
    openChat: openConversation,
  };
})();
