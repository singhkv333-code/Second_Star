"""Live calibration runner for the Polymarket LLM contract matcher.

Hits real Gamma + the real configured LLM (whatever LLM_PROVIDER is
set to — openai / sarvam / azure). Runs a curated prompt set covering:

  - common political asks ("Trump 2028", "BJP 2029")
  - economic / rate ("Fed cuts rates in June")
  - crypto thresholds ("Bitcoin > $150k by year-end")
  - sports (IPL, T20 World Cup)
  - negation ("Modi WON'T be PM by 2029")
  - vague / non-existent (junk inputs that should NOT match)
  - direct phrasing ("alert me if Modi-wins YES > 70%")

Output:
  - stderr: per-prompt log
  - stdout: markdown table summarizing each prompt's match result
  - --json out.json: machine-readable dump

The goal is calibration, not pass/fail. Read the table to decide
whether the 0.70 auto-pick cutoff is right and whether the LLM
correctly picks the NO side for negation asks.

Usage:
    LLM_PROVIDER=openai OPENAI_API_KEY=... \\
        python pivot/scripts/polymarket_match_eval.py
    python pivot/scripts/polymarket_match_eval.py --prompts custom.json
    python pivot/scripts/polymarket_match_eval.py --json out.json
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

# Add the project root (pivot/) to sys.path so `backend.*` resolves
# when this script is run directly, regardless of cwd.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# Default curated set. Each entry has a description + an optional
# `expect` note for the human reviewer ("should auto-pick YES",
# "should pick NO side", "should NOT match anything") — used only
# for the readable output, NOT enforced by the script.
PROMPTS: list[dict] = [
    # ── Direct, common ─────────────────────────────────────────────
    {"id": "trump_2028_yes",
     "prompt": "alert me if Trump wins the 2028 US presidential election",
     "expect": "should auto-pick YES on a Trump 2028 market if one is open"},
    {"id": "btc_150k_threshold",
     "prompt": "tell me when Bitcoin hits $150k probability above 30%",
     "expect": "should auto-pick YES on the BTC $150k market"},
    {"id": "fed_june_cut",
     "prompt": "Fed cuts rates at the June 2026 meeting",
     "expect": "should match a Fed-cuts-June market with reasonable confidence"},

    # ── India-flavored ─────────────────────────────────────────────
    {"id": "modi_pm_2029",
     "prompt": "Modi remains PM after the 2029 Indian general election",
     "expect": "may or may not find a market — depends on Polymarket coverage"},
    {"id": "india_t20_wc",
     "prompt": "India wins the next T20 World Cup",
     "expect": "should match a cricket WC market if open"},

    # ── Negation (side = NO) ───────────────────────────────────────
    {"id": "trump_2028_no_negation",
     "prompt": "alert me if Trump does NOT win the 2028 US election",
     "expect": "should pick NO side on the same Trump market"},
    {"id": "fed_no_cut",
     "prompt": "Fed does NOT cut rates in June 2026",
     "expect": "should pick NO side on the Fed market"},

    # ── Vague / generic (should refuse or low-confidence) ──────────
    {"id": "vague_crypto",
     "prompt": "something good happens in crypto soon",
     "expect": "should refuse or low-confidence — too vague"},
    {"id": "vague_election",
     "prompt": "the election goes well",
     "expect": "should refuse — too vague, no specific event"},

    # ── Non-existent / sci-fi (should refuse) ─────────────────────
    {"id": "aliens_2027",
     "prompt": "aliens land on Earth before 2027",
     "expect": "should refuse — no such market on Polymarket"},

    # ── Threshold-phrased asks ─────────────────────────────────────
    {"id": "btc_150k_explicit_threshold",
     "prompt": "alert me if YES probability of Bitcoin hitting $150k goes above 25%",
     "expect": "should auto-pick YES on the BTC $150k market"},
]


def _load_prompts(path: Optional[str]) -> list[dict]:
    if not path:
        return PROMPTS
    return json.loads(Path(path).read_text())


async def _run_one(prompt: dict) -> dict:
    """Run one prompt; return a dict ready for the JSON dump + the
    markdown row formatter. Imports are inside so a missing LLM
    config doesn't crash before we've parsed argv."""
    from backend.news_events.parsing.polymarket_match import (
        match_event_to_polymarket_contract,
    )

    t0 = time.monotonic()
    try:
        result = await match_event_to_polymarket_contract(prompt["prompt"])
        err: Optional[str] = None
    except Exception as exc:  # noqa: BLE001
        result = None
        err = f"{type(exc).__name__}: {exc}"
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    candidates_summary = []
    if result is not None:
        for i, c in enumerate(result.candidates):
            candidates_summary.append({
                "i": i,
                "question": c.question,
                "yes_price": c.yes_price,
                "market_id": c.market_id,
            })

    return {
        "id": prompt["id"],
        "prompt": prompt["prompt"],
        "expect": prompt.get("expect"),
        "elapsed_ms": elapsed_ms,
        "error": err,
        "matched": (result.matched if result else None),
        "side": (result.side if result else None),
        "confidence": (round(result.confidence, 3) if result else None),
        "reason": (result.reason if result else None),
        "chosen_question": (result.question if result else None),
        "market_id": (result.market_id if result else None),
        "token_id": (result.token_id if result else None),
        "candidate_count": (len(result.candidates) if result else 0),
        "candidates": candidates_summary,
    }


def _md_table(rows: list[dict]) -> str:
    """Compact, human-readable markdown table."""
    header = (
        "| id | prompt | matched | side | conf | chosen | candidates | error |\n"
        "|---|---|---|---|---|---|---|---|\n"
    )
    body = []
    for r in rows:
        prompt = (r["prompt"][:60] + "…") if len(r["prompt"]) > 60 else r["prompt"]
        chosen = ""
        if r["chosen_question"]:
            cq = r["chosen_question"]
            chosen = (cq[:55] + "…") if len(cq) > 55 else cq
        err = r["error"] or ""
        body.append(
            f"| {r['id']} | {prompt} | "
            f"{r['matched']} | {r['side'] or ''} | "
            f"{r['confidence']!r} | {chosen} | "
            f"{r['candidate_count']} | {err[:60]} |"
        )
    return header + "\n".join(body) + "\n"


async def _main_async(prompts: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for idx, p in enumerate(prompts, 1):
        print(f"[matcheval] [{idx:>2}/{len(prompts)}] {p['id']}: {p['prompt']!r}",
              file=sys.stderr, flush=True)
        row = await _run_one(p)
        err_str = repr(row["error"]) if row["error"] else "none"
        print(
            f"[matcheval]   → matched={row['matched']} side={row['side']} "
            f"conf={row['confidence']} cand={row['candidate_count']} "
            f"err={err_str} ({row['elapsed_ms']}ms)",
            file=sys.stderr, flush=True,
        )
        rows.append(row)
    return rows


def _print_env_summary() -> None:
    """Surface the resolved LLM config from .env (via pydantic-settings)
    so the operator knows which provider is about to be billed."""
    try:
        from backend.config import settings
    except Exception as exc:  # noqa: BLE001
        print(f"[matcheval] could not load settings: {exc}", file=sys.stderr)
        return
    provider = (
        os.environ.get("LLM_PROVIDER")
        or settings.llm_provider
        or "openai"
    )
    print(
        f"[matcheval] LLM_PROVIDER={provider} "
        f"openai_key={'set' if settings.openai_api_key else 'UNSET'} "
        f"sarvam_key={'set' if settings.sarvam_api_key else 'UNSET'} "
        f"azure_key={'set' if settings.azure_key else 'UNSET'}",
        file=sys.stderr,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompts", default=None,
                    help="optional path to a JSON list[{id, prompt, expect?}]")
    ap.add_argument("--json", default=None,
                    help="optional path to dump full results JSON")
    args = ap.parse_args()

    _print_env_summary()
    prompts = _load_prompts(args.prompts)
    print(f"[matcheval] running {len(prompts)} prompts…", file=sys.stderr)

    rows = asyncio.run(_main_async(prompts))

    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2))
        print(f"[matcheval] wrote JSON → {args.json}", file=sys.stderr)

    # Markdown summary on stdout — easy to paste into a doc / PR.
    print(_md_table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
