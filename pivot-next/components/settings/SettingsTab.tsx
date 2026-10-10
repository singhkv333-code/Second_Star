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
  CalendarDays,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleUserRound,
  CreditCard,
  Gauge,
  Info,
  Loader2,
  RefreshCw,
  Search,
  ShieldCheck,
  TrendingUp,
} from "lucide-react";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import {
  getMe,
  listBrokers,
  requestPasswordReset,
  submitBugReport,
  updateProfile,
  type ProfilePatch,
  type UserProfile,
} from "@/lib/api";
import { isError, type Broker } from "@/lib/types";
import type { TradingMode } from "@/lib/trading-mode";
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
// Fields and buttons are borderless fills, the chart's `.searchfield` look.
// Tints of the text colour read on the rail, the pane, and in both themes.
const FILL = "color-mix(in srgb, var(--text-primary) 5%, transparent)";
const FILL_HOVER = "color-mix(in srgb, var(--text-primary) 9%, transparent)";
const DANGER_FILL = "color-mix(in srgb, var(--color-loss, #dc2626) 10%, transparent)";
const DANGER_FILL_HOVER = "color-mix(in srgb, var(--color-loss, #dc2626) 16%, transparent)";
const RADIUS = 6;

// ---------------------------------------------------------------------------
// Dialog
// ---------------------------------------------------------------------------

export type SettingsDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Which tab to land on when opened. */
  initialSection?: SectionKey;
  /** Live vs paper, owned by AppShell so every surface agrees. */
  tradingMode: TradingMode;
  onChooseTradingMode: (m: TradingMode) => void | Promise<void>;
  /** Opens the shared BrokerOnboarding dialog (owned by AppShell). */
  onOpenBroker: () => void;
};

export function SettingsDialog({
  open,
  onOpenChange,
  initialSection = "profile",
  tradingMode,
  onChooseTradingMode,
  onOpenBroker,
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
        className="gap-0 p-0 max-sm:h-[100dvh] sm:max-w-[980px] sm:rounded-[10px]"
        style={{ background: "var(--bg-base)", overflow: "hidden" }}
      >
        <DialogTitle className="sr-only">Settings</DialogTitle>
        {/* w-full min-w-0: the dialog is a grid, whose item defaults to
            min-width:auto; without it the mobile nav strip blows the column
            out past the viewport. */}
        <div className="flex h-full w-full min-w-0 flex-col sm:h-[80vh] sm:max-h-[720px] sm:flex-row">
          <SettingsRail active={section} onSelect={setSection} query={query} onQueryChange={setQuery} />

          <div data-settings-pane className="min-w-0 flex-1 overflow-y-auto px-5 py-6 sm:px-10 sm:py-9">
            <div className="mx-auto w-full" style={{ maxWidth: 660 }}>
              {section === "profile" && <ProfileSection />}
              {section === "account" && <AccountSection />}
              {section === "usage" && <UsageSection onClose={close} />}
              {section === "billing" && <BillingSection onClose={close} />}
              {section === "trading" && (
                <TradingSection mode={tradingMode} onChooseMode={onChooseTradingMode} onOpenBroker={onOpenBroker} />
              )}
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
          height: 36,
          padding: "0 11px",
          background: FILL,
          borderRadius: 4,
        }}
      >
        <Search size={15} strokeWidth={2} aria-hidden={true} style={{ color: "var(--text-secondary)" }} />
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
                borderRadius: RADIUS,
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

type BtnVariant = "secondary" | "primary" | "danger" | "danger-soft";

