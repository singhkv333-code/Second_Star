"use client";

/**
 * KiteCredentialsPanel — modal for managing Zerodha Kite Connect access.
 *
 * Two-stage flow:
 *   1. Enter API key + API secret (both required by Kite policy) → Save.
 *      Backend stores them in-process and flips out of mock mode.
 *   2. Click "Connect to Zerodha" → frontend fetches /kite/login_url,
 *      window.location -> Kite's hosted login. After auth Kite redirects to
 *      the configured callback (/callback or /kite/callback on this backend),
 *      which exchanges the request_token, saves the access_token to
 *      KiteSession, and redirects to `/?kite=connected` (or `?kite=error`).
 *      AppShell detects that query param, re-opens this panel, and passes
 *      `oauthResult` so we can show success / failure inline.
 */

import { useCallback, useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { CheckCircle2, ExternalLink, Eye, EyeOff, KeyRound, Loader2, ShoppingCart, XCircle } from "lucide-react";
import {
  cancelKiteTestOrder,
  clearKiteCredentials,
  disconnectKite,
  getKiteCredentials,
  getKiteLoginUrl,
  getKiteStatus,
  placeKiteTestOrder,
  setKiteCredentials,
  type KiteCredentialsStatus,
  type KiteStatus,
  type KiteTestOrderResult,
} from "@/lib/api";
import { isError } from "@/lib/types";

type SubmitState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved" }
  | { kind: "error"; message: string };

export type KiteOAuthResult =
  | { kind: "connected" }
  | { kind: "error"; reason: string };

export type KiteCredentialsPanelProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Result of the most recent OAuth round-trip, surfaced by AppShell after
   *  reading the ?kite=… query param on mount. Cleared by AppShell once
   *  read, so the panel only shows it once. */
  oauthResult?: KiteOAuthResult | null;
};

