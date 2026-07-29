"use client";

/**
 * MyViews — the user's deployment ledger for the Views surface.
 *
 * Every view the user has put a position behind, as one calm row-card each:
 * the belief (dateless question title), whether it has resolved, the user's
 * position (size, open fraction), the LIVE return since entry, the exit plan
 * (take-profit / stop-loss levels), and an Edit affordance that opens an
 * inline editor — change levels, change size, exit a portion, exit all.
 *
 * Register-not-execute, everywhere: this is a LEDGER. Pivot records what the
 * user armed and how it's doing — it never places, resizes, or exits an
 * order, and every exit confirms with a "place it in your broker app" note.
 *
 * DESIGN LAW (ViewSurface): rounded corners, borders-only (no fills),
 * >=13px floor, tabular numerals, progressive disclosure. Minimal and quiet —
 * one accent number per row (the live return), everything else recedes.
 *
 * Honesty: a position without live marks shows "—" / "Priced at deploy",
 * never a fabricated 0%; rupee values render only when the user declared a
 * size; TP/SL "hit" states are computed at read time (there is no auto-exit
 * watcher and we never claim one).
 */

import * as React from "react";
import {
  AlertCircle,
  ChevronDown,
  Pencil,
  RefreshCw,
  Telescope,
  X,
} from "lucide-react";
import {
  exitViewPosition,
  listViewPositions,
  updateViewPosition,
} from "@/lib/api";
import { isError } from "@/lib/types";
import type { ViewPositionItem } from "@/lib/types";
import { ViewSurface, Hairline } from "./ViewSurface";
import {
  fmtPct,
  fmtDate,
  signColor,
  statusDotColor,
  statusLabel,
  tierLabel,
} from "./view-format";

const FONT = "var(--font-display)";

// ---------------------------------------------------------------------------
// Types + tiny helpers
// ---------------------------------------------------------------------------

type FetchState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; items: ViewPositionItem[] };

export type MyViewsProps = {
  /** Open the underlying view's detail page (omit to hide the affordance). */
  onOpenView?: (viewId: string) => void;
  /** "Browse views" from the empty state (omit to hide the button). */
  onBrowse?: () => void;
  /** Hide the section heading when the host already renders one. */
  embedded?: boolean;
};

/** "₹1,00,000" with no paise — ledger sizes are round rupee amounts. */
function inr(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(value);
}

function statusChip(p: ViewPositionItem): { word: string; color: string } {
  if (p.status === "exited") {
    return { word: "Exited", color: "var(--text-tertiary)" };
  }
  if (p.view_resolved) {
    return { word: "Resolved", color: "var(--text-secondary)" };
  }
  return {
    word: statusLabel(p.view_status),
    color: statusDotColor(p.view_status),
  };
}

// ---------------------------------------------------------------------------
// Small primitives (label/value pair, quiet chip)
// ---------------------------------------------------------------------------

function Cell({
  label,
  value,
  valueColor,
}: {
  label: string;
  value: React.ReactNode;
  valueColor?: string;
}): React.ReactElement {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
      <span
        style={{
          fontFamily: FONT,
          fontSize: 13,
          fontWeight: 500,
          color: "var(--text-tertiary)",
          whiteSpace: "nowrap",
        }}
      >
        {label}
      </span>
      <span
        style={{
          fontFamily: FONT,
          fontSize: 14.5,
          fontWeight: 600,
          color: valueColor ?? "var(--text-primary)",
          fontVariantNumeric: "tabular-nums",
          whiteSpace: "nowrap",
        }}
      >
        {value}
      </span>
    </div>
  );
}

