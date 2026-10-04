"use client";

import { PricingPage } from "@/components/PricingPage";

/**
 * /pricing — the plans & upgrade surface, rendered as a full-screen overlay
 * (ChatGPT-style): it covers the whole viewport on its own backdrop, with a
 * close button that returns to wherever you were. NOT mounted inside AppShell,
 * so the topbar and sidebar are hidden behind it.
 *
 * AppBootstrap (auth gate + token provider) is wired once in app/layout.tsx.
 */
export default function Page(): React.ReactElement {
  return <PricingPage />;
}
