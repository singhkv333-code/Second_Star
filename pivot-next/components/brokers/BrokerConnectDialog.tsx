"use client";

/**
 * BrokerConnectDialog — the connect surface (BrokerGrid) in a dialog.
 *
 * Shared by the two entry points that replaced the old Brokers tab: the topbar
 * pill (desktop) and the account-menu "Brokers" row (mobile). Extracted so the
 * exact same grid + per-broker forms open from either, with no duplication.
 */

import { BrokerGrid } from "@/components/brokers/BrokerGrid";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function BrokerConnectDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}): React.ReactElement {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="sm:max-w-[680px] sm:max-h-[calc(100dvh_-_2rem)] sm:overflow-y-auto"
        data-testid="broker-grid-dialog"
      >
        <DialogHeader>
          <DialogTitle>Brokers</DialogTitle>
          <DialogDescription>
            Connect a broker to pull live holdings and arm automations.
          </DialogDescription>
        </DialogHeader>
        <BrokerGrid />
      </DialogContent>
    </Dialog>
  );
}
