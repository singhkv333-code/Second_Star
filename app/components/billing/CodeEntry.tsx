"use client";

/**
 * "Have a code?": the one input both doors use.
 *
 *   checkout   a discount lowers the order total; a free-time code offers to
 *              redeem instead of paying
 *   settings   a free-time code is redeemed on the spot; a discount points to
 *              checkout with the code already filled in
 *
 * The server decides everything (coupons.py). This only asks what a code
 * does for the signed-in account and shows the answer.
 */

import * as React from "react";
import { useRouter } from "next/navigation";
import { Check, Loader2, Ticket, X } from "lucide-react";
import { billingApi } from "@/lib/billing/api";
import { inr } from "@/lib/billing/format";
import type { BillingMe, CodeInfo, Cycle, PaidPlanId } from "@/lib/billing/types";
import { Notice } from "./primitives";

export function CodeEntry({
  plan,
  cycle,
  initialCode,
  applied,
  onApplied,
}: {
  plan?: PaidPlanId;
  cycle?: Cycle;
  initialCode?: string | null;
  applied: CodeInfo | null;
  onApplied: (c: CodeInfo | null) => void;
}): React.ReactElement {
  const [open, setOpen] = React.useState(!!initialCode);
  const [value, setValue] = React.useState(initialCode ?? "");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const tried = React.useRef(false);

  const apply = React.useCallback(
    async (raw: string) => {
      const code = raw.replace(/\s+/g, "").toUpperCase();
      if (!code) return;
      setBusy(true);
      setError(null);
      const r = await billingApi.checkCode(code, plan, cycle);
      setBusy(false);
      if (!r.ok) {
        setError(r.status === 401 ? "Sign in to use a code." : r.error);
        onApplied(null);
        return;
      }
      onApplied(r.data.coupon);
    },
    [plan, cycle, onApplied],
  );

  // A code in the link (?code=) is checked once, on arrival.
  React.useEffect(() => {
    if (initialCode && !tried.current) {
      tried.current = true;
      void apply(initialCode);
    }
  }, [initialCode, apply]);

  if (applied) {
    return (
      <div className="bl-code-applied" role="status">
        <span className="bl-row-flex" style={{ gap: 8 }}>
          <Check size={16} aria-hidden /> <strong>{applied.code}</strong> {applied.summary}
        </span>
        <button
          type="button"
          className="bl-x"
          aria-label="Remove code"
          onClick={() => {
            onApplied(null);
            setValue("");
          }}
        >
          <X size={16} />
        </button>
      </div>
    );
  }
  if (!open) {
    return (
      <button type="button" className="bl-link bl-link--muted bl-row-flex" style={{ gap: 6 }} onClick={() => setOpen(true)}>
        <Ticket size={15} aria-hidden /> Have a code?
      </button>
    );
  }
  return (
    <div className="bl-stack" style={{ gap: 8 }}>
      <form
        className="bl-code"
        onSubmit={(e) => {
          e.preventDefault();
          void apply(value);
        }}
      >
        <input
          className="bl-input"
          aria-label="Code"
          placeholder="Enter your code"
          value={value}
          autoComplete="off"
          autoCapitalize="characters"
          spellCheck={false}
          maxLength={48}
          onChange={(e) => setValue(e.target.value)}
        />
        <button type="submit" className="bl-btn bl-btn--secondary" disabled={busy || !value.trim()}>
          {busy ? <Loader2 size={16} className="bl-spin" /> : "Apply"}
        </button>
      </form>
      {error ? <span className="bl-small" role="alert" style={{ color: "var(--bl-bad)" }}>{error}</span> : null}
    </div>
  );
}

/** The discounted line for this plan and cycle, when the code covers it. */
export function offerFor(c: CodeInfo | null, plan: PaidPlanId, cycle: Cycle) {
  if (!c || c.kind !== "discount") return null;
  return c.offers.find((o) => o.plan === plan && o.cycle === cycle) ?? null;
}

/** Plan & billing: redeem free time here, or carry a discount to checkout. */
export function RedeemCodeCard({ onRedeemed }: { onRedeemed: (m: BillingMe) => void }): React.ReactElement {
  const router = useRouter();
  const [code, setCode] = React.useState<CodeInfo | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [done, setDone] = React.useState<string | null>(null);

  const first = code?.kind === "discount" ? code.offers[0] : undefined;

  const redeem = async (): Promise<void> => {
    if (!code) return;
    setBusy(true);
    setError(null);
    const r = await billingApi.redeem(code.code);
    setBusy(false);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    onRedeemed(r.data.billing);
    const until = new Date(r.data.redeemed.until * 1000).toLocaleDateString("en-IN", { day: "numeric", month: "long", year: "numeric" });
    setDone(`${r.data.redeemed.plan_name} is active until ${until}.`);
    setCode(null);
  };

  return (
    <section className="bl-section" aria-labelledby="bl-code">
      <h2 id="bl-code" className="bl-h3">Redeem a code</h2>
      <div className="bl-card bl-stack">
        {done ? <Notice tone="good" title="Code redeemed">{done}</Notice> : null}
        <CodeEntry applied={code} onApplied={(c) => { setCode(c); setDone(null); setError(null); }} />
        {code?.kind === "access" ? (
          <button type="button" className="bl-btn bl-btn--primary" onClick={redeem} disabled={busy}>
            {busy ? <Loader2 size={16} className="bl-spin" /> : null}
            Start {code.days} days of {code.plan_name}
          </button>
        ) : null}
        {first ? (
          <div className="bl-stack" style={{ gap: 8 }}>
            <span className="bl-small">
              This code lowers the price at checkout: {first.plan_name} from {inr(first.was)} to {inr(first.amount)}{" "}
              {first.cycle === "annual" ? "a year" : "a month"}.
            </span>
            <button
              type="button"
              className="bl-btn bl-btn--primary"
              onClick={() =>
                router.push(
                  `/checkout?plan=${first.plan}&cycle=${first.cycle}&code=${encodeURIComponent(code?.code ?? "")}&return=/settings/billing`,
                )
              }
            >
              Continue to checkout
            </button>
          </div>
        ) : null}
        {error ? <span className="bl-small" role="alert" style={{ color: "var(--bl-bad)" }}>{error}</span> : null}
      </div>
    </section>
  );
}
