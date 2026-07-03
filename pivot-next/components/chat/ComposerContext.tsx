"use client";

/**
 * ComposerContext — the chat composer's context-attachment system.
 *
 * Three ways to attach context to a message (ChatGPT/Claude "+"-menu and
 * Quartr-Pro "@"-mention patterns, adapted to securities):
 *
 *   1. The "+" button (left edge of the composer pill) opens a menu:
 *      Tag a security / Select an agent / Select a position, plus two
 *      intentionally-disabled entries (Deep research, Web browsing) that
 *      mark the roadmap without pretending to work.
 *   2. Typing "@" in the composer opens a typeahead over the company
 *      universe (`/api/companies/search`); picking a result inserts
 *      `@SYMBOL` into the text AND records a structured attachment.
 *   3. "Edit with chat" from the Agents grid attaches that agent.
 *
 * Selected items render as compact chips docked INSIDE the pill above the
 * textarea (logo/icon + primary label + quiet sublabel + dismiss). The
 * structured attachments ride the chat request (`attachments: [...]`) so
 * the backend can ground the LLM on exactly what was tagged.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Briefcase,
  Bot,
  Check,
  ChevronLeft,
  Globe,
  Loader2,
  Plus,
  Telescope,
  Workflow as WorkflowIcon,
  X,
} from "lucide-react";

import {
  getPortfolioHoldings,
  getWorkflow,
  listWorkflows,
  searchCompanies,
  type CompanySearchResult,
  type Holding,
} from "@/lib/api";
import { isError, type Workflow, type WorkflowSummary } from "@/lib/types";
import { CompanyLogo } from "@/components/CompanyLogo";

// ---------------------------------------------------------------------------
// Attachment model — mirrors the backend ChatRequest.attachments contract.
// ---------------------------------------------------------------------------

export type ChatAttachment =
  | {
      kind: "security";
      symbol: string;
      name: string;
      logo_url?: string | null;
    }
  | {
      kind: "position";
      symbol: string;
      name?: string;
      quantity: number;
      avg_price: number;
      last_price: number | null;
      pnl: number | null;
      book: "portfolio" | "paper";
    }
  | {
      kind: "agent";
      workflow_id: string;
      name: string;
      description: string;
      status: string;
    };

/** Stable identity for dedupe — one chip per unique subject. */
export function attachmentKey(a: ChatAttachment): string {
  if (a.kind === "agent") return `agent:${a.workflow_id}`;
  return `${a.kind}:${a.symbol.toUpperCase()}`;
}

/** Strip UI-only fields down to the wire shape the backend formats. */
export function toWireAttachment(a: ChatAttachment): Record<string, unknown> {
  if (a.kind === "security") {
    return { kind: "security", symbol: a.symbol, name: a.name };
  }
  if (a.kind === "position") {
    return {
      kind: "position",
      symbol: a.symbol,
      quantity: a.quantity,
      avg_price: a.avg_price,
      last_price: a.last_price,
      pnl: a.pnl,
      book: a.book,
    };
  }
  return {
    kind: "agent",
    workflow_id: a.workflow_id,
    name: a.name,
    description: a.description,
    status: a.status,
  };
}

// ---------------------------------------------------------------------------
// @-mention detection — pure helper shared with the composer.
// ---------------------------------------------------------------------------

const MENTION_RE = /(?:^|[\s(])@([A-Za-z0-9&.\-]{0,24})$/;

/**
 * If the text immediately before the caret is an in-progress `@mention`,
 * return its query + the index of the `@`. Null when the caret isn't in
 * a mention context (so the dropdown stays closed for emails etc. — the
 * char before `@` must be a word boundary).
 */
export function detectMentionQuery(
  text: string,
  caret: number,
): { query: string; start: number } | null {
  const upto = text.slice(0, caret);
  const m = MENTION_RE.exec(upto);
  if (!m) return null;
  const query = m[1] ?? "";
  return { query, start: caret - query.length - 1 };
}

// ---------------------------------------------------------------------------
// Shared inline-style tokens (Quartr idiom used across the composer).
// ---------------------------------------------------------------------------

const FONT = "var(--font-ui)";

const panelStyle: React.CSSProperties = {
  background: "var(--bg-elevated)",
  border: "1px solid var(--glass-border)",
  borderRadius: "var(--radius-lg, 14px)",
  boxShadow: "0 12px 32px rgba(0,0,0,0.22)",
  overflow: "hidden",
  fontFamily: FONT,
};

const rowBase: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  width: "100%",
  gap: 10,
  padding: "9px 12px",
  background: "transparent",
  border: "none",
  textAlign: "left",
  fontFamily: FONT,
  fontSize: 13,
  color: "var(--text-primary)",
  cursor: "pointer",
  transition: "background-color 0.15s var(--ease-quartr)",
};

