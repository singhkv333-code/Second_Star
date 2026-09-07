"use client";

import {
  Activity,
  BookOpen,
  CalendarClock,
  CircleDot,
  Clock,
  FileText,
  GitBranch,
  HelpCircle,
  LineChart,
  ListPlus,
  Newspaper,
  Package,
  Play,
  Send,
  ShieldAlert,
  ShoppingCart,
  SkipForward,
  Timer,
  TrendingUp,
  UserCheck,
  Wallet,
  Webhook,
  XOctagon,
  type LucideIcon,
} from "lucide-react";

/**
 * Map of catalog `icon` strings → lucide-react icons. The mock catalog
 * already references each entry; backend's real catalog must use the same
 * keys (validated as part of the contract). Unknown names fall back to
 * `HelpCircle` so a typo never breaks the panel.
 */
const ICON_MAP: Record<string, LucideIcon> = {
  activity: Activity,
  "book-open": BookOpen,
  "calendar-clock": CalendarClock,
  "circle-dot": CircleDot,
  clock: Clock,
  "file-text": FileText,
  "git-branch": GitBranch,
  "line-chart": LineChart,
  "list-plus": ListPlus,
  newspaper: Newspaper,
  package: Package,
  play: Play,
  send: Send,
  "shield-alert": ShieldAlert,
  "shopping-cart": ShoppingCart,
  "skip-forward": SkipForward,
  timer: Timer,
  "trending-up": TrendingUp,
  "user-check": UserCheck,
  wallet: Wallet,
  webhook: Webhook,
  "x-octagon": XOctagon,
};

export function StepIcon({
  name,
  className,
}: {
  name: string;
  className?: string;
}): React.ReactElement {
  const Icon = ICON_MAP[name] ?? HelpCircle;
  return <Icon className={className} aria-hidden="true" />;
}
