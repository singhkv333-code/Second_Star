"""
NLU Intent parser — classifies user message into structured intent + entities.
Returns JSON: {intent, sub_intent, entities, confidence, clarification_needed, clarification_question}
"""
import json
import logging
from backend.agents.router import route_and_call, TaskType

logger = logging.getLogger(__name__)

INTENT_SYSTEM_PROMPT = """You are Pivot's intent classifier for an Indian retail investing platform.

Classify the user message into exactly ONE intent from this list:
- PROTECTION: user wants to protect capital, avoid loss (SafeGrow, StormShield, Buffer)
- INCOME: user wants regular income/yield (EarnMore covered call, Autocall, Cash-secured put)
- GROWTH: user wants to grow wealth faster (Leveraged note, Sector basket, Earnings play)
- YIELD_OPT: user wants better yield on idle cash/FD (Yield ladder, Yield switcher)
- HEDGING: user wants to hedge existing portfolio (Portfolio insurance, Rupee hedge)
- LIFE_EVENT: user has specific goal (education fund, retirement, wedding)
- MACRO_VIEW: user has market conviction (rate cut bet, war basket, budget play)
- PORTFOLIO: user asking about their portfolio, holdings, P&L
- ORDER_PLACE: user wants to buy/sell a specific stock or ETF
- ORDER_CANCEL: user wants to cancel an order
- SIP_CREATE: user wants to set up a recurring investment
- SIP_MANAGE: user wants to pause/resume/cancel a SIP
- STRATEGY_CREATE: user wants to automate a trading rule
- STRATEGY_MANAGE: user wants to pause/stop a strategy
- BACKTEST: user wants to test a strategy on historical data
- GENERAL: greeting, general question, unclear

Return ONLY valid JSON. No explanation. No markdown. Example:
{
  "intent": "PROTECTION",
  "sub_intent": "capital_guarantee",
  "entities": {
    "capital": 100000,
    "horizon_months": 12,
    "underlying": "Nifty 50",
    "risk_tolerance": "zero"
  },
  "confidence": 0.92,
  "clarification_needed": false,
  "clarification_question": null,
  "recommended_product": "safegrow",
  "language_detected": "english"
}"""

# NSE ticker dictionary — company name to symbol
TICKER_MAP = {
    "reliance": "RELIANCE", "hdfc bank": "HDFCBANK", "hdfc": "HDFCBANK",
    "tcs": "TCS", "tata consultancy": "TCS", "infosys": "INFY", "infy": "INFY",
    "wipro": "WIPRO", "icici bank": "ICICIBANK", "icici": "ICICIBANK",
    "axis bank": "AXISBANK", "kotak": "KOTAKBANK", "sbi": "SBIN",
    "bharti airtel": "BHARTIARTL", "airtel": "BHARTIARTL",
    "l&t": "LT", "larsen": "LT", "bajaj finance": "BAJFINANCE",
    "asian paints": "ASIANPAINT", "nestle": "NESTLEIND",
    "hal": "HAL", "hindustan aeronautics": "HAL", "bel": "BEL",
    "bharat electronics": "BEL", "nifty bees": "NIFTYBEES",
    "gold bees": "GOLDBEES", "bharat bond": "EBBETF0430",
}


async def parse_intent(user_message: str, conversation_history: list = None) -> dict:
    """
    Parse user message into structured intent.
    Returns intent dict with entities, confidence, recommended_product.
    """
    messages = [{"role": "user", "content": user_message}]
    if conversation_history:
        # Include last 3 exchanges for context
        messages = conversation_history[-6:] + messages

    try:
        response = await route_and_call(
            task_type=TaskType.INTENT,
            messages=messages,
            system_prompt=INTENT_SYSTEM_PROMPT,
            json_mode=True,
        )
        result = json.loads(response)

        # Extract and normalize ticker mentions
        msg_lower = user_message.lower()
        for name, ticker in TICKER_MAP.items():
            if name in msg_lower:
                if "entities" not in result:
                    result["entities"] = {}
                result["entities"]["ticker"] = ticker
                break

        return result
    except json.JSONDecodeError:
        return {
            "intent": "GENERAL",
            "sub_intent": "unclear",
            "entities": {},
            "confidence": 0.3,
            "clarification_needed": True,
            "clarification_question": "Could you tell me more about what you'd like to do with your investment?",
            "language_detected": "unknown",
        }
    except Exception as e:
        logger.error(f"Intent parsing failed: {e}")
        return {"intent": "GENERAL", "entities": {}, "confidence": 0.0}
