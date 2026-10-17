import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MacroEventStepRow } from "@/components/chat/steps/MacroEventStepRow";

describe("MacroEventStepRow", () => {
  it("renders an RBI rate-cut trigger with source + verify note", () => {
    render(
      <MacroEventStepRow
        step={{
          step_type: "trigger.scheduled_macro",
          config: { kind: "rbi_mpc", expected_outcome: "cut", min_confidence: 0.85 },
        }}
      />,
    );
    expect(screen.getByText(/RBI MPC/i)).toBeInTheDocument();
    expect(screen.getByText(/Fires on a rate CUT/i)).toBeInTheDocument();
    expect(screen.getByText(/RBI Press Releases/i)).toBeInTheDocument();
    expect(screen.getByText(/Confirms the actual outcome before firing/i)).toBeInTheDocument();
  });

  it("renders a CPI threshold trigger numerically", () => {
    render(
      <MacroEventStepRow
        step={{
          step_type: "trigger.scheduled_macro",
          config: {
            kind: "us_cpi",
            expected_outcome: "met",
            comparison: ">",
            threshold: 3,
          },
        }}
      />,
    );
    expect(screen.getByText(/US CPI/i)).toBeInTheDocument();
    expect(screen.getByText(/Fires when CPI is > 3%/i)).toBeInTheDocument();
  });

  it("mentions the prediction-market fallback when enabled", () => {
    render(
      <MacroEventStepRow
        step={{
          step_type: "trigger.scheduled_macro",
          config: {
            kind: "us_fomc",
            expected_outcome: "hold",
            allow_prediction_market_fallback: true,
          },
        }}
      />,
    );
    expect(screen.getByText(/prediction-market resolution/i)).toBeInTheDocument();
  });
});
