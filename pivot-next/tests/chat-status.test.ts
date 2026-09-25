import { describe, expect, it } from "vitest";
import { statusPhrase, type StatusTool } from "@/lib/chatStatus";

const run = (name: string, hint?: string): StatusTool => ({ name, hint, ok: undefined });
const done = (name: string): StatusTool => ({ name, ok: true });

describe("chat loader phrase", () => {
  it("only says 'thinking' before any tool runs, never a scripted tool name", () => {
    expect(statusPhrase([], false, 1000)).toBe("Thinking");
    expect(statusPhrase([], false, 6000)).toBe("Thinking it through");
    expect(statusPhrase([], false, 20000)).toBe("Still working on it");
  });

  it("names the subject the backend sent, and merges same-kind parallel calls", () => {
    expect(statusPhrase([run("get_index_level", "NIFTY 50")], false, 0)).toBe("Checking NIFTY 50");
    expect(
      statusPhrase([run("get_index_level", "NIFTY 50"), run("get_index_level", "SENSEX")], false, 0),
    ).toBe("Checking NIFTY 50 and SENSEX");
    expect(
      statusPhrase(["A", "B", "C", "D"].map((s) => run("get_market_data", s)), false, 0),
    ).toBe("Pulling prices for A, B and 2 more");
  });

  it("falls back to a subject-free phrase rather than guessing one", () => {
    expect(statusPhrase([run("get_index_level")], false, 0)).toBe("Checking the indices");
    expect(statusPhrase([run("some_new_tool")], false, 0)).toBe("Working on it");
  });

  it("speaks for the newest running call", () => {
    const tools = [run("get_index_level", "NIFTY 50"), run("get_top_movers")];
    expect(statusPhrase(tools, false, 0)).toBe("Scanning the day's movers");
  });

  it("moves to reading, then writing", () => {
    expect(statusPhrase([done("get_top_movers")], false, 0)).toBe("Reading the results");
    expect(statusPhrase([done("get_top_movers")], true, 0)).toBe("Writing the answer");
  });
});
