import type { Workflow } from "@/lib/types";

/**
 * The exact 5-step demo workflow from docs/ARCHITECTURE.md §14:
 *   schedule → fetch portfolio → numeric condition → place order (with approval) → notification
 *
 * Used as the seed data for the Day 1 hardcoded panel render. Shape matches
 * `Workflow` from `lib/types.ts` so swapping to a real `getWorkflow(id)`
 * call later is one line.
 */
export const DEMO_WORKFLOW: Workflow = {
  id: "00000000-0000-4000-8000-000000000001",
  name: "RELIANCE 3:55 PM buy",
  description:
    "Every weekday at 3:55 PM IST, if buying power is over ₹50,000, buy 10 shares of RELIANCE and notify by email.",
  status: "draft",
  version: 1,
  single_instance: true,
  created_at: "2026-05-02T00:00:00Z",
  updated_at: "2026-05-02T00:00:00Z",
  activated_at: null,
  last_run_at: null,
  next_run_at: null,
  steps: [
    {
      id: "00000000-0000-4000-8000-000000000010",
      step_index: 0,
      step_type: "trigger.schedule",
      label: "Every weekday at 3:55 PM IST",
      config: { cron: "55 15 * * 1-5", timezone: "Asia/Kolkata" },
    },
    {
      id: "00000000-0000-4000-8000-000000000011",
      step_index: 1,
      step_type: "fetch.portfolio",
      label: "Get my portfolio",
      config: {},
    },
    {
      id: "00000000-0000-4000-8000-000000000012",
      step_index: 2,
      step_type: "condition.numeric",
      label: "Buying power above ₹50,000",
      config: {
        left: "{{ context.1.buying_power }}",
        operator: ">",
        right: 50000,
      },
    },
    {
      id: "00000000-0000-4000-8000-000000000013",
      step_index: 3,
      step_type: "action.place_order",
      label: "Buy 10 RELIANCE",
      config: {
        symbol: "RELIANCE",
        side: "buy",
        quantity: 10,
        order_type: "market",
        requires_approval: true,
      },
    },
    {
      id: "00000000-0000-4000-8000-000000000014",
      step_index: 4,
      step_type: "notify.message",
      label: "Email me a confirmation",
      config: {
        channel: "email",
        template: "Bought {{ context.3.broker_order_id }}: 10 RELIANCE",
        vars: {},
      },
    },
  ],
};
