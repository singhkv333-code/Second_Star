"""
Strategy explainer — generates plain-language explanations for LogicCard.
Never uses investment advice language. Always factual.
"""
import logging
from backend.agents.router import route_and_call, TaskType

logger = logging.getLogger(__name__)

EXPLAINER_SYSTEM_PROMPT = """You are Pivot's strategy explainer for Indian retail investors.

Your job: explain exactly what a financial strategy does in plain language.

RULES — never break these:
1. Never say "I recommend", "you should buy", "this is a good investment"
2. Always say "this strategy works by..." or "this construction does..."
3. Explain what happens in: a market crash, a flat market, a rising market
4. Use ₹ amounts, not percentages only
5. End every explanation with: "This is automation of your instructions, not financial advice."
6. Write for someone with no financial background
7. Max 200 words
8. Support Hinglish — if user message has Hindi words, mix Hindi naturally"""


async def explain_strategy(
    product_type: str,
    capital: float,
    safety_leg_amount: float,
    growth_leg_amount: float,
    safety_instrument: str,
    growth_instrument: str,
    arb_yield: float,
    horizon_months: int,
) -> str:
    """Generate plain language explanation for a synthetic product."""

    prompt = f"""Explain this investment strategy in simple terms:

Product: {product_type}
Total capital: ₹{capital:,.0f}
Safety leg: ₹{safety_leg_amount:,.0f} in {safety_instrument} (current yield: {arb_yield:.1f}%)
Growth leg: ₹{growth_leg_amount:,.0f} in {growth_instrument}
Time horizon: {horizon_months} months

Explain:
1. What these two parts do
2. What happens if market rises 20%
3. What happens if market falls 30%
4. Why the capital is safe"""

    messages = [{"role": "user", "content": prompt}]
    return await route_and_call(
        task_type=TaskType.EXPLAIN,
        messages=messages,
        system_prompt=EXPLAINER_SYSTEM_PROMPT,
        max_tokens=350,
    )


async def explain_order(symbol: str, action: str, quantity: int, price: float) -> str:
    """Explain a plain order before execution."""
    prompt = f"Explain in 2 sentences what this order does: {action} {quantity} shares of {symbol} at ₹{price}"
    messages = [{"role": "user", "content": prompt}]
    return await route_and_call(TaskType.EXPLAIN, messages, system_prompt=EXPLAINER_SYSTEM_PROMPT, max_tokens=100)
