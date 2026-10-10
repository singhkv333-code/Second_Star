"use client";

/**
 * BillingProvider — the plan, mirrored once for the whole app.
 *
 * Loads GET /billing/plans and GET /billing/me, exposes them with
 * `openPaywall()`, and mounts the one contextual paywall dialog. Anything
 * that receives a 402 — a React component, a fetch helper, the chart iframe —
 * can raise the right paywall with:
 *
 *     window.dispatchEvent(new CustomEvent("pivot:paywall", { detail: body }))
 *
 * where `body` is the server's refusal ({code, feature, limit, ...}) or a
 * PaywallTrigger. The user's place is preserved: the dialog opens over the
 * page they are on, and checkout returns them to it.
 */

import * as React from "react";
import { usePathname } from "next/navigation";
import { SNAPSHOT } from "@/lib/billing/catalog";
import { billingApi, hasChartoSession } from "@/lib/billing/api";
import { ensureDevChartoSession } from "@/lib/charto-auth";
import {
  isRefusal,
  triggerFromRefusal,
  type PaywallTrigger,
} from "@/lib/billing/entitlements";
import { track } from "@/lib/billing/analytics";
import type { BillingMe, PublicCatalog } from "@/lib/billing/types";
import { PaywallDialog } from "./PaywallDialog";

type BillingCtx = {
  catalog: PublicCatalog;
  me: BillingMe | null;
  /** first load of /billing/me still in flight */
  loading: boolean;
  /** /billing/me failed (the dataserver is down or unreachable) */
  error: string | null;
  signedIn: boolean;
  refresh: () => Promise<BillingMe | null>;
  /** replace `me` after a write answered with a fresh summary */
  setMe: (me: BillingMe) => void;
  trigger: PaywallTrigger | null;
  openPaywall: (t: PaywallTrigger | unknown) => void;
  closePaywall: (reason?: "dismiss" | "cta") => void;
  /** gallery / tests: fixed data, no network */
  demo: boolean;
};

const Ctx = React.createContext<BillingCtx | null>(null);

export function useBilling(): BillingCtx {
  const c = React.useContext(Ctx);
  if (!c) throw new Error("useBilling() outside <BillingProvider>");
  return c;
}

/** For code outside React. */
export function showPaywall(detail: PaywallTrigger | unknown): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent("pivot:paywall", { detail }));
}

function toTrigger(x: unknown, returnTo: string): PaywallTrigger | null {
  if (isRefusal(x)) return triggerFromRefusal(x, { returnTo });
  if (x && typeof x === "object" && "kind" in x && "plan" in x) {
    const t = x as PaywallTrigger;
    return { returnTo, ...t };
  }
  return null;
}

export function BillingProvider({
  children,
  initial,
  demo = false,
  mountDialog = true,
}: {
  children: React.ReactNode;
  initial?: { catalog?: PublicCatalog; me?: BillingMe | null };
  demo?: boolean;
  mountDialog?: boolean;
}): React.ReactElement {
  const pathname = usePathname();
  const [catalog, setCatalog] = React.useState<PublicCatalog>(initial?.catalog ?? SNAPSHOT);
  const [me, setMe] = React.useState<BillingMe | null>(initial?.me ?? null);
  const [loading, setLoading] = React.useState(!demo && initial?.me === undefined);
  const [error, setError] = React.useState<string | null>(null);
  const [trigger, setTrigger] = React.useState<PaywallTrigger | null>(null);

  // Demo data follows its props (the gallery swaps fixtures).
  React.useEffect(() => {
    if (!demo) return;
    if (initial?.catalog) setCatalog(initial.catalog);
    if (initial?.me !== undefined) setMe(initial.me);
  }, [demo, initial?.catalog, initial?.me]);

  const refresh = React.useCallback(async (): Promise<BillingMe | null> => {
    if (demo) return me;
    const r = await billingApi.me();
    setLoading(false);
    if (r.ok) {
      setMe(r.data);
      setError(null);
      return r.data;
    }
    setError(r.error);
    return null;
  }, [demo, me]);

  React.useEffect(() => {
    if (demo) return;
    let alive = true;
    void billingApi.plans().then((r) => {
      if (alive && r.ok && r.data?.plans?.length) setCatalog(r.data);
    });
    void ensureDevChartoSession()
      .then(() => billingApi.me())
      .then((r) => {
        if (!alive) return;
        setLoading(false);
        if (r.ok) setMe(r.data);
        else setError(r.error);
      });
    return () => {
      alive = false;
    };
  }, [demo]);

  const openPaywall = React.useCallback(
    (t: PaywallTrigger | unknown) => {
      const trig = toTrigger(t, pathname || "/");
      if (!trig) return;
      setTrigger(trig);
      track("paywall_viewed", { kind: trig.kind, feature: trig.feature, plan: trig.plan, surface: trig.surface });
    },
    [pathname],
  );

  const closePaywall = React.useCallback(
    (reason: "dismiss" | "cta" = "dismiss") => {
      setTrigger((cur) => {
        if (cur && reason === "dismiss") {
          track("paywall_dismissed", { kind: cur.kind, feature: cur.feature, surface: cur.surface });
        }
        return null;
      });
    },
    [],
  );

  React.useEffect(() => {
    const on = (e: Event): void => openPaywall((e as CustomEvent).detail);
    window.addEventListener("pivot:paywall", on);
    return () => window.removeEventListener("pivot:paywall", on);
  }, [openPaywall]);

  const value = React.useMemo<BillingCtx>(
    () => ({
      catalog,
      me,
      loading,
      error,
      signedIn: demo ? !!me && me.plan !== "anonymous" : hasChartoSession() && !!me && me.plan !== "anonymous",
      refresh,
      setMe,
      trigger,
      openPaywall,
      closePaywall,
      demo,
    }),
    [catalog, me, loading, error, demo, refresh, trigger, openPaywall, closePaywall],
  );

  return (
    <Ctx.Provider value={value}>
      {children}
      {mountDialog ? <PaywallDialog /> : null}
    </Ctx.Provider>
  );
}
