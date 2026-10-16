"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { StockDetailPage } from "@/components/StockDetailPage";
import { loginForHere } from "@/lib/returnTo";
import { sessionToken } from "@/lib/session";

/**
 * Two readers, one URL.
 *
 * Signed out (and every crawler): the public page the server rendered —
 * prices, financials, ownership, peers and answers, no sign-in.
 * Signed in: the full research page inside the AppShell (topbar + sidebar),
 * exactly as before.
 *
 * The server cannot see the session (it lives in localStorage), so it always
 * renders the public page and this switches after mount. A symbol with no
 * public page (an index, a crypto pair) has nothing to show signed out, so
 * that reader is sent to sign in as the auth gate used to do.
 *
 * Quick Ask and the expanded Copilot are mounted once by AppShell. Keeping
 * them out of StockDetailPage prevents a second thread and duplicate stream.
 */
export function StockSymbolView({
  symbol,
  publicPage,
}: {
  symbol: string;
  publicPage: React.ReactNode;
}): React.ReactElement | null {
  const router = useRouter();
  const [signedIn, setSignedIn] = useState<boolean | null>(null);

  useEffect(() => {
    const has = Boolean(sessionToken());
    setSignedIn(has);
    if (!has && !publicPage) router.replace(loginForHere());
  }, [publicPage, router]);

  if (signedIn) {
    return (
      <AppShell>
        <StockDetailPage symbol={symbol} />
        <div aria-hidden style={{ height: 96 }} />
      </AppShell>
    );
  }
  return <>{publicPage}</>;
}
