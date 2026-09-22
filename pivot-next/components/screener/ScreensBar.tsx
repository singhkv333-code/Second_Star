"use client";

// The Screener's two screen controls, and the picker behind one of them.
//
// A "screen" is one of two things and the UI must never blur them (see
// lib/screensApi.ts):
//
//   symbols  a frozen membership, usually handed over from the chart's chat.
//            Re-opening prices those names today; it does NOT re-run the
//            screen. The banner says so, with the session it was screened
//            against, because otherwise a list from three weeks ago reads like
//            a live signal.
//   filters  this service's own query, re-run on open.
//
// The saved screens used to sit in a chip rail across the page. That put a
// permanent, growing strip of secondary UI above the grid — the thing the page
// is actually for — and it got worse with every screen saved. They live in a
// picker now: two small controls on the right, and the list appears only when
// asked for.

import { useCallback, useEffect, useState } from "react";
import {
  Bookmark,
  Check,
  FolderOpen,
  ListFilter,
  Loader2,
  Radio,
  Trash2,
  X,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { isError } from "@/lib/types";
import { deleteScreen, listScreens, saveScreen } from "@/lib/screensApi";
import type {
  SavedScreen,
  ScreenFilters,
  ScreenSource,
} from "@/lib/screensApi";

/** The screen the grid is currently showing, if any. `id` is set once it has
 *  been saved — an unsaved screen is one the chart just handed over. */
export type ActiveScreen = {
  id?: number;
  name?: string;
  symbols: string[];
  criteria: string;
  ranking?: string;
  as_of: string;
  /** How many the SOURCE screen matched. Kept separate from `symbols.length`
   *  so a screen naming instruments this service cannot price still reports
   *  what it actually found. */
  matched: number;
  universe: number;
  source: ScreenSource;
};

type Props = {
  active: ActiveScreen | null;
  onOpen: (screen: SavedScreen) => void;
  onClear: () => void;
  onSaved: (screen: SavedScreen) => void;
  /** The Screener's live filter state, for saving a screen built right here.
   *  Null when no filter is set — there is nothing to save. */
  currentFilters: ScreenFilters | null;
  /** How the current filter state reads in a sentence, for the saved row. */
  currentCriteria: string;
  /** Rows the grid resolved for the active screen — used only to say how much
   *  of the membership this service could actually price. */
  resolvedCount: number | null;
};

function Kbd({ children }: { children: React.ReactNode }): React.ReactElement {
  return (
    <kbd className="inline-flex h-[18px] min-w-[18px] items-center justify-center rounded border border-border bg-background px-1 font-sans text-[10px] font-medium text-muted-foreground">
      {children}
    </kbd>
  );
}

function relativeTime(iso?: string | null): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const mins = Math.round((Date.now() - t) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return days < 30 ? `${days}d ago` : `${Math.round(days / 30)}mo ago`;
}

export function ScreensBar({
  active,
  onOpen,
  onClear,
  onSaved,
  currentFilters,
  currentCriteria,
  resolvedCount,
}: Props): React.ReactElement | null {
  const [screens, setScreens] = useState<SavedScreen[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [justSaved, setJustSaved] = useState(false);

  const refresh = useCallback(async () => {
    const res = await listScreens();
    // A screens list that fails is not worth an error banner over the grid —
    // the grid is the page. Signing out is the only realistic cause, and the
    // picker simply comes up empty.
    if (!isError(res)) setScreens(res.data);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Anything worth saving? Either a screen handed over from chat, or filters
  // set right here. "The whole universe" is not a screen.
  const canSave = !!active || !!currentFilters;

  const suggestedName = useCallback((): string => {
    const src = active?.criteria || currentCriteria || "";
    const head = (src.split("—")[0] ?? "").trim().replace(/[.,]$/, "");
    return head.length > 60 ? `${head.slice(0, 57)}…` : head || "My screen";
  }, [active, currentCriteria]);

  const beginSave = (): void => {
    setName(active?.name || suggestedName());
    setErr(null);
    setSaveOpen(true);
  };

  const commitSave = async (): Promise<void> => {
    const clean = name.trim();
    if (!clean) {
      setErr("Give the screen a name.");
      return;
    }
    setBusy(true);
    setErr(null);
    const res = active
      ? await saveScreen({
          name: clean,
          kind: "symbols",
          source: active.source,
          symbols: active.symbols,
          criteria: active.criteria,
          as_of: active.as_of,
        })
      : await saveScreen({
          name: clean,
          kind: "filters",
          source: "screener",
          filters: currentFilters ?? {},
          criteria: currentCriteria,
        });
    setBusy(false);
    if (isError(res)) {
      setErr(res.error.message);
      return;
    }
    setSaveOpen(false);
    setJustSaved(true);
    window.setTimeout(() => setJustSaved(false), 2200);
    onSaved(res.data);
    void refresh();
  };

  const remove = async (id: number): Promise<void> => {
    setScreens((prev) => prev.filter((s) => s.id !== id));   // optimistic
    const res = await deleteScreen(id);
    if (isError(res)) void refresh();                        // put it back
  };

  // cmdk owns the filtering — its matcher is better than a substring scan and
  // it keeps the keyboard behaviour (highlight follows the filtered list).
  const fromChat = screens.filter((s) => s.source === "chat");
  const fromScreener = screens.filter((s) => s.source !== "chat");

  const shortfall =
    active && resolvedCount != null && resolvedCount < active.symbols.length
      ? active.symbols.length - resolvedCount
      : 0;

  // One row = two lines. The old one was three, and the third ("saved list ·
  // screened … · 54m ago") repeated what the badge already says. Kind lives in
  // the badge, recency right-aligned, and the criteria gets the second line to
  // itself — so a row scans in one pass instead of three.
  const row = (s: SavedScreen): React.ReactElement => {
    const frozen = s.kind === "symbols";
    return (
      <CommandItem
        key={s.id}
        value={`${s.name} ${s.criteria ?? ""}`}
        onSelect={() => {
          onOpen(s);
          setPickerOpen(false);
        }}
        className="group gap-3 rounded-md px-2 py-2"
      >
        {frozen ? (
          <ListFilter
            className="h-4 w-4 shrink-0 text-muted-foreground"
            aria-hidden
          />
        ) : (
          <Radio className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        )}
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate text-sm font-medium">{s.name}</span>
            <Badge
              variant="muted"
              className="shrink-0 px-1.5 py-0 text-[10px] font-normal"
            >
              {frozen ? `${s.count} names` : "Live"}
            </Badge>
            <span className="ml-auto shrink-0 text-[11px] tabular-nums text-muted-foreground/60 group-aria-selected:text-muted-foreground">
              {relativeTime(s.updated_at)}
            </span>
          </span>
          <span className="truncate text-xs text-muted-foreground">
            {s.criteria || (frozen ? "A saved list of names" : "A saved query")}
          </span>
        </span>
        {/* Destructive action stays out of the way until the row is reached,
            by pointer or by keyboard — a delete sitting permanently beside
            every row is one mis-click from losing a screen. */}
        <button
          type="button"
          aria-label={`Delete ${s.name}`}
          onClick={(e) => {
            e.stopPropagation();
            void remove(s.id);
          }}
          className="shrink-0 rounded p-1 text-muted-foreground opacity-0 transition hover:bg-destructive/10 hover:text-destructive focus-visible:opacity-100 group-aria-selected:opacity-60 group-hover:opacity-100"
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden />
        </button>
      </CommandItem>
    );
  };

  return (
    <>
      {/* An active screen is a SCOPE, so it reads as a chip beside the other
          scoping controls — not as a panel. It was a full-width tinted slab
          holding one short line, which is mostly empty space and reads as a
          placeholder. The detail that does not fit (the conditions, and the
          warning that a saved list is not re-run) moves to the tooltip, but
          the as-of date stays ON the chip: it is the one fact that stops a
          three-week-old list being read as today's signal. */}
      {/* Same horizontal inset as every sibling section on this page
          (.screener-watchlist / .screener-toolbar both use `0 32px`). Without
          it this row sat on the page container's edge while the watchlist
          above and the filter pills below sat 32px in, so the chip hung off
          to the left of everything it belongs with. */}
      <div
        className="flex items-center gap-3"
        style={{ padding: "0 32px 12px", flexShrink: 0 }}
      >
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {active && (
            <TooltipProvider delayDuration={200}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="inline-flex max-w-full min-w-0 items-center gap-2 rounded-full border border-border bg-muted/50 py-1 pl-2.5 pr-1 text-sm">
                    <ListFilter
                      className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                      aria-hidden
                    />
                    <span className="truncate font-medium">
                      {active.name ?? "Screen from chat"}
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {active.matched} names
                      {active.as_of ? ` · ${active.as_of}` : ""}
                      {shortfall ? ` · ${shortfall} unpriced` : ""}
                    </span>
                    <button
                      type="button"
                      onClick={onClear}
                      aria-label="Clear this screen"
                      className="ml-0.5 shrink-0 rounded-full p-1 text-muted-foreground transition hover:bg-accent hover:text-foreground"
                    >
                      <X className="h-3 w-3" aria-hidden />
                    </button>
                  </span>
                </TooltipTrigger>
                <TooltipContent side="bottom" align="start" className="max-w-sm">
                  <p className="text-xs">{active.criteria}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    A saved list, priced today — opening it does not re-run the
                    screen.
                  </p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}
          {!active && currentCriteria && (
            <p className="truncate text-sm text-muted-foreground">
              {currentCriteria}
            </p>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => setPickerOpen(true)}
          >
            <FolderOpen className="h-4 w-4" aria-hidden />
            Open
          </Button>
          <Button size="sm" onClick={beginSave} disabled={!canSave}>
            {justSaved ? (
              <>
                <Check className="h-4 w-4" aria-hidden /> Saved
              </>
            ) : (
              <>
                <Bookmark className="h-4 w-4" aria-hidden />
                {active?.id ? "Update" : "Save screen"}
              </>
            )}
          </Button>
        </div>
      </div>

      {/* ── The picker ─────────────────────────────────────────────
          A command palette, not a list in a box: the search field IS the
          header (no title bar competing with it), rows are dense, and the
          footer states the keyboard contract — the convention every palette
          the user already uses follows. */}
      <Dialog open={pickerOpen} onOpenChange={setPickerOpen}>
        {/* `sm:max-w-xl` (not `max-w-xl`) because the shared DialogContent
            pins `sm:max-w-lg`, and a base utility loses to a responsive one.
            The close button is hidden rather than removed from dialog.tsx —
            it is shared, and in a palette an X floating over the search field
            is noise; Esc and click-outside already close this. */}
        <DialogContent className="gap-0 overflow-hidden p-0 shadow-2xl sm:max-w-xl [&>button[aria-label='Close']]:hidden">
          {/* Radix requires a title for the accessible name; the visible
              header is the search field, so this is for screen readers. */}
          <DialogTitle className="sr-only">Saved screens</DialogTitle>
          <DialogDescription className="sr-only">
            Open a screen kept from chat or built in the Screener.
          </DialogDescription>
          <Command className="bg-transparent">
            <CommandInput placeholder="Search screens…" />
            <CommandList className="max-h-[min(60vh,380px)] p-1.5">
              <CommandEmpty className="px-3 py-12 text-center">
                <p className="text-sm text-muted-foreground">
                  {screens.length
                    ? "No screen matches that."
                    : "No saved screens yet."}
                </p>
                {!screens.length && (
                  <p className="mx-auto mt-1.5 max-w-[34ch] text-xs leading-relaxed text-muted-foreground/70">
                    Run a screen in chat and press Open, or set filters here and
                    press Save screen.
                  </p>
                )}
              </CommandEmpty>
              {fromChat.length > 0 && (
                <CommandGroup heading="From chat">
                  {fromChat.map(row)}
                </CommandGroup>
              )}
              {fromScreener.length > 0 && (
                <CommandGroup heading="Built in Screener">
                  {fromScreener.map(row)}
                </CommandGroup>
              )}
            </CommandList>
          </Command>
          {screens.length > 0 && (
            <div className="flex items-center gap-4 border-t border-border bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground">
              <span className="flex items-center gap-1">
                <Kbd>↑</Kbd>
                <Kbd>↓</Kbd>
                navigate
              </span>
              <span className="flex items-center gap-1">
                <Kbd>↵</Kbd>
                open
              </span>
              <span className="ml-auto flex items-center gap-1">
                <Kbd>esc</Kbd>
                close
              </span>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* ── Name-and-save ──────────────────────────────────────────── */}
      <Dialog open={saveOpen} onOpenChange={setSaveOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="text-sm">
              {active?.id ? "Update screen" : "Save screen"}
            </DialogTitle>
            <DialogDescription className="text-xs">
              {active
                ? `${active.symbols.length} names, screened ${active.as_of || "recently"}. Saved as a list — opening it will not re-run the screen.`
                : "Saved as a live query — opening it re-runs these filters."}
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2">
            <Input
              value={name}
              autoFocus
              maxLength={120}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void commitSave();
              }}
              placeholder="Name this screen"
            />
            <p className="text-xs text-muted-foreground">
              {active?.criteria || currentCriteria}
            </p>
            {err && <p className="text-xs text-[var(--color-loss)]">{err}</p>}
          </div>
          <div className="flex justify-end gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setSaveOpen(false)}
            >
              Cancel
            </Button>
            <Button size="sm" onClick={() => void commitSave()} disabled={busy}>
              {busy ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <Check className="h-4 w-4" aria-hidden />
              )}
              {active?.id ? "Update" : "Save"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
