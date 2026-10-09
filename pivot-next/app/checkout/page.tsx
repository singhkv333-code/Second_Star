"use client";

import * as React from "react";
import { useSearchParams } from "next/navigation";
import { CheckoutFallback, CheckoutView } from "@/components/billing/CheckoutView";
import type { Cycle, PaidPlanId } from "@/lib/billing/types";

/**
 * /checkout?plan=pro&cycle=annual&return=/chart&feature=chart.panes
 *
 * `return` is where the user was when they hit the paywall; success sends
 * them straight back there (filtered by safeReturnPath, so it can only be a
 * path inside this app).
 */
function Checkout(): React.ReactElement {
  const q = useSearchParams();
  const plan = q?.get("plan");
  const cycle: Cycle = q?.get("cycle") === "monthly" ? "monthly" : "annual";
  if (plan !== "pro" && plan !== "pro_plus") return <CheckoutFallback />;
  return (
    <CheckoutView
      key={plan}
      plan={plan as PaidPlanId}
      initialCycle={cycle}
      returnTo={q?.get("return")}
      feature={q?.get("feature")}
      surface={q?.get("from")}
    />
  );
}

export default function Page(): React.ReactElement {
  return (
    <React.Suspense fallback={null}>
      <Checkout />
    </React.Suspense>
  );
}
