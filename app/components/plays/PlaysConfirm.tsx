"use client";

import { useCallback, useEffect, useState } from "react";
import { BellRing, Mail } from "lucide-react";
import { toast } from "sonner";

import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { requestDataserver } from "@/lib/api";

/**
 * Two prompts over the whole app for plays.
 *
 * The order prompt: a play's proposed order waiting for the trader
 * (`/plays/pending`), with Confirm and Dismiss. The same order is also in
 * their email and their Inbox; whichever they use first decides it.
 *
 * The guide: once per browser, the first time a play is switched on (a Plays
 * page posts `plays:armed` to the top window), it says how orders get
 * confirmed and why Pivot never places one on its own.
 */
type Pending = { id: number; play: string; order: string; where: string; live: boolean; reason: string };

const GUIDE_KEY = "pivot:plays:guide-seen";
const POLL_MS = 8000;

export function PlaysConfirm(): React.ReactElement {
  const [pending, setPending] = useState<Pending[]>([]);
  const [later, setLater] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [guide, setGuide] = useState(false);

  const load = useCallback(async (): Promise<void> => {
    if (document.hidden) return;
    try {
      const r = await requestDataserver<{ pending: Pending[] }>("/plays/pending");
      if ("data" in r) setPending(r.data.pending);
    } catch { /* offline: try again on the next tick */ }
  }, []);

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), POLL_MS);
    document.addEventListener("visibilitychange", load);
    return () => { window.clearInterval(t); document.removeEventListener("visibilitychange", load); };
  }, [load]);

  useEffect(() => {
    const onMessage = (e: MessageEvent): void => {
      if (e.origin !== window.location.origin || (e.data as { type?: string } | null)?.type !== "plays:armed") return;
      void load();
      try { if (!localStorage.getItem(GUIDE_KEY)) setGuide(true); } catch { setGuide(true); }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [load]);

  const cur = pending.find((p) => !later.has(p.id)) ?? null;
  useEffect(() => setError(""), [cur?.id]);

  const decide = async (action: "confirm" | "dismiss"): Promise<void> => {
    if (!cur) return;
    setBusy(true);
    setError("");
    try {
      const r = await requestDataserver<{ status: string }>(`/plays/events/${cur.id}`, { method: "POST", body: { action } });
      if ("error" in r) { setError(r.error.message); void load(); return; }
      toast(action === "confirm" ? (cur.live ? "Sent to your broker" : "Placed on paper") : "Dismissed");
      setPending((ps) => ps.filter((p) => p.id !== cur.id));
      window.dispatchEvent(new Event("plays:changed"));
    } finally {
      setBusy(false);
    }
  };

  const understood = (): void => {
    try { localStorage.setItem(GUIDE_KEY, "1"); } catch { /* storage off: it may show again */ }
    setGuide(false);
  };

  return (
    <>
      <Dialog open={!!cur && !guide} onOpenChange={(o) => { if (!o && cur) setLater((s) => new Set(s).add(cur.id)); }}>
        <DialogContent className="sm:max-w-md">
          <DialogTitle>Confirm your order</DialogTitle>
          <DialogDescription>Your play “{cur?.play}” triggered. Nothing is placed until you confirm.</DialogDescription>
          <div className="rounded-lg border bg-muted/40 p-4">
            <div className="text-base font-semibold">{cur?.order}</div>
            <div className="mt-1 text-sm text-muted-foreground">{cur?.where}</div>
            {cur?.reason ? <div className="mt-2 text-sm text-muted-foreground">{cur.reason[0]?.toUpperCase()}{cur.reason.slice(1)}</div> : null}
          </div>
          {error ? <div className="text-sm text-destructive">{error}</div> : null}
          <div className="flex gap-2">
            <button type="button" className="h-10 flex-1 rounded-md border text-sm font-medium hover:bg-muted" disabled={busy}
                    onClick={() => cur && setLater((s) => new Set(s).add(cur.id))}>Later</button>
            <button type="button" className="h-10 flex-1 rounded-md border text-sm font-medium hover:bg-muted" disabled={busy}
                    onClick={() => void decide("dismiss")}>Dismiss</button>
            <button type="button" className="h-10 flex-1 rounded-md bg-foreground text-sm font-semibold text-background hover:opacity-90 disabled:opacity-50"
                    disabled={busy} onClick={() => void decide("confirm")}>{busy ? "Sending…" : "Confirm"}</button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={guide} onOpenChange={(o) => { if (!o) understood(); }}>
        <DialogContent className="overflow-hidden p-0 sm:max-w-md">
          <GuideArt />
          <div className="grid gap-3 px-6 pb-6">
            <DialogTitle>Your play is on. You confirm every order.</DialogTitle>
            <DialogDescription>
              When it triggers, Pivot emails you and shows a prompt here. Confirm the order from either one; it is
              placed only after you do, and only once.
            </DialogDescription>
            <p className="rounded-md border bg-muted/40 p-3 text-sm text-muted-foreground">
              In line with SEBI&apos;s rules for retail algorithmic trading, Pivot does not place orders automatically.
              Each order waits for your confirmation.
            </p>
            <button type="button" onClick={understood}
                    className="h-10 rounded-md bg-foreground text-sm font-semibold text-background hover:opacity-90">
              Understood
            </button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

/** The guide's picture: the same order, confirmable from the email or from the prompt in Pivot. */
function GuideArt(): React.ReactElement {
  const order = "Buy 7 RELIANCE at about 1,316.80";
  return (
    <div className="grid grid-cols-2 gap-3 border-b bg-muted/50 p-5 pt-12" aria-hidden="true">
      <div className="rounded-lg border bg-background p-3 shadow-sm">
        <div className="mb-2 flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground"><Mail className="h-3.5 w-3.5" />Email</div>
        <div className="text-[12px] font-semibold leading-snug">Confirm your order</div>
        <div className="mt-1 text-[11px] leading-snug text-muted-foreground">{order}</div>
        <div className="mt-2.5 rounded bg-foreground py-1 text-center text-[11px] font-semibold text-background">Review and confirm</div>
      </div>
      <div className="rounded-lg border bg-background p-3 shadow-sm">
        <div className="mb-2 flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground"><BellRing className="h-3.5 w-3.5" />In Pivot</div>
        <div className="text-[12px] font-semibold leading-snug">Confirm your order</div>
        <div className="mt-1 text-[11px] leading-snug text-muted-foreground">{order}</div>
        <div className="mt-2.5 flex gap-1.5">
          <div className="flex-1 rounded border py-1 text-center text-[11px] font-medium">Dismiss</div>
          <div className="flex-1 rounded bg-foreground py-1 text-center text-[11px] font-semibold text-background">Confirm</div>
        </div>
      </div>
    </div>
  );
}
