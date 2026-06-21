"use client";

/**
 * Login page — split-screen: Pivot brand on the left, form on the right.
 * Matches the Quartr/ink aesthetic established in globals.css and AppShell.
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Loader2 } from "lucide-react";
import { loginUser } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

// ---------------------------------------------------------------------------
// Schema
// ---------------------------------------------------------------------------

const loginSchema = z.object({
  email: z.string().email("Enter a valid email address"),
  password: z.string().min(1, "Password is required"),
});

type LoginFormData = z.infer<typeof loginSchema>;

// ---------------------------------------------------------------------------
// Error mapping
// ---------------------------------------------------------------------------

function mapLoginError(status: number, message: string, retryAfter?: string | null): string {
  if (status === 401) return "Invalid email or password.";
  if (status === 429) {
    const mins = retryAfter ? Math.ceil(Number(retryAfter) / 60) : null;
    return mins
      ? `Too many attempts. Try again in ${mins} min.`
      : "Too many attempts. Try again later.";
  }
  return message || "Something went wrong. Please try again.";
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function LoginPage(): React.ReactElement {
  const router = useRouter();
  const emailRef = useRef<HTMLInputElement>(null);
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginFormData>({
    resolver: zodResolver(loginSchema),
  });

  // Autofocus first field after mount
  useEffect(() => {
    emailRef.current?.focus();
  }, []);

  // Cmd+Enter submits (handled via form keydown)
  const onKeyDown = (e: React.KeyboardEvent<HTMLFormElement>): void => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      void handleSubmit(onSubmit)();
    }
  };

  const onSubmit = async (data: LoginFormData): Promise<void> => {
    setServerError(null);
    const result = await loginUser(data);
    if ("error" in result) {
      // Extract HTTP status from the error code (e.g. "http_401")
      const codeStr = result.error.code ?? "";
      const status = parseInt(codeStr.replace("http_", ""), 10) || 0;
      setServerError(mapLoginError(status, result.error.message));
      return;
    }
    router.replace("/");
  };

  return (
    <div className="flex min-h-screen">
      {/* Left — brand panel */}
      <LeftPanel />

      {/* Right — form */}
      <div className="flex flex-1 flex-col items-center justify-center px-6 py-12 lg:px-12">
        <div className="w-full max-w-sm">
          {/* Mobile brand (visible below lg) */}
          <div className="mb-8 lg:hidden">
            <span
              style={{
                fontFamily: "var(--font-experiment)",
                fontWeight: 600,
                fontSize: 26,
                letterSpacing: "-0.02em",
                color: "var(--text-primary)",
              }}
            >
              pivot
            </span>
          </div>

          <div className="mb-8">
            <h1
              className="text-2xl font-semibold tracking-tight"
              style={{ color: "var(--text-primary)", letterSpacing: "-0.025em" }}
            >
              Sign in
            </h1>
            <p className="mt-1.5 text-sm" style={{ color: "var(--text-secondary)" }}>
              Welcome back. Your copilot is waiting.
            </p>
          </div>

          <form
            onSubmit={handleSubmit(onSubmit)}
            onKeyDown={onKeyDown}
            noValidate
            className="space-y-4"
          >
            {/* Email */}
            <div className="space-y-1.5">
              <Label htmlFor="email" className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>
                Email
              </Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                placeholder="you@example.com"
                aria-invalid={!!errors.email}
                aria-describedby={errors.email ? "email-error" : undefined}
                {...register("email")}
                ref={(el) => {
                  // Merge react-hook-form ref + our local ref for autofocus
                  const { ref: rhfRef } = register("email");
                  if (typeof rhfRef === "function") rhfRef(el);
                  (emailRef as React.MutableRefObject<HTMLInputElement | null>).current = el;
                }}
                className="h-10"
                style={{
                  background: "var(--bg-primary)",
                  borderColor: errors.email ? "hsl(var(--destructive))" : "var(--glass-border)",
                }}
              />
              {errors.email && (
                <p id="email-error" role="alert" className="text-xs" style={{ color: "hsl(var(--destructive))" }}>
                  {errors.email.message}
                </p>
              )}
            </div>

            {/* Password */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="password" className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>
                  Password
                </Label>
                <span className="text-xs" style={{ color: "var(--text-tertiary)" }}>
                  Forgot password? Contact support.
                </span>
              </div>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                placeholder="••••••••"
                aria-invalid={!!errors.password}
                aria-describedby={errors.password ? "password-error" : undefined}
                {...register("password")}
                className="h-10"
                style={{
                  background: "var(--bg-primary)",
                  borderColor: errors.password ? "hsl(var(--destructive))" : "var(--glass-border)",
                }}
              />
              {errors.password && (
                <p id="password-error" role="alert" className="text-xs" style={{ color: "hsl(var(--destructive))" }}>
                  {errors.password.message}
                </p>
              )}
            </div>

            {/* Server error */}
            {serverError && (
              <div
                role="alert"
                className="rounded-lg px-3 py-2.5 text-sm"
                style={{
                  background: "hsl(var(--destructive) / 0.08)",
                  color: "hsl(var(--destructive))",
                  border: "1px solid hsl(var(--destructive) / 0.2)",
                }}
              >
                {serverError}
              </div>
            )}

            {/* Submit */}
            <Button
              type="submit"
              disabled={isSubmitting}
              className="h-10 w-full text-sm font-semibold"
              style={{ marginTop: 8 }}
            >
              {isSubmitting ? (
                <>
                  <Loader2 size={15} className="animate-spin" aria-hidden="true" />
                  Signing in…
                </>
              ) : (
                "Sign in"
              )}
            </Button>
          </form>

          <p className="mt-6 text-center text-sm" style={{ color: "var(--text-secondary)" }}>
            New to Pivot?{" "}
            <Link
              href="/signup"
              className="font-medium underline-offset-4 hover:underline"
              style={{ color: "var(--text-primary)" }}
            >
              Create an account
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Left brand panel
// ---------------------------------------------------------------------------

function LeftPanel(): React.ReactElement {
  return (
    <div
      className="relative hidden flex-col justify-between overflow-hidden lg:flex lg:w-[44%] xl:w-[42%]"
      style={{
        background: "var(--bg-primary)",
        borderRight: "1px solid var(--glass-border)",
        padding: "48px 56px",
      }}
    >
      {/* Subtle gradient orb — top-left */}
      <div
        aria-hidden="true"
        style={{
          position: "absolute",
          top: -120,
          left: -120,
          width: 420,
          height: 420,
          borderRadius: "50%",
          background:
            "radial-gradient(circle, rgba(33,158,188,0.08) 0%, transparent 70%)",
          pointerEvents: "none",
        }}
      />

      {/* Wordmark */}
      <div>
        <span
          style={{
            fontFamily: "var(--font-experiment)",
            fontWeight: 600,
            fontSize: 28,
            letterSpacing: "-0.02em",
            color: "var(--text-primary)",
          }}
        >
          pivot
        </span>
      </div>

      {/* Hero copy — center of panel */}
      <div style={{ maxWidth: 340 }}>
        <p
          style={{
            fontFamily: "var(--font-experiment)",
            fontStyle: "italic",
            fontWeight: 400,
            fontSize: 32,
            lineHeight: 1.25,
            letterSpacing: "-0.02em",
            color: "var(--text-primary)",
            marginBottom: 20,
          }}
        >
          Your chat-first investing copilot.
        </p>
        <p className="text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>
          Ask questions, build automations, backtest strategies — all from a
          single conversation.
        </p>

        {/* Feature chips */}
        <div className="mt-8 flex flex-wrap gap-2">
          {[
            "Live quotes",
            "Option chains",
            "Workflow agents",
            "Backtests",
            "Paper trading",
          ].map((label) => (
            <span
              key={label}
              className="rounded-full px-3 py-1 text-xs font-medium"
              style={{
                background: "var(--bg-secondary)",
                color: "var(--text-secondary)",
                border: "1px solid var(--glass-border)",
              }}
            >
              {label}
            </span>
          ))}
        </div>
      </div>

      {/* Footer note */}
      <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>
        Data &amp; analysis only. Not financial advice.
      </p>
    </div>
  );
}
