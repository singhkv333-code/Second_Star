/**
 * CompanyLogo — renders the logo <img> when a URL is present, and a
 * sector-hued first-letter monogram otherwise (or on image load error).
 */
import { describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CompanyLogo } from "@/components/CompanyLogo";

const LOGO = "https://img.logo.dev/reliance.com?token=pk_test&size=128&format=png";

describe("CompanyLogo", () => {
  it("renders an <img> with the logo URL when provided", () => {
    render(
      <CompanyLogo logoUrl={LOGO} name="Reliance Industries" symbol="RELIANCE" hue="#2D6" />,
    );
    const img = screen.getByAltText("Reliance Industries logo") as HTMLImageElement;
    expect(img.tagName).toBe("IMG");
    expect(img.getAttribute("src")).toBe(LOGO);
  });

  it("renders a first-letter monogram when no logo URL is given", () => {
    render(
      <CompanyLogo logoUrl={null} name="Tata Consultancy" symbol="TCS" hue="#2D6" />,
    );
    expect(screen.queryByAltText("Tata Consultancy logo")).toBeNull();
    expect(screen.getByText("T")).toBeInTheDocument();
  });

  it("falls back to the monogram when the image fails to load", () => {
    render(
      <CompanyLogo logoUrl={LOGO} name="Infosys" symbol="INFY" hue="#2D6" />,
    );
    const img = screen.getByAltText("Infosys logo");
    fireEvent.error(img); // simulate a broken logo
    expect(screen.queryByAltText("Infosys logo")).toBeNull();
    expect(screen.getByText("I")).toBeInTheDocument();
  });

  it("draws a SharePerks tile edge to edge, but pads a wordmark on a plate", () => {
    const tile = "https://company-logo.shareperks.in/logo/INE002A01018/icon.svg";
    render(<CompanyLogo logoUrl={tile} name="Reliance Industries" symbol="RELIANCE" />);
    const t = screen.getByAltText("Reliance Industries logo") as HTMLImageElement;
    expect(t.style.padding).toBe("");
    expect(t.style.background).toBe("");
    render(<CompanyLogo logoUrl={LOGO} name="Infosys" symbol="INFY" />);
    expect((screen.getByAltText("Infosys logo") as HTMLImageElement).style.padding).not.toBe("");
  });
});
