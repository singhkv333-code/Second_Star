"use client";

/**
 * Login page — premium split layout.
 *   Left  : dark editorial brand panel (true brand surface, theme-independent).
 *   Right : a clean, focused sign-in form on paper white.
 * Aesthetic follows the Quartr/ink tokens in globals.css and AppShell.
 */

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Loader2, Eye, EyeOff } from "lucide-react";
import { loginUser } from "@/lib/api";
import { BrandPanel } from "@/components/auth/BrandPanel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

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
  const [serverError, setServerError] = useState<string | null>(null);
  const [showPassword, setShowPassword] = useState(false);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginFormData>({
    resolver: zodResolver(loginSchema),
  });

  // Cmd/Ctrl+Enter submits
  const onKeyDown = (e: React.KeyboardEvent<HTMLFormElement>): void => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      void handleSubmit(onSubmit)();
    }
  };

  const onSubmit = async (data: LoginFormData): Promise<void> => {
    setServerError(null);
    const result = await loginUser(data);
    if ("error" in result) {
      const codeStr = result.error.code ?? "";
      const status = parseInt(codeStr.replace("http_", ""), 10) || 0;
      setServerError(mapLoginError(status, result.error.message));
      return;
    }
    router.replace("/");
  };

  return (
    <div className="flex min-h-screen" style={{ background: "var(--bg-base)" }}>
      {/* Left — brand panel */}
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
          <div className="w-full max-w-sm">
            <div className="mb-8 flex flex-col gap-2 text-center sm:text-left">
              <h1 className="text-2xl font-semibold tracking-tight">Sign in to Pivot</h1>
              <p className="text-sm text-muted-foreground">
                Enter your email and password to access your account.
              </p>
            </div>

            <form onSubmit={handleSubmit(onSubmit)} onKeyDown={onKeyDown} noValidate className="flex flex-col gap-5">
              {/* Email */}
              <div className="grid gap-2">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  autoFocus
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
                <div className="flex items-center">
                  <Label htmlFor="password">Password</Label>
                  <span className="ml-auto text-xs text-muted-foreground">Forgot? Contact support</span>
                </div>
                <div className="relative">
                  <Input
                    id="password"
                    type={showPassword ? "text" : "password"}
                    autoComplete="current-password"
                    placeholder="••••••••"
                    aria-invalid={!!errors.password}
                    aria-describedby={errors.password ? "password-error" : undefined}
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
                {errors.password && (
                  <p id="password-error" role="alert" className="text-sm text-destructive">
                    {errors.password.message}
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
                    Signing in…
                  </>
                ) : (
                  "Sign in"
                )}
              </Button>
            </form>

            <p className="mt-6 text-center text-sm text-muted-foreground">
              New to Pivot?{" "}
              <Link href="/signup" className="font-medium text-foreground underline-offset-4 hover:underline">
                Create an account
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