const BTN_FILL: Record<BtnVariant, { bg: string; hover: string; color: string }> = {
  secondary: { bg: FILL, hover: FILL_HOVER, color: "var(--text-primary)" },
  // --bg-base is the inverse of --text-primary in both themes.
  primary: { bg: "var(--text-primary)", hover: "color-mix(in srgb, var(--text-primary) 86%, transparent)", color: "var(--bg-base)" },
  danger: { bg: DANGER, hover: "color-mix(in srgb, var(--color-loss, #dc2626) 88%, #000)", color: "#fff" },
  "danger-soft": { bg: DANGER_FILL, hover: DANGER_FILL_HOVER, color: DANGER },
};

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
  const fill = BTN_FILL[variant];
  const inert = disabled || busy;
  const style: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    height: 34,
    padding: "0 14px",
    border: "none",
    borderRadius: RADIUS,
    background: fill.bg,
    color: fill.color,
    fontSize: 13.5,
    fontWeight: 500,
    whiteSpace: "nowrap",
    textDecoration: "none",
    cursor: inert ? "default" : "pointer",
    opacity: disabled ? 0.45 : 1,
    transition: "background 0.15s var(--ease-quartr)",
  };
  const hover = {
    onMouseEnter: (e: React.MouseEvent<HTMLElement>) => {
      if (!inert) e.currentTarget.style.background = fill.hover;
    },
    onMouseLeave: (e: React.MouseEvent<HTMLElement>) => {
      e.currentTarget.style.background = fill.bg;
    },
  };
  if (href && !disabled) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" style={style} title={title} {...hover}>
        {children}
      </a>
    );
  }
  return (
    <button type="button" onClick={onClick} disabled={inert} title={title} style={style} {...hover}>
      {busy && <Loader2 size={14} className="animate-spin" aria-hidden={true} />}
      {children}
    </button>
  );
}

const FIELD_WIDTH = "min(300px, 72vw)";

function fieldBox(invalid: boolean, focused = false): React.CSSProperties {
  return {
    width: FIELD_WIDTH,
    height: 38,
    padding: "0 12px",
    background: invalid ? DANGER_FILL : focused ? FILL_HOVER : FILL,
    border: "none",
    borderRadius: RADIUS,
    transition: "background 0.15s var(--ease-quartr)",
  };
}

function TextField({
  id,
  value,
  onChange,
  onCommit,
  placeholder,
  prefix,
  invalid = false,
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  onCommit: () => void;
  placeholder?: string;
  prefix?: string;
  invalid?: boolean;
}): React.ReactElement {
  const [focused, setFocused] = useState(false);
  return (
    <div className="flex items-center" style={fieldBox(invalid, focused)}>
      {prefix && <span style={{ fontSize: 14, color: "var(--text-tertiary)", marginRight: 2 }}>{prefix}</span>}
      <input
        id={id}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => {
          setFocused(false);
          onCommit();
        }}
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
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Date picker: days, months and years views; dates after `max` are disabled
// ---------------------------------------------------------------------------

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const WEEKDAYS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];

type Ymd = { y: number; m: number; d: number };

