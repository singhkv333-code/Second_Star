# Refresh per the provider's pricing page on every quarter; cost values here drive billing dashboards.
"""Cost computation + persistence for every LLM call.

Two responsibilities:

  1. ``compute_cost(...)`` — pure arithmetic over the static ``PRICING``
     table. Reasoning tokens on the OpenAI Responses API are billed at
     the output-token rate, so they get folded in.

  2. ``record_llm_usage(...)`` — opens its OWN short-lived ``SessionLocal()``
     (the trace can close from outside any FastAPI request scope, e.g.
     a scheduler tick or an agentic worker), inserts a ``LlmUsage`` row,
     and emits a structured ``event="llm.usage"`` log line carrying the
     computed cost. Both the DB write and the log emission are wrapped
     so that any failure inside this module NEVER propagates back into
     the LLM call path. Cost tracking that breaks production is worse
     than no cost tracking.

Pricing rates here are USD per 1,000,000 tokens. Update them when the
provider's pricing page changes — they are the source of truth for the
billing dashboards downstream.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import structlog

from backend.observability.request_context import request_id_var, user_id_var


logger = structlog.get_logger(__name__)


# USD per 1,000,000 tokens. Keep keys lowercased on read.
PRICING: dict[str, dict[str, float]] = {
    "gpt-5-mini": {"input": 0.25, "output": 2.00},   # placeholder — verify against current OpenAI page
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o":     {"input": 2.50, "output": 10.00},
    "sarvam-m":   {"input": 0.00, "output": 0.00},   # Sarvam dev tier, no per-token billing today
}


# Six decimal places of USD precision. Fractions below this are below
# our reporting resolution; rounding half-up matches what an accountant
# would do.
_COST_QUANTUM = Decimal("0.000001")


def _normalize_model(model: str) -> str:
    """Strip date / version suffixes that providers tack on.

    OpenAI returns model strings like ``gpt-4o-mini-2024-07-18``; we
    only carry the family name in PRICING. We match by longest-prefix:
    walk the PRICING keys longest-first and pick the first that the
    requested model starts with.
    """
    if not model:
        return ""
    m = model.strip().lower()
    if m in PRICING:
        return m
    # longest-prefix match so "gpt-4o" doesn't shadow "gpt-4o-mini"
    for key in sorted(PRICING.keys(), key=len, reverse=True):
        if m.startswith(key):
            return key
    return m


def compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int = 0,
) -> Decimal:
    """Return the USD cost of a single LLM call as a 6-decimal Decimal.

    Reasoning tokens (visible on GPT-5 / o-series via the Responses API
    ``output_tokens_details.reasoning_tokens`` field) are billed at the
    output-token rate, NOT a separate rate, so we fold them in.

    Unknown models cost 0 — we still WARN once so that an undocumented
    provider variant doesn't silently disappear from the spend ledger.
    """
    normalized = _normalize_model(model)
    rates = PRICING.get(normalized)
    if rates is None:
        logger.warning(
            "llm_cost.unknown_model",
            model=model,
            normalized=normalized,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
        )
        return Decimal("0").quantize(_COST_QUANTUM)

    # Sentinel for negative / NaN inputs from a misbehaving provider.
    in_t = max(0, int(input_tokens or 0))
    out_t = max(0, int(output_tokens or 0))
    rsn_t = max(0, int(reasoning_tokens or 0))

    input_rate = Decimal(str(rates["input"])) / Decimal("1000000")
    output_rate = Decimal(str(rates["output"])) / Decimal("1000000")

    cost = (
        Decimal(in_t) * input_rate
        + Decimal(out_t + rsn_t) * output_rate
    )
    return cost.quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP)


def record_llm_usage(
    *,
    model: str,
    provider: str,
    endpoint: str,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    latency_ms: float,
    user_id: Optional[int] = None,
    conversation_id: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> None:
    """Persist one LLM call to the ``llm_usage`` table and emit a log line.

    Failure modes are swallowed locally: a DB outage or schema drift
    must never break the LLM call path. The structured log line is the
    secondary record so a DB write failure still leaves a trace.

    ``user_id`` falls back to the request-scope ``user_id_var`` when not
    explicitly provided. ``request_id`` is read off the request scope's
    contextvar — when present, it lands on the row AND on the log line
    (structlog's ``merge_contextvars`` already attaches it, but adding it
    explicitly to the row keeps the DB queryable without needing the log
    aggregator alongside).
    """
    try:
        effective_user_id = user_id if user_id is not None else user_id_var.get()
        request_id = request_id_var.get()

        cost = compute_cost(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
        )
        total_tokens = int(input_tokens or 0) + int(output_tokens or 0) + int(reasoning_tokens or 0)

        # --- log line first; the dashboard / log aggregator path is
        # cheap and shouldn't be gated on the DB write succeeding.
        logger.info(
            "llm.usage",
            endpoint=endpoint,
            provider=provider,
            model=model,
            input_tokens=int(input_tokens or 0),
            output_tokens=int(output_tokens or 0),
            reasoning_tokens=int(reasoning_tokens or 0),
            total_tokens=total_tokens,
            cost_usd=str(cost),
            latency_ms=float(latency_ms) if latency_ms is not None else None,
            user_id=effective_user_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            request_id=request_id,
        )

        # --- DB write. Own session; closed in finally. Never raises out.
        try:
            from backend.database import SessionLocal
            from backend.models import LlmUsage

            db = SessionLocal()
            try:
                row = LlmUsage(
                    user_id=effective_user_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    request_id=request_id,
                    endpoint=endpoint,
                    provider=provider,
                    model=model,
                    input_tokens=int(input_tokens or 0),
                    output_tokens=int(output_tokens or 0),
                    reasoning_tokens=int(reasoning_tokens or 0),
                    total_tokens=total_tokens,
                    cost_usd=cost,
                    latency_ms=(
                        float(latency_ms) if latency_ms is not None else None
                    ),
                )
                db.add(row)
                db.commit()
            except Exception as exc:  # noqa: BLE001 — must never propagate
                try:
                    db.rollback()
                except Exception:
                    pass
                logger.warning(
                    "llm_cost.persist_failed",
                    error=f"{type(exc).__name__}: {exc}",
                    endpoint=endpoint,
                    model=model,
                )
            finally:
                try:
                    db.close()
                except Exception:
                    pass
        except Exception as exc:  # noqa: BLE001 — defensive double net
            logger.warning(
                "llm_cost.persist_failed_outer",
                error=f"{type(exc).__name__}: {exc}",
            )
    except Exception as exc:  # noqa: BLE001 — absolute outermost net
        # Even the logger or contextvar read could in theory raise. The
        # caller is an LLM client mid-finally; we eat everything.
        try:
            logger.warning(
                "llm_cost.record_failed",
                error=f"{type(exc).__name__}: {exc}",
            )
        except Exception:
            pass
