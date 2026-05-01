"""Pattern-finding over a results.json → suggestions.md.

This module emits *suggestions*, not patches. It does not edit any chatbot
files. The whole point is to produce a brief for the maintainer to skim.
"""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .config import RUNS_DIR


def latest_run_dir() -> Path:
    candidates = sorted(
        (p for p in RUNS_DIR.iterdir() if p.is_dir()),
        key=lambda p: p.name,
    )
    if not candidates:
        raise FileNotFoundError(f"no runs found under {RUNS_DIR}")
    return candidates[-1]


def write_suggestions(run_dir: Path | None = None) -> Path:
    run_dir = run_dir or latest_run_dir()
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))

    sections = _build_suggestions(results)
    md = _render(sections, run_dir.name, results.get("summary", {}))

    out = run_dir / "suggestions.md"
    out.write_text(md, encoding="utf-8")
    return out


# ---- Pattern detection -------------------------------------------------


def _build_suggestions(results: dict) -> dict:
    cases = results.get("cases", [])
    high: list[dict] = []
    medium: list[dict] = []
    low: list[dict] = []
    questions: list[str] = []

    # 1) Casual-category unsolicited advice
    advice_cases = [
        c for c in cases
        if c["category"] == "CASUAL"
        and any("unsolicited" in v or "investment_advice" in v
                for v in c.get("scoring", {}).get("violations", []))
    ]
    if len(advice_cases) >= 3:
        high.append({
            "title": "Casual greetings frequently push investing topics",
            "evidence": [
                f"{len(advice_cases)} of {sum(1 for c in cases if c['category']=='CASUAL')} "
                "CASUAL cases tripped a `must_not_unsolicited_investment_advice`-class violation.",
                "Example case IDs: " + ", ".join(c["id"] for c in advice_cases[:6]),
            ],
            "suggested_change":
                "Tighten the system prompt with an explicit clause for casual inputs:\n"
                '> "On greetings, thank-yous, and small talk, do NOT bring up '
                "stocks, the screener, or any investing topic unless the user "
                'asks. Reply warmly and briefly."',
            "files":
                "system prompt definition (locate and inspect — do not edit).",
        })

    # 2) Tool-call gaps in FINANCIAL
    fin_no_tool = [
        c for c in cases
        if c["category"] == "FIN"
        and any(v.startswith("missing tool family") or "must_use_tool" in v
                for v in c.get("scoring", {}).get("violations", []))
    ]
    if len(fin_no_tool) >= 3:
        high.append({
            "title": "Financial questions answered without calling a tool",
            "evidence": [
                f"{len(fin_no_tool)} FINANCIAL cases failed because the bot didn't fire a tool",
                "Example case IDs: " + ", ".join(c["id"] for c in fin_no_tool[:6]),
            ],
            "suggested_change":
                "Audit the intent classifier — these cases probably routed to "
                "`GENERAL` instead of `MARKET_QUERY`. Either widen the keyword "
                "set for the classifier, or add a small post-classifier rule "
                "that escalates 'price/PE/52w/quote' patterns to MARKET_QUERY.",
            "files":
                "backend/agents/classifier.py and the system-prompt tools list.",
        })

    # 3) Multi-turn context loss
    mt_failures = [
        c for c in cases
        if c["category"] == "MULTI"
        and c["verdict"] in ("fail", "partial")
    ]
    if len(mt_failures) >= 4:
        high.append({
            "title": "Multi-turn context not carried for follow-up questions",
            "evidence": [
                f"{len(mt_failures)} of {sum(1 for c in cases if c['category']=='MULTI')} "
                "MULTITURN cases failed or partially failed on the final turn.",
                "Example case IDs: " + ", ".join(c["id"] for c in mt_failures[:6]),
            ],
            "suggested_change":
                "Inspect whether the chat router is actually forwarding the "
                "full message history on each turn (vs. just the latest user "
                "message). If history is forwarded, consider adding to the "
                "system prompt: \"When the user says 'and X' or 'what about X', "
                "interpret X as a continuation of the previous query topic.\"",
            "files":
                "backend/routers/chat.py — verify the `messages` array passed "
                "to Sarvam includes prior assistant turns.",
        })

    # 4) Tone scoring averages
    tone_scores = []
    for c in cases:
        if c["category"] != "CASUAL":
            continue
        for crit in c.get("scoring", {}).get("criteria", []):
            if crit["kind"] == "should" and "warm" in crit["name"].lower():
                tone_scores.append(crit["score"])
    if tone_scores and statistics.mean(tone_scores) < 2.4:
        medium.append({
            "title": f"CASUAL tone average is {statistics.mean(tone_scores):.2f}/3",
            "evidence": [
                f"{len(tone_scores)} tone-related criteria scored, "
                f"mean {statistics.mean(tone_scores):.2f}.",
                "Most chatbot greetings score 2 (partial) — likely too transactional.",
            ],
            "suggested_change":
                "Add 1-2 example greetings to the system prompt with the "
                "warmth level you want, and an explicit 'don't be robotic' line.",
            "files": "system prompt definition.",
        })

    # 5) Length anomalies
    long_greetings = []
    for c in cases:
        if c["category"] != "CASUAL":
            continue
        for crit in c.get("scoring", {}).get("criteria", []):
            if crit["name"].startswith("length:") and crit["score"] == 1:
                long_greetings.append(c["id"])
    if len(long_greetings) >= 5:
        medium.append({
            "title": "Casual responses run far longer than the rubric target",
            "evidence": [
                f"{len(long_greetings)} CASUAL cases exceeded their length target by 50%+.",
                "Example case IDs: " + ", ".join(long_greetings[:6]),
            ],
            "suggested_change":
                "Tighten the brevity rule in the system prompt. Current: "
                "'Maximum 2-3 sentences for simple questions.' "
                "Suggest: 'For greetings and small talk, reply in <=15 words. "
                "Do not add capability lists or follow-up offers unless asked.'",
            "files": "system prompt definition.",
        })

    # 6) Latency outliers
    slow_cases = []
    for c in cases:
        for t in c.get("transcript", []):
            if t.get("latency_ms", 0) > 5000:
                slow_cases.append((c["id"], t["latency_ms"]))
                break
    if slow_cases:
        low.append({
            "title": f"{len(slow_cases)} cases had >5s latency",
            "evidence": [
                f"Slowest: " + ", ".join(
                    f"{cid} ({ms} ms)" for cid, ms in
                    sorted(slow_cases, key=lambda x: -x[1])[:5]
                ),
            ],
            "suggested_change":
                "These are usually expression backtests or compare calls "
                "with cold cache. Pre-warming common symbols, or adding a "
                "small in-memory cache on `/api/backtest/expr/screen` for "
                "the same expression+date pair, would shrink P90.",
            "files": "backend/routers/expr_backtest.py · backend/routers/compare.py",
        })

    # 7) Errors
    errored = [c for c in cases if c["verdict"] == "error"]
    if errored:
        high.append({
            "title": f"{len(errored)} cases errored at runtime (no scoring)",
            "evidence": [
                f"Example case IDs: " + ", ".join(c["id"] for c in errored[:6]),
                f"First error message: {(errored[0].get('error') or '—')[:160]}",
            ],
            "suggested_change":
                "These didn't fail the rubric — they failed to even produce "
                "a response. Check whether the chatbot is timing out, "
                "rate-limited, or hitting an exception during tool execution.",
            "files": "uvicorn logs · backend/agents/tool_executor.py",
        })

    # 8) Open questions for maintainer
    if any(c["category"] == "AMB" for c in cases):
        amb_results = [c for c in cases if c["category"] == "AMB"]
        amb_pass = sum(1 for c in amb_results if c["verdict"] == "pass")
        if amb_pass / max(len(amb_results), 1) < 0.7:
            questions.append(
                "AMBIGUOUS cases (e.g. 'should I buy reliance') pass at "
                f"{amb_pass}/{len(amb_results)}. Should these answer with "
                "disclaimers + facts, fully redirect, or refuse? Current "
                "behaviour appears inconsistent across runs."
            )

    return {"high": high, "medium": medium, "low": low, "questions": questions}