function parseIso(iso: string): Ymd | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  return m ? { y: Number(m[1]), m: Number(m[2]) - 1, d: Number(m[3]) } : null;
}
const toIso = ({ y, m, d }: Ymd): string => `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
const cmp = (a: Ymd, b: Ymd): number => a.y - b.y || a.m - b.m || a.d - b.d;

function DatePicker({
  id,
  value,
  max,
  onChange,
  invalid = false,
  placeholder = "Select date",
}: {
  id: string;
  value: string;
  max: string;
  onChange: (iso: string) => void;
  invalid?: boolean;
  placeholder?: string;
}): React.ReactElement {
  const sel = parseIso(value);
  const limit = parseIso(max) ?? { y: 9999, m: 11, d: 31 };
  const [open, setOpen] = useState(false);
  const [up, setUp] = useState(false);
  const [view, setView] = useState<"days" | "months" | "years">("days");
  const [cursor, setCursor] = useState<{ y: number; m: number }>({ y: 2000, m: 0 });
  const wrap = useRef<HTMLDivElement>(null);

  const show = (): void => {
    const start = sel ?? { y: limit.y - 25, m: 0, d: 1 };
    setCursor({ y: start.y, m: start.m });
    // A date of birth is far from today, so an empty picker opens on years.
    setView(sel ? "days" : "years");
    // Open upward when the scroll pane has no room below the field.
    const el = wrap.current;
    const pane = el?.closest("[data-settings-pane]") as HTMLElement | null;
    if (el && pane) {
      const r = el.getBoundingClientRect();
      const p = pane.getBoundingClientRect();
      setUp(p.bottom - r.bottom < 340 && r.top - p.top > p.bottom - r.bottom);
    }
    setOpen(true);
  };

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent): void => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        // Close the picker, not the settings dialog around it.
        e.stopPropagation();
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  const pick = (d: number): void => {
    onChange(toIso({ y: cursor.y, m: cursor.m, d }));
    setOpen(false);
  };

  const step = (dir: -1 | 1): void => {
    if (view === "days") {
      const m = cursor.m + dir;
      setCursor({ y: cursor.y + Math.floor(m / 12), m: ((m % 12) + 12) % 12 });
    } else if (view === "months") setCursor({ ...cursor, y: cursor.y + dir });
    else setCursor({ ...cursor, y: cursor.y + dir * 12 });
  };

  const yearStart = cursor.y - (((cursor.y % 12) + 12) % 12);
  const canNext =
    view === "days"
      ? cursor.y < limit.y || (cursor.y === limit.y && cursor.m < limit.m)
      : view === "months"
        ? cursor.y < limit.y
        : yearStart + 12 <= limit.y;

  const title =
    view === "days" ? `${MONTHS[cursor.m]} ${cursor.y}` : view === "months" ? `${cursor.y}` : `${yearStart} – ${yearStart + 11}`;

  const cell = (on: boolean, disabled: boolean, today = false): React.CSSProperties => ({
    height: 34,
    border: "none",
    borderRadius: RADIUS,
    fontSize: 13.5,
    fontVariantNumeric: "tabular-nums",
    cursor: disabled ? "default" : "pointer",
    background: on ? "var(--text-primary)" : "transparent",
    color: on ? "var(--bg-base)" : disabled ? "var(--text-tertiary)" : "var(--text-primary)",
    opacity: disabled ? 0.4 : 1,
    fontWeight: on || today ? 600 : 400,
    boxShadow: today && !on ? "inset 0 0 0 1px var(--glass-border)" : "none",
  });
  const hoverable = (on: boolean, disabled: boolean) => ({
    onMouseEnter: (e: React.MouseEvent<HTMLButtonElement>) => {
      if (!on && !disabled) e.currentTarget.style.background = FILL_HOVER;
    },
    onMouseLeave: (e: React.MouseEvent<HTMLButtonElement>) => {
      if (!on) e.currentTarget.style.background = "transparent";
    },
  });

  const days = ((): React.ReactNode[] => {
    const first = new Date(cursor.y, cursor.m, 1).getDay();
    const count = new Date(cursor.y, cursor.m + 1, 0).getDate();
    const now = new Date();
    const out: React.ReactNode[] = Array.from({ length: first }, (_, i) => <span key={`b${i}`} />);
    for (let d = 1; d <= count; d++) {
      const ymd = { y: cursor.y, m: cursor.m, d };
      const on = !!sel && cmp(sel, ymd) === 0;
      const disabled = cmp(ymd, limit) > 0;
      const today = now.getFullYear() === ymd.y && now.getMonth() === ymd.m && now.getDate() === d;
      out.push(
        <button key={d} type="button" disabled={disabled} onClick={() => pick(d)} style={cell(on, disabled, today)} {...hoverable(on, disabled)}>
          {d}
        </button>,
      );
    }
    return out;
  })();

  const navBtn = (dir: -1 | 1, label: string, enabled: boolean): React.ReactElement => (
    <button
      type="button"
      aria-label={label}
      disabled={!enabled}
      onClick={() => step(dir)}
      className="flex items-center justify-center"
      style={{
        width: 30,
        height: 30,
        border: "none",
        borderRadius: RADIUS,
        background: "transparent",
        color: "var(--text-secondary)",
        cursor: enabled ? "pointer" : "default",
        opacity: enabled ? 1 : 0.3,
      }}
      {...hoverable(false, !enabled)}
    >
      {dir < 0 ? <ChevronLeft size={16} aria-hidden={true} /> : <ChevronRight size={16} aria-hidden={true} />}
    </button>
  );

  const grid = (cells: { key: string; label: string; on: boolean; disabled: boolean; go: () => void }[]): React.ReactElement => (
    <div className="grid grid-cols-3 gap-1">
      {cells.map((c) => (
        <button key={c.key} type="button" disabled={c.disabled} onClick={c.go} style={{ ...cell(c.on, c.disabled), height: 40 }} {...hoverable(c.on, c.disabled)}>
          {c.label}
        </button>
      ))}
    </div>
  );

  return (
    <div ref={wrap} style={{ position: "relative" }}>
      <button
        id={id}
        type="button"
        onClick={() => (open ? setOpen(false) : show())}
        aria-haspopup="dialog"
        aria-expanded={open}
        className="flex items-center justify-between"
        style={{ ...fieldBox(invalid, open), cursor: "pointer", fontSize: 14, color: sel ? "var(--text-primary)" : "var(--text-tertiary)" }}
      >
        {sel ? `${sel.d} ${MONTHS[sel.m]} ${sel.y}` : placeholder}
        <CalendarDays size={15} aria-hidden={true} style={{ color: "var(--text-tertiary)" }} />
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Choose a date"
          style={{
            position: "absolute",
            right: 0,
            ...(up ? { bottom: "calc(100% + 6px)" } : { top: "calc(100% + 6px)" }),
            zIndex: 20,
            width: 292,
            padding: 12,
            background: "var(--bg-base)",
            borderRadius: 8,
            boxShadow: "0 18px 48px -12px rgba(0,0,0,0.28), 0 0 0 1px var(--glass-border)",
          }}
        >
          <div className="flex items-center justify-between" style={{ marginBottom: 8 }}>
            <button
              type="button"
              onClick={() => setView(view === "days" ? "years" : view === "months" ? "years" : "days")}
              className="inline-flex items-center gap-1"
              style={{
                height: 30,
                padding: "0 8px",
                border: "none",
                borderRadius: RADIUS,
                background: "transparent",
                color: "var(--text-primary)",
                fontSize: 14,
                fontWeight: 600,
                cursor: "pointer",
              }}
              {...hoverable(false, false)}
            >
              {title}
              <ChevronDown
                size={14}
                aria-hidden={true}
                style={{ color: "var(--text-tertiary)", transform: view === "days" ? "none" : "rotate(180deg)" }}
              />
            </button>
            <div className="flex">
              {navBtn(-1, "Previous", true)}
              {navBtn(1, "Next", canNext)}
            </div>
          </div>

          {view === "days" && (
            <>
              <div className="grid grid-cols-7" style={{ marginBottom: 2 }}>
                {WEEKDAYS.map((w) => (
                  <span key={w} style={{ height: 26, display: "grid", placeItems: "center", fontSize: 12, color: "var(--text-tertiary)" }}>
                    {w}
                  </span>
                ))}
              </div>
              <div className="grid grid-cols-7 gap-0.5">{days}</div>
            </>
          )}

          {view === "months" &&
            grid(
              MONTHS.map((name, m) => ({
                key: name,
                label: name.slice(0, 3),
                on: !!sel && sel.y === cursor.y && sel.m === m,
                disabled: cursor.y > limit.y || (cursor.y === limit.y && m > limit.m),
                go: () => {
                  setCursor({ ...cursor, m });
                  setView("days");
                },
              })),
            )}

          {view === "years" &&
            grid(
              Array.from({ length: 12 }, (_, i) => yearStart + i).map((y) => ({
                key: String(y),
                label: String(y),
                on: !!sel && sel.y === y,
                disabled: y > limit.y || y < 1900,
                go: () => {
                  setCursor({ ...cursor, y });
                  setView("months");
                },
              })),
            )}

          {sel && (
            <div className="flex justify-end" style={{ marginTop: 10, paddingTop: 10, borderTop: HAIRLINE }}>
              <Btn
                onClick={() => {
                  onChange("");
                  setOpen(false);
                }}
              >
                Clear
              </Btn>
            </div>
          )}
        </div>
      )}
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
            hint={errors.full_name && <span style={{ color: DANGER }}>{errors.full_name}</span>}
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
            hint={errors.username && <span style={{ color: DANGER }}>{errors.username}</span>}
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
            hint={errors.dob && <span style={{ color: DANGER }}>{errors.dob}</span>}
            control={
              <DatePicker
                id="pf-dob"
                value={dob}
                max={todayIso()}
                onChange={(v) => {
                  setDob(v);
                  commit("dob", v);
                }}
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
                    : null
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
                <Btn variant="danger-soft" onClick={() => setDel("confirm")} disabled={del === "sent"}>
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
          style={{ padding: "18px 20px", marginTop: 8, borderRadius: 8, background: "var(--bg-primary)" }}
        >
          <div className="min-w-0">
            <div style={{ fontSize: 13, color: "var(--text-tertiary)" }}>AI credits left · {planName(cat, me.plan)} plan</div>
            <div style={{ marginTop: 4, fontSize: 26, fontWeight: 600, letterSpacing: "-0.02em", color: "var(--text-primary)", fontVariantNumeric: "tabular-nums" }}>
              {credits ? (credits.limit === null ? "Unlimited" : num(credits.left ?? 0)) : "—"}
              {credits && credits.limit !== null && (
                <span style={{ fontSize: 15, fontWeight: 400, color: "var(--text-tertiary)" }}> of {num(credits.limit)}</span>
              )}
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

// Blue that stays readable in both themes (mixed toward the text colour).
const LINK = "color-mix(in srgb, #2563eb 82%, var(--text-primary))";
const BADGE_BG = "color-mix(in srgb, #3b82f6 16%, transparent)";

const H2: React.CSSProperties = { fontSize: 17, fontWeight: 600, letterSpacing: "-0.01em", color: "var(--text-primary)", margin: 0 };
const TD: React.CSSProperties = { padding: "11px 16px 11px 0", fontSize: 15, color: "var(--text-primary)", whiteSpace: "nowrap" };

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
  const cycleLine =
    (paying || v.state === "canceling") && v.cycle
      ? v.cycle === "annual"
        ? "Yearly"
        : "Monthly"
      : v.state === "comp"
        ? "Granted"
        : "No subscription";

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
        <Btn onClick={() => go(`/checkout?plan=${p}&cycle=${v.cycle ?? "annual"}&return=/settings/billing`)}>
          {v.state === "expired" ? "Resubscribe" : "Resume checkout"}
        </Btn>
      );
    }
    if (v.state === "free") return <Btn onClick={() => go("/pricing")}>Upgrade plan</Btn>;
    return null;
  })();

  const ready = inv.status === "ready" ? inv.data : null;
  const method = hasSub && ready?.payment_method ? (METHOD[ready.payment_method] ?? ready.payment_method) : null;
  const list = hasSub && ready ? ready.invoices : [];

  const tableNote = ((): React.ReactNode => {
    if (!hasSub) return "No invoices yet. They appear here after your first payment.";
    if (inv.status === "loading") return "Loading invoices…";
    if (inv.status === "error") {
      return inv.unavailable ? "Payments are not switched on yet, so there are no invoices." : null;
    }
    return list.length === 0 ? "No invoices yet. They appear here after your first payment." : null;
  })();

  return (
    <>
      {/* Plan */}
      <section className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
        <div className="flex min-w-0 items-start">
          <div className="min-w-0">
            <div style={{ fontSize: 17, fontWeight: 600, color: "var(--text-primary)" }}>{planName(cat, planShown)} plan</div>
            <div style={{ marginTop: 3, fontSize: 15, color: "var(--text-primary)" }}>{cycleLine}</div>
            <div style={{ marginTop: 3, fontSize: 14, color: "var(--text-tertiary)" }}>{statusLine}</div>
            {v.pending && (
              <div style={{ marginTop: 6, fontSize: 13.5, color: "var(--text-secondary)" }}>
                Changing to {planName(cat, v.pending.plan)}, {v.pending.cycle === "annual" ? "yearly" : "monthly"}, on{" "}
                {fmtDate(v.pending.at)}.{" "}
                <button
                  type="button"
                  onClick={() => void undo()}
                  disabled={undoBusy}
                  style={{ background: "none", border: "none", padding: 0, color: LINK, textDecoration: "underline", cursor: "pointer", fontSize: 13.5 }}
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
      <section style={{ marginTop: 52 }}>
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
          <div className="min-w-0">
            <h2 style={H2}>Payment</h2>
            <p style={{ margin: "4px 0 0", fontSize: 14, lineHeight: 1.5, color: "var(--text-tertiary)", maxWidth: 460 }}>
              Your default payment method is charged for subscription renewals.
            </p>
          </div>
          {hasSub && ready && !ready.payment_method && ready.manage_url && <Btn href={ready.manage_url}>Add payment method</Btn>}
        </div>

        {hasSub && inv.status === "error" && !inv.unavailable ? (
          <LoadError message={inv.message} onRetry={() => void loadInvoices()} />
        ) : (
          <div className="flex items-center justify-between gap-4" style={{ marginTop: 22 }}>
            <div className="flex min-w-0 items-center gap-3">
              <span
                className="flex shrink-0 items-center justify-center"
                style={{ width: 30, height: 30, borderRadius: RADIUS, background: method ? "var(--text-primary)" : FILL, color: method ? "var(--bg-base)" : "var(--text-tertiary)" }}
              >
                <CreditCard size={16} aria-hidden={true} />
              </span>
              <div className="min-w-0">
                <div className="flex items-center gap-2" style={{ fontSize: 15, color: method ? "var(--text-primary)" : "var(--text-secondary)" }}>
                  {hasSub && inv.status === "loading" ? "Loading…" : (method ?? "No payment method on file")}
                  {method && (
                    <span style={{ fontSize: 12.5, fontWeight: 500, padding: "2px 8px", borderRadius: RADIUS, background: BADGE_BG, color: LINK }}>
                      Default
                    </span>
                  )}
                </div>
                <div style={{ marginTop: 2, fontSize: 14, color: "var(--text-tertiary)" }}>
                  {method
                    ? "Held by Razorpay"
                    : inv.status === "error" && inv.unavailable
                      ? "Payments are not switched on yet"
                      : "Added securely through Razorpay when you subscribe"}
                </div>
              </div>
            </div>
            {method && ready?.manage_url && <Btn href={ready.manage_url}>Update</Btn>}
          </div>
        )}
      </section>

      {/* Invoices */}
      <section style={{ marginTop: 52 }}>
        <h2 style={H2}>Invoices</h2>
        {hasSub && inv.status === "error" && !inv.unavailable ? (
          <LoadError message={inv.message} onRetry={() => void loadInvoices()} />
        ) : (
          <div className="overflow-x-auto" style={{ marginTop: 20 }}>
            <table style={{ width: "100%", minWidth: 460, borderCollapse: "collapse", tableLayout: "fixed" }}>
              <colgroup>
                <col style={{ width: "34%" }} />
                <col style={{ width: "22%" }} />
                <col style={{ width: "18%" }} />
                <col />
              </colgroup>
              <thead>
                <tr style={{ textAlign: "left" }}>
                  {["Date", "Total", "Status", "Actions"].map((h) => (
                    <th key={h} scope="col" style={{ ...TD, fontWeight: 500, paddingBottom: 13 }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {tableNote ? (
                  <tr>
                    <td colSpan={4} style={{ ...TD, color: "var(--text-tertiary)", fontSize: 14, whiteSpace: "normal" }}>
                      {tableNote}
                    </td>
                  </tr>
                ) : (
                  list.slice(0, shown).map((i) => (
                    <tr key={i.id}>
                      <td style={TD}>{fmtDate(i.date)}</td>
                      <td style={{ ...TD, fontVariantNumeric: "tabular-nums" }}>
                        <span className="inline-flex items-center gap-1.5">
                          {inr(i.amount)}
                          <span title="Includes GST" aria-label="Includes GST" style={{ display: "inline-flex", color: "var(--text-secondary)" }}>
                            <Info size={14} aria-hidden={true} />
                          </span>
                        </span>
                      </td>
                      <td style={TD}>{INV_STATUS[i.status] ?? i.status}</td>
                      <td style={TD}>
                        {i.url ? (
                          <a href={i.url} target="_blank" rel="noopener noreferrer" style={{ color: LINK, textDecoration: "underline", textUnderlineOffset: 3 }}>
                            View invoice
                          </a>
                        ) : (
                          <span style={{ color: "var(--text-tertiary)" }}>Not available</span>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
        {list.length > shown && (
          <div className="flex justify-center" style={{ marginTop: 14, paddingTop: 18, borderTop: HAIRLINE }}>
            <Btn onClick={() => setShown((n) => n + INVOICE_PAGE)}>Load more</Btn>
          </div>
        )}
      </section>

      {/* Cancellation */}
      {paying && (
        <section style={{ marginTop: 52 }}>
          <h2 style={H2}>Cancellation</h2>
          <div className="flex items-center justify-between gap-4" style={{ marginTop: 20 }}>
            <span style={{ fontSize: 15, color: "var(--text-primary)" }}>Cancel plan</span>
            <Btn
              variant="danger"
              onClick={() => {
                track("cancel_started", { plan: v.subPlan });
                setModal({ kind: "cancel" });
              }}
            >
              Cancel
            </Btn>
          </div>
        </section>
      )}

      <PlanModals modal={modal} setModal={setModal} cat={cat} me={me} demo={demo} setMe={setMe} />
    </>
  );
}

// ---------------------------------------------------------------------------
// 5. Trading
// ---------------------------------------------------------------------------

const MODES: { value: TradingMode; label: string }[] = [
  { value: "real", label: "Live" },
  { value: "paper", label: "Paper" },
];

function ModeToggle({
  value,
  onChange,
}: {
  value: TradingMode;
  onChange: (m: TradingMode) => void;
}): React.ReactElement {
  return (
    <div role="radiogroup" aria-label="Trading mode" className="inline-flex" style={{ padding: 3, gap: 2, background: FILL, borderRadius: RADIUS + 2 }}>
      {MODES.map((m) => {
        const on = m.value === value;
        return (
          <button
            key={m.value}
            type="button"
            role="radio"
            aria-checked={on}
            onClick={() => onChange(m.value)}
            style={{
              height: 30,
              minWidth: 76,
              padding: "0 14px",
              border: "none",
              borderRadius: RADIUS,
              background: on ? "var(--bg-base)" : "transparent",
              color: on ? "var(--text-primary)" : "var(--text-secondary)",
              boxShadow: on ? "0 1px 2px rgba(0,0,0,0.10)" : "none",
              fontSize: 13.5,
              fontWeight: on ? 600 : 500,
              cursor: "pointer",
              transition: "background 0.15s var(--ease-quartr), color 0.15s var(--ease-quartr)",
            }}
          >
            {m.label}
          </button>
        );
      })}
    </div>
  );
}

function BrokerLogo({ broker }: { broker: Broker }): React.ReactElement {
  const [failed, setFailed] = useState(false);
  return (
    <span
      className="flex shrink-0 items-center justify-center overflow-hidden"
      style={{ width: 34, height: 34, borderRadius: RADIUS, background: FILL, fontSize: 13, fontWeight: 600, color: "var(--text-secondary)" }}
    >
      {failed ? (
        broker.name.charAt(0)
      ) : (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={broker.logo || `/brokers/${broker.id}.svg`} alt="" width={20} height={20} style={{ objectFit: "contain" }} onError={() => setFailed(true)} />
      )}
    </span>
  );
}

function TradingSection({
  mode,
  onChooseMode,
  onOpenBroker,
}: {
  mode: TradingMode;
  onChooseMode: (m: TradingMode) => void | Promise<void>;
  onOpenBroker: () => void;
}): React.ReactElement {
  const [brokers, setBrokers] = useState<Fetch<Broker[]>>({ kind: "loading" });

  const load = useCallback((): void => {
    setBrokers({ kind: "loading" });
    void listBrokers().then((res) => {
      if (isError(res)) setBrokers({ kind: "error", message: res.error.message || "Could not load brokers." });
      else setBrokers({ kind: "ok", value: res.data.brokers });
    });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const list =
    brokers.kind === "ok"
      ? [...brokers.value].sort((a, b) => Number(!!b.status?.connected) - Number(!!a.status?.connected))
      : [];
  const connected = list.filter((b) => b.status?.connected).length;

  return (
    <>
      <Group first title="Trading">
        <Row
          last
          label="Trading mode"
          hint={
            mode === "paper"
              ? "Orders fill in a simulated paper book."
              : "Portfolio and P&L come from your connected broker. Pivot does not place live orders."
          }
          control={<ModeToggle value={mode} onChange={(m) => void onChooseMode(m)} />}
        />
      </Group>

      <Group
        title="Brokers"
        description={brokers.kind === "ok" ? (connected ? `${connected} of ${list.length} connected` : "None connected") : undefined}
        action={<Btn onClick={onOpenBroker}>Manage</Btn>}
      >
        {brokers.kind === "loading" && <Muted>Checking broker connections…</Muted>}
        {brokers.kind === "error" && <LoadError message={brokers.message} onRetry={load} />}
        {brokers.kind === "ok" && list.length === 0 && <Muted>No brokers are available.</Muted>}
        {list.map((b, i) => {
          const on = !!b.status?.connected;
          const detail = on
            ? b.status?.mock_mode
              ? "Connected with mock data"
              : `Connected${b.status?.broker_user_id ? ` · ${b.status.broker_user_id}` : ""}`
            : "Not connected";
          return (
            <div
              key={b.id}
              className="flex items-center justify-between gap-4"
              style={{ padding: "14px 0", borderBottom: i === list.length - 1 ? "none" : HAIRLINE }}
            >
              <div className="flex min-w-0 items-center gap-3">
                <BrokerLogo broker={b} />
                <div className="min-w-0">
                  <div style={{ fontSize: 14.5, color: "var(--text-primary)" }}>{b.name}</div>
                  <div style={{ marginTop: 2, fontSize: 13, color: "var(--text-tertiary)" }}>{detail}</div>
                </div>
              </div>
              {on ? (
                <span
                  className="inline-flex shrink-0 items-center gap-1.5"
                  style={{
                    height: 26,
                    padding: "0 10px",
                    borderRadius: RADIUS,
                    fontSize: 12.5,
                    fontWeight: 500,
                    color: "var(--color-profit, #059669)",
                    background: "color-mix(in srgb, var(--color-profit, #059669) 12%, transparent)",
                  }}
                >
                  <Check size={13} strokeWidth={2.5} aria-hidden={true} />
                  Connected
                </span>
              ) : (
                <Btn onClick={onOpenBroker}>Connect</Btn>
              )}
            </div>
          );
        })}
      </Group>
    </>
  );
}

// ---------------------------------------------------------------------------
// 6. Notifications (to be filled in)
// ---------------------------------------------------------------------------

function EmptySection({ title }: { title: string }): React.ReactElement {
  return <Group first title={title} />;
}