export function KiteCredentialsPanel({
  open,
  onOpenChange,
  oauthResult,
}: KiteCredentialsPanelProps): React.ReactElement {
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [showSecret, setShowSecret] = useState(false);
  const [creds, setCreds] = useState<KiteCredentialsStatus | null>(null);
  const [conn, setConn] = useState<KiteStatus | null>(null);
  const [submit, setSubmit] = useState<SubmitState>({ kind: "idle" });
  const [connecting, setConnecting] = useState(false);
  const [testOrder, setTestOrder] = useState<KiteTestOrderResult | null>(null);
  const [orderState, setOrderState] = useState<
    | { kind: "idle" }
    | { kind: "placing" }
    | { kind: "cancelling" }
    | { kind: "cancelled"; order_id: string }
    | { kind: "error"; message: string }
  >({ kind: "idle" });

  const refresh = useCallback(async (): Promise<void> => {
    const [credsRes, connRes] = await Promise.all([
      getKiteCredentials(),
      getKiteStatus(),
    ]);
    setCreds(isError(credsRes) ? null : credsRes.data);
    setConn(isError(connRes) ? null : connRes.data);
  }, []);

  useEffect(() => {
    if (!open) return;
    setSubmit({ kind: "idle" });
    setApiKey("");
    setApiSecret("");
    setShowSecret(false);
    setTestOrder(null);
    setOrderState({ kind: "idle" });
    void refresh();
  }, [open, refresh]);

  const handleSave = useCallback(async (): Promise<void> => {
    if (!apiKey.trim() || !apiSecret.trim()) {
      setSubmit({
        kind: "error",
        message: "API key and API secret are both required.",
      });
      return;
    }
    setSubmit({ kind: "saving" });
    const result = await setKiteCredentials(apiKey.trim(), apiSecret.trim());
    if (isError(result)) {
      setSubmit({
        kind: "error",
        message: result.error.message || "Failed to save credentials.",
      });
      return;
    }
    setCreds(result.data);
    setSubmit({ kind: "saved" });
    setApiSecret("");
  }, [apiKey, apiSecret]);

  const handleClear = useCallback(async (): Promise<void> => {
    setSubmit({ kind: "saving" });
    const result = await clearKiteCredentials();
    if (isError(result)) {
      setSubmit({
        kind: "error",
        message: result.error.message || "Failed to clear credentials.",
      });
      return;
    }
    setCreds(result.data);
    setSubmit({ kind: "saved" });
    setApiKey("");
    setApiSecret("");
    void refresh();
  }, [refresh]);

  const handleConnect = useCallback(async (): Promise<void> => {
    setConnecting(true);
    const result = await getKiteLoginUrl();
    if (isError(result)) {
      setSubmit({
        kind: "error",
        message: result.error.message || "Couldn't get login URL.",
      });
      setConnecting(false);
      return;
    }
    const url = result.data.login_url;
    if (!url) {
      setSubmit({
        kind: "error",
        message: "Backend is in mock mode — save your credentials first.",
      });
      setConnecting(false);
      return;
    }
    // Hard nav to Kite. After login Kite redirects to our /callback alias
    // (or /kite/callback), which redirects back here with ?kite=connected
    // (or ?kite=error&reason=...).
    window.location.href = url;
  }, []);

  const handlePlaceTestOrder = useCallback(async (): Promise<void> => {
    setOrderState({ kind: "placing" });
    setTestOrder(null);
    const result = await placeKiteTestOrder();
    if (isError(result)) {
      setOrderState({
        kind: "error",
        message: result.error.message || "Couldn't place order.",
      });
      return;
    }
    setTestOrder(result.data);
    setOrderState({ kind: "idle" });
  }, []);

  const handleCancelTestOrder = useCallback(async (): Promise<void> => {
    if (!testOrder) return;
    setOrderState({ kind: "cancelling" });
    const result = await cancelKiteTestOrder(
      testOrder.order_id,
      testOrder.variety,
    );
    if (isError(result)) {
      setOrderState({
        kind: "error",
        message: result.error.message || "Couldn't cancel order.",
      });
      return;
    }
    setOrderState({ kind: "cancelled", order_id: testOrder.order_id });
    setTestOrder(null);
  }, [testOrder]);

  const handleDisconnect = useCallback(async (): Promise<void> => {
    const result = await disconnectKite();
    if (isError(result)) {
      setSubmit({
        kind: "error",
        message: result.error.message || "Failed to disconnect.",
      });
      return;
    }
    setConn(result.data);
    setSubmit({ kind: "saved" });
  }, []);

  const hasCreds = !!creds?.has_api_key && !!creds?.has_api_secret;
  const isConnected = !!conn?.connected;
  const liveMode = creds && !creds.mock_mode;
  const saving = submit.kind === "saving";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <KeyRound size={16} aria-hidden={true} />
            Zerodha Kite credentials
          </DialogTitle>
          <DialogDescription>
            Enter your Kite Connect API key + secret, then click{" "}
            <strong>Connect to Zerodha</strong> to authorise this app for
            order placement.
          </DialogDescription>
        </DialogHeader>

        {oauthResult?.kind === "connected" && (
          <div
            role="status"
            data-testid="kite-oauth-connected"
            className="flex items-start gap-2 rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-500"
          >
            <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
            <span>
              Connected to Zerodha. Access token stored — order placement is
              now live.
            </span>
          </div>
        )}
        {oauthResult?.kind === "error" && (
          <div
            role="alert"
            data-testid="kite-oauth-error"
            className="flex items-start gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-500"
          >
            <XCircle size={14} className="mt-0.5 shrink-0" />
            <span>OAuth failed: {oauthResult.reason}</span>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-muted-foreground">Credentials:</span>
          {liveMode ? (
            <Badge
              variant="default"
              data-testid="kite-cred-status"
              className="bg-emerald-600 hover:bg-emerald-600"
            >
              {creds?.api_key_masked || "set"}
            </Badge>
          ) : (
            <Badge variant="secondary" data-testid="kite-cred-status">
              Mock mode (no credentials)
            </Badge>
          )}
          <span className="ml-2 text-muted-foreground">Session:</span>
          {isConnected ? (
            <Badge
              variant="default"
              data-testid="kite-conn-status"
              className="bg-emerald-600 hover:bg-emerald-600"
            >
              Live — {conn?.kite_user_id ?? "connected"}
            </Badge>
          ) : (
            <Badge variant="secondary" data-testid="kite-conn-status">
              Not connected
            </Badge>
          )}
        </div>

        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="kite-api-key">API key</Label>
            <Input
              id="kite-api-key"
              data-testid="kite-api-key"
              autoComplete="off"
              spellCheck={false}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="Your Kite Connect API key"
              disabled={saving}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="kite-api-secret">API secret</Label>
            <div className="relative">
              <Input
                id="kite-api-secret"
                data-testid="kite-api-secret"
                autoComplete="off"
                spellCheck={false}
                type={showSecret ? "text" : "password"}
                value={apiSecret}
                onChange={(e) => setApiSecret(e.target.value)}
                placeholder="Paste your Kite API secret"
                disabled={saving}
                className="pr-9"
              />
              <button
                type="button"
                aria-label={showSecret ? "Hide secret" : "Show secret"}
                onClick={() => setShowSecret((v) => !v)}
                className="absolute inset-y-0 right-0 flex items-center px-2 text-muted-foreground hover:text-foreground"
                tabIndex={-1}
              >
                {showSecret ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
            <p className="text-[11px] text-muted-foreground">
              Stored only in the running backend process. Never written to
              disk in this build.
            </p>
          </div>

          {submit.kind === "error" && (
            <div
              role="alert"
              data-testid="kite-cred-error"
              className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-500"
            >
              {submit.message}
            </div>
          )}
          {submit.kind === "saved" && (
            <div
              role="status"
              data-testid="kite-cred-saved"
              className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-500"
            >
              Saved.
            </div>
          )}
        </div>

        {hasCreds && (
          <div className="rounded-md border border-border bg-muted/30 p-3 text-xs">
            <p className="mb-2 text-muted-foreground">
              <strong className="text-foreground">Step 2:</strong> redirect to
              Kite to authorise this app. Your Kite developer app&apos;s
              Redirect URL should point to
              <code className="ml-1 rounded bg-muted px-1 py-0.5 font-mono">
                http://127.0.0.1:8000/callback
              </code>
              {" "}or{" "}
              <code className="rounded bg-muted px-1 py-0.5 font-mono">
                /kite/callback
              </code>.
            </p>
            <Button
              type="button"
              variant="default"
              onClick={handleConnect}
              disabled={connecting}
              data-testid="kite-connect-btn"
              className="w-full"
            >
              {connecting ? (
                <>
                  <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                  Redirecting…
                </>
              ) : (
                <>
                  <ExternalLink size={14} className="mr-2" />
                  {isConnected ? "Re-connect to Zerodha" : "Connect to Zerodha"}
                </>
              )}
            </Button>
            {isConnected && (
              <button
                type="button"
                onClick={handleDisconnect}
                disabled={saving}
                data-testid="kite-disconnect-btn"
                className="mt-2 w-full text-[11px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
              >
                Disconnect this session
              </button>
            )}
          </div>
        )}

        {isConnected && (
          <div className="rounded-md border border-border bg-muted/30 p-3 text-xs">
            <p className="mb-2 text-muted-foreground">
              <strong className="text-foreground">Step 3:</strong> place a
              safe live test — LIMIT BUY 1 TCS on BSE @ ₹3500 (well below
              market). Verifies the full wire (credentials → access token →
              real kite.place_order). Falls back to AMO when markets are
              closed. Cancel before market open to avoid any fill.
            </p>

            {!testOrder ? (
              <Button
                type="button"
                variant="default"
                onClick={handlePlaceTestOrder}
                disabled={orderState.kind === "placing"}
                data-testid="kite-place-test-order"
                className="w-full"
              >
                {orderState.kind === "placing" ? (
                  <>
                    <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                    Placing…
                  </>
                ) : (
                  <>
                    <ShoppingCart size={14} className="mr-2" />
                    Place test order (BUY 1 TCS @ ₹3500 LIMIT, BSE)
                  </>
                )}
              </Button>
            ) : (
              <div className="space-y-2">
                <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-emerald-500">
                  <p className="font-medium">Order placed on Kite ✔</p>
                  <p className="mt-0.5 font-mono text-[11px]">
                    order_id: {testOrder.order_id}
                  </p>
                  <p className="font-mono text-[11px]">
                    variety: {testOrder.variety}
                    {testOrder.regular_error && " (regular rejected — placed as AMO)"}
                  </p>
                </div>
                <Button
                  type="button"
                  variant="destructive"
                  onClick={handleCancelTestOrder}
                  disabled={orderState.kind === "cancelling"}
                  data-testid="kite-cancel-test-order"
                  className="w-full"
                >
                  {orderState.kind === "cancelling" ? (
                    <>
                      <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                      Cancelling…
                    </>
                  ) : (
                    <>
                      <XCircle size={14} className="mr-2" />
                      Cancel order {testOrder.order_id}
                    </>
                  )}
                </Button>
              </div>
            )}

            {orderState.kind === "cancelled" && (
              <div className="mt-2 rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-emerald-500">
                Cancelled order {orderState.order_id}.
              </div>
            )}
            {orderState.kind === "error" && (
              <div
                role="alert"
                data-testid="kite-test-order-error"
                className="mt-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-red-500"
              >
                {orderState.message}
              </div>
            )}
          </div>
        )}

        <DialogFooter className="gap-2 sm:gap-2">
          {creds?.has_api_key && (
            <Button
              type="button"
              variant="ghost"
              onClick={handleClear}
              disabled={saving}
              data-testid="kite-cred-clear"
            >
              Clear credentials
            </Button>
          )}
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={saving}
          >
            Close
          </Button>
          <Button
            type="button"
            onClick={handleSave}
            disabled={saving || !apiKey.trim() || !apiSecret.trim()}
            data-testid="kite-cred-save"
          >
            {saving ? (
              <>
                <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                Saving…
              </>
            ) : (
              "Save credentials"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
