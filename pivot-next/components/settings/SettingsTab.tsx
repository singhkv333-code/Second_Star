"use client";

/**
 * SettingsDialog — Pivot's account surface, in the Claude settings pattern:
 * a left rail (search + sections) and a scrolling pane of hairline-divided
 * rows, label on the left and the control flush right. On <sm the rail
 * becomes a horizontal strip above the content.
 *
 * Tabs:
 *   1. Profile        photo, full name, username, date of birth
 *                     (GET/PATCH /auth/me; each field saves on its own).
 *   2. Account        email, password reset, two-factor, delete request.
 *   3. Usage          AI credits left, plan usage bars, upgrade.
 *   4. Billing        plan, payment method, invoices, cancellation.
 *   5. Trading        empty for now.
 *   6. Notifications  empty for now.
 *
 * Usage and Billing read the plan from BillingProvider (charto's
 * /billing/me), so they agree with /settings/billing and the paywall. Plan
 * changes and cancellation reuse the billing panels in a BillingModal.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Bell,
  CircleUserRound,
  CreditCard,
  Gauge,
  Info,
  Loader2,
  Receipt,
  RefreshCw,
  Search,
  ShieldCheck,
  TrendingUp,
} from "lucide-react";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import {
  getMe,
  requestPasswordReset,
  submitBugReport,
  updateProfile,
  type ProfilePatch,
  type UserProfile,
} from "@/lib/api";
import { isError } from "@/lib/types";
import { useBilling } from "@/components/billing/BillingProvider";
import { BillingModal } from "@/components/billing/modal";
import { PlanChangePanel, PlanPickerPanel } from "@/components/billing/PlanChange";
import { CancelPanel } from "@/components/billing/CancelFlow";
import { billingApi } from "@/lib/billing/api";
import { planName, rankOf } from "@/lib/billing/catalog";
import { meter, quotaView, subscriptionView, type QuotaView } from "@/lib/billing/entitlements";
import { fmtDate, inr, num } from "@/lib/billing/format";
import { track } from "@/lib/billing/analytics";
import type { BillingMe, Cycle, InvoiceList, PaidPlanId, PlanId, PublicCatalog } from "@/lib/billing/types";

type SectionKey = "profile" | "account" | "usage" | "billing" | "trading" | "notifications";

type IconType = React.ComponentType<{ size?: number; strokeWidth?: number; "aria-hidden"?: boolean }>;

const SECTIONS: { key: SectionKey; label: string; Icon: IconType; keywords: string }[] = [
  { key: "profile", label: "Profile", Icon: CircleUserRound, keywords: "photo avatar name username birth dob" },
  { key: "account", label: "Account", Icon: ShieldCheck, keywords: "email password two-factor 2fa delete security" },
  { key: "usage", label: "Usage", Icon: Gauge, keywords: "credits ai limits alerts upgrade" },
  { key: "billing", label: "Billing", Icon: CreditCard, keywords: "plan payment invoices subscription cancel" },
  { key: "trading", label: "Trading", Icon: TrendingUp, keywords: "paper orders" },
  { key: "notifications", label: "Notifications", Icon: Bell, keywords: "alerts email push" },
];

const HAIRLINE = "1px solid var(--glass-border)";
const DANGER = "var(--color-loss, #dc2626)";

// ---------------------------------------------------------------------------
// Dialog
// ---------------------------------------------------------------------------

export type SettingsDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Which tab to land on when opened. */
  initialSection?: SectionKey;
};

