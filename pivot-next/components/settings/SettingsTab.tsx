"use client";

/**
 * SettingsTab — Pivot's account & preferences surface.
 *
 * Layout is the Claude / ChatGPT settings pattern: a vertical section rail
 * on the left and a scrolling detail pane on the right. On <lg the rail
 * collapses into a horizontal scroll strip pinned above the content.
 *
 * Visual language is deliberately minimal — borderless grouped sections with
 * hairline-divided rows (no boxed cards), right-aligned values, generous
 * whitespace. Controls sit flush-right; descriptions stay quiet.
 *
 * Sections (top → bottom):
 *   1. Profile        — name / email / account id (read-only; /auth/me).
 *   2. Appearance     — theme (Light / Dark / System), reuses the shell's
 *                       real theme store so the toggle here and the one in
 *                       the account menu never disagree.
 *   3. Trading        — Real vs Paper mode + the register-not-execute
 *                       contract Pivot operates under.
 *   4. Brokers        — live connection status from GET /brokers; "Manage"
 *                       opens the existing BrokerOnboarding dialog.
 *   5. Notifications  — local alert preferences (device-scoped; no backend
 *                       store yet, and the UI says so plainly).
 *   6. Privacy & Data — policy / terms links + log out.
 *   7. About          — what Pivot is + the not-financial-advice boundary.
 *
 * Theme + trading mode are owned by AppShell and passed in as props so this
 * page edits the single source of truth rather than a drifting copy.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bell,
  Check,
  ChevronRight,
  CircleUserRound,
  ExternalLink,
  FileText,
  Info,
  Keyboard,
  LogOut,
  Monitor,
  Moon,
  Palette,
  Plug,
  RefreshCw,
  ShieldCheck,
  Sun,
  TrendingUp,
} from "lucide-react";
import { Search } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { getMe, listBrokers, type UserProfile } from "@/lib/api";
import type { Broker } from "@/lib/types";
import { isError } from "@/lib/types";
import type { TradingMode } from "@/lib/trading-mode";

type Theme = "light" | "dark" | "system";

type SectionKey =
  | "profile"
  | "appearance"
  | "trading"
  | "brokers"
  | "notifications"
  | "privacy"
  | "about";

const SECTIONS: {
  key: SectionKey;
  label: string;
  Icon: React.ComponentType<{ size?: number; strokeWidth?: number; "aria-hidden"?: boolean }>;
}[] = [
  { key: "profile", label: "Profile", Icon: CircleUserRound },
  { key: "appearance", label: "Appearance", Icon: Palette },
  { key: "trading", label: "Trading", Icon: TrendingUp },
  { key: "brokers", label: "Brokers", Icon: Plug },
  { key: "notifications", label: "Notifications", Icon: Bell },
  { key: "privacy", label: "Privacy & Data", Icon: ShieldCheck },
  { key: "about", label: "About", Icon: Info },
];

const APP_VERSION = "v1 · beta";
const HAIRLINE = "1px solid var(--glass-border)";

// ---------------------------------------------------------------------------
// Notification preferences — device-local (no backend store yet). Honest:
// the UI labels these "saved on this device".
// ---------------------------------------------------------------------------

type NotifPrefs = {
  triggerAlerts: boolean;
  agentRuns: boolean;
  priceMoves: boolean;
  productUpdates: boolean;
};

const NOTIF_LS_KEY = "pivot-notif-prefs";
const NOTIF_DEFAULTS: NotifPrefs = {
  triggerAlerts: true,
  agentRuns: true,
  priceMoves: false,
  productUpdates: true,
};

function readNotifPrefs(): NotifPrefs {
  if (typeof window === "undefined") return NOTIF_DEFAULTS;
  try {
    const raw = window.localStorage.getItem(NOTIF_LS_KEY);
    if (!raw) return NOTIF_DEFAULTS;
    const parsed = JSON.parse(raw) as Partial<NotifPrefs>;
    return { ...NOTIF_DEFAULTS, ...parsed };
  } catch {
    return NOTIF_DEFAULTS;
  }
}

function writeNotifPrefs(prefs: NotifPrefs): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(NOTIF_LS_KEY, JSON.stringify(prefs));
  } catch {
    /* non-persistent fallback still works in-memory this session */
  }
}

// ---------------------------------------------------------------------------
// SettingsTab
// ---------------------------------------------------------------------------

