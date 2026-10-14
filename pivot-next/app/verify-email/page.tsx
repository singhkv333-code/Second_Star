"use client";

/**
 * Confirm email — the page the "Confirm your email" message links to
 * (charto/data/auth_guard.py). It posts the token once and says what happened.
 * It works signed in or out: the link alone proves the inbox.
 */

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { BrandPanel } from "@/components/auth/BrandPanel";
import { confirmEmail } from "@/lib/emailVerification";

type State = { kind: "busy" } | { kind: "done"; email?: string } | { kind: "error"; message: string };

function Confirm(): React.ReactElement {
  const token = useSearchParams().get("token") || "";
  const [state, setState] = useState<State>(token ? { kind: "busy" } : {
    kind: "error", message: "This link is missing its code. Open the link from the email again.",
  });
  const sent = useRef(false);

  useEffect(() => {
    if (!token || sent.current) return;     // one request, even in dev's double mount
    sent.current = true;
    void confirmEmail(token).then((r) => setState(r.ok
      ? { kind: "done", email: r.email }
      : { kind: "error", message: r.message || "This confirmation link has expired or was already used." }));
  }, [token]);

  if (state.kind === "busy") {
    return (
      <div className="flex items-center gap-3 text-[15px] text-foreground">
        <Loader2 className="size-4 animate-spin" aria-hidden="true" /> Confirming your email…
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold tracking-tight">
        {state.kind === "done" ? "Email confirmed" : "We couldn't confirm your email"}
      </h1>
      <p className="text-[15px] leading-relaxed text-foreground">
        {state.kind === "done"
          ? `${state.email ?? "Your address"} is confirmed. Your alerts and account emails will reach you there.`
          : `${state.message} You can send a new link from Settings, under Account.`}
      </p>
      <Link href="/" className="mt-2 text-[15px] font-medium text-foreground underline underline-offset-4">
        Go to Pivot
      </Link>
    </div>
  );
}

export default function VerifyEmailPage(): React.ReactElement {
  return (
    <div className="flex min-h-screen" style={{ background: "var(--bg-base)" }}>
      <BrandPanel />
      <div className="flex flex-1 flex-col px-6 py-8 sm:px-10 lg:px-14">
        <div className="flex flex-1 items-center justify-center">
          <div className="w-full max-w-sm">
            <Suspense fallback={null}>
              <Confirm />
            </Suspense>
          </div>
        </div>
      </div>
    </div>
  );
}
