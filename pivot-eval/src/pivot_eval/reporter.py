"""Persist run artefacts: conversations.md, results.json, report.md."""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .config import RUNS_DIR
from .runner import EvalResult


VERDICT_ORDER = {"fail": 0, "error": 1, "partial": 2, "pass": 3}
VERDICT_GLYPH = {"pass": "✓", "partial": "~", "fail": "✗", "error": "!"}


def write_run(results: list[EvalResult]) -> Path:
    """Create runs/<timestamp>/ and write all artefacts. Returns the run dir."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS_DIR / ts
    run_dir.mkdir(parents=True, exist_ok=True)

    summary = _summary(results)

    (run_dir / "conversations.md").write_text(
        _conversations_md(results, summary, ts), encoding="utf-8",
    )
    (run_dir / "results.json").write_text(
        json.dumps({"run_id": ts, "summary": summary,
                    "cases": [r.to_dict() for r in results]},
                   indent=2, default=str),
        encoding="utf-8",
    )
    (run_dir / "report.md").write_text(
        _report_md(summary, results, ts), encoding="utf-8",
    )
    return run_dir


# ---- Summary stats -----------------------------------------------------


def _summary(results: list[EvalResult]) -> dict:
    by_cat = defaultdict(lambda: {"total": 0, "pass": 0, "partial": 0,
                                   "fail": 0, "error": 0})
    by_verdict = {"pass": 0, "partial": 0, "fail": 0, "error": 0}
    latencies: list[int] = []
    for r in results:
        by_cat[r.category]["total"] += 1
        v = r.score.verdict if r.score else "error"
        by_cat[r.category][v] = by_cat[r.category].get(v, 0) + 1
        by_verdict[v] = by_verdict.get(v, 0) + 1
        for t in r.transcript:
            latencies.append(t.latency_ms)

    pass_rate = round(by_verdict["pass"] / max(len(results), 1), 3)
    return {
        "total": len(results),
        "passed": by_verdict["pass"],
        "partial": by_verdict["partial"],
        "failed": by_verdict["fail"],
        "errors": by_verdict["error"],
        "pass_rate": pass_rate,
        "by_category": {
            cat: dict(v) for cat, v in sorted(by_cat.items())
        },
        "latency_ms": _latency_stats(latencies),
    }


def _latency_stats(latencies: list[int]) -> dict:
    if not latencies:
        return {"n": 0}
    sorted_l = sorted(latencies)
    def pct(p):
        i = max(0, min(len(sorted_l) - 1, int(round(p * (len(sorted_l) - 1)))))
        return sorted_l[i]
    return {
        "n": len(latencies),
        "median": int(statistics.median(latencies)),
        "p90": int(pct(0.9)),
        "p99": int(pct(0.99)),
        "max": max(latencies),
    }


# ---- conversations.md --------------------------------------------------


def _conversations_md(results: list[EvalResult], summary: dict, run_id: str) -> str:
    lines: list[str] = []
    lines.append(f"# Pivot Eval Run — {run_id}")
    lines.append("")
    lines.append(
        f"**Total:** {summary['total']}  "
        f"**Passed:** {summary['passed']}  "
        f"**Partial:** {summary['partial']}  "
        f"**Failed:** {summary['failed']}  "
        f"**Errors:** {summary['errors']}  "
        f"**Pass rate:** {summary['pass_rate']:.0%}"
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # Group by category, FAILS first within category
    by_cat = defaultdict(list)
    for r in results:
        by_cat[r.category].append(r)
    for cat in sorted(by_cat.keys()):
        items = sorted(
            by_cat[cat],
            key=lambda r: (VERDICT_ORDER.get(r.score.verdict if r.score else "error", 5), r.case_id),
        )
        lines.append(f"## {cat} ({len(items)} cases)")
        lines.append("")
        for r in items:
            lines.extend(_case_md(r))
            lines.append("---")
            lines.append("")
    return "\n".join(lines)


def _case_md(r: EvalResult) -> list[str]:
    verdict = (r.score.verdict if r.score else "error").upper()
    glyph = VERDICT_GLYPH.get(verdict.lower(), "?")
    out = [f"### {r.case_id} — {verdict} {glyph}", ""]

    if len(r.transcript) == 1:
        t = r.transcript[0]
        out.append("**Input:**")
        out.append(f"> {t.user}")
        out.append("")
        out.append("**Bot Response:**")
        out.append("> " + (t.response_text.replace("\n", "\n> ") or "_(empty)_"))
        out.append("")
        out.append(f"**Tools called:** {_fmt_tools(t.tools_called)}")
        out.append(f"**Intent:** `{t.intent or '—'}` · **Latency:** {t.latency_ms} ms")
    else:
        out.append("**Conversation:**")
        for t in r.transcript:
            out.append(f"> User: {t.user}")
            text = (t.response_text or "_(empty)_").replace("\n", " ")
            out.append(f"> Bot: {text}")
            out.append(">")
        out.append("")
        last = r.transcript[-1]
        out.append(f"**Tools called on final turn:** {_fmt_tools(last.tools_called)}")
        out.append(f"**Final intent:** `{last.intent or '—'}` · "
                   f"**Final latency:** {last.latency_ms} ms")
    out.append("")

    if r.error:
        out.append(f"**Error:** {r.error}")
        out.append("")
    if r.score and r.score.criteria:
        out.append("**Scoring:**")
        for c in r.score.criteria:
            mark = "✓" if c.score == 3 else ("~" if c.score == 2 else "✗")
            tag = " *(deterministic)*" if c.deterministic else ""
            rat = f" — {c.rationale}" if c.rationale else ""
            out.append(f"- [{c.kind}] `{c.name}` → {c.score} {mark}{tag}{rat}")
        out.append("")
    if r.score and r.score.violations:
        out.append("**Violations:** " + ", ".join(r.score.violations))
        out.append("")
    return out


def _fmt_tools(tools) -> str:
    if not tools:
        return "_(none)_"
    return ", ".join(f"`{t.name}`" for t in tools)


# ---- report.md ---------------------------------------------------------


def _report_md(summary: dict, results: list[EvalResult], run_id: str) -> str:
    lines: list[str] = []
    lines.append(f"# Pivot Eval Report — {run_id}")
    lines.append("")
    lines.append(
        f"**{summary['total']} cases** &middot; "
        f"**{summary['passed']} pass** "
        f"({summary['pass_rate']:.0%}), "
        f"{summary['partial']} partial, "
        f"{summary['failed']} fail, "
        f"{summary['errors']} error"
    )
    lines.append("")

    # Per-category table
    lines.append("## Pass rate by category")
    lines.append("")
    lines.append("| Category | Total | Pass | Partial | Fail | Error |")
    lines.append("|----------|------:|-----:|--------:|-----:|------:|")
    for cat, c in summary["by_category"].items():
        lines.append(
            f"| {cat} | {c['total']} | {c.get('pass', 0)} | "
            f"{c.get('partial', 0)} | {c.get('fail', 0)} | {c.get('error', 0)} |"
        )
    lines.append("")

    # Latency
    lat = summary["latency_ms"]
    if lat.get("n", 0) > 0:
        lines.append("## Latency (ms)")
        lines.append("")
        lines.append(f"- median: {lat['median']}, p90: {lat['p90']}, "
                     f"p99: {lat['p99']}, max: {lat['max']} (n={lat['n']})")
        lines.append("")

    # Top violations
    violation_count = defaultdict(int)
    for r in results:
        if r.score:
            for v in r.score.violations:
                violation_count[v] += 1
    if violation_count:
        lines.append("## Top violations")
        lines.append("")
        for v, n in sorted(violation_count.items(), key=lambda x: -x[1])[:10]:
            lines.append(f"- {n}× — `{v}`")
        lines.append("")

    # Top failing cases
    fails = [r for r in results
             if r.score and r.score.verdict in ("fail", "error")]
    if fails:
        lines.append(f"## First failures ({min(len(fails), 10)} of {len(fails)})")
        lines.append("")
        for r in fails[:10]:
            short_input = ""
            if r.transcript:
                short_input = r.transcript[0].user[:60]
            lines.append(f"- **{r.case_id}** "
                         f"({r.score.verdict if r.score else 'error'}) — `{short_input}`")
        lines.append("")
        lines.append("See `conversations.md` for full transcripts.")
        lines.append("")
    return "\n".join(lines)
