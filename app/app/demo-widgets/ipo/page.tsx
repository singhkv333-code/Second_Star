"use client";

/**
 * /demo-widgets/ipo — non-routed sandbox to preview every IPO chat-card
 * variation with deterministic dummy data, so the frontend can be designed
 * without a live IPO feed / Kite token.
 *
 * Covers all states of:
 *  - IpoListCard      (populated / empty / unreachable)
 *  - IpoApplicationCard (open mainboard / upcoming TBA / closed / SME / +GMP)
 *  - IpoListedCard    (gain / loss / flat / pending price / missing issue)
 *
 * Action callbacks are no-ops (logged). The "Register intent" / "Refresh"
 * buttons on IpoApplicationCard still hit the real API — expect those to
 * error against a dev backend; everything else is pure render.
 */

import { useState } from "react";

import { IpoListCard } from "@/components/chat/IpoListCard";
import { IpoApplicationCard } from "@/components/chat/IpoApplicationCard";
import { IpoApplicationPanel } from "@/components/chat/IpoApplicationPanel";
import { IpoDetailPanel, type IpoCompanyInfo } from "@/components/chat/IpoDetailPanel";
import { IpoListedCard } from "@/components/chat/IpoListedCard";
import type {
  IpoListPayload,
  IpoApplicationPayload,
  IpoListedPayload,
  IpoSubscription,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Shared dummy bits
// ---------------------------------------------------------------------------

const SUBSCRIPTION: IpoSubscription = {
  qib: 1.42,
  nii: 0.83,
  rii: 2.14,
  employee: 1.05,
  shareholder: null,
  overall: 1.51,
  as_of: "2026-06-16T11:42:00+05:30",
};

const DISCLAIMER =
  "Pivot registers your intent only — you must place and fund the bid in your own broker/UPI app. This is not financial advice.";

// ---------------------------------------------------------------------------
// IpoListCard payloads
// ---------------------------------------------------------------------------

const LIST_POPULATED: IpoListPayload = {
  _render_hint: "ipo_list_card",
  source: "nse",
  note: null,
  count: 5,
  ipos: [
    {
      name: "Aurora Renewables Ltd",
      symbol: "AURORA",
      price_band: "395–415",
      open_date: "2026-06-15",
      close_date: "2026-06-18",
      lot_size: 36,
      issue_size: "₹1,240 Cr",
      type: "mainboard",
      status: "open",
    },
    {
      name: "Velocity Logistics",
      symbol: "VELOCITY",
      price_band: "112–120",
      open_date: "2026-06-16",
      close_date: "2026-06-19",
      lot_size: 125,
      issue_size: "₹82 Cr",
      type: "sme",
      status: "open",
    },
    {
      name: "Meridian Pharma Ltd",
      symbol: "MERIDIAN",
      price_band: "680–720",
      open_date: "2026-06-22",
      close_date: "2026-06-25",
      lot_size: 20,
      issue_size: "₹3,100 Cr",
      type: "mainboard",
      status: "upcoming",
    },
    {
      name: "Saffron Foods",
      symbol: "SAFFRON",
      price_band: null,
      open_date: "2026-06-29",
      close_date: null,
      lot_size: null,
      issue_size: "TBA",
      type: "sme",
      status: "upcoming",
    },
    {
      name: "Cobalt Industries",
      symbol: "COBALT",
      price_band: "248–262",
      open_date: "2026-06-08",
      close_date: "2026-06-11",
      lot_size: 57,
      issue_size: "₹540 Cr",
      type: "mainboard",
      status: "closed",
    },
  ],
};

const LIST_EMPTY: IpoListPayload = {
  _render_hint: "ipo_list_card",
  source: "nse",
  note: "NSE shows no mainboard or SME issues open or upcoming this week.",
  count: 0,
  ipos: [],
};

const LIST_UNREACHABLE: IpoListPayload = {
  _render_hint: "ipo_list_card",
  source: "unreachable",
  note: "Couldn't reach the NSE IPO feed just now. Try again in a bit — your Kite session may also need a refresh.",
  count: 0,
  ipos: [],
};

// ---------------------------------------------------------------------------
// IpoApplicationCard payloads
// ---------------------------------------------------------------------------

const APP_OPEN_MAINBOARD: IpoApplicationPayload = {
  _render_hint: "ipo_application_card",
  symbol: "AURORA",
  name: "Aurora Renewables Ltd",
  type: "mainboard",
  status: "open",
  kyc: null,
  automatable: true,
  conversation_id: "demo-conv-1",
  disclaimer: DISCLAIMER,
  locked: {
    price_band: { min: 395, max: 415, is_fixed: false },
    lot_size: 36,
    open_date: "2026-06-15",
    close_date: "2026-06-18",
    issue_size: "₹1,240 Cr",
    rhp_url: "https://example.com/aurora-rhp.pdf",
    registrar: "Link Intime",
    allotment_deeplink: "https://linkintime.co.in/ipo/public-issues",
    listing_date: "2026-06-23",
    subscription: SUBSCRIPTION,
  },
  editable: {
    category: "retail",
    quantity_lots: 1,
    bid_price_mode: "cutoff",
    bid_price: null,
    upi_id: "",
  },
  validation: {
    min_lots: 1,
    lot_size: 36,
    amount_estimate_at_cutoff: 14940,
    retail_max_amount: 200000,
    sme_bypasses_retail_cap: false,
    upi_cap: 500000,
    cutoff_allowed: true,
    price_band: { min: 395, max: 415, is_fixed: false },
    category_options: ["retail", "snii", "bnii", "employee"],
  },
};

const APP_OPEN_WITH_GMP: IpoApplicationPayload & {
  gmp: { value: number; disclaimer: string };
} = {
  ...APP_OPEN_MAINBOARD,
  symbol: "MERIDIAN",
  name: "Meridian Pharma Ltd",
  conversation_id: "demo-conv-gmp",
  locked: {
    ...APP_OPEN_MAINBOARD.locked,
    price_band: { min: 680, max: 720, is_fixed: false },
    lot_size: 20,
    issue_size: "₹3,100 Cr",
    subscription: { ...SUBSCRIPTION, rii: 4.2, nii: 3.1, qib: 5.6 },
  },
  validation: {
    ...APP_OPEN_MAINBOARD.validation,
    lot_size: 20,
    price_band: { min: 680, max: 720, is_fixed: false },
  },
  gmp: {
    value: 145,
    disclaimer:
      "Grey-market premium is an unofficial, unregulated signal — not a price prediction.",
  },
};

const APP_UPCOMING_TBA: IpoApplicationPayload = {
  _render_hint: "ipo_application_card",
  symbol: "SAFFRON",
  name: "Saffron Foods",
  type: "sme",
  status: "upcoming",
  kyc: null,
  automatable: false,
  conversation_id: null,
  disclaimer: DISCLAIMER,
  locked: {
    price_band: null,
    lot_size: null,
    open_date: "2026-06-29",
    close_date: "2026-07-02",
    issue_size: "TBA",
    rhp_url: null,
    registrar: null,
    allotment_deeplink: null,
    listing_date: null,
    subscription: null,
  },
  editable: {
    category: "retail",
    quantity_lots: 1,
    bid_price_mode: "cutoff",
    bid_price: null,
    upi_id: "",
  },
  validation: {
    min_lots: 1,
    lot_size: null,
    amount_estimate_at_cutoff: null,
    retail_max_amount: 200000,
    sme_bypasses_retail_cap: true,
    upi_cap: 500000,
    cutoff_allowed: true,
    price_band: null,
    category_options: ["retail", "snii", "bnii"],
  },
};

const APP_SME_OPEN: IpoApplicationPayload = {
  _render_hint: "ipo_application_card",
  symbol: "VELOCITY",
  name: "Velocity Logistics",
  type: "sme",
  status: "open",
  kyc: null,
  automatable: true,
  conversation_id: "demo-conv-sme",
  disclaimer: DISCLAIMER,
  locked: {
    price_band: { min: 112, max: 120, is_fixed: false },
    lot_size: 125,
    open_date: "2026-06-16",
    close_date: "2026-06-19",
    issue_size: "₹82 Cr",
    rhp_url: "https://example.com/velocity-rhp.pdf",
    registrar: "Bigshare Services",
    allotment_deeplink: null,
    listing_date: "2026-06-24",
    subscription: { ...SUBSCRIPTION, rii: 6.8, nii: 9.2, qib: null },
  },
  editable: {
    category: "retail",
    quantity_lots: 1,
    bid_price_mode: "fixed",
    bid_price: 120,
    upi_id: "",
  },
  validation: {
    min_lots: 1,
    lot_size: 125,
    amount_estimate_at_cutoff: 15000,
    retail_max_amount: 200000,
    sme_bypasses_retail_cap: true,
    upi_cap: 500000,
    cutoff_allowed: true,
    price_band: { min: 112, max: 120, is_fixed: false },
    category_options: ["retail", "snii", "bnii"],
  },
};

const APP_CLOSED: IpoApplicationPayload = {
  _render_hint: "ipo_application_card",
  symbol: "COBALT",
  name: "Cobalt Industries",
  type: "mainboard",
  status: "closed",
  kyc: null,
  automatable: false,
  conversation_id: null,
  disclaimer: DISCLAIMER,
  locked: {
    price_band: { min: 248, max: 262, is_fixed: false },
    lot_size: 57,
    open_date: "2026-06-08",
    close_date: "2026-06-11",
    issue_size: "₹540 Cr",
    rhp_url: "https://example.com/cobalt-rhp.pdf",
    registrar: "KFin Technologies",
    allotment_deeplink: "https://kosmic.kfintech.com/ipostatus/",
    listing_date: "2026-06-16",
    subscription: { ...SUBSCRIPTION, rii: 12.4, nii: 28.1, qib: 6.3 },
  },
  editable: {
    category: "retail",
    quantity_lots: 1,
    bid_price_mode: "cutoff",
    bid_price: null,
    upi_id: "",
  },
  validation: {
    min_lots: 1,
    lot_size: 57,
    amount_estimate_at_cutoff: 14934,
    retail_max_amount: 200000,
    sme_bypasses_retail_cap: false,
    upi_cap: 500000,
    cutoff_allowed: true,
    price_band: { min: 248, max: 262, is_fixed: false },
    category_options: ["retail", "snii", "bnii", "employee"],
  },
};

/**
 * Mirrors the real flow: clicking "Apply" on a list row submits
 * `apply for the {SYMBOL} IPO`, which the backend answers with an
 * ipo_application_card. Here we short-circuit that round-trip locally by
 * mapping each list symbol to its mock application payload.
 */
const APP_BY_SYMBOL: Record<string, IpoApplicationPayload> = {
  AURORA: APP_OPEN_MAINBOARD,
  MERIDIAN: APP_OPEN_WITH_GMP,
  VELOCITY: APP_SME_OPEN,
  SAFFRON: APP_UPCOMING_TBA,
  COBALT: APP_CLOSED,
};

/**
 * Mock qualitative company profiles for the "Know more" drawer. In production
 * these come from the backend; here they're hand-written per demo symbol.
 */
const INFO_BY_SYMBOL: Record<string, IpoCompanyInfo> = {
  AURORA: {
    about:
      "Aurora Renewables develops and operates utility-scale solar and wind farms across western and southern India, selling power under long-term PPAs to state distribution companies and large industrial buyers.",
    founder: "Rohan Mehta",
    foundedYear: 2011,
    strengths: [
      "4.2 GW operational capacity with a contracted pipeline through 2030",
      "Long-dated PPAs give predictable, inflation-linked cash flows",
      "Falling module costs have lifted project IRRs over the last three years",
    ],
    risks: [
      "High leverage — net debt is ~3.8× EBITDA",
      "Receivable delays from state discoms can strain working capital",
      "Policy and tariff changes could compress future project returns",
    ],
  },
  MERIDIAN: {
    about:
      "Meridian Pharma is a mid-cap formulations maker focused on chronic-therapy generics for the domestic market, with a growing US ANDA pipeline and three USFDA-approved plants.",
    founder: "Dr. Anjali Rao",
    foundedYear: 2004,
    strengths: [
      "Diversified across cardiac, diabetic and CNS therapies",
      "Backward-integrated API manufacturing protects margins",
      "Consistent 18%+ ROCE over the past five years",
    ],
    risks: [
      "USFDA observations at one plant remain unresolved",
      "Pricing pressure in the US generics market",
      "Single-molecule concentration in the top revenue line",
    ],
  },
  VELOCITY: {
    about:
      "Velocity Logistics runs an asset-light, tech-enabled trucking and last-mile network for e-commerce and FMCG clients, with a marketplace matching shippers to a fleet of partner carriers.",
    founder: "Karan Shah",
    foundedYear: 2016,
    strengths: [
      "Asset-light model scales without heavy capex",
      "Sticky enterprise contracts with marquee e-commerce clients",
      "Proprietary routing software improves fleet utilisation",
    ],
    risks: [
      "Thin margins typical of the logistics sector",
      "Customer concentration — top 3 clients are ~55% of revenue",
      "Fuel-price and driver-availability volatility",
    ],
  },
  SAFFRON: {
    about:
      "Saffron Foods is a packaged-foods company making ready-to-cook and ready-to-eat regional Indian meals, distributed through modern trade, quick-commerce and its own D2C channel.",
    founder: "Priya Nair",
    foundedYear: 2018,
    strengths: [
      "Fast-growing D2C and quick-commerce revenue mix",
      "Strong brand recall in the regional-cuisine segment",
      "High repeat-purchase rates among urban customers",
    ],
    risks: [
      "Yet to turn profitable — heavy marketing spend",
      "Crowded packaged-foods category with large incumbents",
      "Raw-material (agri) cost inflation",
    ],
  },
  COBALT: {
    about:
      "Cobalt Industries manufactures specialty chemicals and battery-grade materials, supplying domestic EV and electronics makers as well as export markets in Europe and Southeast Asia.",
    founder: "Vikram Desai",
    foundedYear: 2009,
    strengths: [
      "Exposure to the fast-growing EV battery-materials supply chain",
      "Long-term offtake agreements with two large cell manufacturers",
      "Healthy balance sheet with low net debt",
    ],
    risks: [
      "Commodity-linked input prices can swing margins sharply",
      "Customer demand tied to the pace of EV adoption",
      "Environmental-compliance and capex requirements are rising",
    ],
  },
};

// ---------------------------------------------------------------------------
// IpoListedCard payloads
// ---------------------------------------------------------------------------

const LISTED_GAIN: IpoListedPayload = {
  _render_hint: "ipo_listed_card",
  symbol: "COBALT",
  name: "Cobalt Industries",
  type: "mainboard",
  issue_price: 262,
  current_price: 341.5,
  listing_gain_pct: 30.34,
  listing_date: "2026-06-16",
  source: "kite",
  note: null,
};

const LISTED_LOSS: IpoListedPayload = {
  _render_hint: "ipo_listed_card",
  symbol: "GRIFFIN",
  name: "Griffin Textiles",
  type: "sme",
  issue_price: 88,
  current_price: 71.2,
  listing_gain_pct: -19.09,
  listing_date: "2026-06-12",
  source: "kite",
  note: null,
};

const LISTED_FLAT: IpoListedPayload = {
  _render_hint: "ipo_listed_card",
  symbol: "PLATEAU",
  name: "Plateau Cement",
  type: "mainboard",
  issue_price: 500,
  current_price: 500,
  listing_gain_pct: 0,
  listing_date: "2026-06-10",
  source: "kite",
  note: null,
};

const LISTED_PENDING: IpoListedPayload = {
  _render_hint: "ipo_listed_card",
  symbol: "AURORA",
  name: "Aurora Renewables Ltd",
  type: "mainboard",
  issue_price: 415,
  current_price: null,
  listing_gain_pct: null,
  listing_date: "2026-06-23",
  source: "kite",
  note: "This IPO has already closed — listing is scheduled but no live price yet.",
};

const LISTED_MISSING_ISSUE: IpoListedPayload = {
  _render_hint: "ipo_listed_card",
  symbol: "VELOCITY",
  name: "Velocity Logistics",
  type: "sme",
  issue_price: null,
  current_price: 134.8,
  listing_gain_pct: null,
  listing_date: "2026-06-24",
  source: "yfinance",
  note: null,
};

// ---------------------------------------------------------------------------
// Layout helpers
// ---------------------------------------------------------------------------

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <h2
        style={{
          fontFamily: "var(--font-experiment)",
          fontSize: 22,
          color: "var(--text-primary)",
          letterSpacing: "-0.01em",
          margin: 0,
        }}
      >
        {title}
      </h2>
      <div style={{ display: "flex", gap: 28, alignItems: "flex-start", flexWrap: "wrap" }}>
        {children}
      </div>
    </section>
  );
}