def _render(sections: dict, run_id: str, summary: dict) -> str:
    lines: list[str] = []
    lines.append(f"# Suggestions from run {run_id}")
    lines.append("")
    lines.append(
        f"_Read-only: this file proposes changes — it does not implement "
        f"them, and no chatbot files have been modified._"
    )
    lines.append("")
    if summary:
        lines.append(
            f"Run summary: {summary.get('passed', 0)} pass / "
            f"{summary.get('partial', 0)} partial / "
            f"{summary.get('failed', 0)} fail / "
            f"{summary.get('errors', 0)} error "
            f"(out of {summary.get('total', 0)})"
        )
        lines.append("")

    def _section(title: str, items: list[dict]):
        if not items:
            return
        lines.append(f"## {title}")
        lines.append("")
        for i, it in enumerate(items, 1):
            lines.append(f"### {i}. {it['title']}")
            lines.append("")
            lines.append("**Evidence:**")
            for ev in it["evidence"]:
                lines.append(f"- {ev}")
            lines.append("")
            lines.append("**Suggested change:**")
            lines.append("")
            lines.append(it["suggested_change"])
            lines.append("")
            if "files" in it:
                lines.append(f"**Files likely to change:** {it['files']}")
                lines.append("")
            lines.append("---")
            lines.append("")

    _section("High-priority", sections["high"])
    _section("Medium-priority", sections["medium"])
    _section("Low-priority", sections["low"])

    if sections["questions"]:
        lines.append("## Open questions for the maintainer")
        lines.append("")
        for q in sections["questions"]:
            lines.append(f"- {q}")
        lines.append("")

    if not (sections["high"] or sections["medium"] or sections["low"]
            or sections["questions"]):
        lines.append("_No systematic patterns found in this run. "
                     "Either the bot is doing well or the dataset is too small._")
        lines.append("")
    return "\n".join(lines)
