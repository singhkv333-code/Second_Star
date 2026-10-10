"use client";

import { AffiliatePage } from "@/components/affiliate/AffiliatePage";

/**
 * /affiliate — the Referral and Ambassador programs. A page of its own,
 * opened from the account menu; NOT mounted inside AppShell, and ungated
 * (see UNGATED_PATHS in components/AppBootstrap.tsx) so a link to it can be
 * shared with someone who has no account yet.
 */
export default function Page(): React.ReactElement {
  return <AffiliatePage />;
}
