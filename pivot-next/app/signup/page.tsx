"use client";

/**
 * Signup page — shares the dark editorial brand panel with the login page;
 * only the right-side form differs (name, email, password + strength,
 * confirm password).
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Loader2, Eye, EyeOff } from "lucide-react";
import { registerUser } from "@/lib/api";
import { BrandPanel } from "@/components/auth/BrandPanel";
import { armLoginIntro } from "@/components/onboarding/LoginIntroGate";
import { AnybodyFontPreload } from "@/components/onboarding/AnybodyFontPreload";
import { TOUR_PENDING_KEY } from "@/components/onboarding/ProductTour";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

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
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);

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
    // New account → arm the first-run product tour (fires after the brand
    // intro completes). Also mark as a fresh sign-in so AppShell shows
    // the paper-mode notice.
    try {
      localStorage.setItem(TOUR_PENDING_KEY, "1");
      sessionStorage.setItem("pivot:just-signed-in", "1");
    } catch {
      /* private mode / storage full — skip; no functional impact */
    }
    armLoginIntro();
    router.replace("/");
  };

  return (
    <div className="flex min-h-screen" style={{ background: "var(--bg-base)" }}>
      {/* Warm the intro's display font while the user types. */}
      <AnybodyFontPreload />
      {/* Left — shared brand panel */}
      <BrandPanel />

      {/* Right — form */}
      <div className="flex flex-1 flex-col px-6 py-8 sm:px-10 lg:px-14">
        {/* Top bar: mobile wordmark (hidden on lg — the brand panel carries it) */}
        <div className="lg:hidden">
          <span
            className="text-foreground"
            style={{ fontFamily: "var(--font-experiment)", fontWeight: 600, fontSize: 24, letterSpacing: "-0.03em" }}
          >
            pivot
          </span>
        </div>

        {/* Centered form */}
        <div className="flex flex-1 items-center justify-center">
          <div className="w-full max-w-sm py-10">
            <div className="mb-8 flex flex-col gap-2 text-center sm:text-left">
              <h1 className="text-2xl font-semibold tracking-tight">Create your account</h1>
              <p className="text-sm text-muted-foreground">
                Start investing smarter with your own copilot.
              </p>
            </div>

            <form onSubmit={handleSubmit(onSubmit)} onKeyDown={onKeyDown} noValidate className="flex flex-col gap-5">
              {/* Full name */}
              <div className="grid gap-2">
                <Label htmlFor="full_name">Full name</Label>
                <Input
                  id="full_name"
                  type="text"
                  autoComplete="name"
                  autoFocus
                  placeholder="Arjun Sharma"
                  aria-invalid={!!errors.full_name}
                  aria-describedby={errors.full_name ? "name-error" : undefined}
                  {...register("full_name")}
                  className={cn(errors.full_name && "border-destructive focus-visible:ring-destructive")}
                />
                {errors.full_name && (
                  <p id="name-error" role="alert" className="text-sm text-destructive">
                    {errors.full_name.message}
                  </p>
                )}
              </div>

              {/* Email */}
              <div className="grid gap-2">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  placeholder="you@example.com"
                  aria-invalid={!!errors.email}
                  aria-describedby={errors.email ? "email-error" : undefined}
                  {...register("email")}
                  className={cn(errors.email && "border-destructive focus-visible:ring-destructive")}
                />
                {errors.email && (
                  <p id="email-error" role="alert" className="text-sm text-destructive">
                    {errors.email.message}
                  </p>
                )}
              </div>

              {/* Password */}
              <div className="grid gap-2">
                <Label htmlFor="password">Password</Label>
                <div className="relative">
                  <Input
                    id="password"
                    type={showPassword ? "text" : "password"}
                    autoComplete="new-password"
                    placeholder="••••••••"
                    aria-invalid={!!errors.password}
                    aria-describedby="password-hints"
                    {...register("password")}
                    className={cn("pr-10", errors.password && "border-destructive focus-visible:ring-destructive")}
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    aria-label={showPassword ? "Hide password" : "Show password"}
                    className="absolute right-0 top-0 flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-foreground"
                    tabIndex={-1}
                  >
                    {showPassword ? <EyeOff className="size-4" aria-hidden="true" /> : <Eye className="size-4" aria-hidden="true" />}
                  </button>
                </div>

                {/* Strength meter */}
                {watchedPassword.length > 0 && (
                  <div className="grid gap-1.5 pt-0.5" id="password-hints" aria-live="polite">
                    <div className="flex gap-1">
                      {[0, 1, 2].map((i) => (
                        <div
                          key={i}
                          className="h-1 flex-1 rounded-full transition-colors"
                          style={{
                            background: strength.score > i ? STRENGTH_COLORS[strength.level] : "hsl(var(--muted))",
                          }}
                        />
                      ))}
                    </div>
                    <div className="flex items-center justify-between text-xs">
                      {strength.level !== "empty" && (
                        <span className="font-medium" style={{ color: STRENGTH_COLORS[strength.level] }}>
                          {STRENGTH_LABELS[strength.level]}
                        </span>
                      )}
                      {strength.hints.length > 0 && (
                        <span className="text-muted-foreground">{strength.hints[0]}</span>
                      )}
                    </div>
                  </div>
                )}

                {errors.password && (
                  <p role="alert" className="text-sm text-destructive">
                    {errors.password.message}
                  </p>
                )}
              </div>

              {/* Confirm password */}
              <div className="grid gap-2">
                <Label htmlFor="confirm_password">Confirm password</Label>
                <div className="relative">
                  <Input
                    id="confirm_password"
                    type={showConfirm ? "text" : "password"}
                    autoComplete="new-password"
                    placeholder="••••••••"
                    aria-invalid={!!errors.confirm_password}
                    aria-describedby={errors.confirm_password ? "confirm-error" : undefined}
                    {...register("confirm_password")}
                    className={cn("pr-10", errors.confirm_password && "border-destructive focus-visible:ring-destructive")}
                  />
                  <button
                    type="button"
                    onClick={() => setShowConfirm((v) => !v)}
                    aria-label={showConfirm ? "Hide password" : "Show password"}
                    className="absolute right-0 top-0 flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-foreground"
                    tabIndex={-1}
                  >
                    {showConfirm ? <EyeOff className="size-4" aria-hidden="true" /> : <Eye className="size-4" aria-hidden="true" />}
                  </button>
                </div>
                {errors.confirm_password && (
                  <p id="confirm-error" role="alert" className="text-sm text-destructive">
                    {errors.confirm_password.message}
                  </p>
                )}
              </div>

              {/* Server error */}
              {serverError && (
                <div
                  role="alert"
                  className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
                >
                  {serverError}
                </div>
              )}

              {/* Submit */}
              <Button type="submit" disabled={isSubmitting} className="w-full">
                {isSubmitting ? (
                  <>
                    <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                    Creating account…
                  </>
                ) : (
                  "Create account"
                )}
              </Button>
            </form>

            <p className="mt-6 text-center text-sm text-muted-foreground">
              Already have an account?{" "}
              <Link href="/login" className="font-medium text-foreground underline-offset-4 hover:underline">
                Sign in
              </Link>
            </p>
          </div>
        </div>

        {/* Footer — hidden on lg (the brand panel carries the disclaimer there) */}
        <p className="text-center text-xs text-muted-foreground lg:hidden">
          Data &amp; analysis only. Not financial advice.
        </p>
      </div>
    </div>
  );
}
