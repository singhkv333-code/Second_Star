"use client";

/**
 * The modal shell every billing panel shares. A panel renders the same
 * inside this dialog or inline (the design gallery), so its title and
 * description fall back to plain elements when there is no Dialog around
 * them — Radix requires its own Title/Description inside Dialog.Content.
 */

import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { cn } from "@/lib/utils";

const InDialog = React.createContext(false);

export function PanelTitle({ children }: { children: React.ReactNode }): React.ReactElement {
  return React.useContext(InDialog) ? (
    <DialogPrimitive.Title className="bl-panel-title">{children}</DialogPrimitive.Title>
  ) : (
    <h2 className="bl-panel-title">{children}</h2>
  );
}

export function PanelDesc({ children }: { children: React.ReactNode }): React.ReactElement {
  return React.useContext(InDialog) ? (
    <DialogPrimitive.Description className="bl-p">{children}</DialogPrimitive.Description>
  ) : (
    <p className="bl-p">{children}</p>
  );
}

export function BillingModal({
  open,
  onOpenChange,
  wide,
  label,
  children,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  wide?: boolean;
  label?: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="bl-overlay" />
        <DialogPrimitive.Content className={cn("bl-modal", wide && "bl-modal--wide")} aria-label={label}>
          <InDialog.Provider value={true}>{children}</InDialog.Provider>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
