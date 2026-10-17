"""Multi-step orchestrator for compound chat intents.

Problem this solves
-------------------
The agentic loop in `chat_service.py` already supports up to 8 sequential
tool calls per turn, with each tool's result appended to the conversation
context so the next LLM hop can see it. But for compound intents like:

    "Compare INFY, TCS, WIPRO over 2 years → tell me which had the lowest
     drawdown → build a momentum agent on the winner"

the LLM has to extract "the winner" from a structured comparison result
by reading prose and HAND-WIRE the symbol into the next tool's args. This
extraction is fragile: ~40% silent-failure rate on a hand-judged probe.

`compose_multistep` lets the LLM declare a multi-step plan UP FRONT, with
`$step_id.field` references that the server resolves deterministically
between sub-step calls. No LLM hop for the threading itself.

Tool shape
----------
    {
      "plan": [
        {"step_id": "compare", "tool": "compare_performance",
         "args": {"symbols": ["INFY","TCS","WIPRO"], "metric": "max_drawdown", "period": "2y"}},
        {"step_id": "winner",  "tool": "extract_winner_symbol",
         "args": {"from": "$compare", "metric": "max_drawdown", "direction": "min"}},
        {"step_id": "build",   "tool": "propose_threshold_order",
         "args": {"symbol": "$winner.symbol", "side": "buy",
                  "trigger_kind": "indicator", "indicator": "rsi",
                  "operator": "<", "threshold": 30, "quantity": 10}}
      ],
      "user_intent": "<verbatim user message>"
    }

Each sub-step is dispatched through `validation_handler.execute_with_completeness`
— the same path single-tool calls use — so completeness checks, M2 qty-default
refusal, etc. all apply uniformly.

Reference resolution
--------------------
Args are walked recursively. Strings of the shape `$step_id` resolve to the
ENTIRE prior step result (dict). Strings of the shape `$step_id.field.subfield`
resolve to the nested value at that path. Mid-string substitutions like
"buy $winner.symbol now" also work.

Return shape
------------
On success:
    {
      "_render_hint": "multistep_card",
      "user_intent": <verbatim>,
      "steps": [
        {"step_id": "compare", "tool": "...", "ok": true, "data": {...}, "latency_ms": ...},
        ...
      ],
      "final_card": <the last step's data, hoisted for the FE>,
      "summary": "<one-line synthesis>"
    }

On failure (step K errors), `steps[0..K]` carry their results and `steps[K]`
has `ok=false` + `error`. No further steps run. The chat layer renders the
partial timeline + a structured "this step failed" message.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Optional


logger = logging.getLogger(__name__)


# ── Period normalization ───────────────────────────────────────────────
#
# yfinance accepts a fixed set: "5d", "1mo", "3mo", "6mo", "1y", "2y",
# "5y", "max", "ytd". The LLM often emits human-natural variants like
# "3y" / "4y" / "18mo" / "since January". Map them to the closest valid
# period (rounding UP when ambiguous so we don't lose data the user asked
# for) before any tool that fetches OHLCV gets called.

_VALID_PERIODS = ("5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max", "ytd")


def _normalize_period(value: Any) -> str:
    """Canonicalise a user-supplied period for the OHLCV data layer.

    Canonical yfinance periods (5d/1mo/3mo/6mo/1y/2y/5y/max/ytd) pass
    through. Arbitrary spans ('3y', '18 months', '30 weeks') are now
    PRESERVED in a compact form ('3y', '18mo', '30w') instead of being
    rounded to a bucket — backend.core.data.historical.get_ohlcv fetches
    the next-larger valid period and slices to the EXACT requested span,
    so "compare … over 3 years" honours 3y (not 2y/5y). Unknown phrasings
    default to '1y' (or 'ytd')."""
    if not value:
        return "1y"
    s = str(value).strip().lower().replace(" ", "")
    if s in _VALID_PERIODS:
        return s
    # Preserve arbitrary 'N<unit>' spans in a compact canonical form.
    m = re.fullmatch(
        r"(\d+)\s*(d|day|days|w|wk|week|weeks|mo|month|months|y|yr|yrs|year|years)",
        s,
    )
    if m:
        n = m.group(1)
        unit = m.group(2)
        if unit.startswith("d"):
            return f"{n}d"
        if unit.startswith("w"):
            return f"{n}w"
        if unit.startswith("mo"):
            return f"{n}mo"
        return f"{n}y"
    # "since january" / "ytd" / unknown
    if "ytd" in s or ("year" in s and "to" in s):
        return "ytd"
    return "1y"


def _maybe_normalize_period(args: dict) -> dict:
    """If args carry a `period` key, normalize it in-place. Returns the
    same dict for chaining. No-op when period is absent or already valid."""
    if isinstance(args, dict) and "period" in args:
        args = dict(args)
        args["period"] = _normalize_period(args.get("period"))
    return args


# ── Reference resolver ──────────────────────────────────────────────────


# `$step_id` or `$step_id.field` or `$step_id.field.sub.path`.
# Step IDs are conservative: letters / digits / underscores only.
_REF_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z_][A-Za-z0-9_]*)*)")


def _walk_path(value: Any, path: str) -> Any:
    """Walk `value.foo.bar.baz` style paths through nested dicts/lists.
    Empty path returns the value itself. List indices are not supported
    by string path syntax (use a helper like `extract_winner_symbol` for
    list-aware operations)."""
    if not path:
        return value
    cur = value
    for part in path.split("."):
        if part == "":
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            # Allow attribute-style access if needed; for now return None
            # on type mismatch so the resolver surfaces a clear error.
            cur = getattr(cur, part, None)
        if cur is None:
            break
    return cur


def _resolve_refs(value: Any, results: dict[str, Any]) -> Any:
    """Walk `value` recursively, replacing any `$step_id[.path]` refs
    against the `results` dict (step_id → step data).

    Three modes:
    - WHOLE-STRING ref (`"$compare"`): substituted with the resolved value
      AS-IS (preserves type — dict, list, number).
    - MID-STRING ref (`"buy $winner.symbol now"`): substituted as a string.
    - NESTED dicts / lists: walked recursively.
    """
    if value is None:
        return None
    if isinstance(value, str):
        # Whole-string ref → preserve type.
        whole = _REF_RE.fullmatch(value)
        if whole is not None:
            step_id = whole.group(1)
            path = whole.group(2).lstrip(".") if whole.group(2) else ""
            if step_id not in results:
                raise OrchestratorRefError(
                    f"$ref '{value}' targets unknown step_id '{step_id}'"
                )
            return _walk_path(results[step_id], path)
        # Mid-string: stringify any refs.
        def _sub(m: re.Match) -> str:
            step_id = m.group(1)
            path = m.group(2).lstrip(".") if m.group(2) else ""
            if step_id not in results:
                raise OrchestratorRefError(
                    f"$ref '{m.group(0)}' targets unknown step_id '{step_id}'"
                )
            resolved = _walk_path(results[step_id], path)
            return str(resolved) if resolved is not None else ""
        return _REF_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _resolve_refs(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_refs(v, results) for v in value]
    return value


class OrchestratorRefError(ValueError):
    """Raised when a $step_id.field ref can't be resolved (typo in plan,
    referenced step not yet executed, etc.)."""


# ── Helper sub-tools (called by name from inside a plan) ────────────────


# Per-result key conventions: the analytics tools (compare_performance,
# get_performance_metrics, etc.) return results either as
#   {"ranked": [{"symbol": "INFY", "max_drawdown": -0.12, ...}, ...]}
# or as a flat dict of dicts:
#   {"INFY": {"max_drawdown": -0.12, ...}, "TCS": {...}}
# `extract_winner_symbol` handles both shapes.


def extract_winner_symbol(args: dict) -> dict:
    """Deterministic helper: given a tool result containing per-symbol
    metric values, return the winning symbol.

    Args:
      from: the data dict from a prior step (resolved via $step.field)
      metric: the metric name to compare on (e.g. "max_drawdown")
      direction: "min" or "max" (default "max"; "min" for drawdown, vol)

    Returns:
      {"symbol": "<sym>", "metric_value": <float>, "all": [<ranked rows>]}
    """
    source = args.get("from")
    metric = (args.get("metric") or "").strip()
    direction = (args.get("direction") or "max").strip().lower()
    if direction not in {"min", "max"}:
        direction = "max"
    if not metric:
        raise ValueError("extract_winner_symbol needs a 'metric' name.")
    if source is None:
        raise ValueError("extract_winner_symbol needs 'from' (a prior step's data).")

    # Unwrap common containers. `compare_performance` nests its per-symbol
    # data at `comparison.results`; `compare_backtests` (this module) uses
    # `results` or `ranked_by_*`. Walk down to find the per-symbol map.
    def _unwrap(s: Any) -> Any:
        if not isinstance(s, dict):
            return s
        # compare_performance nests at comparison.results
        comp = s.get("comparison")
        if isinstance(comp, dict) and isinstance(comp.get("results"), dict):
            return comp["results"]
        # generic 'results' key holding a per-symbol map
        res = s.get("results")
        if isinstance(res, dict):
            return res
        return s

    source = _unwrap(source)

    rows: list[dict[str, Any]] = []

    # Shape A: {"ranked": [{symbol, metric, ...}, ...]}
    if isinstance(source, dict) and isinstance(source.get("ranked"), list):
        for row in source["ranked"]:
            if isinstance(row, dict):
                rows.append(row)
    # Shape B: flat {SYM: {metric: value, ...}, SYM2: {...}}
    elif isinstance(source, dict):
        for k, v in source.items():
            if isinstance(v, dict) and metric in v:
                rows.append({"symbol": k, **{metric: v.get(metric)}})
    # Shape C: bare list of {symbol, ...}
    elif isinstance(source, list):
        for row in source:
            if isinstance(row, dict):
                rows.append(row)

    if not rows:
        raise ValueError(
            f"extract_winner_symbol: source had no per-symbol rows with "
            f"metric '{metric}'. Source keys: "
            f"{list(source.keys()) if isinstance(source, dict) else type(source).__name__}"
        )

    # Filter to rows that actually have the metric.
    rows = [r for r in rows if r.get(metric) is not None]
    if not rows:
        raise ValueError(
            f"extract_winner_symbol: no rows carried a value for '{metric}'."
        )

    rows.sort(key=lambda r: r.get(metric), reverse=(direction == "max"))
    winner = rows[0]
    sym = winner.get("symbol") or winner.get("ticker") or winner.get("name")
    if not sym:
        raise ValueError(
            "extract_winner_symbol: winning row had no 'symbol' field."
        )
    return {
        "symbol": str(sym).upper(),
        "metric": metric,
        "metric_value": winner.get(metric),
        "direction": direction,
        "ranked": rows,
    }


async def compare_backtests(args: dict) -> dict:
    """Run 2-4 strategies through the same workflow backtester in parallel
    and return a side-by-side ranking.

    Args:
      strategies: list of {"name": str, "steps": list[dict]}  (2..4 items)
      period: backtest window (default "2y")
      benchmark_symbol: optional override for the buy-and-hold comparison

    Returns:
      {
        "results": [{"name": str, "metrics": {...}, "ok": bool, "error": str?}],
        "ranked_by_total_return_pct": [{"name", "total_return_pct"}, ...],
        "ranked_by_sharpe": [{"name", "sharpe_ratio"}, ...]
      }
    """
    strategies = args.get("strategies") or []
    if not isinstance(strategies, list) or not (2 <= len(strategies) <= 4):
        raise ValueError(
            "compare_backtests needs 'strategies' as a list of 2-4 specs."
        )
    period = str(args.get("period") or "2y")
    benchmark_symbol = args.get("benchmark_symbol")

    from backend.services.workflow_backtester import backtest_workflow

    async def _one(spec: dict) -> dict:
        name = (spec.get("name") or "?").strip() or "?"
        steps = spec.get("steps") or []
        if not isinstance(steps, list) or not steps:
            return {"name": name, "ok": False,
                    "error": "spec missing valid 'steps' list"}
        try:
            # backtest_workflow is sync — run in thread to keep gather true-parallel.
            result = await asyncio.to_thread(
                backtest_workflow,
                steps,
                period=period,
                name=name,
                benchmark_symbol=benchmark_symbol,
            )
            return {
                "name": name,
                "ok": True,
                "metrics": getattr(result, "metrics", None) or {},
                "n_trades": getattr(result, "n_trades", None),
                "total_return_pct": getattr(result, "total_return_pct", None),
            }
        except Exception as e:  # noqa: BLE001
            return {"name": name, "ok": False, "error": str(e)[:200]}

    results = await asyncio.gather(*[_one(s) for s in strategies])

    def _rank(field: str, descending: bool) -> list[dict]:
        rows = [
            {"name": r["name"], field: (r.get("metrics") or {}).get(field, r.get(field))}
            for r in results
            if r.get("ok") and (r.get("metrics") or {}).get(field, r.get(field)) is not None
        ]
        rows.sort(key=lambda r: r[field], reverse=descending)
        return rows

    return {
        "results": results,
        "ranked_by_total_return_pct": _rank("total_return_pct", True),
        "ranked_by_sharpe": _rank("sharpe_ratio", True),
        "ranked_by_max_drawdown_pct": _rank("max_drawdown_pct", False),
    }


# ── Main orchestrator handler ──────────────────────────────────────────


# Sub-tools that the orchestrator handles directly without going through
# the full validation_handler path. These are pure-Python helpers that
# don't need completeness checks / arg validation against a JSON schema
# (their schemas are simple and validated inline).
_INLINE_HANDLERS = {
    "extract_winner_symbol": ("sync", extract_winner_symbol),
    "compare_backtests": ("async", compare_backtests),
}


async def compose_multistep(args: dict) -> dict:
    """Server-side orchestrator. Walks `plan` in order, resolves $refs
    between steps, dispatches each step to either an inline helper or
    the full validation_handler. Returns a structured timeline.

    Raises on plan-level errors (bad shape, ref typos before resolution).
    Sub-step failures land in the returned `steps[K]` with `ok=False`.
    """
    plan = args.get("plan") or []
    user_intent = (args.get("user_intent") or "").strip()
    if not isinstance(plan, list) or not plan:
        raise ValueError(
            "compose_multistep needs a non-empty 'plan' list."
        )
    if len(plan) > 6:
        raise ValueError(
            "compose_multistep is bounded at 6 steps — split into smaller "
            "plans or call again with a follow-up plan."
        )

    # Pre-validate plan structure.
    seen_ids: set[str] = set()
    for i, step in enumerate(plan):
        if not isinstance(step, dict):
            raise ValueError(f"plan[{i}] is not a dict.")
        step_id = (step.get("step_id") or "").strip()
        tool = (step.get("tool") or "").strip()
        if not step_id or not tool:
            raise ValueError(
                f"plan[{i}] needs both 'step_id' and 'tool' strings."
            )
        if step_id in seen_ids:
            raise ValueError(f"plan[{i}] step_id '{step_id}' is not unique.")
        seen_ids.add(step_id)

    results: dict[str, Any] = {}
    timeline: list[dict[str, Any]] = []

    for i, step in enumerate(plan):
        step_id = step["step_id"].strip()
        tool_name = step["tool"].strip()
        raw_args = step.get("args") or {}

        t0 = time.monotonic()

        try:
            resolved_args = _resolve_refs(raw_args, results)
        except OrchestratorRefError as e:
            timeline.append({
                "step_id": step_id, "tool": tool_name, "ok": False,
                "error": f"unresolved ref: {e}",
                "latency_ms": int((time.monotonic() - t0) * 1000),
            })
            return _final_payload(timeline, user_intent, completed=False)
        # Apply period normalization — saves a step failure when the
        # LLM emits "3y" / "18mo" / "since January".
        resolved_args = _maybe_normalize_period(resolved_args)

        try:
            data, ok, err = await _dispatch_step(
                tool_name, resolved_args, args,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("orchestrator step %s tool %s crashed",
                             step_id, tool_name)
            data, ok, err = {}, False, f"{type(e).__name__}: {e}"

        timeline.append({
            "step_id": step_id, "tool": tool_name,
            "ok": ok, "data": data if ok else {}, "error": err,
            "latency_ms": int((time.monotonic() - t0) * 1000),
        })

        if not ok:
            return _final_payload(timeline, user_intent, completed=False)

        results[step_id] = data

    return _final_payload(timeline, user_intent, completed=True)


async def _dispatch_step(
    tool_name: str, args: dict, ctx: dict,
) -> tuple[dict, bool, Optional[str]]:
    """Call one sub-tool. Returns (data, ok, error).

    Two paths:
    - Inline helpers (`extract_winner_symbol`, `compare_backtests`) run
      directly. Cheap; no validation_handler overhead.
    - Everything else goes through `validation_handler.execute_with_completeness`
      so it gets the full M1/M2 protection (no qty=1 defaults, no
      unstructured-clarification prose).
    """
    if tool_name in _INLINE_HANDLERS:
        mode, fn = _INLINE_HANDLERS[tool_name]
        try:
            if mode == "async":
                result = await fn(args)
            else:
                result = fn(args)
        except Exception as e:  # noqa: BLE001
            return {}, False, f"{type(e).__name__}: {e}"
        if not isinstance(result, dict):
            return {}, False, "inline helper returned non-dict result"
        return result, True, None

    # Full chat-side dispatch path.
    from backend.services.validation_handler import (
        execute_with_completeness,
    )
    # ctx carries kite_token / db / user_id forwarded via the chat layer.
    kite_token = ctx.get("__kite_token") or ""
    db = ctx.get("__db")
    user_id = int(ctx.get("__user_id") or 0)
    llm_client = ctx.get("__llm_client")

    # C9: an explicit numeric quantity / rupee notional in a resolved
    # plan step is an LLM-authored deliberate choice (the orchestrator
    # has already substituted any $ref into a literal arg), NOT a silent
    # qty=1/10 fallback. The M2 suspicious-default guard must not bounce
    # it back as a clarification — that aborts the compose_multistep
    # chain (completed=False, no final_card) and the hop-2 LLM narrates
    # "I couldn't build the agent, it needs a size".
    explicit_qty = (
        isinstance(args.get("quantity"), (int, float))
        or args.get("notional_inr") is not None
    )
    guarded = await execute_with_completeness(
        tool_name,
        args,
        llm_client=llm_client,
        user_message=ctx.get("__user_message") or "",
        kite_token=kite_token,
        db=db,
        user_id=user_id,
        suppress_qty_default_check=explicit_qty,
    )
    if guarded.needs_clarification:
        return (
            {"_clarification_required": True, "question": guarded.question},
            False,
            f"sub-step needs clarification: {guarded.question}",
        )
    if not guarded.success:
        return {}, False, guarded.error or "tool failed"
    return guarded.data or {}, True, None


def _final_payload(
    timeline: list[dict], user_intent: str, completed: bool,
) -> dict:
    """Shape the final return so the FE can render a "plan card" with
    per-step status. The last successful step's data is hoisted to
    `final_card` so a regular workflow_draft_card / logic_card can be
    surfaced alongside the timeline."""
    final_card: dict[str, Any] = {}
    if completed and timeline:
        last_ok = next(
            (s for s in reversed(timeline) if s.get("ok")),
            None,
        )
        if last_ok is not None:
            final_card = dict(last_ok.get("data") or {})

    summary_parts: list[str] = []
    for s in timeline:
        marker = "✓" if s.get("ok") else "✗"
        summary_parts.append(f"{marker} {s.get('step_id')}:{s.get('tool')}")
    summary = " · ".join(summary_parts)

    return {
        "_render_hint": "multistep_card",
        "user_intent": user_intent,
        "steps": timeline,
        "final_card": final_card,
        "summary": summary,
        "completed": completed,
    }