export type SettingsDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  theme: Theme;
  onChooseTheme: (t: Theme) => void;
  tradingMode: TradingMode;
  onChooseTradingMode: (m: TradingMode) => void | Promise<void>;
  /** Opens the shared BrokerOnboarding dialog (owned by AppShell). */
  onOpenBroker: () => void;
  onLogout: () => void;
  /** Optional — opens the keyboard-shortcuts modal. */
  onOpenShortcuts?: () => void;
};

/**
 * SettingsDialog — the settings surface as a centered modal (Claude/ChatGPT
 * pattern): a left rail with search + section nav, and a scrolling content
 * pane on the right. Full-screen on mobile, a floating card on sm+.
 */
export function SettingsDialog({
  open,
  onOpenChange,
  theme,
  onChooseTheme,
  tradingMode,
  onChooseTradingMode,
  onOpenBroker,
  onLogout,
  onOpenShortcuts,
}: SettingsDialogProps): React.ReactElement {
  const [section, setSection] = useState<SectionKey>("profile");
  const [query, setQuery] = useState("");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="gap-0 p-0 sm:max-w-[940px] sm:rounded-2xl"
        style={{ background: "var(--bg-base)", overflow: "hidden" }}
      >
        <DialogTitle className="sr-only">Settings</DialogTitle>
        <div className="flex h-full flex-col sm:h-[78vh] sm:max-h-[660px] sm:flex-row">
          {/* Left rail — search + section nav. */}
          <SettingsRail
            active={section}
            onSelect={setSection}
            query={query}
            onQueryChange={setQuery}
          />

          {/* Content pane — scrolls independently. */}
          <div className="min-w-0 flex-1 overflow-y-auto px-5 py-6 sm:px-8 sm:py-7">
            <div className="mx-auto w-full" style={{ maxWidth: 560 }}>
              {section === "profile" && <ProfileSection />}
              {section === "appearance" && (
                <AppearanceSection theme={theme} onChooseTheme={onChooseTheme} />
              )}
              {section === "trading" && (
                <TradingSection
                  tradingMode={tradingMode}
                  onChooseTradingMode={onChooseTradingMode}
                />
              )}
              {section === "brokers" && <BrokersSection onOpenBroker={onOpenBroker} />}
              {section === "notifications" && <NotificationsSection />}
              {section === "privacy" && <PrivacySection onLogout={onLogout} />}
              {section === "about" && <AboutSection onOpenShortcuts={onOpenShortcuts} />}
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Left rail — search + section nav (modal)
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
  const items = q ? SECTIONS.filter((s) => s.label.toLowerCase().includes(q)) : SECTIONS;

  return (
    <aside
      className="flex shrink-0 flex-col gap-3 px-3 pb-2 pt-4 sm:w-[200px] sm:gap-2 sm:py-5"
      style={{
        background: "var(--bg-primary)",
        borderBottom: HAIRLINE,
      }}
    >
      {/* Search */}
      <div
        className="flex items-center gap-2"
        style={{
          height: 34,
          padding: "0 10px",
          background: "var(--bg-base)",
          border: HAIRLINE,
          borderRadius: "var(--radius-sm)",
        }}
      >
        <Search size={14} strokeWidth={2} aria-hidden={true} style={{ color: "var(--text-tertiary)" }} />
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
            fontSize: 13,
            color: "var(--text-primary)",
          }}
        />
      </div>

      {/* Section label */}
      <div
        className="hidden sm:block"
        style={{
          padding: "2px 8px",
          fontSize: 11,
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          color: "var(--text-tertiary)",
        }}
      >
        Settings
      </div>

      {/* Nav */}
      <nav
        aria-label="Settings sections"
        className="flex gap-1 overflow-x-auto sm:flex-col sm:gap-0.5 sm:overflow-visible"
      >
        {items.map(({ key, label, Icon }) => {
          const isActive = key === active;
          return (
            <button
              key={key}
              type="button"
              onClick={() => onSelect(key)}
              aria-current={isActive ? "page" : undefined}
              className="inline-flex shrink-0 items-center gap-2.5 whitespace-nowrap"
              style={{
                padding: "8px 10px",
                borderRadius: "var(--radius-sm)",
                background: isActive ? "var(--surface-active)" : "transparent",
                color: isActive ? "var(--text-primary)" : "var(--text-secondary)",
                fontSize: 13.5,
                fontWeight: isActive ? 600 : 500,
                cursor: "pointer",
                transition: "background 0.18s var(--ease-quartr), color 0.18s var(--ease-quartr)",
              }}
              onMouseEnter={(e) => {
                if (!isActive) e.currentTarget.style.color = "var(--text-primary)";
              }}
              onMouseLeave={(e) => {
                if (!isActive) e.currentTarget.style.color = "var(--text-secondary)";
              }}
            >
              <Icon size={16} strokeWidth={1.9} aria-hidden={true} />
              {label}
            </button>
          );
        })}
        {items.length === 0 && (
          <div
            className="hidden sm:block"
            style={{ padding: "8px 10px", fontSize: 12.5, color: "var(--text-tertiary)" }}
          >
            No matches
          </div>
        )}
      </nav>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Shared building blocks