export function SettingsDialog({
  open,
  onOpenChange,
  initialSection = "profile",
}: SettingsDialogProps): React.ReactElement {
  const [section, setSection] = useState<SectionKey>(initialSection);
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (open) setSection(initialSection);
  }, [open, initialSection]);

  const close = useCallback(() => onOpenChange(false), [onOpenChange]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        // max-sm:h-[100dvh] pins the mobile full-screen sheet to the visible
        // viewport, so the phone browser's toolbar never clips the last rows.
        className="gap-0 p-0 max-sm:h-[100dvh] sm:max-w-[980px] sm:rounded-2xl"
        style={{ background: "var(--bg-base)", overflow: "hidden" }}
      >
        <DialogTitle className="sr-only">Settings</DialogTitle>
        {/* w-full min-w-0: the dialog is a grid, whose item defaults to
            min-width:auto; without it the mobile nav strip blows the column
            out past the viewport. */}
        <div className="flex h-full w-full min-w-0 flex-col sm:h-[80vh] sm:max-h-[720px] sm:flex-row">
          <SettingsRail active={section} onSelect={setSection} query={query} onQueryChange={setQuery} />

          <div className="min-w-0 flex-1 overflow-y-auto px-5 py-6 sm:px-10 sm:py-9">
            <div className="mx-auto w-full" style={{ maxWidth: 660 }}>
              {section === "profile" && <ProfileSection />}
              {section === "account" && <AccountSection />}
              {section === "usage" && <UsageSection onClose={close} />}
              {section === "billing" && <BillingSection onClose={close} />}
              {section === "trading" && <EmptySection title="Trading" />}
              {section === "notifications" && <EmptySection title="Notifications" />}
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Left rail
// ---------------------------------------------------------------------------

function SettingsRail({
  active,
  onSelect,
  query,
  onQueryChange,
}: {
  active: SectionKey;
  onSelect: (k: SectionKey) => void;
  query: string;
  onQueryChange: (q: string) => void;
}): React.ReactElement {
  const q = query.trim().toLowerCase();
  const items = q
    ? SECTIONS.filter((s) => s.label.toLowerCase().includes(q) || s.keywords.includes(q))
    : SECTIONS;

  return (
    <aside
      className="flex shrink-0 flex-col gap-3 px-3 pb-2 pt-4 sm:w-[220px] sm:gap-2 sm:px-3.5 sm:py-5"
      style={{ background: "var(--bg-primary)", borderBottom: HAIRLINE, borderRight: HAIRLINE }}
    >
      {/* On mobile the dialog's close (X) floats over this corner; leave it room. */}
      <div
        className="mr-11 flex items-center gap-2 sm:mr-0"
        style={{
          height: 38,
          padding: "0 12px",
          background: "var(--bg-base)",
          border: HAIRLINE,
          borderRadius: 10,
        }}
      >
        <Search size={15} strokeWidth={2} aria-hidden={true} style={{ color: "var(--text-tertiary)" }} />
        <input
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder="Search"
          aria-label="Search settings"
          style={{
            flex: 1,
            minWidth: 0,
            background: "transparent",
            border: "none",
            outline: "none",
            fontSize: 14,
            color: "var(--text-primary)",
          }}
        />
      </div>

      <div
        className="hidden sm:block"
        style={{ padding: "12px 10px 4px", fontSize: 12.5, fontWeight: 500, color: "var(--text-tertiary)" }}
      >
        Settings
      </div>

      <nav
        aria-label="Settings sections"
        className="quartr-no-scrollbar flex gap-1 overflow-x-auto sm:flex-col sm:gap-0.5 sm:overflow-visible"
      >
        {items.map(({ key, label, Icon }) => {
          const isActive = key === active;
          return (
            <button
              key={key}
              type="button"
              onClick={() => onSelect(key)}
              aria-current={isActive ? "page" : undefined}
              className="inline-flex shrink-0 items-center gap-3 whitespace-nowrap"
              style={{
                padding: "8px 10px",
                borderRadius: 8,
                background: isActive ? "var(--surface-active)" : "transparent",
                color: isActive ? "var(--text-primary)" : "var(--text-secondary)",
                fontSize: 14,
                fontWeight: isActive ? 600 : 500,
                cursor: "pointer",
                transition: "background 0.18s var(--ease-quartr), color 0.18s var(--ease-quartr)",
              }}
              onMouseEnter={(e) => {
                if (!isActive) e.currentTarget.style.background = "var(--surface-hover)";
              }}
              onMouseLeave={(e) => {
                if (!isActive) e.currentTarget.style.background = "transparent";
              }}
            >
              <Icon size={17} strokeWidth={1.8} aria-hidden={true} />
              {label}
            </button>
          );
        })}
        {items.length === 0 && (
          <div className="hidden sm:block" style={{ padding: "8px 10px", fontSize: 13, color: "var(--text-tertiary)" }}>
            No matches
          </div>
        )}
      </nav>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Building blocks
// ---------------------------------------------------------------------------

/** A titled group. Rows inside carry their own hairline dividers. */
function Group({
  title,
  description,
  action,
  children,
  first = false,
}: {
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  children?: React.ReactNode;
  first?: boolean;
}): React.ReactElement {
  return (
    <section style={{ marginTop: first ? 0 : 44 }}>
      <div className="flex items-start justify-between gap-4" style={{ marginBottom: description ? 4 : 8 }}>
        <h2 style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.01em", color: "var(--text-primary)", margin: 0 }}>
          {title}
        </h2>
        {action}
      </div>
      {description && (
        <p style={{ margin: "0 0 8px", fontSize: 13.5, lineHeight: 1.55, color: "var(--text-tertiary)", maxWidth: 480 }}>
          {description}
        </p>
      )}
      {children}
    </section>
  );
}

/** Label (and quiet hint) on the left, a control flush right. */
function Row({
  label,
  hint,
  control,
  last = false,
  htmlFor,
}: {
  label: React.ReactNode;
  hint?: React.ReactNode;
  control?: React.ReactNode;
  last?: boolean;
  htmlFor?: string;
}): React.ReactElement {
  return (
    <div
      className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2"
      style={{ padding: "16px 0", borderBottom: last ? "none" : HAIRLINE }}
    >
      <div className="min-w-0">
        {htmlFor ? (
          <label htmlFor={htmlFor} style={{ fontSize: 14.5, color: "var(--text-primary)" }}>
            {label}
          </label>
        ) : (
          <div style={{ fontSize: 14.5, color: "var(--text-primary)" }}>{label}</div>
        )}
        {hint && (
          <div style={{ marginTop: 3, fontSize: 13, lineHeight: 1.5, color: "var(--text-tertiary)", maxWidth: 360 }}>
            {hint}
          </div>
        )}
      </div>
      {control && <div className="flex shrink-0 items-center gap-2">{control}</div>}
    </div>
  );
}

type BtnVariant = "secondary" | "primary" | "danger" | "danger-outline";

function Btn({
  children,
  onClick,
  variant = "secondary",
  disabled = false,
  busy = false,
  title,
  href,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  variant?: BtnVariant;
  disabled?: boolean;
  busy?: boolean;
  title?: string;
  href?: string;
}): React.ReactElement {
  const palette: Record<BtnVariant, React.CSSProperties> = {
    secondary: { background: "var(--bg-base)", color: "var(--text-primary)", border: HAIRLINE },
    // --bg-base is the inverse of --text-primary in both themes.
    primary: { background: "var(--text-primary)", color: "var(--bg-base)", border: "1px solid transparent" },
    danger: { background: DANGER, color: "#fff", border: "1px solid transparent" },
    "danger-outline": { background: "var(--bg-base)", color: DANGER, border: HAIRLINE },
  };
  const style: React.CSSProperties = {
    ...palette[variant],
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    height: 34,
    padding: "0 14px",
    borderRadius: 8,
    fontSize: 13.5,
    fontWeight: 500,
    whiteSpace: "nowrap",
    textDecoration: "none",
    cursor: disabled || busy ? "default" : "pointer",
    opacity: disabled ? 0.45 : 1,
    boxShadow: variant === "secondary" || variant === "danger-outline" ? "0 1px 2px rgba(0,0,0,0.04)" : "none",
  };
  if (href && !disabled) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" style={style} title={title}>
        {children}
      </a>
    );
  }
  return (
    <button type="button" onClick={onClick} disabled={disabled || busy} title={title} style={style}>
      {busy && <Loader2 size={14} className="animate-spin" aria-hidden={true} />}
      {children}
    </button>
  );
}

function TextField({
  id,
  value,
  onChange,
  onCommit,
  placeholder,
  prefix,
  type = "text",
  max,
  invalid = false,
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  onCommit: () => void;
  placeholder?: string;
  prefix?: string;
  type?: "text" | "date";
  max?: string;
  invalid?: boolean;
}): React.ReactElement {
  return (
    <div
      className="flex items-center"
      style={{
        width: "min(300px, 72vw)",
        height: 38,
        padding: "0 12px",
        background: "var(--bg-base)",
        border: invalid ? `1px solid ${DANGER}` : HAIRLINE,
        borderRadius: 8,
      }}
    >
      {prefix && <span style={{ fontSize: 14, color: "var(--text-tertiary)", marginRight: 2 }}>{prefix}</span>}
      <input
        id={id}
        type={type}
        value={value}
        max={max}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onCommit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.currentTarget as HTMLInputElement).blur();
        }}
        aria-invalid={invalid || undefined}
        style={{
          flex: 1,
          minWidth: 0,
          background: "transparent",
          border: "none",
          outline: "none",
          fontSize: 14,
          color: "var(--text-primary)",
          colorScheme: "light dark",
        }}
      />
    </div>
  );
}

