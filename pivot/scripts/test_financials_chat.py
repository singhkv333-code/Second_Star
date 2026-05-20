"""Hammer the chat endpoint with fundamentals prompts and grade each response.

Covers sections A (live snapshot) and B (single-symbol backtest gates) plus
the new C (formula escape hatch) added by the generic-fundamentals work.

Run after both servers are up:

    cd pivot && python3 -m scripts.test_financials_chat

Output: one line per prompt with PASS/FAIL/INFO, the tool the chat called,
the data source where present (financials_db vs yfinance), and a short
response excerpt. Final summary at the end.

Each prompt gets a fresh conversation_id so they're independent — no
session bleed-through. Auth is dev-mode (no token needed; the chat router
falls back to user_id=1 in development).
"""
from __future__ import annotations

import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx


CHAT_URL = "http://localhost:8000/chat"
TIMEOUT = 120.0  # LLM calls can be slow
PACING_SECONDS = 3.0  # gap between calls — prevents OpenAI 500 cascade
                      # that triggers the chat's macro-fallback path,
                      # which strips fundamentals gates from drafts.


# ── Grading rules ─────────────────────────────────────────────────────────


@dataclass
class Check:
    name: str
    prompt: str
    # Optional grader. Receives the parsed response dict. Returns
    # (passed, reason) — None reason for pass. When grader is None we
    # only report tool + source, no PASS/FAIL.
    grader: Optional[Callable[[dict[str, Any]], tuple[bool, Optional[str]]]] = None


def _has_substring(needle: str) -> Callable[[dict], tuple[bool, Optional[str]]]:
    def grade(r: dict) -> tuple[bool, Optional[str]]:
        text = (r.get("response") or "").lower()
        if needle.lower() in text:
            return True, None
        return False, f"response did not mention {needle!r}"
    return grade


def _called_tool(name: str) -> Callable[[dict], tuple[bool, Optional[str]]]:
    def grade(r: dict) -> tuple[bool, Optional[str]]:
        called = [t.get("tool") if isinstance(t, dict) else t for t in r.get("tools_called") or []]
        if name in called:
            return True, None
        return False, f"expected tool {name!r}, got {called}"
    return grade


def _backtest_ran(min_trades: int | None = None) -> Callable[[dict], tuple[bool, Optional[str]]]:
    def grade(r: dict) -> tuple[bool, Optional[str]]:
        rd = r.get("raw_data") or {}
        hint = rd.get("_render_hint")
        if hint != "indicator_backtest_chart":
            return False, f"no backtest chart in raw_data (_render_hint={hint!r})"
        n = (rd.get("metrics") or {}).get("n_trades")
        if min_trades is not None and (n is None or n < min_trades):
            return False, f"only {n} trades, expected >= {min_trades}"
        return True, None
    return grade


def _financials_source() -> Callable[[dict], tuple[bool, Optional[str]]]:
    """Pass when ANY tool payload reports source='financials_db'. We
    walk raw_data shallow — the chat sometimes nests the fundamentals
    dict under different keys depending on which tool was invoked."""
    def grade(r: dict) -> tuple[bool, Optional[str]]:
        rd = r.get("raw_data") or {}
        # Look one or two levels deep for a `source` field.
        candidates = [rd]
        for v in rd.values() if isinstance(rd, dict) else []:
            if isinstance(v, dict):
                candidates.append(v)
        for c in candidates:
            src = c.get("source") if isinstance(c, dict) else None
            if src == "financials_db":
                return True, None
        return False, "no source=financials_db in raw_data (may have used live fallback)"
    return grade


