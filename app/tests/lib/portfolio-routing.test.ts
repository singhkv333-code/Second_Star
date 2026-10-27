import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  getPaperSummary, getPaperHoldings, getPaperOpenOrders, getPaperFills,
  getPaperNavCurve, getPortfolioPerformance, cancelOrder, setAuthTokenProvider, setAccountMode,
} from "@/lib/api";
import { getPortfolioPerformance as getChartPerformance } from "@/lib/portfolioApi";
import { getMarketExplore } from "@/lib/markets";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_PIVOT_API_BASE", "C:/Program Files/Git/pv/api");
  setAuthTokenProvider(() => "test-session");
  fetchMock.mockReset().mockImplementation(async () => new Response("{}", { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  setAuthTokenProvider(() => null);
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it("reads the user's paper book and performance on the dataserver origin", async () => {
  await getPaperSummary();
  await getPaperHoldings();
  await getPaperOpenOrders();
  await getPaperFills();
  await getPaperNavCurve();
  await getPortfolioPerformance("1M");
  await getChartPerformance("5Y");
  await cancelOrder("order-1");
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    "/paper/summary", "/paper/holdings", "/paper/orders",
    "/paper/fills?limit=50&offset=0", "/paper/nav",
    "/api/portfolio/performance?period=1M", "/api/portfolio/performance?period=5Y",
    "/paper/orders/order-1/cancel",
  ]);
  for (const [, options] of fetchMock.mock.calls) {
    expect(options.headers.Authorization).toBe("Bearer test-session");
  }
});

it("fetches Markets through the API proxy when Git Bash has converted the base", async () => {
  await getMarketExplore();
  expect(fetchMock).toHaveBeenCalledWith("/pv/api/markets/explore", expect.anything());
});

it("initializes paper mode by reading the dataserver's fixed mode", async () => {
  await setAccountMode("paper");
  expect(fetchMock).toHaveBeenCalledWith("/paper/account/mode", expect.objectContaining({ method: "GET" }));
});
