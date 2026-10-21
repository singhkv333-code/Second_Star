/* Charto preview — plays: the card a play prints in the chat, and the Plays
 * widget (the plays you have, and the inbox of what they did).
 *
 * A play is the execution layer's one object (data/plays.py). Everything
 * drawn here comes off the card the server built from the STORED row: the
 * conditions, the summary sentence, the contract line and the replay count
 * are all generated from the rule's tree on the server, so the card cannot
 * describe a rule other than the one that is armed. Nothing is computed on
 * this side except formatting.
 *
 * Shape borrowed from the two products traders already read this way:
 * Instinct's thesis card (each condition with its live state, then the
 * order, then the exit line) and Catalyst's watch summary and inbox (one
 * plain sentence for the whole rule; an alert says what it saw).
 */
"use strict";

const PlayUI = (() => {
  const API = location.port === "5173" ? "http://127.0.0.1:5174" : "";
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const ic = (n, c = "xs") => (typeof Icons !== "undefined" ? Icons.svg(n, c) : "");

  async function call(path, body) {
    const res = await fetch(API + path, {
      method: body === undefined ? "GET" : "POST",
      headers: Auth.headers(body === undefined ? {} : { "Content-Type": "application/json" }),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = {};
    try { data = await res.json(); } catch { /* empty */ }
    if (!res.ok) throw new Error(data.error || `request failed (${res.status})`);
    return data;
  }

  const num = (v) => {
    if (v == null || !Number.isFinite(+v)) return "";
    const a = Math.abs(+v);
    return (+v).toLocaleString("en-IN", { maximumFractionDigits: a >= 100 ? 2 : a >= 1 ? 3 : 5 });
  };
  const day = (ts) => ts ? new Date(ts * 1000).toLocaleDateString("en-IN",
    { day: "numeric", month: "short", year: "numeric" }) : "";
  const ago = (ts) => {
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return new Date(ts * 1000).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
  };
  const STATE = { armed: "Watching", paused: "Paused", done: "Finished", draft: "Draft" };

  /** A measure's last 60 values as a line. Shape only: no axis, because the
   *  number beside it is the value the reader needs. */
  function spark(vals) {
    const v = (vals || []).filter((x) => x != null && Number.isFinite(+x));
    if (v.length < 2) return "";
    const lo = Math.min(...v), hi = Math.max(...v), w = 88, h = 22;
    const pts = (vals || []).map((x, i) => x == null ? null :
      [i / ((vals.length - 1) || 1) * w, h - 2 - (hi === lo ? .5 : (x - lo) / (hi - lo)) * (h - 4)]);
    let d = "", pen = false;
    for (const p of pts) {
      if (!p) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`;
      pen = true;
    }
    return `<svg class="py-spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true">` +
      `<path d="${d}"/></svg>`;
  }

  function condRow(c) {
    const met = c.met === true;
    let right = "";
    if (met) right = "Met";
    else if (c.away_pct != null) right = `${num(c.away_pct)}% away`;
    else if (c.now != null && c.target != null) right = `now ${num(c.now)}`;
    return `<li class="py-cond${met ? " met" : ""}">` +
      `<span class="py-mark" aria-hidden="true">${met ? ic("check") : ""}</span>` +
      `<span class="py-ctext">${esc(c.text)}</span>` +
      (right ? `<span class="py-cnow">${esc(right)}</span>` : "") + `</li>`;
  }

  /** The widget's row: Catalyst's strategy tile. Name, the sentence, each
   *  condition's state, the contract; the measures and replay open on click. */
  function compact(c) {
    const conds = c.conditions || [];
    const k = c.contract || {};
    return `<div class="py-card compact" data-play="${esc(c.id)}" data-state="${esc(c.state)}">` +
      `<div class="py-head"><h3 class="py-title">${esc(c.title)}</h3>` +
      `<span class="py-state"><i class="py-dot" aria-hidden="true"></i>${esc(STATE[c.state] || c.state)}</span></div>` +
      (c.summary ? `<p class="py-summary">${esc(c.summary)}</p>` : "") +
      (conds.length ? `<ul class="py-conds">${conds.map(condRow).join("")}</ul>` : "") +
      `<p class="py-line">${esc([k.runs, k.acts, k.limit].filter(Boolean).join(" · "))}` +
      (c.fires ? ` · fired ${esc(c.fires)}×` : "") + `</p>` +
      (c.why && c.state !== "armed" ? `<p class="py-why">${esc(c.why)}</p>` : "") +
      `<div class="py-actions">${buttons(c)}<button type="button" class="py-btn" data-py-more>Details</button></div>` +
      `</div>`;
  }

  function html(c) {
    const conds = c.conditions || [];
    const h = c.history || {};
    const measures = (c.measures || []).filter((m) => m.formula || m.spark);
    const did = c.action
      ? `${c.action.side === "buy" ? "Buy" : "Sell"} ${c.action.size} of ${c.action.symbol}`
      : "Tell you";
    const contract = c.contract || {};
    const span = h.bars ? `${h.bars} ${c.interval === "1d" ? "daily" : c.interval} bars since ${day(h.from)}` : "";
    const hist = h.bars == null ? "" : !conds.length ? "" :
      h.fires ? `Would have fired ${h.fires} time${h.fires === 1 ? "" : "s"} in the last ${span}` +
        (h.last_fire ? `, most recently on ${day(h.last_fire)}` : "") + "."
        : `Would not have fired in the last ${span}.`;
    return `<div class="py-card" data-play="${esc(c.id)}" data-state="${esc(c.state)}">` +
      `<div class="py-head"><h3 class="py-title">${esc(c.title)}</h3>` +
      `<span class="py-state"><i class="py-dot" aria-hidden="true"></i>${esc(STATE[c.state] || c.state)}</span></div>` +
      (c.summary ? `<p class="py-summary">${esc(c.summary)}</p>` : "") +
      (conds.length
        ? `<div class="py-sec"><div class="py-label">${c.join === "any" ? "When any of these" : "When all of these"}</div>` +
          `<ul class="py-conds">${conds.map(condRow).join("")}</ul></div>` : "") +
      (measures.length
        ? `<div class="py-sec"><div class="py-label">Measures it computes</div>` +
          measures.map((m) => `<div class="py-measure"><div class="py-mname">${esc(m.title)}` +
            (m.formula && m.formula !== m.title ? `<span class="py-formula">${esc(m.formula)}</span>` : "") +
            `</div>${spark(m.spark)}<span class="py-mnow">${esc(num(m.now)) || "no value"}</span></div>`).join("") +
          `</div>` : "") +
      `<div class="py-sec"><div class="py-label">Then</div>` +
      `<div class="py-then"><span class="py-act">${esc(did)}</span>` +
      ((c.exits || []).length ? `<span class="py-exits">${esc(c.exits.join(" · "))}</span>` : "") +
      `</div></div>` +
      `<dl class="py-contract">` +
      `<div><dt>Runs</dt><dd>${esc(contract.runs || "")}</dd></div>` +
      `<div><dt>Acts</dt><dd>${esc(contract.acts || "")}</dd></div>` +
      (contract.limit ? `<div><dt>Limit</dt><dd>${esc(contract.limit)}</dd></div>` : "") +
      `</dl>` +
      (hist ? `<p class="py-hist">${esc(hist)}</p>` : "") +
      ((c.notes || []).length ? `<ul class="py-notes">${c.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : "") +
      (c.why && c.state !== "armed" ? `<p class="py-why">${esc(c.why)}</p>` : "") +
      `<div class="py-actions">${buttons(c)}</div>` +
      `<p class="py-paper">Orders go to the paper book. No real order is placed.</p>` +
      `</div>`;
  }

  function buttons(c) {
    const b = (act, label) => `<button type="button" class="py-btn" data-py="${act}">${label}</button>`;
    if (c.state === "armed") return b("pause", "Pause") + b("delete", "Delete");
    if (c.state === "paused") return b("resume", "Resume") + b("delete", "Delete");
    return b("delete", "Delete");
  }

  /** Card buttons: one POST, and the card repaints from what came back. */
  function wire(box, c, after) {
    box.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-py]");
      if (!btn) return;
      const act = btn.dataset.py;
      if (act === "delete" && !btn.dataset.sure) {
        btn.dataset.sure = "1";
        btn.textContent = "Delete for good";
        return;
      }
      btn.disabled = true;
      try {
        const got = await call(`/plays/${c.id}`, { action: act });
        if (got.deleted) {
          box.innerHTML = `<p class="py-gone">Deleted. It will not fire again.</p>`;
        } else if (got.play) {
          Object.assign(c, got.play);
          box.innerHTML = html(c);
        }
        window.dispatchEvent(new CustomEvent("pivot:plays-changed"));
        if (after) after(got);
      } catch (err) {
        btn.disabled = false;
        toast(err.message);
      }
    });
  }

  /** The app's one toast element (layouts.js owns it), as alerts.js uses it. */
  function toast(msg) {
    let t = document.getElementById("layoutToast");
    if (!t) {
      t = document.createElement("div");
      t.id = "layoutToast";
      t.className = "layout-toast";
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => t.classList.remove("show"), 3200);
  }

  /* ── the inbox ─────────────────────────────────────────────────────────── */
  const KIND = { fired: "Triggered", proposed: "Waiting for you", filled: "Placed on paper",
                 rejected: "Not placed", exited: "Exited", note: "Note" };

  function seenLine(rows) {
    return (rows || []).map((r) => {
      const v = r.left != null ? ` (${num(r.left)}${r.right != null ? ` vs ${num(r.right)}` : ""})` : "";
      return `<li>${esc(r.text)}${esc(v)}</li>`;
    }).join("");
  }

  function eventHTML(ev) {
    const b = ev.body || {};
    const t = b.ticket;
    const ticket = t ? `<div class="py-ticket"><span>${t.side === "BUY" ? "Buy" : "Sell"} ${esc(num(t.qty))} ` +
      `${esc(t.symbol)}</span><span>at about ${esc(num(t.price))}</span>` +
      (t.stop_loss ? `<span>stop ${esc(num(t.stop_loss))}</span>` : "") +
      (t.take_profit ? `<span>target ${esc(num(t.take_profit))}</span>` : "") + `</div>` : "";
    const open = ev.kind === "proposed" && ev.status === "open";
    return `<article class="py-ev" data-ev="${esc(ev.id)}" data-kind="${esc(ev.kind)}">` +
      `<header><span class="py-evk">${ic(ev.kind === "proposed" ? "clock" : ev.kind === "filled" ? "check" : "bell")}` +
      `${esc(KIND[ev.kind] || ev.kind)}</span><time>${esc(ago(ev.created))}</time></header>` +
      `<div class="py-evt">${esc(ev.play || ev.title)}</div>` +
      (b.seen && b.seen.length ? `<ul class="py-seen">${seenLine(b.seen)}</ul>` : "") +
      ticket +
      (b.fill_price ? `<p class="py-evline">Filled at ${esc(num(b.fill_price))}.</p>` : "") +
      (b.reason ? `<p class="py-evline">${esc(cap(b.reason))}.</p>` : "") +
      (open ? `<div class="py-actions"><button type="button" class="py-btn primary" data-evact="confirm">Confirm on paper</button>` +
              `<button type="button" class="py-btn" data-evact="dismiss">Dismiss</button></div>` :
       ev.kind === "proposed" ? `<p class="py-evline">${ev.status === "confirmed" ? "Confirmed." : "Dismissed."}</p>` : "") +
      `</article>`;
  }
  const cap = (s) => String(s || "").charAt(0).toUpperCase() + String(s || "").slice(1);

  function wireInbox(root, refresh) {
    root.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-evact]");
      if (!btn) return;
      const art = btn.closest("[data-ev]");
      btn.disabled = true;
      try {
        await call(`/plays/events/${art.dataset.ev}`, { action: btn.dataset.evact });
        refresh();
      } catch (err) {
        btn.disabled = false;
        toast(err.message);
      }
    });
  }

  /* ── the widget ────────────────────────────────────────────────────────── */
  function mount(host) {
    let tab = "plays", data = null, seq = 0;
    host.innerHTML =
      `<div class="py-w-head"><div class="dk-seg py-tabs">` +
      `<button type="button" data-tab="plays">Plays</button>` +
      `<button type="button" data-tab="inbox">Inbox</button></div>` +
      `<span class="sh-gap"></span>` +
      `<button type="button" class="sh-btn i" data-py-refresh title="Refresh">${ic("rotateCw")}</button></div>` +
      `<div class="side-body py-w-body"></div>`;
    const body = host.querySelector(".py-w-body");

    function paintTabs() {
      host.querySelectorAll("[data-tab]").forEach((b) => {
        b.classList.toggle("on", b.dataset.tab === tab);
        if (b.dataset.tab === "inbox") {
          const n = data ? data.events.filter((e) => e.kind === "proposed" && e.status === "open").length : 0;
          b.textContent = n ? `Inbox ${n}` : "Inbox";
        }
      });
    }

    function paint() {
      paintTabs();
      if (!Auth.user) {
        body.innerHTML = `<div class="side-empty"><p>Sign in to build plays. They run on the server, so they fire with this tab closed.</p></div>`;
        return;
      }
      if (!data) return;
      if (tab === "plays") {
        if (!data.plays.length) {
          body.innerHTML = `<div class="side-empty"><p>No plays yet. Ask the chat for one, for example "tell me when HDFC Bank crosses its 50-day average" or "buy ₹10,000 of TCS if it closes above 4,200".</p></div>`;
          return;
        }
        body.innerHTML = "";
        for (const c of data.plays) {
          const box = document.createElement("div");
          box.className = "py-w-item";
          box.innerHTML = compact(c);
          box.addEventListener("click", (e) => {
            if (e.target.closest("[data-py-more]")) box.innerHTML = html(c);
          });
          wire(box, c, load);
          body.appendChild(box);
        }
      } else {
        body.innerHTML = data.events.length
          ? data.events.map(eventHTML).join("")
          : `<div class="side-empty"><p>Nothing yet. When a play triggers, what it saw and what it did land here.</p></div>`;
      }
    }

    async function load() {
      const my = ++seq;
      if (!Auth.user) { data = null; return paint(); }
      try {
        const d = await call("/plays");
        if (my !== seq) return;
        data = d;
        paint();
      } catch (err) {
        if (my !== seq) return;
        body.innerHTML = `<div class="side-empty"><p>Plays could not be loaded: ${esc(err.message)}</p></div>`;
      }
    }

    host.addEventListener("click", (e) => {
      const t = e.target.closest("[data-tab]");
      if (t) { tab = t.dataset.tab; paint(); return; }
      if (e.target.closest("[data-py-refresh]")) load();
    });
    wireInbox(body, load);
    const onChange = () => load();
    window.addEventListener("pivot:plays-changed", onChange);
    window.addEventListener("pivot:play", onChange);
    return { show() { load(); }, hide() {} };
  }

  if (typeof Dock !== "undefined") {
    Dock.register({
      type: "plays", title: "Plays", icon: "radio", hue: "green", group: "Trade",
      desc: "Your plays, and what they saw and did", zone: "right", minW: 300, mount,
      single: true,
    });
  }

  /* A play's event on the alerts stream: tell the widget, and say it once. */
  window.addEventListener("pivot:play", (e) => {
    const ev = e.detail && e.detail.event;
    if (!ev) return;
    const what = ev.kind === "proposed" ? "is waiting for your confirm" :
      ev.kind === "filled" ? "placed an order on paper" : "triggered";
    toast(`${ev.play || ev.title} ${what}`);
  });

  return { html, wire, eventHTML, call };
})();
