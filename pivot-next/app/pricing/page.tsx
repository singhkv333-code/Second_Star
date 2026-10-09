"use client";

import * as React from "react";
import { PricingPage } from "@/components/PricingPage";

/**
 * /pricing — the plans & upgrade surface, rendered as a full-screen overlay
 * (ChatGPT-style): it covers the whole viewport on its own backdrop, with a
 * close button that returns to wherever you were. NOT mounted inside AppShell,
 * so the topbar and sidebar are hidden behind it. Public: a signed-out
 * visitor can read the plans before making an account.
 *
 * BillingProvider (catalog + the viewer's plan) is wired once in app/layout.tsx.
 */
export default function Page(): React.ReactElement {
  return (
    <React.Suspense fallback={null}>
      <PricingPage />
    </React.Suspense>
  );
}
