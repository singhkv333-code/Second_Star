/* Charto preview — the pricing surface.
 *
 * Opened from the account menu's "Upgrade" row (main.js wires it). A single
 * full-screen overlay on the app's own scrim, not a settings dialog: pricing
 * is a marketing page, it needs room to breathe, and the three plans have to
 * be comparable side by side. Everything it draws is built from the app's
 * tokens — the same CTA ink, the same hairline seams, the same radii — so it
 * reads as a room in this product rather than a template bolted on.
 *
 * ONE source of truth. Every number a card or a comparison row shows comes
 * from PLANS / FEATURES below. Change a limit there and both the card summary
 * and the matrix move together; nothing is written twice.
 */
"use strict";

const Pricing = (() => {
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ── the three plans ──────────────────────────────────────
  // `monthly` / `annual` are the per-month figures in ₹. `annual` is what you
  // pay each month when billed for the year; the yearly total and the saving
  // are derived, never typed, so they cannot drift from the two prices.
  const PLANS = [
    {
      id: "free", name: "Free", icon: "sparkles",
      tagline: "Everything you need to chart, screen and learn the markets.",
      monthly: 0, annual: 0,
      cta: "Current plan", ctaKind: "outline",
    },
    {
      id: "pro", name: "Pro", icon: "zap", featured: true,
      tagline: "Deeper alerts, unlimited AI summaries and the full indicator set.",
      monthly: 499, annual: 449,
      cta: "Upgrade to Pro", ctaKind: "cta",
    },
    {
      id: "proplus", name: "Pro+", icon: "crown",
      tagline: "The highest limits across alerts, screening and parallel charts.",
      monthly: 999, annual: 899,
      cta: "Upgrade to Pro+", ctaKind: "outline",
    },
  ];

  // ── the feature matrix ───────────────────────────────────
  // Transcribed from the source table, in the order the product groups them.
  // A value is one of:
  //   a number / short string  → shown as-is (e.g. 150, "All", "10K", "Never")
  //   true                     → a check (included)
  //   false                    → an em dash (not included)
  // `note` is an optional one-line clarification under a group's heading.
  const FEATURES = [
    {
      group: "AI",
      rows: [
        { label: "Daily AI credits", values: [false, false, false] },
        { label: "Monthly AI credits", values: [150, 200, 500] },
        { label: "AI chart summaries", values: [10, "Unlimited", "Unlimited"] },
      ],
    },
    {
      group: "Alerts",
      rows: [
        { label: "Fundamental alerts", values: [false, false, 500] },
        { label: "Technical alerts", values: [20, 100, 1000] },
        { label: "Price alerts", values: [20, 400, 1000] },
        { label: "Watchlist alerts", values: [false, true, true] },
        { label: "Multi-condition alerts", values: [true, true, true] },
        { label: "Alert expiry", values: ["2 months", "6 months", "Never"] },
        { label: "Screen alerts", values: [3, 50, 75] },
      ],
    },
    {
      group: "Charting",
      rows: [
        { label: "Indicators", values: [5, 10, "All"] },
        { label: "Charts per tab", values: [4, 8, 8] },
        { label: "Parallel charts", values: [10, 20, 50] },
        { label: "Custom time frames", values: [false, true, true] },
        { label: "Historical bars", values: ["10K", false, false] },
        { label: "Ad-free experience", values: [false, true, true] },
      ],
    },
    {
      group: "Watchlists & screening",
      rows: [
        { label: "Watchlists", values: ["Unlimited", "Unlimited", "Unlimited"] },
        { label: "Saved screens", values: [5, 50, 50] },
      ],
    },
  ];

  const inr = (n) => "₹" + Number(n).toLocaleString("en-IN");

  // ── price block for one card, re-rendered on every toggle ──
  function priceHTML(plan, annual) {
    if (plan.monthly === 0) {
      return `<div class="pr-amount"><span class="pr-cur">₹</span>`
        + `<span class="pr-num">0</span></div>`
        + `<div class="pr-period">Free forever</div>`;
    }
    const perMonth = annual ? plan.annual : plan.monthly;
    const sub = annual
      ? `${inr(plan.annual * 12)} billed yearly`
      : `Billed monthly · ${inr(plan.annual)}/mo annually`;
    return `<div class="pr-amount"><span class="pr-cur">₹</span>`
      + `<span class="pr-num">${perMonth.toLocaleString("en-IN")}</span>`
      + `<span class="pr-per">/ month</span></div>`
      + `<div class="pr-period">${sub}</div>`;
  }

  // The handful of features each card headlines — the ones a buyer scans for
  // before dropping into the full matrix. Pulled from FEATURES so they stay
  // true to the single source.
  const HIGHLIGHTS = {
    free: [
      ["Monthly AI credits", "150"],
      ["AI chart summaries", "10"],
      ["Indicators", "5"],
      ["Price & technical alerts", "20 each"],
      ["Historical bars", "10K"],
    ],
    pro: [
      ["Monthly AI credits", "200"],
      ["AI chart summaries", "Unlimited"],
      ["Indicators", "10"],
      ["Technical alerts", "100"],
      ["Watchlist alerts & ad-free", "Included"],
    ],
    proplus: [
      ["Monthly AI credits", "500"],
      ["All indicators", "Unlocked"],
      ["Fundamental alerts", "500"],
      ["Technical & price alerts", "1,000 each"],
      ["Parallel charts", "50"],
    ],
  };

  function cardHTML(plan, annual) {
    const save = plan.monthly > 0
      ? Math.round((1 - plan.annual / plan.monthly) * 100) : 0;
    const highs = HIGHLIGHTS[plan.id].map(([label, val]) =>
      `<li><span class="hi-tick">${Icons.svg("check", "xs")}</span>`
      + `<span class="hi-txt">${esc(label)}</span>`
      + `<span class="hi-val">${esc(val)}</span></li>`).join("");
    return `<article class="pr-card${plan.featured ? " featured" : ""}" data-plan="${plan.id}">`
      + (plan.featured ? `<span class="pr-flag">Most popular</span>` : "")
      + `<header class="pr-card-head">`
      +   `<span class="pr-mark">${Icons.svg(plan.icon, "sm")}</span>`
      +   `<span class="pr-name">${esc(plan.name)}</span>`
      +   (annual && save > 0
            ? `<span class="pr-save">Save ${save}%</span>` : "")
      + `</header>`
      + `<p class="pr-tag">${esc(plan.tagline)}</p>`
      + `<div class="pr-price" data-price="${plan.id}">${priceHTML(plan, annual)}</div>`
      + `<button type="button" class="btn ${plan.ctaKind} pr-cta" data-cta="${plan.id}">`
      +   esc(plan.cta)
      +   (plan.ctaKind === "cta" ? Icons.svg("arrowUpRight", "xs") : "")
      + `</button>`
      + `<ul class="pr-highlights">${highs}</ul>`
      + `</article>`;
  }

  // ── one cell in the comparison matrix ────────────────────
  function cellHTML(v) {
    if (v === true)  return `<span class="mx-yes">${Icons.svg("check", "xs")}</span>`;
    if (v === false) return `<span class="mx-no" aria-label="Not included">—</span>`;
    return `<span class="mx-val">${esc(v)}</span>`;
  }

  function matrixHTML() {
    const head = `<tr><th scope="col" class="mx-feat">Features</th>`
      + PLANS.map((p) => `<th scope="col" class="mx-plan${p.featured ? " featured" : ""}">`
        + esc(p.name) + `</th>`).join("")
      + `</tr>`;
    const body = FEATURES.map((grp) =>
      `<tr class="mx-group"><th scope="rowgroup" colspan="4">${esc(grp.group)}</th></tr>`
      + grp.rows.map((r) =>
        `<tr><th scope="row" class="mx-feat">${esc(r.label)}</th>`
        + r.values.map((v, i) =>
          `<td class="${PLANS[i].featured ? "featured" : ""}">${cellHTML(v)}</td>`).join("")
        + `</tr>`).join("")
    ).join("");
    return `<table class="pr-matrix"><thead>${head}</thead>`
      + `<tbody>${body}</tbody></table>`;
  }

  // Mobile: the table collapses to one stacked block per plan, each a list of
  // feature → value rows. Readable top to bottom, no sideways scrolling.
  function stackHTML() {
    return `<div class="pr-stack">`
      + PLANS.map((p, pi) =>
        `<section class="pr-stack-plan${p.featured ? " featured" : ""}">`
        + `<h4 class="pr-stack-name">${Icons.svg(p.icon, "xs")}${esc(p.name)}</h4>`
        + FEATURES.map((grp) =>
          `<div class="pr-stack-group">${esc(grp.group)}</div>`
          + grp.rows.map((r) =>
            `<div class="pr-stack-row"><span class="pr-stack-label">${esc(r.label)}</span>`
            + `<span class="pr-stack-val">${cellHTML(r.values[pi])}</span></div>`).join("")
        ).join("")
        + `</section>`).join("")
      + `</div>`;
  }

  // ── the overlay ──────────────────────────────────────────
  let wrap = null, annual = false;

  function build() {
    wrap = document.createElement("div");
    wrap.className = "pr-wrap";
    wrap.setAttribute("role", "dialog");
    wrap.setAttribute("aria-modal", "true");
    wrap.setAttribute("aria-label", "Pricing plans");
    wrap.innerHTML =
      `<div class="pr-sheet" role="document">`
      + `<button type="button" class="pr-close btn icon" aria-label="Close">`
      +   Icons.svg("x", "sm") + `</button>`
      + `<div class="pr-scroll">`
      +   `<header class="pr-hero">`
      +     `<span class="pr-eyebrow">Plans &amp; pricing</span>`
      +     `<h2 class="pr-title">Choose the plan that fits your workflow</h2>`
      +     `<p class="pr-sub">Start free and upgrade when you need more alerts, `
      +       `AI and room on the chart. This is plan information, not financial advice.</p>`
      +     `<div class="pr-toggle" role="group" aria-label="Billing period">`
      +       `<button type="button" class="pr-seg active" data-bill="monthly" aria-pressed="true">Monthly</button>`
      +       `<button type="button" class="pr-seg" data-bill="annual" aria-pressed="false">Annual`
      +         `<span class="pr-seg-badge">Save 10%</span></button>`
      +     `</div>`
      +   `</header>`
      +   `<div class="pr-cards">${PLANS.map((p) => cardHTML(p, annual)).join("")}</div>`
      +   `<div class="pr-compare">`
      +     `<h3 class="pr-compare-title">Compare every feature</h3>`
      +     matrixHTML()
      +     stackHTML()
      +   `</div>`
      +   `<p class="pr-foot">Prices in INR, inclusive of applicable taxes. `
      +     `Cancel or change your plan anytime. Pivot builds and simulates — `
      +     `it does not place live broker orders.</p>`
      + `</div>`
      + `</div>`;
    document.body.appendChild(wrap);

    wrap.querySelector(".pr-close").addEventListener("click", close);
    // click on the scrim (outside the sheet) closes
    wrap.addEventListener("pointerdown", (e) => {
      if (e.target === wrap) close();
    });
    wrap.querySelector(".pr-toggle").addEventListener("click", (e) => {
      const seg = e.target.closest("[data-bill]");
      if (!seg) return;
      setBilling(seg.dataset.bill === "annual");
    });
    // CTAs: a signed-out visitor is sent to sign-in; the real checkout is a
    // business decision (payments), so for now the row is a no-op beyond that
    // and simply closes. Kept as one hook so wiring a gateway later is one
    // place, not three.
    wrap.querySelector(".pr-cards").addEventListener("click", (e) => {
      const b = e.target.closest("[data-cta]");
      if (!b) return;
      const id = b.dataset.cta;
      if (id === "free") { close(); return; }
      if (window.Auth && !window.Auth.user && window.CHARTO_AUTH_OPEN) {
        close();
        window.CHARTO_AUTH_OPEN("login");
      }
      // else: checkout gateway goes here.
    });
  }

  function setBilling(next) {
    annual = !!next;
    wrap.querySelectorAll(".pr-seg").forEach((s) => {
      const on = (s.dataset.bill === "annual") === annual;
      s.classList.toggle("active", on);
      s.setAttribute("aria-pressed", String(on));
    });
    // Re-render only the price blocks, so the switch is a quiet cross-fade on
    // the numbers rather than a repaint of the whole page.
    PLANS.forEach((p) => {
      const box = wrap.querySelector(`.pr-price[data-price="${p.id}"]`);
      if (!box) return;
      box.classList.remove("swap");
      // reflow to restart the animation
      void box.offsetWidth;
      box.innerHTML = priceHTML(p, annual);
      box.classList.add("swap");
      const save = p.monthly > 0 ? Math.round((1 - p.annual / p.monthly) * 100) : 0;
      const head = wrap.querySelector(`.pr-card[data-plan="${p.id}"] .pr-card-head`);
      if (head) {
        let badge = head.querySelector(".pr-save");
        if (annual && save > 0) {
          if (!badge) {
            badge = document.createElement("span");
            badge.className = "pr-save";
            head.appendChild(badge);
          }
          badge.textContent = `Save ${save}%`;
        } else if (badge) {
          badge.remove();
        }
      }
    });
  }

  let lastFocus = null;
  function onKey(e) {
    if (e.key === "Escape") { e.preventDefault(); close(); }
  }

  function open() {
    if (!wrap) build();
    lastFocus = document.activeElement;
    annual = false;
    setBilling(false);
    wrap.classList.add("open");
    document.addEventListener("keydown", onKey);
    // land focus on the close control, so Tab walks the sheet and Esc is live
    requestAnimationFrame(() => {
      const c = wrap.querySelector(".pr-close");
      if (c) c.focus();
    });
  }

  function close() {
    if (!wrap) return;
    wrap.classList.remove("open");
    document.removeEventListener("keydown", onKey);
    if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (e) {} }
  }

  return { open, close, PLANS, FEATURES };
})();

window.Pricing = Pricing;
