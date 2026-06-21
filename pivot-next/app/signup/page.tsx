"use client";

/**
 * Signup page — split-screen: Pivot brand on the left, registration form
 * on the right. Matches the login page layout exactly.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Loader2 } from "lucide-react";
import { registerUser } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

// ---------------------------------------------------------------------------
// Password-strength types + helpers
// ---------------------------------------------------------------------------

type StrengthLevel = "empty" | "weak" | "fair" | "strong";

function measureStrength(pw: string): {
  level: StrengthLevel;
  score: number; // 0–3
  hints: string[];
} {
  if (!pw) return { level: "empty", score: 0, hints: [] };
  const hints: string[] = [];
  let score = 0;

  if (pw.length >= 8) {
    score += 1;
  } else {
    hints.push("At least 8 characters");
  }
  if (/[a-zA-Z]/.test(pw)) {
    score += 1;
  } else {
    hints.push("Include a letter");
  }
  if (/[0-9]/.test(pw)) {
    score += 1;
  } else {
    hints.push("Include a number");
  }

  const level: StrengthLevel =
    score === 0 ? "empty"
    : score === 1 ? "weak"
    : score === 2 ? "fair"
    : "strong";

  return { level, score, hints };
}

const STRENGTH_COLORS: Record<StrengthLevel, string> = {
  empty: "var(--glass-border)",
  weak: "hsl(var(--destructive))",
  fair: "var(--color-warn)",
  strong: "var(--color-profit)",
};

const STRENGTH_LABELS: Record<StrengthLevel, string> = {
  empty: "",
  weak: "Weak",
  fair: "Fair",
  strong: "Strong",
};

// ---------------------------------------------------------------------------
// Schema — mirrors backend password rule (length + letter + digit)
// ---------------------------------------------------------------------------

const signupSchema = z
  .object({
    full_name: z.string().min(1, "Name is required").max(100),
    email: z.string().email("Enter a valid email address"),
    password: z
      .string()
      .min(8, "At least 8 characters")
      .regex(/[a-zA-Z]/, "Must include a letter")
      .regex(/[0-9]/, "Must include a number"),
    confirm_password: z.string().min(1, "Please confirm your password"),
  })
  .refine((d) => d.password === d.confirm_password, {
    message: "Passwords do not match",
    path: ["confirm_password"],
  });

type SignupFormData = z.infer<typeof signupSchema>;

// ---------------------------------------------------------------------------
// Error mapping
// ---------------------------------------------------------------------------

function mapSignupError(status: number, message: string, retryAfter?: string | null): string {
  if (status === 409 || status === 400) {
    const lower = message.toLowerCase();
    if (lower.includes("email") || lower.includes("already") || lower.includes("exist")) {
      return "That email is already registered. Try signing in instead.";
    }
    return message;
  }
  if (status === 429) {
    const mins = retryAfter ? Math.ceil(Number(retryAfter) / 60) : null;
    return mins
      ? `Too many attempts. Try again in ${mins} min.`
      : "Too many attempts. Try again later.";
  }
  if (status === 422) {
    return message || "Please check your details and try again.";
  }
  return message || "Something went wrong. Please try again.";
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function SignupPage(): React.ReactElement {
  const router = useRouter();
  const [serverError, setServerError] = useState<string | null>(null);
  const [watchedPassword, setWatchedPassword] = useState("");

  const {
    register,
    handleSubmit,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<SignupFormData>({
    resolver: zodResolver(signupSchema),
    mode: "onChange",
  });

  // Keep strength meter in sync
  const passwordValue = watch("password", "");
  useEffect(() => {
    setWatchedPassword(passwordValue ?? "");
  }, [passwordValue]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLFormElement>): void => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      void handleSubmit(onSubmit)();
    }
  };

  const strength = measureStrength(watchedPassword);

  const onSubmit = async (data: SignupFormData): Promise<void> => {
    setServerError(null);
    const result = await registerUser({
      email: data.email,
      password: data.password,
      full_name: data.full_name,
    });
    if ("error" in result) {
      const codeStr = result.error.code ?? "";
      const status = parseInt(codeStr.replace("http_", ""), 10) || 0;
      setServerError(mapSignupError(status, result.error.message));
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
          {/* Mobile brand */}
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
              Create an account
            </h1>
            <p className="mt-1.5 text-sm" style={{ color: "var(--text-secondary)" }}>
              Start investing smarter with your own copilot.
            </p>
          </div>

          <form
            onSubmit={handleSubmit(onSubmit)}
            onKeyDown={onKeyDown}
            noValidate
            className="space-y-4"
          >
            {/* Full name */}
            <div className="space-y-1.5">
              <Label
                htmlFor="full_name"
                className="text-sm font-medium"
                style={{ color: "var(--text-primary)" }}
              >
                Full name
              </Label>
              <Input
                id="full_name"
                type="text"
                autoComplete="name"
                autoFocus
                placeholder="Arjun Sharma"
                aria-invalid={!!errors.full_name}
                aria-describedby={errors.full_name ? "name-error" : undefined}
                {...register("full_name")}
                className="h-10"
                style={{
                  background: "var(--bg-primary)",
                  borderColor: errors.full_name ? "hsl(var(--destructive))" : "var(--glass-border)",
                }}
              />
              {errors.full_name && (
                <p
                  id="name-error"
                  role="alert"
                  className="text-xs"
                  style={{ color: "hsl(var(--destructive))" }}
                >
                  {errors.full_name.message}
                </p>
              )}
            </div>

            {/* Email */}
            <div className="space-y-1.5">
              <Label
                htmlFor="email"
                className="text-sm font-medium"
                style={{ color: "var(--text-primary)" }}
              >
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
                className="h-10"
                style={{
                  background: "var(--bg-primary)",
                  borderColor: errors.email ? "hsl(var(--destructive))" : "var(--glass-border)",
                }}
              />
              {errors.email && (
                <p
                  id="email-error"
                  role="alert"
                  className="text-xs"
                  style={{ color: "hsl(var(--destructive))" }}
                >
                  {errors.email.message}
                </p>
              )}
            </div>

            {/* Password */}
            <div className="space-y-1.5">
              <Label
                htmlFor="password"
                className="text-sm font-medium"
                style={{ color: "var(--text-primary)" }}
              >
                Password
              </Label>
              <Input
                id="password"
                type="password"
                autoComplete="new-password"
                placeholder="••••••••"
                aria-invalid={!!errors.password}
                aria-describedby="password-hints"
                {...register("password")}
                className="h-10"
                style={{
                  background: "var(--bg-primary)",
                  borderColor: errors.password ? "hsl(var(--destructive))" : "var(--glass-border)",
                }}
              />

              {/* Strength meter */}
              {watchedPassword.length > 0 && (
                <div className="space-y-1" id="password-hints" aria-live="polite">
                  <div className="flex gap-1">
                    {[0, 1, 2].map((i) => (
                      <div
                        key={i}
                        style={{
                          flex: 1,
                          height: 3,
                          borderRadius: 99,
                          background:
                            strength.score > i
                              ? STRENGTH_COLORS[strength.level]
                              : "var(--glass-border)",
                          transition: "background 0.2s var(--ease-quartr)",
                        }}
                      />
                    ))}
                  </div>
                  <div className="flex items-center justify-between">
                    {strength.level !== "empty" && (
                      <span
                        className="text-xs font-medium"
                        style={{ color: STRENGTH_COLORS[strength.level] }}
                      >
                        {STRENGTH_LABELS[strength.level]}
                      </span>
                    )}
                    {strength.hints.length > 0 && (
                      <span className="text-xs" style={{ color: "var(--text-tertiary)" }}>
                        {strength.hints[0]}
                      </span>
                    )}
                  </div>
                </div>
              )}

              {errors.password && (
                <p
                  role="alert"
                  className="text-xs"
                  style={{ color: "hsl(var(--destructive))" }}
                >
                  {errors.password.message}
                </p>
              )}
            </div>

            {/* Confirm password */}
            <div className="space-y-1.5">
              <Label
                htmlFor="confirm_password"
                className="text-sm font-medium"
                style={{ color: "var(--text-primary)" }}
              >
                Confirm password
              </Label>
              <Input
                id="confirm_password"
                type="password"
                autoComplete="new-password"
                placeholder="••••••••"
                aria-invalid={!!errors.confirm_password}
                aria-describedby={errors.confirm_password ? "confirm-error" : undefined}
                {...register("confirm_password")}
                className="h-10"
                style={{
                  background: "var(--bg-primary)",
                  borderColor: errors.confirm_password
                    ? "hsl(var(--destructive))"
                    : "var(--glass-border)",
                }}
              />
              {errors.confirm_password && (
                <p
                  id="confirm-error"
                  role="alert"
                  className="text-xs"
                  style={{ color: "hsl(var(--destructive))" }}
                >
                  {errors.confirm_password.message}
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
                  Creating account…
                </>
              ) : (
                "Create account"
              )}
            </Button>
          </form>

          <p className="mt-6 text-center text-sm" style={{ color: "var(--text-secondary)" }}>
            Already have an account?{" "}
            <Link
              href="/login"
              className="font-medium underline-offset-4 hover:underline"
              style={{ color: "var(--text-primary)" }}
            >
              Sign in
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Left brand panel (shared visual identity with login page)
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
      {/* Gradient orb */}
      <div
        aria-hidden="true"
        style={{
          position: "absolute",
          bottom: -80,
          right: -80,
          width: 360,
          height: 360,
          borderRadius: "50%",
          background:
            "radial-gradient(circle, rgba(33,158,188,0.07) 0%, transparent 70%)",
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

      {/* Hero copy */}
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
          One conversation. Live prices, automations, backtests, paper trades —
          no fragmented tools.
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

      {/* Footer */}
      <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>
        Data &amp; analysis only. Not financial advice.
      </p>
    </div>
  );
}
