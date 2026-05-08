"use client";

/**
 * CommandPalette — Cmd+K global palette.
 *
 * Groups: Navigation (all tabs), Recent Conversations.
 * Selecting a nav item calls onNavigate(tabKey).
 * Selecting a conversation calls onOpenConversation(id).
 * Esc and click-outside close.
 */

import { useEffect, useState } from "react";
import {
  BarChart2,
  CalendarDays,
  MessageSquare,
  Newspaper,
  PieChart,
  Settings,
  MessageCircle,
} from "lucide-react";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";
import { Dialog } from "@/components/ui/dialog";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { cn } from "@/lib/utils";

type TabKey =
  | "chat"
  | "portfolio"
  | "news"
  | "agents"
  | "calendar"
  | "screener";

const NAV_ITEMS: {
  key: TabKey;
  label: string;
  Icon: React.ComponentType<{ className?: string }>;
}[] = [
  { key: "chat", label: "Chat", Icon: MessageSquare },
  { key: "portfolio", label: "Portfolio", Icon: PieChart },
  { key: "news", label: "News", Icon: Newspaper },
  { key: "agents", label: "Agents", Icon: Settings },
  { key: "calendar", label: "Calendar", Icon: CalendarDays },
  { key: "screener", label: "Screener", Icon: BarChart2 },
];

export type CommandPaletteProps = {
  conversations: { id: string; preview: string }[];
  onNavigate: (tab: TabKey) => void;
  onOpenConversation: (id: string) => void;
};

export function CommandPalette({
  conversations,
  onNavigate,
  onOpenConversation,
}: CommandPaletteProps): React.ReactElement {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const handler = (e: KeyboardEvent): void => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setOpen((prev) => !prev);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const runAndClose = (fn: () => void): void => {
    fn();
    setOpen(false);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          className={cn(
            "fixed inset-0 z-50 bg-black/40 backdrop-blur-sm",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
            "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
          )}
        />
        <DialogPrimitive.Content
          className={cn(
            "fixed left-1/2 top-[20%] z-50 w-full max-w-lg -translate-x-1/2",
            "rounded-xl border bg-popover shadow-2xl",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
            "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
            "data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95",
            "data-[state=closed]:slide-out-to-left-1/2 data-[state=open]:slide-in-from-left-1/2",
          )}
          aria-describedby={undefined}
        >
          <DialogPrimitive.Title className="sr-only">
            Command palette
          </DialogPrimitive.Title>
          <Command className="rounded-xl">
            <CommandInput
              placeholder="Search pages, conversations…"
              data-testid="command-palette-input"
            />
            <CommandList className="max-h-[320px]">
              <CommandEmpty>No results found.</CommandEmpty>

              <CommandGroup heading="Navigation">
                {NAV_ITEMS.map(({ key, label, Icon }) => (
                  <CommandItem
                    key={key}
                    value={label}
                    onSelect={() => runAndClose(() => onNavigate(key))}
                    data-testid={`cmd-nav-${key}`}
                  >
                    <Icon className="mr-2 h-4 w-4 text-muted-foreground" />
                    {label}
                  </CommandItem>
                ))}
              </CommandGroup>

              {conversations.length > 0 && (
                <>
                  <CommandSeparator />
                  <CommandGroup heading="Recent conversations">
                    {conversations.map((c) => (
                      <CommandItem
                        key={c.id}
                        value={c.preview}
                        onSelect={() =>
                          runAndClose(() => onOpenConversation(c.id))
                        }
                        data-testid={`cmd-conv-${c.id}`}
                      >
                        <MessageCircle className="mr-2 h-4 w-4 text-muted-foreground" />
                        <span className="truncate">{c.preview}</span>
                      </CommandItem>
                    ))}
                  </CommandGroup>
                </>
              )}
            </CommandList>
          </Command>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </Dialog>
  );
}