function Muted({ children }: { children: React.ReactNode }): React.ReactElement {
  return <div style={{ padding: "16px 0", fontSize: 13.5, color: "var(--text-tertiary)" }}>{children}</div>;
}

function LoadError({ message, onRetry }: { message: string; onRetry: () => void }): React.ReactElement {
  return (
    <div className="flex items-center justify-between gap-4" style={{ padding: "16px 0" }}>
      <span style={{ fontSize: 13.5, color: DANGER }}>{message}</span>
      <Btn onClick={onRetry}>
        <RefreshCw size={13} aria-hidden={true} /> Retry
      </Btn>
    </div>
  );
}

/** Pydantic messages arrive as "Value error, <text>"; keep the text. */
function cleanError(message: string | undefined, fallback: string): string {
  if (!message) return fallback;
  const m = message.replace(/^Value error,\s*/i, "");
  return m.charAt(0).toUpperCase() + m.slice(1);
}

type Fetch<T> = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; value: T };

function useProfile(): [Fetch<UserProfile>, () => void, (p: UserProfile) => void] {
  const [state, setState] = useState<Fetch<UserProfile>>({ kind: "loading" });
  const load = useCallback((): void => {
    setState({ kind: "loading" });
    void getMe().then((res) => {
      if (isError(res)) setState({ kind: "error", message: res.error.message || "Could not load your profile." });
      else setState({ kind: "ok", value: res.data });
    });
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  const set = useCallback((p: UserProfile) => setState({ kind: "ok", value: p }), []);
  return [state, load, set];
}

// ---------------------------------------------------------------------------
// 1. Profile
// ---------------------------------------------------------------------------

const MAX_PHOTO_BYTES = 10 * 1024 * 1024;
const PHOTO_TYPES = ["image/png", "image/jpeg", "image/webp"];
const AVATAR_PX = 256;

/** Centre-crop to a square and downsize, so the stored photo stays small. */
async function toAvatarDataUrl(file: File): Promise<string> {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error("That image could not be read."));
      el.src = url;
    });
    const side = Math.min(img.naturalWidth, img.naturalHeight);
    const canvas = document.createElement("canvas");
    canvas.width = AVATAR_PX;
    canvas.height = AVATAR_PX;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("That image could not be read.");
    ctx.drawImage(
      img,
      (img.naturalWidth - side) / 2,
      (img.naturalHeight - side) / 2,
      side,
      side,
      0,
      0,
      AVATAR_PX,
      AVATAR_PX,
    );
    // Browsers that cannot encode WebP fall back to PNG; JPEG is smaller.
    const webp = canvas.toDataURL("image/webp", 0.86);
    return webp.startsWith("data:image/webp") ? webp : canvas.toDataURL("image/jpeg", 0.88);
  } finally {
    URL.revokeObjectURL(url);
  }
}

function todayIso(): string {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return d.toISOString().slice(0, 10);
}

