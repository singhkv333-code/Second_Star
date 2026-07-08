/**
 * CompanyAutosuggest — the browse surface where you see companies WITHOUT
 * clicking in. Proves each result row renders the company logo <img> (not
 * just a monogram), and that the logo.dev attribution link is present.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CompanyAutosuggest } from "@/components/CompanyAutosuggest";
import * as api from "@/lib/api";

describe("CompanyAutosuggest logos", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("renders the company logo image in each result row", async () => {
    vi.spyOn(api, "searchCompanies").mockResolvedValue({
      data: {
        results: [
          {
            symbol: "TCS",
            name: "Tata Consultancy Services",
            sector: "IT",
            has_fundamentals: true,
            logo_url: "https://img.logo.dev/tcs.com?token=pk_test",
          },
        ],
      },
    } as Awaited<ReturnType<typeof api.searchCompanies>>);

    render(<CompanyAutosuggest placeholder="Search companies" onSelect={() => {}} />);
    fireEvent.change(screen.getByLabelText("Search companies"), {
      target: { value: "TCS" },
    });

    const img = (await screen.findByAltText(
      "Tata Consultancy Services logo",
    )) as HTMLImageElement;
    expect(img.tagName).toBe("IMG");
    expect(img.getAttribute("src")).toContain("img.logo.dev/tcs.com");

    // logo.dev attribution link is shown wherever logos render.
    const attrib = screen.getByRole("link", { name: "Logo.dev" });
    expect(attrib).toHaveAttribute("href", "https://logo.dev");
  });

  it("falls back to a monogram when a result has no logo_url", async () => {
    vi.spyOn(api, "searchCompanies").mockResolvedValue({
      data: {
        results: [
          {
            symbol: "IRS02",
            name: "Indemnity Shell",
            sector: null,
            has_fundamentals: false,
            logo_url: null,
          },
        ],
      },
    } as Awaited<ReturnType<typeof api.searchCompanies>>);

    render(<CompanyAutosuggest placeholder="Search companies" onSelect={() => {}} />);
    fireEvent.change(screen.getByLabelText("Search companies"), {
      target: { value: "IRS" },
    });

    await waitFor(() =>
      expect(screen.queryByText("Indemnity Shell")).toBeInTheDocument(),
    );
    expect(screen.queryByAltText("Indemnity Shell logo")).toBeNull(); // monogram, no <img>
    expect(screen.getByText("I")).toBeInTheDocument(); // first-letter monogram
  });
});
