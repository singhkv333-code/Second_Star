/**
 * OptionStrategyCard — inline lot editability.
 *
 * Covers: the inline lots stepper recomputes the strategy via
 * computeOptionStrategy and the displayed decision stats reflect the
 * recomputed payload (max profit / max loss scale with lots).
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { OptionStrategyCard } from "@/components/chat/OptionStrategyCard";
import * as api from "@/lib/api";
import type { OptionStrategyPayload } from "@/lib/types";

function makePayload(qtyLots: number, maxProfit: number, maxLoss: number): OptionStrategyPayload {
  return {
    _render_hint: "option_strategy_card",
    locked: {
      underlying: "NIFTY",
      segment: "NFO",
      exchange: "NSE",
      spot: 24100,
      forward: 24100,
      expiry: "2026-06-23",
      expiry_kind: "weekly",
      lot_size: 75,
      research_only: false,
      disclosure: "Options carry risk.",
    },
    editable: {
      template: "bull_call_spread",
      book: "paper",
      qty_lots: qtyLots,
      legs: [
        { option_type: "CE", side: "BUY", strike: 24000, mid: 200, iv: 0.12, delta: 0.6 },
        { option_type: "CE", side: "SELL", strike: 24200, mid: 90, iv: 0.11, delta: 0.4 },
      ],
    },
    computed: {
      net_premium: -110 * qtyLots * 75,
      payoff: [
        { s: 23000, pnl: -110 * qtyLots * 75 },
        { s: 25000, pnl: maxProfit },
      ],
      breakevens: [24110],
      max_loss: maxLoss,
      max_profit: maxProfit,
      pop: 0.45,
      net_greeks: { delta: 0.2, gamma: 0.01, theta: -5, vega: 3 },
      capital_required: 8250 * qtyLots,
      margin_estimate: 8250 * qtyLots,
      margin_note: "",
    },
    validation: {
      lot_multiple_ok: true,
      min_lots: 1,
      max_lots: 50,
      liquidity_ok: true,
      liquidity_flags: [],
      expiry_gamma_warn: false,
      mcx_execution_blocked: false,
      requires_disclosure: false,
    },
    critique: { verdict: "ok", flags: [], summary: "Balanced spread." },
    candidates: [],
    conversation_id: "c1",
  };
}

describe("OptionStrategyCard inline lot editability", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it("recomputes via computeOptionStrategy and reflects the new max profit when lots increase", async () => {
    // 1 lot → max profit ₹6,750 (shown as ₹6.8K). Recompute at 2 lots → ₹13,500 (₹13.5K).
    const onePayload = makePayload(1, 6750, -8250);
    const twoPayload = makePayload(2, 13500, -16500);

    const computeSpy = vi
      .spyOn(api, "computeOptionStrategy")
      .mockResolvedValue({ data: { success: true, payload: twoPayload } });

    render(<OptionStrategyCard payload={onePayload} />);

    // Baseline reflects 1-lot max profit.
    expect(screen.getByText("₹6.8K")).toBeInTheDocument();

    // Bump lots via the stepper "+".
    fireEvent.click(screen.getByLabelText("Increase lots"));

    // The input reflects the new lot count immediately.
    const input = screen.getByTestId("option-strategy-lots-input") as HTMLInputElement;
    await waitFor(() => expect(input.value).toBe("2"));

    // Recompute fires with the new qty_lots and the unchanged legs.
    await waitFor(() => expect(computeSpy).toHaveBeenCalledTimes(1));
    expect(computeSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        underlying: "NIFTY",
        expiry: "2026-06-23",
        qty_lots: 2,
        legs: expect.arrayContaining([
          expect.objectContaining({ option_type: "CE", side: "BUY", strike: 24000 }),
        ]),
      }),
    );

    // Displayed max profit reflects the recomputed (2-lot) payload.
    await waitFor(() => expect(screen.getByText("₹13.5K")).toBeInTheDocument());
  });

  it("surfaces an honest error and does not fabricate when recompute fails", async () => {
    vi.spyOn(api, "computeOptionStrategy").mockResolvedValue({
      data: { success: false, payload: null, error: "Strike isn't quotable." },
    });

    render(<OptionStrategyCard payload={makePayload(1, 6750, -8250)} />);
    fireEvent.click(screen.getByLabelText("Increase lots"));

    await waitFor(() => expect(screen.getByText("Strike isn't quotable.")).toBeInTheDocument());
    // The original 1-lot figure stays (no silent fabricated value).
    expect(screen.getByText("₹6.8K")).toBeInTheDocument();
  });
});