function ProfileSection(): React.ReactElement {
  const [state, load, setProfile] = useProfile();
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [dob, setDob] = useState("");
  const [errors, setErrors] = useState<Partial<Record<keyof ProfilePatch, string>>>({});
  const [note, setNote] = useState<"saving" | "saved" | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const noteTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const profile = state.kind === "ok" ? state.value : null;

  useEffect(() => {
    if (!profile) return;
    setName(profile.full_name ?? "");
    setUsername(profile.username ?? "");
    setDob(profile.dob ?? "");
    // Only when a different profile arrives, not on every save.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile?.id]);

  useEffect(() => () => {
    if (noteTimer.current) clearTimeout(noteTimer.current);
  }, []);

  const save = useCallback(
    async (field: keyof ProfilePatch, value: string | null): Promise<void> => {
      setErrors((e) => ({ ...e, [field]: undefined }));
      setNote("saving");
      const res = await updateProfile({ [field]: value });
      if (isError(res)) {
        setNote(null);
        setErrors((e) => ({ ...e, [field]: cleanError(res.error.message, "Could not save. Try again.") }));
        return;
      }
      setProfile(res.data);
      setNote("saved");
      if (noteTimer.current) clearTimeout(noteTimer.current);
      noteTimer.current = setTimeout(() => setNote(null), 1800);
    },
    [setProfile],
  );

  const commit = (field: "full_name" | "username" | "dob", raw: string): void => {
    if (!profile) return;
    const value = field === "username" ? raw.trim().replace(/^@/, "").toLowerCase() : raw.trim();
    const current = (profile[field] ?? "") as string;
    if (value === current) return;
    void save(field, value || null);
  };

  const onPhoto = async (file: File | undefined): Promise<void> => {
    if (!file) return;
    if (!PHOTO_TYPES.includes(file.type)) {
      setErrors((e) => ({ ...e, avatar: "Choose a PNG, JPEG or WebP image." }));
      return;
    }
    if (file.size > MAX_PHOTO_BYTES) {
      setErrors((e) => ({ ...e, avatar: "That image is over 10MB." }));
      return;
    }
    try {
      await save("avatar", await toAvatarDataUrl(file));
    } catch (err) {
      setErrors((e) => ({ ...e, avatar: err instanceof Error ? err.message : "That image could not be read." }));
    }
  };

  const initial = useMemo(() => {
    const src = (profile?.full_name || profile?.username || profile?.email || "").trim();
    return (src[0] || "U").toUpperCase();
  }, [profile]);

  return (
    <Group
      first
      title="Profile"
      action={
        note && (
          <span style={{ fontSize: 12.5, color: "var(--text-tertiary)" }} aria-live="polite">
            {note === "saving" ? "Saving…" : "Saved"}
          </span>
        )
      }
    >
      {state.kind === "loading" && <Muted>Loading your profile…</Muted>}
      {state.kind === "error" && <LoadError message={state.message} onRetry={load} />}

      {profile && (
        <>
          <Row
            label="Profile photo"
            hint={
              errors.avatar ? <span style={{ color: DANGER }}>{errors.avatar}</span> : "PNG, JPEG, or WebP, up to 10MB."
            }
            control={
              <>
                {profile.avatar && (
                  <button
                    type="button"
                    onClick={() => void save("avatar", null)}
                    style={{ fontSize: 13, color: "var(--text-tertiary)", background: "none", border: "none", cursor: "pointer" }}
                  >
                    Remove
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => fileRef.current?.click()}
                  aria-label={profile.avatar ? "Change profile photo" : "Upload profile photo"}
                  title={profile.avatar ? "Change photo" : "Upload photo"}
                  className="flex items-center justify-center overflow-hidden"
                  style={{
                    width: 48,
                    height: 48,
                    borderRadius: "50%",
                    background: "var(--surface-active)",
                    border: "none",
                    color: "var(--text-primary)",
                    fontSize: 16,
                    fontWeight: 500,
                    cursor: "pointer",
                    padding: 0,
                  }}
                >
                  {profile.avatar ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={profile.avatar} alt="" width={48} height={48} style={{ objectFit: "cover" }} />
                  ) : (
                    initial
                  )}
                </button>
                <input
                  ref={fileRef}
                  type="file"
                  accept={PHOTO_TYPES.join(",")}
                  hidden
                  onChange={(e) => {
                    void onPhoto(e.target.files?.[0]);
                    e.target.value = "";
                  }}
                />
              </>
            }
          />
          <Row
            label="Full name"
            htmlFor="pf-name"
            hint={errors.full_name ? <span style={{ color: DANGER }}>{errors.full_name}</span> : "Optional"}
            control={
              <TextField
                id="pf-name"
                value={name}
                onChange={setName}
                onCommit={() => commit("full_name", name)}
                placeholder="Your name"
                invalid={!!errors.full_name}
              />
            }
          />
          <Row
            label="Username"
            htmlFor="pf-username"
            hint={
              errors.username ? (
                <span style={{ color: DANGER }}>{errors.username}</span>
              ) : (
                "Letters, digits, dots and underscores."
              )
            }
            control={
              <TextField
                id="pf-username"
                value={username}
                onChange={(v) => setUsername(v.replace(/\s/g, ""))}
                onCommit={() => commit("username", username)}
                placeholder="username"
                prefix="@"
                invalid={!!errors.username}
              />
            }
          />
          <Row
            last
            label="Date of birth"
            htmlFor="pf-dob"
            hint={errors.dob ? <span style={{ color: DANGER }}>{errors.dob}</span> : "Optional"}
            control={
              <TextField
                id="pf-dob"
                type="date"
                value={dob}
                max={todayIso()}
                onChange={setDob}
                onCommit={() => commit("dob", dob)}
                invalid={!!errors.dob}
              />
            }
          />
        </>
      )}
    </Group>
  );
}

// ---------------------------------------------------------------------------
// 2. Account
// ---------------------------------------------------------------------------

function AccountSection(): React.ReactElement {
  const [state, load] = useProfile();
  const [reset, setReset] = useState<"idle" | "busy" | "sent" | "error">("idle");
  const [del, setDel] = useState<"idle" | "confirm" | "busy" | "sent" | "error">("idle");
  const email = state.kind === "ok" ? state.value.email : "";

  const sendReset = async (): Promise<void> => {
    setReset("busy");
    const res = await requestPasswordReset(email);
    setReset(isError(res) ? "error" : "sent");
  };

  const requestDelete = async (): Promise<void> => {
    setDel("busy");
    const res = await submitBugReport({
      category: "other",
      severity: "normal",
      title: "Account deletion request",
      description: `The account holder asked for this account to be deleted from Settings → Account.\nAccount: ${email}`,
      email,
      context: { page: "settings/account" },
    });
    setDel(isError(res) ? "error" : "sent");
  };

  return (
    <Group first title="Account">
      {state.kind === "loading" && <Muted>Loading your account…</Muted>}
      {state.kind === "error" && <LoadError message={state.message} onRetry={load} />}

      {state.kind === "ok" && (
        <>
          <Row
            label="Email"
            hint={<span style={{ wordBreak: "break-all" }}>{email}</span>}
            control={
              <Btn disabled title="Changing your email is not available yet.">
                Change email
              </Btn>
            }
          />
          <Row
            label="Password"
            hint={
              reset === "sent"
                ? `We sent a reset link to ${email}. It expires in 1 hour.`
                : reset === "error"
                  ? <span style={{ color: DANGER }}>The reset link could not be sent. Try again.</span>
                  : "••••••••"
            }
            control={
              <Btn onClick={() => void sendReset()} busy={reset === "busy"} disabled={reset === "sent"}>
                {reset === "sent" ? "Link sent" : "Reset password"}
              </Btn>
            }
          />
          <Row
            label="Two-factor authentication"
            hint="Not available yet."
            control={
              <Btn disabled title="Two-factor authentication is not available yet.">
                Connect
              </Btn>
            }
          />
          <Row
            last
            label="Delete my account"
            hint={
              del === "confirm"
                ? "We will email you to confirm before anything is deleted. Your plan, layouts, alerts and paper book go with it."
                : del === "sent"
                  ? "Request received. We will email you to confirm before deleting anything."
                  : del === "error"
                    ? <span style={{ color: DANGER }}>The request could not be sent. Try again.</span>
                    : "Permanently remove your account and its data."
            }
            control={
              del === "confirm" || del === "busy" ? (
                <>
                  <Btn onClick={() => setDel("idle")} disabled={del === "busy"}>
                    Keep account
                  </Btn>
                  <Btn variant="danger" onClick={() => void requestDelete()} busy={del === "busy"}>
                    Send request
                  </Btn>
                </>
              ) : (
                <Btn variant="danger-outline" onClick={() => setDel("confirm")} disabled={del === "sent"}>
                  {del === "sent" ? "Requested" : "Request"}
                </Btn>
              )
            }
          />
        </>
      )}
    </Group>
  );
}

// ---------------------------------------------------------------------------
// Plan helpers shared by Usage and Billing
// ---------------------------------------------------------------------------

type PlanModal =
  | { kind: "pick" }
  | { kind: "change"; plan: PaidPlanId; cycle: Cycle }
  | { kind: "cancel" }
  | null;

/** The next public plan above `plan`, or null when it is already the top. */
function nextPlanUp(cat: PublicCatalog, plan: PlanId): PaidPlanId | null {
  const from = rankOf(cat, plan === "anonymous" ? "free" : plan);
  const above = cat.plans
    .filter((p) => p.id !== "anonymous" && p.id !== "free" && p.rank > from)
    .sort((a, b) => a.rank - b.rank);
  return (above[0]?.id as PaidPlanId | undefined) ?? null;
}

const PAYING = ["active", "trialing", "past_due"];

function PlanModals({
  modal,
  setModal,
  cat,
  me,
  demo,
  setMe,
}: {
  modal: PlanModal;
  setModal: (m: PlanModal) => void;
  cat: PublicCatalog;
  me: BillingMe;
  demo: boolean;
  setMe: (m: BillingMe) => void;
}): React.ReactElement | null {
  if (!modal) return null;
  return (
    <BillingModal open onOpenChange={(o) => !o && setModal(null)} label="Manage plan">
      {modal.kind === "pick" ? (
        <PlanPickerPanel
          catalog={cat}
          me={me}
          onPick={(plan, cycle) => setModal({ kind: "change", plan, cycle })}
          onCancelInstead={() => setModal({ kind: "cancel" })}
          onClose={() => setModal(null)}
        />
      ) : modal.kind === "change" ? (
        <PlanChangePanel
          catalog={cat}
          me={me}
          to={modal.plan}
          cycle={modal.cycle}
          demo={demo}
          onBack={() => setModal({ kind: "pick" })}
          onDone={(m) => {
            setMe(m);
            setModal(null);
          }}
        />
      ) : (
        <CancelPanel
          catalog={cat}
          me={me}
          demo={demo}
          onClose={() => setModal(null)}
          onAlternative={(plan, cycle) => setModal({ kind: "change", plan, cycle })}
          onDone={(m) => {
            if (m) setMe(m);
            setModal(null);
          }}
        />
      )}
    </BillingModal>
  );
}

/** Shared states for the two billing-backed tabs. Null when `me` is ready. */
function useBillingGate(onClose: () => void): React.ReactElement | null {
  const { me, loading, error, refresh } = useBilling();
  const router = useRouter();
  if (loading && !me) return <Muted>Loading your plan…</Muted>;
  if (!me) return <LoadError message={error ?? "Your plan could not be loaded."} onRetry={() => void refresh()} />;
  if (me.plan === "anonymous") {
    return (
      <Row
        last
        label="Sign in to see your plan"
        hint="Plans, usage and invoices belong to your chart account."
        control={
          <Btn
            variant="primary"
            onClick={() => {
              onClose();
              router.push("/login?next=/settings/billing");
            }}
          >
            Sign in
          </Btn>
        }
      />
    );
  }
  return null;
}

// ---------------------------------------------------------------------------
// 3. Usage
// ---------------------------------------------------------------------------

function relativeReset(unix: number | null): string | null {
  if (!unix) return null;
  const secs = unix - Math.floor(Date.now() / 1000);
  if (secs <= 0) return "Resets soon";
  const days = Math.floor(secs / 86400);
  const hours = Math.floor((secs % 86400) / 3600);
  if (days >= 2) return `Resets ${fmtDate(unix)}`;
  if (days === 1) return `Resets in 1 day ${hours} hr`;
  const mins = Math.max(1, Math.floor((secs % 3600) / 60));
  return hours > 0 ? `Resets in ${hours} hr ${mins} min` : `Resets in ${mins} min`;
}

/** Claude's usage row: label + reset on the left, the bar, then "% used". */
function UsageBar({
  label,
  sub,
  view,
  last = false,
}: {
  label: string;
  sub?: string | null;
  view: QuotaView;
  last?: boolean;
}): React.ReactElement {
  const unlimited = view.limit === null;
  const fill = view.tone === "out" ? DANGER : view.tone === "low" ? "var(--color-warn, #d97706)" : "var(--text-primary)";
  return (
    <div
      className="grid items-center gap-x-6 gap-y-2 max-sm:grid-cols-1 sm:grid-cols-[190px_1fr_auto]"
      style={{ padding: "18px 0", borderBottom: last ? "none" : HAIRLINE }}
    >
      <div className="min-w-0">
        <div style={{ fontSize: 14.5, color: "var(--text-primary)" }}>{label}</div>
        {sub && <div style={{ marginTop: 2, fontSize: 13, color: "var(--text-tertiary)" }}>{sub}</div>}
      </div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={view.limit ?? undefined}
        aria-valuenow={view.used}
        style={{ height: 8, borderRadius: 999, background: "var(--surface-track, var(--surface-active))", overflow: "hidden" }}
      >
        {!unlimited && (
          <div style={{ width: `${view.pct}%`, height: "100%", borderRadius: 999, background: fill, transition: "width 0.4s var(--ease-quartr)" }} />
        )}
      </div>
      <div style={{ fontSize: 13.5, color: "var(--text-secondary)", minWidth: 96, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
        {unlimited ? `${num(view.used)} · no limit` : `${view.pct}% used`}
      </div>
    </div>
  );
}

function UsageSection({ onClose }: { onClose: () => void }): React.ReactElement {
  const { catalog: cat, me, refresh, setMe, demo } = useBilling();
  const router = useRouter();
  const [modal, setModal] = useState<PlanModal>(null);
  const [checkedAt, setCheckedAt] = useState(() => Date.now());
  const [refreshing, setRefreshing] = useState(false);
  const [, tick] = useState(0);

  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 30_000);
    return () => clearInterval(t);
  }, []);

  const gate = useBillingGate(onClose);
  if (gate || !me) return <Group first title="Usage">{gate}</Group>;

  const view = subscriptionView(me);
  const credits = quotaView(me, "ai.credits");
  const price = me.features["alerts.price"];
  const tech = me.features["alerts.technical"];
  const up = nextPlanUp(cat, me.plan);
  const ago = Math.round((Date.now() - checkedAt) / 60_000);

  const upgrade = (): void => {
    if (!up) return;
    track("plan_selected", { surface: "settings_usage", from_plan: me.plan, to_plan: up });
    if (PAYING.includes(view.state)) {
      // A subscriber is never sent to checkout: the change happens in place.
      setModal({ kind: "change", plan: up, cycle: view.cycle ?? "monthly" });
    } else {
      onClose();
      router.push("/pricing");
    }
  };

  const reload = async (): Promise<void> => {
    setRefreshing(true);
    await refresh();
    setCheckedAt(Date.now());
    setRefreshing(false);
  };

  return (
    <>
      <Group first title="Usage">
        {/* Credits summary */}
        <div
          className="flex flex-wrap items-center justify-between gap-4"
          style={{ padding: "18px 20px", marginTop: 8, borderRadius: 12, border: HAIRLINE, background: "var(--bg-primary)" }}
        >
          <div className="min-w-0">
            <div style={{ fontSize: 13, color: "var(--text-tertiary)" }}>AI credits left · {planName(cat, me.plan)} plan</div>
            <div style={{ marginTop: 4, fontSize: 26, fontWeight: 600, letterSpacing: "-0.02em", color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>
              {credits ? (credits.limit === null ? "Unlimited" : num(credits.left ?? 0)) : "—"}
              {credits && credits.limit !== null && (
                <span style={{ fontSize: 15, fontWeight: 400, color: "var(--text-tertiary)" }}> of {num(credits.limit)}</span>
              )}
            </div>
            <div style={{ marginTop: 2, fontSize: 13, color: "var(--text-tertiary)" }}>
              One credit is one prompt. Follow-ups and failed turns are free.
            </div>
          </div>
          {up ? (
            <Btn variant="primary" onClick={upgrade}>
              Upgrade to {planName(cat, up)}
            </Btn>
          ) : (
            <span style={{ fontSize: 13, color: "var(--text-tertiary)" }}>You are on the highest plan</span>
          )}
        </div>
      </Group>

      <Group
        title="Plan usage limits"
        description={me.paywall_enabled ? undefined : "Limits are shown for reference and are not enforced yet."}
      >
        {credits && <UsageBar label="AI credits" sub={relativeReset(credits.resetsAt)} view={credits} />}
        {price && typeof price.used === "number" && (
          <UsageBar
            label="Price alerts"
            sub={`${num(price.used)} armed`}
            view={meter(price.used, (price.value as number | null) ?? null)}
          />
        )}
        {tech && typeof tech.used === "number" && (
          <UsageBar
            last
            label="Technical alerts"
            sub={`${num(tech.used)} armed`}
            view={meter(tech.used, (tech.value as number | null) ?? null)}
          />
        )}
        <div className="flex items-center gap-2" style={{ marginTop: 12, fontSize: 12.5, color: "var(--text-tertiary)" }}>
          Last updated: {ago < 1 ? "less than a minute ago" : `${ago} min ago`}
          <button
            type="button"
            onClick={() => void reload()}
            aria-label="Refresh usage"
            style={{ display: "inline-flex", background: "none", border: "none", padding: 2, cursor: "pointer", color: "inherit" }}
          >
            <RefreshCw size={13} className={refreshing ? "animate-spin" : undefined} aria-hidden={true} />
          </button>
        </div>
      </Group>

      <PlanModals modal={modal} setModal={setModal} cat={cat} me={me} demo={demo} setMe={setMe} />
    </>
  );
}

// ---------------------------------------------------------------------------
// 4. Billing
// ---------------------------------------------------------------------------

type InvoiceState =
  | { status: "loading" }
  | { status: "ready"; data: InvoiceList }
  | { status: "error"; message: string; unavailable?: boolean };

const METHOD: Record<string, string> = {
  card: "Card",
  upi: "UPI Autopay",
  emandate: "Bank mandate",
  nach: "NACH mandate",
};

const INV_STATUS: Record<string, string> = {
  paid: "Paid",
  issued: "Due",
  partially_paid: "Partly paid",
  cancelled: "Cancelled",
  expired: "Expired",
};

const INVOICE_PAGE = 6;

function PlanGlyph(): React.ReactElement {
  return (
    <svg width="44" height="44" viewBox="0 0 44 44" fill="none" aria-hidden={true} style={{ color: "var(--text-primary)", flexShrink: 0 }}>
      <path d="M6 32 L15 22 L22 27 L31 14 L38 19" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="15" cy="22" r="2.6" fill="var(--bg-base)" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="22" cy="27" r="2.6" fill="var(--bg-base)" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="31" cy="14" r="3.4" fill="var(--bg-base)" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="31" cy="14" r="1.2" fill="currentColor" />
    </svg>
  );
}

function BillingSection({ onClose }: { onClose: () => void }): React.ReactElement {
  const { catalog: cat, me, setMe, demo, signedIn } = useBilling();
  const router = useRouter();
  const [modal, setModal] = useState<PlanModal>(null);
  const [inv, setInv] = useState<InvoiceState>({ status: "loading" });
  const [shown, setShown] = useState(INVOICE_PAGE);
  const [undoBusy, setUndoBusy] = useState(false);
  const [undoError, setUndoError] = useState<string | null>(null);

  const loadInvoices = useCallback(async () => {
    setInv({ status: "loading" });
    const r = await billingApi.invoices();
    if (r.ok) setInv({ status: "ready", data: r.data });
    else setInv({ status: "error", message: r.error, unavailable: r.status === 503 });
  }, []);

  useEffect(() => {
    if (!demo && signedIn) void loadInvoices();
  }, [demo, signedIn, loadInvoices]);

  const gate = useBillingGate(onClose);
  if (gate || !me) return <Group first title="Billing">{gate}</Group>;

  const v = subscriptionView(me);
  const planShown = v.state === "expired" || v.state === "incomplete" ? me.plan : (v.subPlan ?? me.plan);
  const paying = PAYING.includes(v.state);
  const hasSub = !!me.subscription && v.state !== "comp";
  const cycleWord = v.cycle === "annual" ? "Yearly" : v.cycle === "monthly" ? "Monthly" : null;

  const go = (path: string): void => {
    onClose();
    router.push(path);
  };

  const undo = async (): Promise<void> => {
    setUndoBusy(true);
    setUndoError(null);
    const r = await billingApi.undoChange();
    setUndoBusy(false);
    if (r.ok) {
      setMe(r.data.billing);
      track("plan_change_undone", { plan: v.subPlan });
    } else setUndoError(r.error);
  };

  const statusLine = ((): React.ReactNode => {
    switch (v.state) {
      case "active":
        return `Your subscription will auto renew on ${fmtDate(v.renewsAt)}.`;
      case "trialing":
        return `Your trial ends on ${fmtDate(v.endsAt)}.`;
      case "canceling":
        return `Your subscription ends on ${fmtDate(v.endsAt)} and will not renew.`;
      case "past_due":
        return <span style={{ color: DANGER }}>Your last payment failed. The plan is held until {fmtDate(v.graceUntil)}.</span>;
      case "expired":
        return `Your ${planName(cat, v.subPlan)} plan ended on ${fmtDate(v.endsAt)}.`;
      case "incomplete":
        return "Checkout was started but not paid.";
      case "comp":
        return "Granted by Pivot. Nothing is charged.";
      default:
        return "Upgrade for more AI credits, alerts and charts.";
    }
  })();

  const planAction = ((): React.ReactNode => {
    if (paying) return <Btn onClick={() => setModal({ kind: "pick" })}>Adjust plan</Btn>;
    if (v.state === "canceling") return <Btn onClick={() => go("/pricing")}>See plans</Btn>;
    if (v.state === "expired" || v.state === "incomplete") {
      const p = (v.subPlan && v.subPlan !== "free" && v.subPlan !== "anonymous" ? v.subPlan : "pro") as PaidPlanId;
      return (
        <Btn variant="primary" onClick={() => go(`/checkout?plan=${p}&cycle=${v.cycle ?? "annual"}&return=/settings/billing`)}>
          {v.state === "expired" ? "Resubscribe" : "Resume checkout"}
        </Btn>
      );
    }
    if (v.state === "free") return <Btn variant="primary" onClick={() => go("/pricing")}>Upgrade</Btn>;
    return null;
  })();

  const list = inv.status === "ready" ? inv.data.invoices : [];

  return (
    <>
      {/* Plan */}
      <section className="flex flex-wrap items-start justify-between gap-4" style={{ paddingBottom: 28, borderBottom: HAIRLINE }}>
        <div className="flex min-w-0 items-start gap-4">
          <PlanGlyph />
          <div className="min-w-0">
            <div style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)" }}>{planName(cat, planShown)} plan</div>
            {cycleWord && paying && <div style={{ marginTop: 2, fontSize: 14, color: "var(--text-primary)" }}>{cycleWord}</div>}
            <div style={{ marginTop: 2, fontSize: 13.5, color: "var(--text-tertiary)" }}>{statusLine}</div>
            {v.pending && (
              <div style={{ marginTop: 8, fontSize: 13, color: "var(--text-secondary)" }}>
                Changing to {planName(cat, v.pending.plan)}, {v.pending.cycle === "annual" ? "yearly" : "monthly"}, on{" "}
                {fmtDate(v.pending.at)}.{" "}
                <button
                  type="button"
                  onClick={() => void undo()}
                  disabled={undoBusy}
                  style={{ background: "none", border: "none", padding: 0, color: "var(--text-primary)", textDecoration: "underline", cursor: "pointer", fontSize: 13 }}
                >
                  Undo
                </button>
                {undoError && <span style={{ color: DANGER }}> {undoError}</span>}
              </div>
            )}
          </div>
        </div>
        {planAction}
      </section>

      {/* Payment */}
      {hasSub && (
        <section style={{ paddingTop: 28 }}>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0">
              <h2 style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>Payment</h2>
              <p style={{ margin: "4px 0 0", fontSize: 13.5, lineHeight: 1.55, color: "var(--text-tertiary)", maxWidth: 440 }}>
                Your payment method is charged for subscription renewals. It is held by Razorpay; Pivot never sees the details.
              </p>
            </div>
            {inv.status === "ready" && !inv.data.payment_method && inv.data.manage_url && (
              <Btn href={inv.data.manage_url}>Add payment method</Btn>
            )}
          </div>
          {inv.status === "loading" && <Muted>Loading payment method…</Muted>}
          {inv.status === "error" &&
            (inv.unavailable ? (
              <Muted>Payments are not switched on for this server, so there is no payment method on file.</Muted>
            ) : (
              <LoadError message={inv.message} onRetry={() => void loadInvoices()} />
            ))}
          {inv.status === "ready" && inv.data.payment_method && (
            <div className="flex items-center justify-between gap-4" style={{ paddingTop: 18 }}>
              <div className="flex items-center gap-3">
                <span
                  className="flex items-center justify-center"
                  style={{ width: 34, height: 24, borderRadius: 5, background: "var(--surface-active)", color: "var(--text-secondary)" }}
                >
                  <CreditCard size={15} aria-hidden={true} />
                </span>
                <span style={{ fontSize: 14.5, color: "var(--text-primary)" }}>
                  {METHOD[inv.data.payment_method] ?? inv.data.payment_method}
                </span>
                <span
                  style={{ fontSize: 12, fontWeight: 500, padding: "2px 8px", borderRadius: 6, background: "rgba(59,130,246,0.14)", color: "rgb(37,99,235)" }}
                >
                  Default
                </span>
              </div>
              {inv.data.manage_url && <Btn href={inv.data.manage_url}>Update</Btn>}
            </div>
          )}
        </section>
      )}

      {/* Invoices */}
      <section style={{ paddingTop: 40 }}>
        <h2 style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>Invoices</h2>
        {!hasSub ? (
          <EmptyInvoices />
        ) : inv.status === "loading" ? (
          <Muted>Loading invoices…</Muted>
        ) : inv.status === "error" ? (
          inv.unavailable ? (
            <Muted>Payments are not switched on for this server, so there are no invoices.</Muted>
          ) : (
            <LoadError message={inv.message} onRetry={() => void loadInvoices()} />
          )
        ) : list.length === 0 ? (
          <EmptyInvoices />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table style={{ width: "100%", minWidth: 440, marginTop: 14, borderCollapse: "collapse", fontSize: 14 }}>
                <thead>
                  <tr style={{ textAlign: "left" }}>
                    {["Date", "Total", "Status", "Actions"].map((h) => (
                      <th key={h} scope="col" style={{ padding: "10px 0", fontWeight: 600, color: "var(--text-primary)" }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {list.slice(0, shown).map((i) => (
                    <tr key={i.id}>
                      <td style={{ padding: "9px 0", color: "var(--text-primary)" }}>{fmtDate(i.date)}</td>
                      <td style={{ padding: "9px 0", color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>
                        <span className="inline-flex items-center gap-1.5">
                          {inr(i.amount)}
                          <span title="Includes GST" aria-label="Includes GST" style={{ display: "inline-flex", color: "var(--text-tertiary)" }}>
                            <Info size={13} aria-hidden={true} />
                          </span>
                        </span>
                      </td>
                      <td style={{ padding: "9px 0", color: "var(--text-primary)" }}>{INV_STATUS[i.status] ?? i.status}</td>
                      <td style={{ padding: "9px 0" }}>
                        {i.url ? (
                          <a
                            href={i.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{ color: "rgb(37,99,235)", textDecoration: "underline", textUnderlineOffset: 3 }}
                          >
                            View invoice
                          </a>
                        ) : (
                          <span style={{ color: "var(--text-tertiary)" }}>Not available</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {list.length > shown && (
              <div className="flex justify-center" style={{ marginTop: 14, paddingTop: 16, borderTop: HAIRLINE }}>
                <Btn onClick={() => setShown((n) => n + INVOICE_PAGE)}>Load more</Btn>
              </div>
            )}
          </>
        )}
      </section>

      {/* Cancellation */}
      {paying && (
        <section style={{ paddingTop: 40 }}>
          <h2 style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>Cancellation</h2>
          <Row
            last
            label="Cancel plan"
            hint={`You keep ${planName(cat, v.subPlan)} until ${fmtDate(v.renewsAt ?? v.endsAt)}.`}
            control={
              <Btn
                variant="danger"
                onClick={() => {
                  track("cancel_started", { plan: v.subPlan });
                  setModal({ kind: "cancel" });
                }}
              >
                Cancel
              </Btn>
            }
          />
        </section>
      )}

      <p style={{ marginTop: 32, fontSize: 12, lineHeight: 1.55, color: "var(--text-tertiary)" }}>
        Prices include GST. Payments are processed by Razorpay.
      </p>

      <PlanModals modal={modal} setModal={setModal} cat={cat} me={me} demo={demo} setMe={setMe} />
    </>
  );
}

function EmptyInvoices(): React.ReactElement {
  return (
    <div className="flex items-center gap-3" style={{ padding: "18px 0", fontSize: 13.5, color: "var(--text-tertiary)" }}>
      <Receipt size={16} aria-hidden={true} />
      No invoices yet. They appear here after your first payment.
    </div>
  );
}

// ---------------------------------------------------------------------------
// 5–6. Trading, Notifications (to be filled in)
// ---------------------------------------------------------------------------

function EmptySection({ title }: { title: string }): React.ReactElement {
  return <Group first title={title} />;
}