// ---------------------------------------------------------------------------

/** A borderless titled group. Rows inside are divided by hairlines. */
function Group({
  title,
  description,
  action,
  children,
  first = false,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
  children?: React.ReactNode;
  first?: boolean;
}): React.ReactElement {
  return (
    <section style={{ marginTop: first ? 0 : 38 }}>
      <div
        className="flex items-baseline justify-between gap-4"
        style={{ marginBottom: description ? 4 : 10 }}
      >
        <h2
          style={{
            fontSize: 16,
            fontWeight: 600,
            letterSpacing: "-0.01em",
            color: "var(--text-primary)",
            margin: 0,
          }}
        >
          {title}
        </h2>
        {action}
      </div>
      {description && (
        <p
          style={{
            margin: "0 0 10px",
            fontSize: 13,
            lineHeight: 1.55,
            color: "var(--text-tertiary)",
            maxWidth: 520,
          }}
        >
          {description}
        </p>
      )}
      {children && <div style={{ borderTop: HAIRLINE }}>{children}</div>}
    </section>
  );
}

/** A label/description on the left, a control on the right. */
function Row({
  label,
  hint,
  control,
  align = "center",
}: {
  label: string;
  hint?: string;
  control: React.ReactNode;
  align?: "center" | "start";
}): React.ReactElement {
  return (
    <div
      className="flex gap-6"
      style={{
        alignItems: align === "center" ? "center" : "flex-start",
        padding: "15px 2px",
        borderBottom: HAIRLINE,
      }}
    >
      <div className="min-w-0 flex-1">
        <div style={{ fontSize: 14, fontWeight: 500, color: "var(--text-primary)" }}>
          {label}
        </div>
        {hint && (
          <div
            style={{
              marginTop: 3,
              fontSize: 12.5,
              lineHeight: 1.5,
              color: "var(--text-tertiary)",
              maxWidth: 380,
            }}
          >
            {hint}
          </div>
        )}
      </div>
      <div className="shrink-0">{control}</div>
    </div>
  );
}

/** Read-only label (left) → value (right), the Claude/ChatGPT pattern. */
function ValueRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}): React.ReactElement {
  return (
    <div
      className="flex items-center gap-6"
      style={{ padding: "15px 2px", borderBottom: HAIRLINE }}
    >
      <div style={{ fontSize: 14, fontWeight: 500, color: "var(--text-primary)" }}>
        {label}
      </div>
      <div
        className="min-w-0 flex-1"
        style={{
          textAlign: "right",
          fontSize: 13.5,
          color: "var(--text-secondary)",
          fontFamily: mono ? "var(--font-numeric, monospace)" : undefined,
          wordBreak: "break-all",
        }}
      >
        {value}
      </div>
    </div>
  );
}

/** A soft, borderless informational callout. */
function Callout({
  Icon,
  tone = "neutral",
  children,
}: {
  Icon: React.ComponentType<{
    size?: number;
    strokeWidth?: number;
    "aria-hidden"?: boolean;
    style?: React.CSSProperties;
  }>;
  tone?: "neutral" | "positive";
  children: React.ReactNode;
}): React.ReactElement {
  const accent =
    tone === "positive" ? "var(--color-profit, #059669)" : "var(--text-tertiary)";
  return (
    <div
      className="flex gap-3"
      style={{
        marginTop: 12,
        padding: "13px 15px",
        borderRadius: "var(--radius-md)",
        background: "var(--surface-hover)",
      }}
    >
      <Icon size={17} strokeWidth={2} aria-hidden={true} style={{ color: accent, flexShrink: 0, marginTop: 1 }} />
      <p style={{ fontSize: 12.5, lineHeight: 1.6, color: "var(--text-secondary)", margin: 0 }}>
        {children}
      </p>
    </div>
  );
}