function Variant({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span
        style={{
          fontSize: 11,
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          color: "var(--text-secondary, #6b7280)",
        }}
      >
        {label}
      </span>
      {children}
    </div>
  );
}

const noop = (_s: string): void => {};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function IpoWidgetSandbox(): React.ReactElement {
  // Local stand-in for the chat round-trip: which IPO's application card to show.
  const [appliedSymbol, setAppliedSymbol] = useState<string | null>(null);
  const appliedPayload = appliedSymbol ? APP_BY_SYMBOL[appliedSymbol] ?? null : null;
  // "slide" when the editor opens directly from Apply; "fade" when it hands off
  // from the "Know more" drawer (same slot → cross-fade, no re-slide).
  const [applyEntrance, setApplyEntrance] = useState<"slide" | "fade">("slide");
  // During a hand-off the details drawer's scrim stays up, so the editor
  // suppresses its own scrim to avoid two stacking (which darkens = flicker).
  const [applySuppressBackdrop, setApplySuppressBackdrop] = useState(false);

  // "Know more" details sidebar — independent of the apply editor.
  const [detailSymbol, setDetailSymbol] = useState<string | null>(null);
  const detailPayload = detailSymbol ? APP_BY_SYMBOL[detailSymbol] ?? null : null;
  const detailInfo = detailSymbol ? INFO_BY_SYMBOL[detailSymbol] ?? null : null;

  return (
    <div
      style={{
        height: "100vh",
        overflowY: "auto",
        background: "var(--bg-base)",
        padding: 56,
        display: "flex",
        flexDirection: "column",
        gap: 48,
      }}
    >
      <header style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <h1
          style={{
            fontFamily: "var(--font-experiment)",
            fontSize: 32,
            color: "var(--text-primary)",
            letterSpacing: "-0.02em",
            margin: 0,
          }}
        >
          IPO widget sandbox
        </h1>
        <p style={{ fontSize: 13, color: "var(--text-secondary, #6b7280)", margin: 0, maxWidth: 640 }}>
          Every IPO chat-card variation with mock data — no live feed or Kite token needed.
          Register / Refresh buttons still call the real API and will error on a dev backend;
          everything else is pure render.
        </p>
      </header>

      <Section title="IpoListCard → Apply (opens side panel, like editor / backtest)">
        <Variant label="Populated — click Apply to slide in the application panel →">
          <IpoListCard
            payload={LIST_POPULATED}
            onKnowMore={(sym) => {
              noop(`know more about the ${sym} IPO`);
              setDetailSymbol(sym);
            }}
          />
        </Variant>
        <Variant label="Behaviour">
          <div
            style={{
              width: 440,
              minHeight: 120,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              textAlign: "center",
              padding: 24,
              fontSize: 12.5,
              lineHeight: 1.6,
              color: "var(--text-secondary, #9ca3af)",
              border: "1px dashed var(--glass-border, #d1d5db)",
              borderRadius: 24,
            }}
          >
            <strong style={{ margin: "0 4px" }}>Apply</strong> opens the apply
            editor directly. <strong style={{ margin: "0 4px" }}>Know more</strong>{" "}
            opens a read-only details drawer whose bottom <strong style={{ margin: "0 4px" }}>Apply →</strong>{" "}
            hands off to that same editor. Both match the agent editor / backtest
            width. Close with X, the scrim, or Esc.
          </div>
        </Variant>
      </Section>

      {/* "Know more" details drawer — rendered BEFORE the apply editor so that,
          during a hand-off, the editor (later in the DOM, same z-index) paints
          on top and cross-fades over the still-present details drawer. */}
      <IpoDetailPanel
        open={detailSymbol !== null}
        onOpenChange={(o) => {
          if (!o) setDetailSymbol(null);
        }}
        payload={detailPayload}
        info={detailInfo}
        onApply={(sym) => {
          // Hand off: fade the editor in on top of the details drawer while the
          // drawer's scrim stays up (editor suppresses its own to avoid a
          // doubled/darkened flicker). Once the fade finishes, drop the drawer
          // and enable the editor's scrim in the SAME commit — a seamless swap
          // of identical scrims, so the background never flickers.
          setApplyEntrance("fade");
          setApplySuppressBackdrop(true);
          setAppliedSymbol(sym);
          window.setTimeout(() => {
            setDetailSymbol(null);
            setApplySuppressBackdrop(false);
          }, 240);
        }}
      />

      {/* Right-side application drawer — same shell/width as editor & backtest. */}
      <IpoApplicationPanel
        open={appliedSymbol !== null}
        onOpenChange={(o) => {
          if (!o) setAppliedSymbol(null);
        }}
        payload={appliedPayload}
        onSetupReminders={noop}
        entrance={applyEntrance}
        suppressBackdrop={applySuppressBackdrop}
      />

      <Section title="IpoListCard — other states">
        <Variant label="Empty (feed reachable, 0 issues)">
          <IpoListCard payload={LIST_EMPTY} />
        </Variant>
        <Variant label="Unreachable feed">
          <IpoListCard payload={LIST_UNREACHABLE} />
        </Variant>
      </Section>

      <Section title="IpoApplicationCard">
        <Variant label="Open · mainboard · full data">
          <IpoApplicationCard payload={APP_OPEN_MAINBOARD} onSetupReminders={noop} />
        </Variant>
        <Variant label="Open · with GMP chip">
          <IpoApplicationCard payload={APP_OPEN_WITH_GMP} onSetupReminders={noop} />
        </Variant>
        <Variant label="Open · SME (fixed-price)">
          <IpoApplicationCard payload={APP_SME_OPEN} onSetupReminders={noop} />
        </Variant>
        <Variant label="Upcoming · price band TBA">
          <IpoApplicationCard payload={APP_UPCOMING_TBA} />
        </Variant>
        <Variant label="Closed · read-only">
          <IpoApplicationCard payload={APP_CLOSED} />
        </Variant>
      </Section>

      <Section title="IpoListedCard">
        <Variant label="Positive listing gain">
          <IpoListedCard payload={LISTED_GAIN} />
        </Variant>
        <Variant label="Negative listing gain">
          <IpoListedCard payload={LISTED_LOSS} />
        </Variant>
        <Variant label="Flat (0%)">
          <IpoListedCard payload={LISTED_FLAT} />
        </Variant>
        <Variant label="Listing pending (no live price)">
          <IpoListedCard payload={LISTED_PENDING} />
        </Variant>
        <Variant label="Missing issue price">
          <IpoListedCard payload={LISTED_MISSING_ISSUE} />
        </Variant>
      </Section>
    </div>
  );
}
