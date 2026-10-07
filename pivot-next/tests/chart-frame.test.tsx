import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChartFrame } from "@/components/chart/ChartFrame";

describe("ChartFrame company navigation", () => {
  it("relays a trusted company-page request from Charto to the shell", () => {
    const onOpenCompany = vi.fn();
    render(<ChartFrame onOpenCompany={onOpenCompany} />);

    const frame = screen.getByTitle("Chart") as HTMLIFrameElement;
    fireEvent(
      window,
      new MessageEvent("message", {
        data: { type: "charto:open-company", symbol: " hdfcbank " },
        origin: window.location.origin,
        source: frame.contentWindow,
      }),
    );

    expect(onOpenCompany).toHaveBeenCalledOnce();
    expect(onOpenCompany).toHaveBeenCalledWith("HDFCBANK");
  });

  it("ignores a company-page request from outside the chart frame", () => {
    const onOpenCompany = vi.fn();
    render(<ChartFrame onOpenCompany={onOpenCompany} />);

    fireEvent(
      window,
      new MessageEvent("message", {
        data: { type: "charto:open-company", symbol: "HDFCBANK" },
        origin: "https://example.com",
      }),
    );

    expect(onOpenCompany).not.toHaveBeenCalled();
  });
});