function LevelChip({
  label,
  hit,
}: {
  label: string;
  hit: boolean;
}): React.ReactElement {
  return (
    <span
      style={{
        fontFamily: FONT,
        fontSize: 13,
        fontWeight: 600,
        padding: "3px 10px",
        borderRadius: "var(--radius-pill)",
        border: `1px solid ${hit ? "var(--color-warn)" : "var(--glass-border)"}`,
        color: hit ? "var(--color-warn)" : "var(--text-secondary)",
        whiteSpace: "nowrap",
      }}
    >
      {label}
      {hit ? " · hit" : ""}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Inline editor — levels, size, exits. One quiet panel below the row.
// ---------------------------------------------------------------------------

function num(v: string): number | null {
  const t = v.trim();
  if (!t) return null;
  const n = Number(t.replace(/,/g, ""));
  return Number.isFinite(n) && n > 0 ? n : null;
}

const FIELD_STYLE: React.CSSProperties = {
  fontFamily: FONT,
  fontSize: 14,
  fontWeight: 500,
  color: "var(--text-primary)",
  background: "var(--bg-base)",
  border: "1px solid var(--glass-border)",
  borderRadius: "var(--radius-md)",
  padding: "8px 10px",
  width: "100%",
  outline: "none",
  fontVariantNumeric: "tabular-nums",
};

function Field({
  label,
  suffix,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  suffix: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}): React.ReactElement {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 5, flex: 1, minWidth: 0 }}>
      <span
        style={{
          fontFamily: FONT,
          fontSize: 13,
          fontWeight: 500,
          color: "var(--text-tertiary)",
        }}
      >
        {label}
      </span>
      <span style={{ position: "relative", display: "block" }}>
        <input
          type="text"
          inputMode="decimal"
          value={value}
          placeholder={placeholder ?? "—"}
          onChange={(e) => onChange(e.target.value)}
          style={{ ...FIELD_STYLE, paddingRight: 34 }}
        />
        <span
          aria-hidden
          style={{
            position: "absolute",
            right: 10,
            top: "50%",
            transform: "translateY(-50%)",
            fontFamily: FONT,
            fontSize: 13,
            color: "var(--text-tertiary)",
          }}
        >
          {suffix}
        </span>
      </span>
    </label>
  );
}