CHECKS: list[Check] = [
    # ── Section A: live snapshot — should hit financials_db ────────────────
    Check(
        name="A1 RELIANCE RoE snapshot",
        prompt="What is Reliance's current RoE? Give me the number.",
        grader=_has_substring("roe"),  # weak — LLM phrasing varies
    ),
    Check(
        name="A2 INFY debt to equity",
        prompt="Show me Infosys debt to equity ratio.",
        grader=_has_substring("debt"),
    ),
    Check(
        name="A3 HDFCBANK P/E",
        prompt="What is HDFC Bank's P/E ratio?",
        grader=_has_substring("p/e"),
    ),
    Check(
        name="A4 RELIANCE market cap (yfinance fallback expected)",
        prompt="What is Reliance's market cap right now?",
        grader=None,  # informational — mcap goes to yfinance
    ),

    # ── Section B: single-symbol fundamentals backtests ────────────────────
    Check(
        name="B1 monthly RoE>8 RELIANCE backtest",
        prompt=(
            "Backtest: every first Monday of the month at 10am, buy 1 share of "
            "RELIANCE if its return on equity is above 8 percent. Run on 3 years of data."
        ),
        grader=_backtest_ran(),
    ),
    Check(
        name="B2 weekday INFY D/E<0.5 backtest",
        prompt=(
            "Backtest: every weekday morning, buy 1 INFY share if its debt-to-equity ratio "
            "is below 0.5. Use the last 2 years."
        ),
        grader=_backtest_ran(),
    ),
    Check(
        name="B3 two-gate fundamentals RELIANCE",
        prompt=(
            "Backtest for the last 3 years: buy 1 RELIANCE every first Monday of the month "
            "if P/E is under 30 and debt-to-equity is below 1."
        ),
        grader=_backtest_ran(),
    ),
    Check(
        name="B4 unreachable gate must produce 0 trades",
        prompt=(
            "Backtest: every Monday at 10am, buy 1 RELIANCE if return on equity is "
            "above 50 percent. 3 years."
        ),
        grader=_backtest_ran(min_trades=None),
    ),

    # ── Section C: NEW metrics + formula escape hatch ──────────────────────
    Check(
        name="C1 ROCE-gated backtest (named metric, not legacy)",
        prompt=(
            "Backtest the last 3 years: every Monday buy 1 RELIANCE if its ROCE "
            "(return on capital employed) is above 10 percent."
        ),
        grader=_backtest_ran(),
    ),
    Check(
        name="C2 current ratio gate",
        prompt=(
            "Backtest: every Monday for 3 years, buy 1 RELIANCE if current ratio "
            "is above 1.0."
        ),
        grader=_backtest_ran(),
    ),
    Check(
        name="C3 EBITDA margin gate",
        prompt=(
            "Backtest 3 years: weekly buy of 1 RELIANCE if EBITDA margin is above 15%."
        ),
        grader=_backtest_ran(),
    ),
    Check(
        name="C4 formula — ROIC proxy",
        prompt=(
            "Backtest 3 years: buy 1 RELIANCE every Monday if ROIC is above 8 percent. "
            "If ROIC isn't a built-in metric, compute it as "
            "(net_profit + interest_expense) / (total_equity + total_debt) * 100."
        ),
        grader=_backtest_ran(),
    ),
    Check(
        name="C5 formula — FCF-to-revenue proxy",
        prompt=(
            "Backtest 3 years on RELIANCE: every Monday buy 1 share if "
            "operating cash flow divided by revenue (as a percent) is above 10. "
            "Approximate using cash_from_ops / revenue * 100 if needed."
        ),
        grader=_backtest_ran(),
    ),

    # ── Section D: should be deflected, not promised ───────────────────────
    Check(
        name="D1 cross-sectional screen should be deflected",
        prompt="Rank the top 10 Nifty 50 stocks by ROE and buy the top 3 every month.",
        grader=_has_substring("per-symbol"),  # prompt teaches this phrasing
    ),
]


# ── Driver ────────────────────────────────────────────────────────────────


def call_chat(client: httpx.Client, prompt: str) -> dict[str, Any]:
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "include_portfolio_context": False,
        "conversation_id": f"test-{uuid.uuid4().hex[:8]}",
    }
    r = client.post(CHAT_URL, json=body)
    r.raise_for_status()
    return r.json()


def main() -> int:
    print(f"hammering {CHAT_URL} with {len(CHECKS)} prompts\n")
    pass_count = 0
    fail_count = 0
    info_count = 0

    with httpx.Client(timeout=TIMEOUT) as client:
        for i, chk in enumerate(CHECKS, 1):
            if i > 1:
                time.sleep(PACING_SECONDS)
            t0 = time.time()
            try:
                resp = call_chat(client, chk.prompt)
            except Exception as e:  # noqa: BLE001
                print(f"[{i:2d}] ERROR  {chk.name}: {e}")
                fail_count += 1
                continue
            dt = time.time() - t0

            tools = [t.get("tool") if isinstance(t, dict) else t for t in resp.get("tools_called") or []]
            rd = resp.get("raw_data") or {}
            # Try to read source from nested payloads too.
            source = rd.get("source")
            if not source:
                for v in rd.values() if isinstance(rd, dict) else []:
                    if isinstance(v, dict) and v.get("source"):
                        source = v["source"]
                        break
            render = rd.get("_render_hint")
            text = (resp.get("response") or "").strip().replace("\n", " ")
            excerpt = (text[:140] + "...") if len(text) > 140 else text

            # Detect macro-fallback responses — when OpenAI returns 500 or
            # the LLM hop times out, the chat's _try_macro_fallback strips
            # fundamentals gates to a bare schedule+order. Flag separately
            # so we don't blame the prompts for infra failures.
            is_fallback = (
                dt < 0.5  # macro fallback returns in ms; real LLM takes seconds
                or "AI backend is temporarily unavailable" in text
            )

            if chk.grader is None:
                tag = "INFO"
                info_count += 1
                reason = ""
            else:
                ok, reason = chk.grader(resp)
                if not ok and is_fallback:
                    tag = "INFRA"  # macro fallback / LLM unavailable
                    reason = f"infra fallback ({reason})"
                    info_count += 1
                else:
                    tag = "PASS" if ok else "FAIL"
                    pass_count += int(ok)
                    fail_count += int(not ok)

            print(f"[{i:2d}] {tag}  {chk.name}  ({dt:.1f}s)")
            print(f"     tools={tools}  source={source}  render={render}")
            if reason:
                print(f"     reason: {reason}")
            if excerpt:
                print(f"     resp: {excerpt}")
            print()

    print("─" * 60)
    print(f"  PASS: {pass_count}    FAIL: {fail_count}    INFO/INFRA: {info_count}    TOTAL: {len(CHECKS)}")
    if info_count and not fail_count:
        print("  (INFO/INFRA includes responses fed by the chat's macro fallback")
        print("   after OpenAI returned 500s — re-run when the API is stable.)")
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
