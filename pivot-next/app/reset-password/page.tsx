"use client";

/**
 * Reset password — the page the reset email links to, and the page "Forgot
 * password?" opens.
 *   No token : ask for the email; Charto mails a one-time link (POST /auth/reset/request).
 *   ?token=  : choose a new password (POST /auth/reset). Success signs the
 *              person in on this device; every other session is signed out.
 * The request answer never says whether an account exists, so this page
 * never does either.
 */

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { storeToken } from "@/lib/api";
import { BrandPanel } from "@/components/auth/BrandPanel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const CHARTO_TOKEN_KEY = "charto:auth:token";

async function post(path: string, body: Record<string, string>): Promise<{ ok: boolean; data: Record<string, unknown> }> {
  try {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    if (res.status === 429 && !data.error) data.error = "Too many attempts. Please try again in a few minutes.";
    return { ok: res.ok, data };
  } catch {
    return { ok: false, data: { error: "Could not reach Pivot. Check your connection and try again." } };
  }
}

function RequestLink(): React.ReactElement {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    setError(null);
    if (!/^\S+@\S+\.\S+$/.test(email.trim())) { setError("Enter a valid email address."); return; }
    setBusy(true);
    const { ok, data } = await post("/auth/reset/request", { email: email.trim() });
    setBusy(false);
    if (!ok) { setError(String(data.error || "Something went wrong. Please try again.")); return; }
    setSent(String(data.message || "If an account exists for that email, a reset link is on its way."));
  };

  if (sent) {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">Check your email</h1>
        <p className="text-[15px] leading-relaxed text-foreground">{sent}</p>
        <p className="text-[15px] leading-relaxed text-foreground">
          The email comes from contact@pivotnow.in. If it hasn&apos;t arrived in a few minutes, check your spam folder.
        </p>
        <Link href="/login" className="mt-2 text-[15px] font-medium text-foreground underline underline-offset-4">
          Back to sign in
        </Link>
      </div>
    );
  }

  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-5">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">Reset your password</h1>
        <p className="text-[15px] leading-relaxed text-foreground">
          Enter the email you sign in with. We&apos;ll send you a link to choose a new password.
        </p>
      </div>
      <div className="grid gap-2">
        <Label htmlFor="email">Email</Label>
        <Input id="email" type="email" autoComplete="email" autoFocus placeholder="you@example.com"
          value={email} onChange={(e) => setEmail(e.target.value)} aria-invalid={!!error} />
      </div>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <Button type="submit" disabled={busy} className="w-full">
        {busy ? <><Loader2 className="size-4 animate-spin" aria-hidden="true" />Sending…</> : "Email me a reset link"}
      </Button>
      <Link href="/login" className="text-center text-[15px] font-medium text-foreground underline-offset-4 hover:underline">
        Back to sign in
      </Link>
    </form>
  );
}

function ChoosePassword({ token }: { token: string }): React.ReactElement {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    setError(null);
    if (password.length < 8) { setError("Use at least 8 characters."); return; }
    if (password !== confirm) { setError("The two passwords don't match."); return; }
    setBusy(true);
    const { ok, data } = await post("/auth/reset", { token, password });
    if (!ok) {
      setBusy(false);
      setError(String(data.error || "Something went wrong. Please try again."));
      return;
    }
    const session = String(data.token || data.access_token || "");
    if (session) {
      storeToken(session);
      try { window.localStorage.setItem(CHARTO_TOKEN_KEY, session); } catch { /* storage denied */ }
    }
    router.replace(session ? "/" : "/login");
  };

  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-5">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">Choose a new password</h1>
        <p className="text-[15px] leading-relaxed text-foreground">
          Use at least 8 characters. Saving it signs you out on every other device.
        </p>
      </div>
      <div className="grid gap-2">
        <Label htmlFor="password">New password</Label>
        <Input id="password" type="password" autoComplete="new-password" autoFocus
          value={password} onChange={(e) => setPassword(e.target.value)} />
      </div>
      <div className="grid gap-2">
        <Label htmlFor="confirm">Confirm new password</Label>
        <Input id="confirm" type="password" autoComplete="new-password"
          value={confirm} onChange={(e) => setConfirm(e.target.value)} />
      </div>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}{" "}
          {error.includes("expired") && <Link href="/reset-password" className="underline underline-offset-4">Request a new link</Link>}
        </p>
      )}
      <Button type="submit" disabled={busy} className="w-full">
        {busy ? <><Loader2 className="size-4 animate-spin" aria-hidden="true" />Saving…</> : "Save and sign in"}
      </Button>
    </form>
  );
}

function ResetPassword(): React.ReactElement {
  const token = useSearchParams().get("token") || "";
  return token ? <ChoosePassword token={token} /> : <RequestLink />;
}

export default function ResetPasswordPage(): React.ReactElement {
  return (
    <div className="flex min-h-screen" style={{ background: "var(--bg-base)" }}>
      <BrandPanel />
      <div className="flex flex-1 flex-col px-6 py-8 sm:px-10 lg:px-14">
        <div className="flex flex-1 items-center justify-center">
          <div className="w-full max-w-sm">
            <Suspense fallback={null}>
              <ResetPassword />
            </Suspense>
          </div>
        </div>
      </div>
    </div>
  );
}
