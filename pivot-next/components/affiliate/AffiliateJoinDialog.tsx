"use client";

import { useEffect, useId, useState } from "react";
import { Loader2 } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { REFERRAL_RATE_PERCENT, fetchReferralSummary } from "./referrals";

/**
 * The join popup the product opens on `/?affiliate=join` — every "Sign up"
 * on /affiliate lands here. Signing up is the first read of `/referrals/me`,
 * which mints the account's link; the popup then hands over to
 * Settings → Affiliate, where the link and every referral live.
 */
export function AffiliateJoinDialog({
  open,
  onOpenChange,
  onJoined,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called once the link exists: opens Settings on its Affiliate section. */
  onJoined: () => void;
}): React.ReactElement {
  const [accepted, setAccepted] = useState(false);
  const [state, setState] = useState<{ kind: "idle" | "joining" } | { kind: "error"; message: string }>({ kind: "idle" });
  const checkboxId = useId();
  useEffect(() => { if (!open) { setAccepted(false); setState({ kind: "idle" }); } }, [open]);

  const join = async (): Promise<void> => {
    setState({ kind: "joining" });
    try {
      await fetchReferralSummary();
      onJoined();
    } catch (e) {
      setState({ kind: "error", message: (e as Error).message });
    }
  };
  const busy = state.kind === "joining";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        // The photo owns the top half, edge to edge; the close button sits on
        // it, so it is light on a dark disc instead of the muted default.
        className={
          "flex flex-col gap-0 p-0 sm:max-w-[480px] sm:overflow-hidden sm:rounded-[14px] " +
          "[&>button[aria-label=Close]]:bg-black/40 [&>button[aria-label=Close]]:text-white " +
          "[&>button[aria-label=Close]]:backdrop-blur-sm [&>button[aria-label=Close]:hover]:bg-black/60 " +
          "[&>button[aria-label=Close]:hover]:text-white"
        }
        style={{ background: "var(--bg-base)", color: "var(--text-primary)" }}
      >
        <div className="relative h-[50dvh] flex-none sm:h-[300px]">
          <img
            src="/affiliate/vesuvius.jpg"
            alt=""
            aria-hidden="true"
            style={{ display: "block", width: "100%", height: "100%", objectFit: "cover", objectPosition: "42% 45%", filter: "grayscale(1) contrast(1.08) brightness(.9)" }}
          />
          {/* the photo settles into the card rather than ending on a hard edge */}
          <div
            aria-hidden="true"
            style={{ position: "absolute", inset: "auto 0 0 0", height: 72, background: "linear-gradient(to bottom, transparent, var(--bg-base))" }}
          />
        </div>

        <div className="flex flex-1 flex-col" style={{ padding: "4px 24px 22px" }}>
          <DialogTitle style={{ margin: "0 0 10px", fontSize: 21, fontWeight: 600, letterSpacing: "-0.015em" }}>
            Pivot Affiliate Programme
          </DialogTitle>
          <DialogDescription asChild>
            <div style={{ margin: "0 0 18px", fontSize: 14.5, lineHeight: 1.6, color: "var(--text-secondary)" }}>
              Become an affiliate and earn {REFERRAL_RATE_PERCENT}% of every eligible subscription payment from people you refer.
              Learn more about the programme{" "}
              <a href="/affiliate" style={{ textDecoration: "underline", textUnderlineOffset: 3, color: "var(--text-primary)" }}>here</a>.
            </div>
          </DialogDescription>

          <label htmlFor={checkboxId} className="flex items-start gap-2.5" style={{ margin: "0 0 16px", fontSize: 13.5, lineHeight: 1.5, cursor: "pointer" }}>
            <input
              id={checkboxId}
              type="checkbox"
              checked={accepted}
              onChange={(e) => setAccepted(e.target.checked)}
              style={{ marginTop: 3, width: 15, height: 15, flex: "none", accentColor: "var(--text-primary)", cursor: "pointer" }}
            />
            <span>
              I accept the{" "}
              <a href="/affiliate#terms" target="_blank" rel="noopener noreferrer" style={{ color: "var(--text-primary)", textDecoration: "underline", textUnderlineOffset: 3 }}>affiliate programme terms</a>
              {" "}and Pivot&apos;s{" "}
              <a href="/terms" target="_blank" rel="noopener noreferrer" style={{ color: "var(--text-primary)", textDecoration: "underline", textUnderlineOffset: 3 }}>terms &amp; conditions</a>.
            </span>
          </label>
          {state.kind === "error" && (
            <p role="alert" style={{ margin: "0 0 14px", fontSize: 13.5, textAlign: "center", color: "var(--color-loss, #dc2626)" }}>{state.message}</p>
          )}
          <button
            type="button"
            onClick={() => void join()}
            disabled={!accepted || busy}
            className="mt-auto flex w-full items-center justify-center gap-2"
            style={{
              height: 44, border: "none", borderRadius: 9999, background: "var(--text-primary)", color: "var(--bg-base)",
              fontSize: 14.5, fontWeight: 600, opacity: accepted ? 1 : 0.4, cursor: !accepted || busy ? "default" : "pointer",
              transition: "opacity .15s ease",
            }}
          >
            {busy && <Loader2 size={15} className="animate-spin" aria-hidden={true} />}
            {state.kind === "error" ? "Try again" : "Sign up"}
          </button>
          <p style={{ margin: "12px 0 0", fontSize: 12.5, lineHeight: 1.5, color: "var(--text-tertiary, var(--text-secondary))", textAlign: "center" }}>
            Your link, referrals, commissions and payouts are in Settings → Affiliate.
          </p>
        </div>
      </DialogContent>
    </Dialog>
  );
}
