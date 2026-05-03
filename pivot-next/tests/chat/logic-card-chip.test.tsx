/**
 * Tests for LogicCardChip — the generic confirm card that renders
 * for the ~30 chat tools that emit a LogicCard (orders, GTT, SL,
 * OCO, dip-buy, basket, squareoff, SIP create, …).
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import {
  LogicCardChip,
  type LogicCard,
} from "@/components/chat/LogicCardChip";
import * as api from "@/lib/api";

const MARKET_BUY: LogicCard = {
  type: "market_order",
  action: "BUY",
  symbol: "RELIANCE",
  details: [
    { label: "Quantity", value: "10" },
    { label: "Order Type", value: "MARKET" },
    { label: "Product", value: "CNC" },
    { label: "Est. Value", value: "₹25,000" },
  ],
  explanation: "Buy 10 RELIANCE immediately at ~₹2,500.00.",
  disclaimer: "This is automation of your instructions, not financial advice.",
  requires_confirmation: true,
  register_payload: {
    symbol: "RELIANCE",
    exchange: "NSE",
    transaction_type: "BUY",
    order_type: "MARKET",
    quantity: 10,
    price: 2500,
    product: "CNC",
  },
};

const BASKET_CARD: LogicCard = {
  type: "basket_order",
  action: "BASKET",
  symbol: "INFY, TCS",
  details: [
    { label: "BUY INFY", value: "5 @ MARKET" },
    { label: "BUY TCS", value: "3 @ LIMIT" },
  ],
  explanation: "Basket: 2 orders execute simultaneously.",
  disclaimer: "This is automation of your instructions, not financial advice.",
  requires_confirmation: true,
  register_payload: {
    basket: true,
    legs: [
      {
        symbol: "INFY",
        exchange: "NSE",
        transaction_type: "BUY",
        order_type: "MARKET",
        quantity: 5,
        product: "CNC",
      },
      {
        symbol: "TCS",
        exchange: "NSE",
        transaction_type: "BUY",
        order_type: "LIMIT",
        quantity: 3,
        price: 4100,
        product: "CNC",
      },
    ],
  },
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("LogicCardChip", () => {
  it("renders header with type, action, and symbol", () => {
    render(<LogicCardChip card={MARKET_BUY} />);
    expect(screen.getByTestId("logic-card-chip")).toBeInTheDocument();
    expect(screen.getByText("RELIANCE")).toBeInTheDocument();
    expect(screen.getByText("BUY")).toBeInTheDocument();
    expect(screen.getByText(/market order/i)).toBeInTheDocument();
  });

  it("renders details list", () => {
    render(<LogicCardChip card={MARKET_BUY} />);
    expect(screen.getByTestId("logic-card-details")).toBeInTheDocument();
    expect(screen.getByText("Quantity")).toBeInTheDocument();
    expect(screen.getByText("10")).toBeInTheDocument();
    expect(screen.getByText("₹25,000")).toBeInTheDocument();
  });

  it("renders explanation and disclaimer", () => {
    render(<LogicCardChip card={MARKET_BUY} />);
    expect(
      screen.getByText(/Buy 10 RELIANCE immediately/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/automation of your instructions/i),
    ).toBeInTheDocument();
  });

  it("confirm button posts to /orders/register and shows confirmed state", async () => {
    const spy = vi
      .spyOn(api, "registerOrder")
      .mockResolvedValue({
        data: {
          id: 42,
          symbol: "RELIANCE",
          exchange: "NSE",
          transaction_type: "BUY",
          order_type: "MARKET",
          quantity: 10,
          price: 2500,
          trigger_price: null,
          status: "registered",
          placed_at: "3 May 2026, 02:55 IST",
        },
      });

    render(<LogicCardChip card={MARKET_BUY} />);
    fireEvent.click(screen.getByTestId("logic-card-confirm-btn"));

    await waitFor(() =>
      expect(screen.getByTestId("logic-card-confirmed")).toBeInTheDocument(),
    );
    expect(spy).toHaveBeenCalledWith(MARKET_BUY.register_payload);
    expect(screen.getByText(/Registered #42/i)).toBeInTheDocument();
  });

  it("basket confirm shows one row per leg in confirmed state", async () => {
    vi.spyOn(api, "registerOrder").mockResolvedValue({
      data: {
        registered: [
          {
            id: 1, symbol: "INFY", exchange: "NSE", transaction_type: "BUY",
            order_type: "MARKET", quantity: 5, price: null, trigger_price: null,
            status: "registered", placed_at: "now",
          },
          {
            id: 2, symbol: "TCS", exchange: "NSE", transaction_type: "BUY",
            order_type: "LIMIT", quantity: 3, price: 4100, trigger_price: null,
            status: "registered", placed_at: "now",
          },
        ],
        count: 2,
      },
    });

    render(<LogicCardChip card={BASKET_CARD} />);
    fireEvent.click(screen.getByTestId("logic-card-confirm-btn"));

    await waitFor(() =>
      expect(screen.getByTestId("logic-card-confirmed")).toBeInTheDocument(),
    );
    expect(screen.getByText(/Registered 2 legs/i)).toBeInTheDocument();
    expect(screen.getByText(/INFY BUY 5/i)).toBeInTheDocument();
    expect(screen.getByText(/TCS BUY 3/i)).toBeInTheDocument();
  });

  it("shows error message when register call fails", async () => {
    vi.spyOn(api, "registerOrder").mockResolvedValue({
      error: { code: "internal_error", message: "DB unavailable" },
    });

    render(<LogicCardChip card={MARKET_BUY} />);
    fireEvent.click(screen.getByTestId("logic-card-confirm-btn"));

    await waitFor(() =>
      expect(screen.getByTestId("logic-card-error")).toBeInTheDocument(),
    );
    expect(screen.getByText(/DB unavailable/i)).toBeInTheDocument();
  });

  it("disables confirm button when no register_payload is present", () => {
    const { register_payload, ...rest } = MARKET_BUY;
    void register_payload;
    render(<LogicCardChip card={rest as LogicCard} />);
    expect(screen.getByTestId("logic-card-confirm-btn")).toBeDisabled();
  });
});