function hoverable(
  e: React.MouseEvent<HTMLElement>,
  on: boolean,
): void {
  e.currentTarget.style.background = on ? "var(--surface-active)" : "transparent";
}

// ---------------------------------------------------------------------------
// Attachment chips — docked inside the composer pill, above the textarea.
// ---------------------------------------------------------------------------

function chipIcon(a: ChatAttachment): React.ReactNode {
  if (a.kind === "security") {
    return (
      <CompanyLogo
        symbol={a.symbol}
        name={a.name || a.symbol}
        logoUrl={a.logo_url ?? undefined}
        size={26}
      />
    );
  }
  const Icon = a.kind === "agent" ? Bot : Briefcase;
  return (
    <span
      aria-hidden={true}
      className="inline-flex items-center justify-center"
      style={{
        width: 26,
        height: 26,
        borderRadius: 8,
        background: "var(--surface-active)",
        color: "var(--text-secondary)",
        flexShrink: 0,
      }}
    >
      <Icon size={14} strokeWidth={2} />
    </span>
  );
}

function chipPrimary(a: ChatAttachment): string {
  if (a.kind === "agent") return a.name;
  return a.symbol.toUpperCase();
}

function chipSecondary(a: ChatAttachment): string {
  if (a.kind === "security") return a.name || "Security";
  if (a.kind === "agent") {
    const st = a.status ? a.status.charAt(0).toUpperCase() + a.status.slice(1) : "";
    return st ? `Agent · ${st}` : "Agent";
  }
  const qty = `${a.quantity}`.replace(/\.0$/, "");
  const side = a.quantity < 0 ? "short" : "";
  return [
    `${qty} sh`,
    a.avg_price ? `avg ₹${a.avg_price.toLocaleString("en-IN", { maximumFractionDigits: 2 })}` : "",
    side,
    a.book === "paper" ? "paper" : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

export function AttachmentChips({
  attachments,
  onRemove,
}: {
  attachments: ChatAttachment[];
  onRemove: (key: string) => void;
}): React.ReactElement | null {
  if (attachments.length === 0) return null;
  return (
    <div
      data-testid="composer-attachments"
      className="flex flex-wrap items-center"
      style={{ gap: 6, padding: "8px 8px 2px" }}
    >
      {attachments.map((a) => {
        const key = attachmentKey(a);
        return (
          <div
            key={key}
            data-testid={`attachment-chip-${key}`}
            className="inline-flex items-center"
            style={{
              gap: 8,
              maxWidth: 260,
              padding: "5px 6px 5px 6px",
              borderRadius: 10,
              background: "var(--bg-elevated)",
              border: "1px solid var(--glass-border)",
            }}
          >
            {chipIcon(a)}
            <div style={{ minWidth: 0, lineHeight: 1.2 }}>
              <div
                style={{
                  fontFamily: FONT,
                  fontSize: 12.5,
                  fontWeight: 600,
                  color: "var(--text-primary)",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {chipPrimary(a)}
              </div>
              <div
                style={{
                  fontFamily: FONT,
                  fontSize: 10.5,
                  color: "var(--text-tertiary)",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {chipSecondary(a)}
              </div>
            </div>
            <button
              type="button"
              onClick={() => onRemove(key)}
              aria-label={`Remove ${chipPrimary(a)}`}
              className="inline-flex items-center justify-center"
              style={{
                width: 20,
                height: 20,
                flexShrink: 0,
                background: "transparent",
                border: "none",
                borderRadius: 6,
                color: "var(--text-tertiary)",
                cursor: "pointer",
                transition: "color 0.15s var(--ease-quartr)",
              }}
              onMouseEnter={(e) => { e.currentTarget.style.color = "var(--text-primary)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.color = "var(--text-tertiary)"; }}
            >
              <X size={13} strokeWidth={2} aria-hidden={true} />
            </button>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// "+" menu — root page + three picker pages, rendered as a popover
// anchored above the + button (the composer is docked at the bottom).
// ---------------------------------------------------------------------------

type MenuPage = "root" | "agent" | "position";

export function ComposerPlusMenu({
  onAttach,
  onAgentPicked,
}: {
  onAttach: (a: ChatAttachment) => void;
  /** Called with the FULL workflow when an agent is picked — the parent
   * attaches the chip, seeds the edit target, and opens the editor. */
  onAgentPicked: (workflow: Workflow) => void;
}): React.ReactElement {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState<MenuPage>("root");
  const wrapRef = useRef<HTMLDivElement>(null);

  // Outside click / Esc dismisses.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent): void => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const close = useCallback((): void => {
    setOpen(false);
    setPage("root");
  }, []);

  return (
    <div ref={wrapRef} style={{ position: "relative" }} className="self-end">
      <button
        type="button"
        onClick={() => {
          setPage("root");
          setOpen((v) => !v);
        }}
        aria-label="Add context"
        aria-expanded={open}
        data-testid="composer-plus-btn"
        className="flex h-7 w-7 shrink-0 items-center justify-center sm:h-8 sm:w-8"
        style={{
          background: open ? "var(--surface-active)" : "transparent",
          color: "var(--text-secondary)",
          border: "none",
          borderRadius: "var(--radius-pill)",
          cursor: "pointer",
          transition:
            "color 0.18s var(--ease-quartr), background-color 0.2s var(--ease-quartr), transform 0.2s var(--ease-quartr)",
          transform: open ? "rotate(45deg)" : "none",
        }}
        onMouseEnter={(e) => { e.currentTarget.style.color = "var(--text-primary)"; }}
        onMouseLeave={(e) => { if (!open) e.currentTarget.style.color = "var(--text-secondary)"; }}
      >
        <Plus className="h-4 w-4 sm:h-[18px] sm:w-[18px]" strokeWidth={2} aria-hidden={true} />
      </button>

      {open && (
        <div
          role="menu"
          data-testid="composer-plus-menu"
          style={{
            ...panelStyle,
            position: "absolute",
            bottom: "calc(100% + 10px)",
            left: 0,
            width: page === "root" ? 250 : 320,
            zIndex: 50,
          }}
        >
          {page === "root" ? (
            <RootMenu onPick={setPage} />
          ) : page === "agent" ? (
            <AgentPicker
              onBack={() => setPage("root")}
              onPick={(wf) => {
                onAgentPicked(wf);
                close();
              }}
            />
          ) : (
            <PositionPicker
              onBack={() => setPage("root")}
              onPick={(a) => {
                onAttach(a);
                close();
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}

function RootMenu({ onPick }: { onPick: (p: MenuPage) => void }): React.ReactElement {
  // NOTE: security tagging is deliberately NOT in this menu — it lives
  // inline in the prompt bar (type "@" → typeahead), matching the
  // Claude-Code/Quartr-Pro mention pattern. The menu covers only the
  // pickers that need a browsable list.
  const items: Array<{
    page?: MenuPage;
    label: string;
    hint?: string;
    icon: React.ComponentType<{ size?: number; strokeWidth?: number; "aria-hidden"?: boolean }>;
    disabled?: boolean;
  }> = [
    { page: "agent", label: "Select an agent", hint: "edit or build on one of yours", icon: WorkflowIcon },
    { page: "position", label: "Select a position", hint: "talk about a holding", icon: Briefcase },
  ];
  return (
    <div style={{ padding: 5 }}>
      {items.map((it) => {
        const Icon = it.icon;
        return (
          <button
            key={it.label}
            type="button"
            role="menuitem"
            data-testid={`plus-item-${it.page}`}
            style={{ ...rowBase, borderRadius: 9 }}
            onClick={() => it.page && onPick(it.page)}
            onMouseEnter={(e) => hoverable(e, true)}
            onMouseLeave={(e) => hoverable(e, false)}
          >
            <Icon size={15} strokeWidth={2} aria-hidden={true} />
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={{ display: "block", fontWeight: 500 }}>{it.label}</span>
              {it.hint && (
                <span style={{ display: "block", fontSize: 11, color: "var(--text-tertiary)" }}>
                  {it.hint}
                </span>
              )}
            </span>
          </button>
        );
      })}

      <div
        aria-hidden={true}
        style={{ height: 1, margin: "5px 8px", background: "var(--glass-border)" }}
      />

      {/* Roadmap entries — deliberately inert. Visible so the surface
          shape is honest about direction without faking capability. */}
      {[
        { label: "Deep research", icon: Telescope },
        { label: "Web browsing", icon: Globe },
      ].map((it) => {
        const Icon = it.icon;
        return (
          <div
            key={it.label}
            role="menuitem"
            aria-disabled={true}
            data-testid={`plus-item-disabled-${it.label.toLowerCase().replace(/\s+/g, "-")}`}
            style={{
              ...rowBase,
              cursor: "default",
              color: "var(--text-disabled)",
            }}
          >
            <Icon size={15} strokeWidth={2} aria-hidden={true} />
            <span style={{ flex: 1 }}>{it.label}</span>
            <span
              style={{
                fontSize: 9.5,
                fontWeight: 600,
                letterSpacing: "0.06em",
                textTransform: "uppercase",
                padding: "2px 6px",
                borderRadius: "var(--radius-pill)",
                background: "var(--surface-active)",
                color: "var(--text-tertiary)",
              }}
            >
              Soon
            </span>
          </div>
        );
      })}

      {/* Discoverability nudge — securities are tagged inline, not here. */}
      <div
        aria-hidden={true}
        style={{
          padding: "7px 12px 4px",
          fontSize: 10.5,
          fontFamily: FONT,
          color: "var(--text-tertiary)",
          borderTop: "1px solid var(--glass-border)",
          marginTop: 5,
        }}
      >
        Tip: type <span style={{ fontWeight: 600 }}>@</span> in the message to
        tag a stock
      </div>
    </div>
  );
}

function PageHeader({
  title,
  onBack,
}: {
  title: string;
  onBack: () => void;
}): React.ReactElement {
  return (
    <div
      className="flex items-center"
      style={{
        gap: 6,
        padding: "8px 10px",
        borderBottom: "1px solid var(--glass-border)",
      }}
    >
      <button
        type="button"
        onClick={onBack}
        aria-label="Back"
        className="inline-flex items-center justify-center"
        style={{
          width: 22,
          height: 22,
          background: "transparent",
          border: "none",
          borderRadius: 6,
          color: "var(--text-secondary)",
          cursor: "pointer",
        }}
      >
        <ChevronLeft size={15} strokeWidth={2} aria-hidden={true} />
      </button>
      <span
        style={{
          fontFamily: FONT,
          fontSize: 12,
          fontWeight: 600,
          color: "var(--text-primary)",
        }}
      >
        {title}
      </span>
    </div>
  );
}

function EmptyRow({ text }: { text: string }): React.ReactElement {
  return (
    <div
      style={{
        padding: "14px 12px",
        fontFamily: FONT,
        fontSize: 12,
        color: "var(--text-tertiary)",
      }}
    >
      {text}
    </div>
  );
}

function LoadingRow(): React.ReactElement {
  return (
    <div
      className="flex items-center justify-center"
      style={{ padding: "16px 0", color: "var(--text-tertiary)" }}
    >
      <Loader2 size={15} className="animate-spin" aria-label="Loading" />
    </div>
  );
}

// ── Agent picker ─────────────────────────────────────────────────────────

const STATUS_DOT: Record<string, string> = {
  active: "#34a853",
  paused: "#f9ab00",
  draft: "var(--text-tertiary)",
};

function AgentPicker({
  onBack,
  onPick,
}: {
  onBack: () => void;
  onPick: (workflow: Workflow) => void;
}): React.ReactElement {
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "error" }
    | { kind: "ok"; items: WorkflowSummary[] }
  >({ kind: "loading" });
  const [fetchingId, setFetchingId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void listWorkflows({ limit: 50 }).then((res) => {
      if (cancelled) return;
      if (isError(res)) setState({ kind: "error" });
      else setState({ kind: "ok", items: res.data.items });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const pick = async (summary: WorkflowSummary): Promise<void> => {
    setFetchingId(summary.id);
    // The picker list is steps-free; the edit flow needs the FULL
    // workflow so the backend amends this exact agent's steps.
    const res = await getWorkflow(summary.id);
    setFetchingId(null);
    if (!isError(res)) onPick(res.data);
  };

  return (
    <div>
      <PageHeader title="Select an agent" onBack={onBack} />
      <div style={{ maxHeight: 280, overflowY: "auto", padding: 5 }}>
        {state.kind === "loading" ? (
          <LoadingRow />
        ) : state.kind === "error" ? (
          <EmptyRow text="Couldn't load your agents." />
        ) : state.items.length === 0 ? (
          <EmptyRow text="No agents yet — describe one in chat to create it." />
        ) : (
          state.items.map((wf) => (
            <button
              key={wf.id}
              type="button"
              data-testid={`plus-agent-${wf.id}`}
              style={{ ...rowBase, borderRadius: 9 }}
              onClick={() => void pick(wf)}
              disabled={fetchingId !== null}
              onMouseEnter={(e) => hoverable(e, true)}
              onMouseLeave={(e) => hoverable(e, false)}
            >
              <span
                aria-hidden={true}
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: "50%",
                  flexShrink: 0,
                  background: STATUS_DOT[wf.status] ?? "var(--text-tertiary)",
                }}
              />
              <span style={{ flex: 1, minWidth: 0 }}>
                <span
                  style={{
                    display: "block",
                    fontWeight: 600,
                    fontSize: 12.5,
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {wf.name}
                </span>
                {wf.description && (
                  <span
                    style={{
                      display: "block",
                      fontSize: 11,
                      color: "var(--text-tertiary)",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {wf.description}
                  </span>
                )}
              </span>
              {fetchingId === wf.id ? (
                <Loader2 size={13} className="animate-spin" aria-hidden={true} />
              ) : (
                <Check
                  size={13}
                  strokeWidth={2}
                  aria-hidden={true}
                  style={{ opacity: 0, flexShrink: 0 }}
                />
              )}
            </button>
          ))
        )}
      </div>
    </div>
  );
}

// ── Position picker ──────────────────────────────────────────────────────

function PositionPicker({
  onBack,
  onPick,
}: {
  onBack: () => void;
  onPick: (a: ChatAttachment) => void;
}): React.ReactElement {
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "error" }
    | { kind: "ok"; items: Holding[] }
  >({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    // Honours the global trading-mode toggle: in paper mode this reads
    // the paper book (adapted to the same Holding shape).
    void getPortfolioHoldings().then((res) => {
      if (cancelled) return;
      if (isError(res)) setState({ kind: "error" });
      else setState({ kind: "ok", items: res.data });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div>
      <PageHeader title="Select a position" onBack={onBack} />
      <div style={{ maxHeight: 280, overflowY: "auto", padding: 5 }}>
        {state.kind === "loading" ? (
          <LoadingRow />
        ) : state.kind === "error" ? (
          <EmptyRow text="Couldn't load your positions." />
        ) : state.items.length === 0 ? (
          <EmptyRow text="No open positions." />
        ) : (
          state.items.map((h) => {
            const pnlPos = h.pnl >= 0;
            return (
              <button
                key={`${h.exchange}:${h.tradingsymbol}`}
                type="button"
                data-testid={`plus-position-${h.tradingsymbol}`}
                style={{ ...rowBase, borderRadius: 9 }}
                onClick={() =>
                  onPick({
                    kind: "position",
                    symbol: h.tradingsymbol,
                    quantity: h.quantity,
                    avg_price: h.average_price,
                    last_price: h.last_price ?? null,
                    pnl: h.pnl ?? null,
                    book: "portfolio",
                  })
                }
                onMouseEnter={(e) => hoverable(e, true)}
                onMouseLeave={(e) => hoverable(e, false)}
              >
                <CompanyLogo symbol={h.tradingsymbol} name={h.tradingsymbol} size={24} />
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: "block", fontWeight: 600, fontSize: 12.5 }}>
                    {h.tradingsymbol}
                  </span>
                  <span style={{ display: "block", fontSize: 11, color: "var(--text-tertiary)" }}>
                    {h.quantity} sh · avg ₹
                    {h.average_price.toLocaleString("en-IN", { maximumFractionDigits: 2 })}
                  </span>
                </span>
                <span
                  style={{
                    fontSize: 11.5,
                    fontWeight: 600,
                    flexShrink: 0,
                    color: pnlPos ? "#34a853" : "#ea4335",
                  }}
                >
                  {pnlPos ? "+" : ""}
                  ₹{Math.abs(h.pnl).toLocaleString("en-IN", { maximumFractionDigits: 0 })}
                </span>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// @-mention dropdown — anchored above the composer pill while an
// in-progress mention exists at the caret.
// ---------------------------------------------------------------------------

export function MentionDropdown({
  query,
  onPick,
  onClose,
  highlighted,
  onHighlight,
  onResults,
}: {
  query: string;
  onPick: (r: CompanySearchResult) => void;
  onClose: () => void;
  /** Controlled highlight index (keyboard nav lives in the textarea). */
  highlighted: number;
  onHighlight: (i: number) => void;
  /** Reports the current result list upward so Enter can resolve it. */
  onResults: (rs: CompanySearchResult[]) => void;
}): React.ReactElement | null {
  const [results, setResults] = useState<CompanySearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const q = query.trim();
    if (!q) {
      // Bare "@" — nothing typed yet. Show nothing rather than junk.
      setResults([]);
      onResults([]);
      return;
    }
    setLoading(true);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      const res = await searchCompanies(q);
      setLoading(false);
      if (isError(res)) return;
      const rs = res.data.results.slice(0, 7);
      setResults(rs);
      onResults(rs);
      onHighlight(0);
    }, 140);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // onResults/onHighlight are stable callbacks from the parent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  if (!query.trim() && !loading) return null;

  return (
    <div
      data-testid="mention-dropdown"
      role="listbox"
      aria-label="Tag a security"
      style={{
        ...panelStyle,
        position: "absolute",
        bottom: "calc(100% + 8px)",
        left: 0,
        width: 340,
        maxWidth: "100%",
        zIndex: 55,
      }}
    >
      <div
        style={{
          padding: "7px 12px 6px",
          fontFamily: FONT,
          fontSize: 10.5,
          fontWeight: 600,
          letterSpacing: "0.05em",
          textTransform: "uppercase",
          color: "var(--text-tertiary)",
          borderBottom: "1px solid var(--glass-border)",
        }}
      >
        Tag a security
      </div>
      {loading && results.length === 0 ? (
        <LoadingRow />
      ) : results.length === 0 ? (
        <EmptyRow text="No matches — keep typing." />
      ) : (
        <div style={{ padding: 4, maxHeight: 260, overflowY: "auto" }}>
          {results.map((r, i) => (
            <button
              key={r.symbol}
              type="button"
              role="option"
              aria-selected={i === highlighted}
              data-testid={`mention-option-${r.symbol}`}
              style={{
                ...rowBase,
                borderRadius: 8,
                padding: "7px 10px",
                background: i === highlighted ? "var(--surface-active)" : "transparent",
              }}
              // mousedown (not click) so the textarea keeps focus.
              onMouseDown={(e) => {
                e.preventDefault();
                onPick(r);
              }}
              onMouseEnter={() => onHighlight(i)}
            >
              <CompanyLogo symbol={r.symbol} name={r.name} logoUrl={r.logo_url ?? undefined} size={22} />
              <span style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "baseline", gap: 8 }}>
                <span style={{ fontWeight: 600, fontSize: 12.5, flexShrink: 0 }}>{r.symbol}</span>
                <span
                  style={{
                    fontSize: 11.5,
                    color: "var(--text-tertiary)",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {r.name}
                </span>
              </span>
            </button>
          ))}
        </div>
      )}
      <button
        type="button"
        onMouseDown={(e) => {
          e.preventDefault();
          onClose();
        }}
        style={{
          ...rowBase,
          justifyContent: "center",
          padding: "6px 0",
          fontSize: 11,
          color: "var(--text-tertiary)",
          borderTop: "1px solid var(--glass-border)",
        }}
      >
        esc to dismiss
      </button>
    </div>
  );
}