/** Segmented control (used for theme). */
function Segmented<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: { value: T; label: string; Icon?: React.ComponentType<{ size?: number; strokeWidth?: number }> }[];
  value: T;
  onChange: (v: T) => void;
  ariaLabel: string;
}): React.ReactElement {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      style={{
        display: "inline-flex",
        padding: 3,
        gap: 2,
        background: "var(--bg-elevated)",
        borderRadius: "var(--radius-sm)",
      }}
    >
      {options.map(({ value: v, label, Icon }) => {
        const isActive = v === value;
        return (
          <button
            key={v}
            type="button"
            role="radio"
            aria-checked={isActive}
            onClick={() => onChange(v)}
            className="inline-flex items-center gap-1.5"
            style={{
              padding: "6px 12px",
              borderRadius: "var(--radius-xs)",
              border: "none",
              cursor: "pointer",
              fontSize: 12.5,
              fontWeight: isActive ? 600 : 500,
              background: isActive ? "var(--bg-base)" : "transparent",
              color: isActive ? "var(--text-primary)" : "var(--text-secondary)",
              boxShadow: isActive ? "0 1px 2px rgba(0,0,0,0.10)" : "none",
              transition: "background 0.16s var(--ease-quartr), color 0.16s var(--ease-quartr)",
            }}
          >
            {Icon && <Icon size={14} strokeWidth={2} />}
            {label}
          </button>
        );
      })}
    </div>
  );
}

/** A row that behaves as a link / action, with a trailing affordance. */
function LinkRow({
  label,
  hint,
  href,
  external = false,
  onClick,
  danger = false,
  Icon,
}: {
  label: string;
  hint?: string;
  href?: string;
  external?: boolean;
  onClick?: () => void;
  danger?: boolean;
  Icon?: React.ComponentType<{ size?: number; strokeWidth?: number }>;
}): React.ReactElement {
  const color = danger ? "var(--color-loss, #dc2626)" : "var(--text-primary)";
  const inner = (
    <>
      <div className="inline-flex min-w-0 items-center gap-3">
        {Icon && (
          <span style={{ color: danger ? color : "var(--text-tertiary)" }}>
            <Icon size={16} strokeWidth={2} />
          </span>
        )}
        <div className="min-w-0">
          <div style={{ fontSize: 14, fontWeight: 500, color }}>{label}</div>
          {hint && (
            <div style={{ marginTop: 2, fontSize: 12.5, color: "var(--text-tertiary)" }}>
              {hint}
            </div>
          )}
        </div>
      </div>
      <span style={{ color: "var(--text-tertiary)" }}>
        {external ? <ExternalLink size={14} strokeWidth={2} /> : <ChevronRight size={16} strokeWidth={2} />}
      </span>
    </>
  );

  const baseStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    width: "100%",
    gap: 12,
    padding: "15px 2px",
    borderBottom: HAIRLINE,
    background: "transparent",
    border: "none",
    borderBottomWidth: 1,
    borderBottomStyle: "solid",
    borderBottomColor: "var(--glass-border)",
    textAlign: "left",
    cursor: "pointer",
    textDecoration: "none",
  };

  if (href) {
    return (
      <a
        href={href}
        target={external ? "_blank" : undefined}
        rel={external ? "noopener noreferrer" : undefined}
        style={baseStyle}
      >
        {inner}
      </a>
    );
  }
  return (
    <button type="button" onClick={onClick} style={baseStyle}>
      {inner}
    </button>
  );
}

