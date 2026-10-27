"use client";

/**
 * The sign-in prompt: what a signed-out visitor sees when the workspace needs
 * an account. The workspace itself is open to everyone; this appears only at
 * the step that needs one — asking the chat, a third indicator, saving or
 * sharing the desk, an alert, a page that reads the account — or when a
 * signed-out limit refuses (a 402 with plan "anonymous").
 *
 * Its words follow the step that raised it, and the free account's numbers
 * come from the catalog, so the prompt never promises what the server does
 * not grant. Both buttons go to the account pages with this page in `?next=`,
 * so signing in lands the visitor back on the desk they were building.
 */

import * as React from "react";
import { Check, X } from "lucide-react";
import { planById } from "@/lib/billing/catalog";
import { featureLine } from "@/lib/billing/presentation";
import { WELCOME_ART } from "@/lib/publicAssets";
import type { PaywallTrigger } from "@/lib/billing/entitlements";
import type { PublicCatalog } from "@/lib/billing/types";
import { PanelDesc, PanelTitle } from "./modal";

const TABS: Record<string, string> = {
  home: "Home", markets: "Markets", chat: "Chat", portfolio: "Portfolio",
  agents: "Strategy", screener: "Screener", brokers: "Brokers", plays: "Execution",
};

/** What the visitor was doing, in their words: the heading and one line. */
function reason(t: PaywallTrigger, cat: PublicCatalog): { title: string; line: string } {
  const f = t.feature ?? "";
  const anon = planById(cat, "anonymous")?.features ?? {};
  const free = planById(cat, "free")?.features ?? {};
  if (f === "ai.credits" || f === "chat") {
    return { title: "Sign in to ask Pivot", line: "Ask any chart a question in plain English, and Pivot draws the answer on live candles." };
  }
  if (f === "chart.indicators") {
    const a = anon["chart.indicators"], b = free["chart.indicators"];
    return {
      title: "Sign in to add more indicators",
      line: typeof a === "number" && typeof b === "number"
        ? `Signed out, a chart carries ${a} indicators. A free account carries ${b}.`
        : "A free account carries more indicators on every chart.",
    };
  }
  if (f.startsWith("alerts.")) {
    return { title: "Sign in to set alerts", line: "Alerts watch the chart while you are away and email you the moment they fire." };
  }
  if (f === "share") {
    return { title: "Sign in to share your desk", line: "Publish this workspace as one link that anyone can open and copy." };
  }
  if (f.startsWith("tab:")) {
    const name = TABS[f.slice(4)] ?? "this page";
    return { title: `Sign in to open ${name}`, line: `${name} reads your account. The chart stays open to everyone.` };
  }
  if (f === "save" || f === "layouts" || f === "chart.panes" || f === "screens.saved") {
    return { title: "Sign in to save your desk", line: "Keep your layouts, drawings and widgets on your account, on every device." };
  }
  return { title: "Sign in to keep going", line: t.message || "This step needs a free Pivot account." };
}

export function SignInPanel({
  trigger,
  catalog,
  onSignIn,
  onClose,
  closable = true,
}: {
  trigger: PaywallTrigger;
  catalog: PublicCatalog;
  onSignIn?: (mode: "signup" | "login") => void;
  onClose: (reason?: "dismiss" | "cta") => void;
  closable?: boolean;
}): React.ReactElement {
  const { title, line } = reason(trigger, catalog);
  const free = planById(catalog, "free");
  const perks = ["ai.credits", "chart.indicators", "alerts.price"].flatMap((k) => {
    const f = catalog.features[k], v = free?.features[k];
    return f && v !== undefined && v !== 0 && v !== false ? [featureLine(k, f, v)] : [];
  });

  return (
    <div className="bl-panel bl-scope bl-signin">
      {closable ? (
        <button type="button" className="bl-x bl-modal-close" aria-label="Close" onClick={() => onClose("dismiss")}>
          <X size={18} />
        </button>
      ) : null}
      <div className="bl-signin-art" aria-hidden>
        {/* eslint-disable-next-line @next/next/no-img-element -- a bundled asset path, see publicAssets.ts */}
        <img src={WELCOME_ART.moon} alt="" />
      </div>

      <div className="bl-panel-body">
        <div className="bl-stack" style={{ gap: 8 }}>
          <span className="bl-panel-kicker">Free account</span>
          <PanelTitle>{title}</PanelTitle>
          <PanelDesc>{line}</PanelDesc>
        </div>
        <ul className="bl-benefits">
          {[...perks.map((p) => `${p}, free`), "Your desk saved to your account", "No card required"].map((s) => (
            <li key={s}>
              <Check size={16} aria-hidden />
              {s}
            </li>
          ))}
        </ul>
      </div>

      <div className="bl-panel-foot">
        <button type="button" className="bl-btn bl-btn--primary bl-btn--lg bl-btn--block" onClick={() => { onClose("cta"); onSignIn?.("signup"); }}>
          Create a free account
        </button>
        <div className="bl-row-flex">
          <span className="bl-small">Already have one?</span>
          <button type="button" className="bl-link" onClick={() => { onClose("cta"); onSignIn?.("login"); }}>
            Sign in
          </button>
        </div>
        <div className="bl-row-flex">
          <button type="button" className="bl-btn bl-btn--ghost bl-btn--sm" onClick={() => onClose("dismiss")}>
            Keep exploring
          </button>
        </div>
      </div>
    </div>
  );
}