function EditorButton({
  children,
  tone = "ghost",
  disabled,
  onClick,
}: {
  children: React.ReactNode;
  tone?: "primary" | "ghost" | "danger";
  disabled?: boolean;
  onClick: () => void;
}): React.ReactElement {
  const solid = tone === "primary";
  const danger = tone === "danger";
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      style={{
        fontFamily: FONT,
        fontSize: 13.5,
        fontWeight: 600,
        padding: "8px 16px",
        borderRadius: "var(--radius-md)",
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.55 : 1,
        border: solid
          ? "1px solid var(--text-primary)"
          : `1px solid ${danger ? "var(--color-warn)" : "var(--glass-border)"}`,
        background: solid ? "var(--text-primary)" : "var(--bg-base)",
        color: solid
          ? "var(--bg-base)"
          : danger
            ? "var(--color-warn)"
            : "var(--text-secondary)",
        transition: "border-color 160ms var(--ease-quartr)",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </button>
  );
}

function PositionEditor({
  position,
  onSaved,
  onClose,
}: {
  position: ViewPositionItem;
  onSaved: (next: ViewPositionItem, note?: string) => void;
  onClose: () => void;
}): React.ReactElement {
  const [tp, setTp] = React.useState(
    position.take_profit_pct != null ? String(position.take_profit_pct) : "",
  );
  const [sl, setSl] = React.useState(
    position.stop_loss_pct != null ? String(position.stop_loss_pct) : "",
  );
  const [size, setSize] = React.useState(
    position.capital_inr != null ? String(Math.round(position.capital_inr)) : "",
  );
  const [exitPct, setExitPct] = React.useState<number | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function save(): Promise<void> {
    setBusy(true);
    setError(null);
    const res = await updateViewPosition(position.id, {
      take_profit_pct: num(tp),
      stop_loss_pct: num(sl),
      capital_inr: num(size),
    });
    setBusy(false);
    if (isError(res)) {
      setError(res.error.message);
      return;
    }
    onSaved(res.data);
    onClose();
  }

  async function doExit(): Promise<void> {
    if (exitPct == null) return;
    setBusy(true);
    setError(null);
    const res = await exitViewPosition(position.id, exitPct);
    setBusy(false);
    if (isError(res)) {
      setError(res.error.message);
      return;
    }
    onSaved(res.data.position, res.data.note);
    onClose();
  }

  const open = position.status === "open";

  return (
    <div
      style={{
        marginTop: 14,
        display: "flex",
        flexDirection: "column",
        gap: 14,
      }}
      data-testid={`position-editor-${position.id}`}
    >
      <Hairline />

      {/* Exit plan — levels on the ledger, compared live, never auto-acted. */}
      <div style={{ display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}>
        <Field
          label="Take profit at"
          suffix="%"
          value={tp}
          onChange={setTp}
          placeholder="e.g. 12"
        />
        <Field
          label="Stop loss at"
          suffix="−%"
          value={sl}
          onChange={setSl}
          placeholder="e.g. 6"
        />
        <Field
          label="Position size"
          suffix="₹"
          value={size}
          onChange={setSize}
          placeholder="e.g. 100000"
        />
        <EditorButton tone="primary" disabled={busy} onClick={() => void save()}>
          Save
        </EditorButton>
      </div>
      <p
        style={{
          margin: 0,
          fontFamily: FONT,
          fontSize: 13,
          color: "var(--text-tertiary)",
          lineHeight: 1.5,
        }}
      >
        Levels live on your ledger — we mark them hit against the live return.
        Orders stay yours: place every entry and exit in your own broker app.
      </p>

      {/* Exit — a portion or all of what's still on. */}
      {open && (
        <>
          <Hairline />
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              flexWrap: "wrap",
            }}
          >
            <span
              style={{
                fontFamily: FONT,
                fontSize: 13,
                fontWeight: 500,
                color: "var(--text-tertiary)",
                marginRight: 2,
              }}
            >
              Exit
            </span>
            {[25, 50, 100].map((p) => {
              const active = exitPct === p;
              return (
                <button
                  key={p}
                  type="button"
                  onClick={() => setExitPct(active ? null : p)}
                  style={{
                    fontFamily: FONT,
                    fontSize: 13,
                    fontWeight: 600,
                    padding: "6px 14px",
                    borderRadius: "var(--radius-pill)",
                    cursor: "pointer",
                    border: `1px solid ${active ? "var(--text-primary)" : "var(--glass-border)"}`,
                    background: active ? "var(--text-primary)" : "var(--bg-base)",
                    color: active ? "var(--bg-base)" : "var(--text-secondary)",
                    transition: "all 160ms var(--ease-quartr)",
                  }}
                >
                  {p === 100 ? "All" : `${p}%`}
                </button>
              );
            })}
            <div style={{ flex: 1 }} />
            <EditorButton
              tone="danger"
              disabled={busy || exitPct == null}
              onClick={() => void doExit()}
            >
              {exitPct == null
                ? "Exit…"
                : exitPct === 100
                  ? "Exit the position"
                  : `Exit ${exitPct}% of what's on`}
            </EditorButton>
          </div>
        </>
      )}

      {error && (
        <p
          role="alert"
          style={{
            margin: 0,
            fontFamily: FONT,
            fontSize: 13,
            color: "var(--color-loss)",
          }}
        >
          {error}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// One position row-card
// ---------------------------------------------------------------------------

function PositionCard({
  position,
  onChanged,
  onOpenView,
}: {
  position: ViewPositionItem;
  onChanged: (next: ViewPositionItem, note?: string) => void;
  onOpenView?: (viewId: string) => void;
}): React.ReactElement {
  const [editing, setEditing] = React.useState(false);
  const [holdingsOpen, setHoldingsOpen] = React.useState(false);
  const [exitNote, setExitNote] = React.useState<string | null>(null);

  const chip = statusChip(position);
  const exited = position.status === "exited";
  const ret = position.return_pct;
  const partial =
    !exited && position.open_fraction > 0 && position.open_fraction < 0.9999;

  const pricedLegs = position.legs.filter((l) => l.entry_price != null);
  const optionsStyle = position.expression_kind === "option_strategy" ||
    position.expression_kind === "hedge";

  return (
    <ViewSurface as="article" data-testid={`my-view-${position.id}`}>
      {/* header row: title + status | live return */}
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: 16,
        }}
      >
        <div style={{ minWidth: 0 }}>
          <div
            style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}
          >
            <button
              type="button"
              onClick={
                onOpenView ? () => onOpenView(position.view_id) : undefined
              }
              style={{
                fontFamily: FONT,
                fontSize: 17,
                fontWeight: 600,
                letterSpacing: "-0.01em",
                lineHeight: 1.3,
                color: "var(--text-primary)",
                background: "none",
                border: "none",
                padding: 0,
                textAlign: "left",
                cursor: onOpenView ? "pointer" : "default",
              }}
            >
              {position.view_title ?? "View"}
            </button>
            <span
              className="inline-flex items-center gap-1.5"
              style={{
                fontFamily: FONT,
                fontSize: 13,
                fontWeight: 500,
                color: "var(--text-secondary)",
                whiteSpace: "nowrap",
              }}
            >
              <span
                aria-hidden
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: chip.color,
                  flexShrink: 0,
                }}
              />
              {chip.word}
            </span>
          </div>
          <p
            style={{
              margin: "5px 0 0",
              fontFamily: FONT,
              fontSize: 13.5,
              fontWeight: 500,
              color: "var(--text-tertiary)",
              lineHeight: 1.4,
            }}
          >
            {position.strategy_name ?? tierLabel(position.tier)}
            {position.tier ? ` · ${tierLabel(position.tier)}` : ""}
            {position.entry_at ? ` · since ${fmtDate(position.entry_at)}` : ""}
            {partial
              ? ` · ${Math.round(position.open_fraction * 100)}% still on`
              : ""}
          </p>
        </div>

        {/* the ONE accent number */}
        <div style={{ textAlign: "right", flexShrink: 0 }}>
          {ret != null ? (
            <>
              <div
                style={{
                  fontFamily: FONT,
                  fontSize: 22,
                  fontWeight: 800,
                  letterSpacing: "-0.02em",
                  color: signColor(ret),
                  fontVariantNumeric: "tabular-nums",
                  lineHeight: 1.1,
                }}
              >
                {fmtPct(ret)}
              </div>
              <div
                style={{
                  fontFamily: FONT,
                  fontSize: 13,
                  color: "var(--text-tertiary)",
                  marginTop: 2,
                }}
              >
                since entry
              </div>
            </>
          ) : (
            <div
              style={{
                fontFamily: FONT,
                fontSize: 13,
                fontWeight: 600,
                color: "var(--text-tertiary)",
                padding: "4px 10px",
                border: "1px solid var(--glass-border)",
                borderRadius: "var(--radius-pill)",
                whiteSpace: "nowrap",
              }}
            >
              {optionsStyle ? "Priced at deploy" : "No live price"}
            </div>
          )}
        </div>
      </div>

      {/* numbers strip */}
      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          gap: 26,
          marginTop: 14,
          flexWrap: "wrap",
        }}
      >
        <Cell label="Put in" value={inr(position.capital_inr)} />
        <Cell
          label="Worth now"
          value={inr(position.open_value_inr)}
          valueColor={
            position.unrealized_pnl_inr != null
              ? signColor(position.unrealized_pnl_inr)
              : undefined
          }
        />
        {position.realized_pnl_inr != null && (
          <Cell
            label="Realized"
            value={inr(position.realized_pnl_inr)}
            valueColor={signColor(position.realized_pnl_inr)}
          />
        )}
        <div style={{ flex: 1 }} />
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {position.take_profit_pct != null && (
            <LevelChip
              label={`Take profit +${position.take_profit_pct}%`}
              hit={position.take_profit_hit}
            />
          )}
          {position.stop_loss_pct != null && (
            <LevelChip
              label={`Stop loss −${position.stop_loss_pct}%`}
              hit={position.stop_loss_hit}
            />
          )}
          {!exited && (
            <button
              type="button"
              onClick={() => setEditing((v) => !v)}
              data-testid={`edit-position-${position.id}`}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                fontFamily: FONT,
                fontSize: 13.5,
                fontWeight: 600,
                padding: "7px 14px",
                borderRadius: "var(--radius-md)",
                border: `1px solid ${editing ? "var(--glass-border-hover)" : "var(--glass-border)"}`,
                background: "var(--bg-base)",
                color: "var(--text-primary)",
                cursor: "pointer",
                transition: "border-color 160ms var(--ease-quartr)",
              }}
            >
              {editing ? (
                <X size={13} aria-hidden />
              ) : (
                <Pencil size={13} aria-hidden />
              )}
              {editing ? "Close" : "Edit"}
            </button>
          )}
        </div>
      </div>

      {/* holdings — progressive disclosure, only when there are priced legs */}
      {pricedLegs.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <button
            type="button"
            onClick={() => setHoldingsOpen((v) => !v)}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              background: "none",
              border: "none",
              padding: 0,
              fontFamily: FONT,
              fontSize: 13,
              fontWeight: 500,
              color: "var(--text-tertiary)",
              cursor: "pointer",
            }}
          >
            <ChevronDown
              size={13}
              aria-hidden
              style={{
                transform: holdingsOpen ? "rotate(180deg)" : "none",
                transition: "transform 160ms var(--ease-quartr)",
              }}
            />
            {pricedLegs.length} holding{pricedLegs.length === 1 ? "" : "s"}
          </button>
          {holdingsOpen && (
            <div
              style={{
                marginTop: 8,
                display: "flex",
                flexDirection: "column",
                gap: 6,
              }}
            >
              {position.legs.map((leg, i) => (
                <div
                  key={`${leg.symbol}-${i}`}
                  style={{
                    display: "flex",
                    alignItems: "baseline",
                    gap: 10,
                    fontFamily: FONT,
                    fontSize: 13.5,
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                    {leg.symbol}
                  </span>
                  {leg.side === "short" && (
                    <span style={{ fontSize: 13, color: "var(--text-tertiary)" }}>
                      short
                    </span>
                  )}
                  <span style={{ flex: 1 }} />
                  <span style={{ color: "var(--text-tertiary)" }}>
                    {leg.entry_price != null && leg.last_price != null
                      ? `${leg.entry_price.toLocaleString("en-IN")} → ${leg.last_price.toLocaleString("en-IN")}`
                      : "no live price"}
                  </span>
                  <span
                    style={{
                      minWidth: 62,
                      textAlign: "right",
                      fontWeight: 600,
                      color:
                        leg.return_pct != null
                          ? signColor(leg.return_pct)
                          : "var(--text-tertiary)",
                    }}
                  >
                    {leg.return_pct != null ? fmtPct(leg.return_pct) : "—"}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* honesty note (e.g. options priced-at-deploy, one-sided pair) */}
      {position.note && (
        <p
          style={{
            margin: "12px 0 0",
            fontFamily: FONT,
            fontSize: 13,
            color: "var(--text-tertiary)",
            lineHeight: 1.5,
          }}
        >
          {position.note}
        </p>
      )}

      {/* post-exit reminder — register-not-execute */}
      {exitNote && (
        <p
          role="status"
          style={{
            margin: "12px 0 0",
            fontFamily: FONT,
            fontSize: 13,
            fontWeight: 500,
            color: "var(--text-secondary)",
            lineHeight: 1.5,
          }}
        >
          {exitNote}
        </p>
      )}

      {editing && (
        <PositionEditor
          position={position}
          onSaved={(next, note) => {
            onChanged(next, note);
            if (note) setExitNote(note);
          }}
          onClose={() => setEditing(false)}
        />
      )}
    </ViewSurface>
  );
}

// ---------------------------------------------------------------------------
// Section shell: header, loading, error, empty, list
// ---------------------------------------------------------------------------

export function MyViews({
  onOpenView,
  onBrowse,
  embedded = false,
}: MyViewsProps): React.ReactElement {
  const [state, setState] = React.useState<FetchState>({ kind: "loading" });

  const load = React.useCallback((): void => {
    setState({ kind: "loading" });
    listViewPositions()
      .then((res) => {
        if (isError(res)) {
          setState({ kind: "error", message: res.error.message });
          return;
        }
        setState({ kind: "ok", items: res.data.items });
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Network error";
        setState({ kind: "error", message: msg });
      });
  }, []);

  React.useEffect(() => {
    load();
  }, [load]);

  const replace = React.useCallback((next: ViewPositionItem): void => {
    setState((prev) =>
      prev.kind === "ok"
        ? {
            kind: "ok",
            items: prev.items.map((p) => (p.id === next.id ? next : p)),
          }
        : prev,
    );
  }, []);

  const open =
    state.kind === "ok" ? state.items.filter((p) => p.status === "open") : [];
  const closed =
    state.kind === "ok" ? state.items.filter((p) => p.status !== "open") : [];

  return (
    <div
      className="flex flex-col"
      style={{ gap: 20 }}
      data-testid="my-views-section"
    >
      {!embedded && (
        <div>
          <h2
            style={{
              fontFamily: FONT,
              fontSize: 22,
              fontWeight: 600,
              letterSpacing: "-0.02em",
              color: "var(--text-primary)",
              margin: "0 0 5px",
              lineHeight: 1.2,
            }}
          >
            My Opinions
          </h2>
          <p
            style={{
              fontFamily: FONT,
              fontSize: 14.5,
              color: "var(--text-secondary)",
              margin: 0,
              lineHeight: 1.5,
            }}
          >
            Every belief you&apos;ve put money behind — and how it&apos;s doing.
          </p>
        </div>
      )}

      {state.kind === "loading" && (
        <div className="flex flex-col" style={{ gap: 14 }} aria-hidden>
          {[0, 1].map((i) => (
            <div
              key={i}
              style={{
                height: 132,
                borderRadius: "var(--radius-lg)",
                border: "1px solid var(--glass-border)",
                opacity: 0.6,
              }}
            />
          ))}
        </div>
      )}

      {state.kind === "error" && (
        <ViewSurface as="div">
          <div
            className="flex items-center"
            style={{ gap: 10, fontFamily: FONT }}
          >
            <AlertCircle
              size={16}
              aria-hidden
              style={{ color: "var(--color-loss)", flexShrink: 0 }}
            />
            <span style={{ fontSize: 14, color: "var(--text-secondary)", flex: 1 }}>
              {state.message}
            </span>
            <button
              type="button"
              onClick={load}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                fontFamily: FONT,
                fontSize: 13.5,
                fontWeight: 600,
                padding: "7px 14px",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--glass-border)",
                background: "var(--bg-base)",
                color: "var(--text-primary)",
                cursor: "pointer",
              }}
            >
              <RefreshCw size={13} aria-hidden />
              Retry
            </button>
          </div>
        </ViewSurface>
      )}

      {state.kind === "ok" && state.items.length === 0 && (
        <ViewSurface as="div" style={{ padding: 36, textAlign: "center" }}>
          <Telescope
            size={22}
            aria-hidden
            style={{ color: "var(--text-tertiary)", margin: "0 auto 10px" }}
          />
          <p
            style={{
              margin: 0,
              fontFamily: FONT,
              fontSize: 15,
              fontWeight: 600,
              color: "var(--text-primary)",
            }}
          >
            Nothing here yet
          </p>
          <p
            style={{
              margin: "5px 0 0",
              fontFamily: FONT,
              fontSize: 13.5,
              color: "var(--text-secondary)",
              lineHeight: 1.5,
            }}
          >
            Open a view, pick a strategy, and press Deploy — it lands here with
            its live return.
          </p>
          {onBrowse && (
            <button
              type="button"
              onClick={onBrowse}
              style={{
                marginTop: 16,
                fontFamily: FONT,
                fontSize: 13.5,
                fontWeight: 600,
                padding: "9px 18px",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--text-primary)",
                background: "var(--text-primary)",
                color: "var(--bg-base)",
                cursor: "pointer",
              }}
            >
              Browse opinion markets
            </button>
          )}
        </ViewSurface>
      )}

      {state.kind === "ok" && open.length > 0 && (
        <div className="flex flex-col" style={{ gap: 14 }} role="list">
          {open.map((p) => (
            <div key={p.id} role="listitem">
              <PositionCard
                position={p}
                onChanged={replace}
                onOpenView={onOpenView}
              />
            </div>
          ))}
        </div>
      )}

      {state.kind === "ok" && closed.length > 0 && (
        <>
          <div
            style={{
              fontFamily: FONT,
              fontSize: 13,
              fontWeight: 600,
              letterSpacing: "0.04em",
              textTransform: "uppercase",
              color: "var(--text-tertiary)",
              marginTop: open.length > 0 ? 6 : 0,
            }}
          >
            Closed
          </div>
          <div className="flex flex-col" style={{ gap: 14 }} role="list">
            {closed.map((p) => (
              <div key={p.id} role="listitem" style={{ opacity: 0.75 }}>
                <PositionCard
                  position={p}
                  onChanged={replace}
                  onOpenView={onOpenView}
                />
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