/** Small quiet text button (used for retry / inline actions). */
function GhostButton({
  onClick,
  Icon,
  children,
}: {
  onClick: () => void;
  Icon?: React.ComponentType<{ size?: number; strokeWidth?: number }>;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1.5"
      style={{
        fontSize: 12.5,
        fontWeight: 500,
        color: "var(--text-secondary)",
        background: "transparent",
        border: HAIRLINE,
        borderRadius: "var(--radius-sm)",
        padding: "6px 11px",
        cursor: "pointer",
      }}
    >
      {Icon && <Icon size={13} strokeWidth={2} />}
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// 1. Profile
// ---------------------------------------------------------------------------

type Fetch<T> =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; value: T };

function ProfileSection(): React.ReactElement {
  const [state, setState] = useState<Fetch<UserProfile>>({ kind: "loading" });

  const load = useCallback((): void => {
    setState({ kind: "loading" });
    void getMe().then((res) => {
      if (isError(res)) {
        setState({ kind: "error", message: res.error.message ?? "Could not load profile." });
        return;
      }
      setState({ kind: "ok", value: res.data });
    });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const initial = useMemo(() => {
    if (state.kind !== "ok") return "U";
    const src = (state.value.full_name && state.value.full_name.trim()) || state.value.email || "";
    return (src.trim()[0] || "U").toUpperCase();
  }, [state]);

  return (
    <Group
      first
      title="Profile"
      description="Your Pivot account. Details are read-only for now — use Report a bug to request a change."
    >
      {state.kind === "loading" && (
        <Muted>Loading your profile…</Muted>
      )}

      {state.kind === "error" && (
        <div className="flex items-center justify-between" style={{ padding: "16px 2px" }}>
          <span style={{ fontSize: 13, color: "var(--color-loss, #dc2626)" }}>{state.message}</span>
          <GhostButton onClick={load} Icon={RefreshCw}>Retry</GhostButton>
        </div>
      )}

      {state.kind === "ok" && (
        <>
          <div className="flex items-center gap-4" style={{ padding: "16px 2px", borderBottom: HAIRLINE }}>
            <div
              aria-hidden={true}
              className="flex shrink-0 items-center justify-center"
              style={{
                // Matches the topbar account avatar (AppShell AccountMenu),
                // scaled up: bordered light circle, grey initial, UI font.
                width: 52,
                height: 52,
                borderRadius: "var(--radius-pill)",
                background: "var(--bg-primary)",
                border: "1px solid var(--glass-border)",
                color: "var(--text-secondary)",
                fontFamily: "var(--font-ui)",
                fontSize: 19,
                fontWeight: 500,
              }}
            >
              {initial}
            </div>
            <div className="min-w-0">
              <div style={{ fontSize: 15.5, fontWeight: 600, color: "var(--text-primary)" }}>
                {state.value.full_name?.trim() || "Pivot user"}
              </div>
              <div style={{ fontSize: 13, color: "var(--text-tertiary)" }}>{state.value.email}</div>
            </div>
          </div>

          <ValueRow label="Full name" value={state.value.full_name?.trim() || "—"} />
          <ValueRow label="Email" value={state.value.email} />
          <ValueRow label="Account ID" value={state.value.id} mono />
        </>
      )}
    </Group>
  );
}

function Muted({ children }: { children: React.ReactNode }): React.ReactElement {
  return (
    <div style={{ padding: "16px 2px", fontSize: 13, color: "var(--text-tertiary)", borderBottom: HAIRLINE }}>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 2. Appearance
// ---------------------------------------------------------------------------

function AppearanceSection({
  theme,
  onChooseTheme,
}: {
  theme: Theme;
  onChooseTheme: (t: Theme) => void;
}): React.ReactElement {
  return (
    <Group
      first
      title="Appearance"
      description="System follows your device's light/dark setting and updates live."
    >
      <Row
        label="Theme"
        hint="Applies across the whole app instantly."
        align="start"
        control={
          <Segmented<Theme>
            ariaLabel="Theme"
            value={theme}
            onChange={onChooseTheme}
            options={[
              { value: "light", label: "Light", Icon: Sun },
              { value: "dark", label: "Dark", Icon: Moon },
              { value: "system", label: "System", Icon: Monitor },
            ]}
          />
        }
      />
    </Group>
  );
}

// ---------------------------------------------------------------------------
// 3. Trading
// ---------------------------------------------------------------------------

function TradingSection({
  tradingMode,
  onChooseTradingMode,
}: {
  tradingMode: TradingMode;
  onChooseTradingMode: (m: TradingMode) => void | Promise<void>;
}): React.ReactElement {
  const isPaper = tradingMode === "paper";
  return (
    <>
      <Group
        first
        title="Trading mode"
        description="Switches the entire app's data source — portfolio, holdings, orders and P&L — and routes any buys/sells to the matching book."
      >
        <Row
          label={isPaper ? "Paper trading" : "Real (live) data"}
          hint={
            isPaper
              ? "Orders fill against a simulated paper book. Nothing reaches a broker."
              : "Reads live broker / market data. Orders are registered for you to confirm in your broker app."
          }
          control={
            <div className="flex items-center" style={{ gap: 10 }}>
              <span
                style={{
                  fontSize: 12.5,
                  fontWeight: 600,
                  color: !isPaper ? "var(--text-primary)" : "var(--text-tertiary)",
                }}
              >
                Real
              </span>
              <Switch
                checked={isPaper}
                onCheckedChange={(checked) => void onChooseTradingMode(checked ? "paper" : "real")}
                aria-label="Toggle paper trading mode"
                className="data-[state=checked]:bg-[#d97706]"
              />
              <span
                style={{
                  fontSize: 12.5,
                  fontWeight: 600,
                  color: isPaper ? "var(--color-warn, #d97706)" : "var(--text-tertiary)",
                }}
              >
                Paper
              </span>
            </div>
          }
        />
      </Group>

      <Group title="How orders work">
        <ValueRow label="Base currency" value="INR (₹) — Indian markets" />
        <ValueRow label="Markets" value="NSE & BSE equities, indices, NSE options (NFO)" />
        <Callout Icon={ShieldCheck} tone="positive">
          Pivot <Strong>registers</Strong> orders and arms automations — it never auto-executes against
          your live broker. You confirm and place every real trade yourself in your broker app. Paper
          trading is fully simulated.
        </Callout>
      </Group>
    </>
  );
}

function Strong({ children }: { children: React.ReactNode }): React.ReactElement {
  return <strong style={{ color: "var(--text-primary)", fontWeight: 600 }}>{children}</strong>;
}

// ---------------------------------------------------------------------------
// 4. Brokers
// ---------------------------------------------------------------------------

function BrokersSection({ onOpenBroker }: { onOpenBroker: () => void }): React.ReactElement {
  const [state, setState] = useState<Fetch<Broker[]>>({ kind: "loading" });

  const load = useCallback((): void => {
    setState({ kind: "loading" });
    void listBrokers().then((res) => {
      if (isError(res)) {
        setState({ kind: "error", message: res.error.message ?? "Could not load brokers." });
        return;
      }
      setState({ kind: "ok", value: res.data.brokers });
    });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <Group
      first
      title="Brokers & connections"
      description="Connect a broker so Pivot can read live quotes, holdings and F&O. Zerodha Kite is the primary data source."
      action={
        <button
          type="button"
          onClick={onOpenBroker}
          className="inline-flex shrink-0 items-center gap-1.5"
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: "var(--primary-foreground, #fff)",
            background: "var(--text-primary)",
            border: "none",
            borderRadius: "var(--radius-sm)",
            padding: "7px 13px",
            cursor: "pointer",
          }}
        >
          <Plug size={14} strokeWidth={2} /> Manage
        </button>
      }
    >
      {state.kind === "loading" && <Muted>Checking broker connections…</Muted>}

      {state.kind === "error" && (
        <div className="flex items-center justify-between" style={{ padding: "16px 2px" }}>
          <span style={{ fontSize: 13, color: "var(--color-loss, #dc2626)" }}>{state.message}</span>
          <GhostButton onClick={load} Icon={RefreshCw}>Retry</GhostButton>
        </div>
      )}

      {state.kind === "ok" &&
        (state.value.length === 0 ? (
          <Muted>No brokers available.</Muted>
        ) : (
          state.value.map((b) => {
            const connected = !!b.status?.connected;
            const mock = b.status?.mock_mode;
            return (
              <div
                key={b.id}
                className="flex items-center gap-3"
                style={{ padding: "13px 2px", borderBottom: HAIRLINE }}
              >
                <div
                  className="flex shrink-0 items-center justify-center"
                  style={{
                    width: 34,
                    height: 34,
                    borderRadius: "var(--radius-sm)",
                    background: "var(--bg-elevated)",
                    overflow: "hidden",
                  }}
                >
                  <img
                    src={b.logo || `/brokers/${b.id}.svg`}
                    alt=""
                    width={21}
                    height={21}
                    style={{ objectFit: "contain" }}
                    onError={(e) => {
                      (e.currentTarget as HTMLImageElement).style.display = "none";
                    }}
                  />
                </div>
                <div className="min-w-0 flex-1">
                  <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
                    {b.name}
                  </div>
                  <div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
                    {connected
                      ? mock
                        ? "Connected (mock data)"
                        : `Connected${b.status?.broker_user_id ? ` · ${b.status.broker_user_id}` : ""}`
                      : "Not connected"}
                  </div>
                </div>
                <StatusPill connected={connected} />
              </div>
            );
          })
        ))}
    </Group>
  );
}

function StatusPill({ connected }: { connected: boolean }): React.ReactElement {
  return (
    <span
      className="inline-flex shrink-0 items-center gap-1.5"
      style={{
        fontSize: 11.5,
        fontWeight: 600,
        padding: "3px 9px",
        borderRadius: "var(--radius-pill)",
        color: connected ? "var(--color-profit, #059669)" : "var(--text-tertiary)",
        background: connected ? "rgba(5,150,105,0.12)" : "var(--surface-active)",
      }}
    >
      {connected && <Check size={12} strokeWidth={2.5} />}
      {connected ? "Connected" : "Inactive"}
    </span>
  );
}

// ---------------------------------------------------------------------------
// 5. Notifications
// ---------------------------------------------------------------------------

function NotificationsSection(): React.ReactElement {
  const [prefs, setPrefs] = useState<NotifPrefs>(NOTIF_DEFAULTS);

  useEffect(() => {
    setPrefs(readNotifPrefs());
  }, []);

  const toggle = useCallback((key: keyof NotifPrefs) => {
    setPrefs((prev) => {
      const next = { ...prev, [key]: !prev[key] };
      writeNotifPrefs(next);
      return next;
    });
  }, []);

  const switchFor = (key: keyof NotifPrefs): React.ReactElement => (
    <Switch checked={prefs[key]} onCheckedChange={() => toggle(key)} aria-label={key} />
  );

  return (
    <Group
      first
      title="Notifications"
      description="Choose what Pivot tells you about. Preferences are saved on this device."
    >
      <Row
        label="Trigger & alert hits"
        hint="When a price alert or automation condition you set fires."
        control={switchFor("triggerAlerts")}
      />
      <Row
        label="Agent runs"
        hint="When an automation registers an order or completes a run."
        control={switchFor("agentRuns")}
      />
      <Row
        label="Watchlist price moves"
        hint="Large intraday moves on stocks you've looked at recently."
        control={switchFor("priceMoves")}
      />
      <Row
        label="Product updates"
        hint="New features and improvements to Pivot."
        control={switchFor("productUpdates")}
      />
    </Group>
  );
}

// ---------------------------------------------------------------------------
// 6. Privacy & Data
// ---------------------------------------------------------------------------

function PrivacySection({ onLogout }: { onLogout: () => void }): React.ReactElement {
  return (
    <>
      <Group first title="Privacy & data" description="Your data, and the terms you're using Pivot under.">
        <LinkRow label="Privacy Policy" hint="How we handle your data." href="/privacy" external Icon={ShieldCheck} />
        <LinkRow label="Terms of Service" hint="The agreement you accepted." href="/terms" external Icon={FileText} />
      </Group>

      <Group title="Session">
        <LinkRow label="Log out" hint="Sign out of Pivot on this device." onClick={onLogout} danger Icon={LogOut} />
      </Group>
    </>
  );
}

// ---------------------------------------------------------------------------
// 7. About
// ---------------------------------------------------------------------------

function AboutSection({
  onOpenShortcuts,
}: {
  onOpenShortcuts?: () => void;
}): React.ReactElement {
  return (
    <>
      <Group first title="About Pivot">
        <div style={{ padding: "4px 0 14px", borderBottom: HAIRLINE }}>
          <p style={{ fontSize: 13.5, lineHeight: 1.65, color: "var(--text-secondary)", margin: 0 }}>
            Pivot is a chat-first investing copilot for Indian retail investors. Describe what you want
            in plain English (or Hinglish) and Pivot answers it with grounded market data, or builds it —
            an automation, an options strategy, a backtest, a paper trade.
          </p>
        </div>
        <ValueRow label="Version" value={APP_VERSION} />
        {onOpenShortcuts && (
          <LinkRow label="Keyboard shortcuts" onClick={onOpenShortcuts} Icon={Keyboard} />
        )}
        <Callout Icon={Info}>
          Pivot gives you <Strong>data and frameworks</Strong>, not personalised buy/sell advice. It is
          not a broker and not a registered advisor — every analysis is exactly that: analysis, not
          financial advice.
        </Callout>
      </Group>
    </>
  );
}
