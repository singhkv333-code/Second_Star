from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from backend.auth.jwt_handler import get_user_id_from_token
from backend.kite.market_data import get_historical_ohlcv
import statistics

router = APIRouter(prefix="/backtest", tags=["Backtest"])

DISCLAIMER = "⚠️ Past performance does not guarantee future results. This is a simulation only. Includes 0.1% friction per trade."


def get_user_id(authorization: str = Header(None)) -> int:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    uid = get_user_id_from_token(authorization.replace("Bearer ", ""))
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid token")
    return uid


class BacktestRequest(BaseModel):
    symbol: str
    strategy_type: str = Field(..., description="price_drop, rsi, price_cross, sip")
    trigger_condition: dict
    action: str = Field(default="BUY", description="BUY or SELL")
    quantity_pct: float = Field(default=10.0, description="% of portfolio per trade")
    period: str = Field(default="1y", description="1mo, 3mo, 6mo, 1y, 2y")
    starting_capital: float = Field(default=100000)


@router.post("/run")
def run_backtest(request: BacktestRequest, user_id: int = Depends(get_user_id)):
    """
    Run a strategy backtest against historical data.
    Uses yfinance — free, no API key needed.
    """
    history = get_historical_ohlcv(request.symbol, period=request.period)
    if len(history) < 10:
        raise HTTPException(status_code=400, detail=f"Insufficient historical data for {request.symbol}")

    FRICTION = 0.001  # 0.1% per trade (brokerage + slippage)
    capital = request.starting_capital
    position = 0
    trades = []
    prices = [d["close"] for d in history]

    for i, day in enumerate(history):
        price = day["close"]
        triggered = False

        if request.strategy_type == "price_drop":
            threshold = request.trigger_condition.get("drop_pct", 5) / 100
            if i > 0 and (prices[i-1] - price) / prices[i-1] >= threshold:
                triggered = True

        elif request.strategy_type == "sip":
            interval = request.trigger_condition.get("interval_days", 30)
            if i % interval == 0:
                triggered = True

        if triggered and capital > 1000:
            trade_capital = capital * (request.quantity_pct / 100)
            qty = int(trade_capital / price)
            if qty > 0:
                cost = qty * price * (1 + FRICTION)
                capital -= cost
                position += qty
                trades.append({"date": day["date"], "action": "BUY", "price": price,
                               "qty": qty, "capital_after": round(capital, 2)})

    final_value = capital + (position * prices[-1] * (1 - FRICTION))
    total_return = ((final_value - request.starting_capital) / request.starting_capital) * 100

    buy_trades = [t for t in trades if t["action"] == "BUY"]
    profitable = sum(1 for t in buy_trades if prices[-1] > t["price"])
    win_rate = (profitable / len(buy_trades) * 100) if buy_trades else 0

    price_changes = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    drawdowns = []
    peak = request.starting_capital
    running_value = request.starting_capital
    for change in price_changes:
        running_value += change
        if running_value > peak:
            peak = running_value
        drawdown = (peak - running_value) / peak * 100
        drawdowns.append(drawdown)
    max_drawdown = round(max(drawdowns) if drawdowns else 0, 2)

    return {
        "symbol": request.symbol,
        "period": request.period,
        "strategy": request.strategy_type,
        "starting_capital": request.starting_capital,
        "final_value": round(final_value, 2),
        "total_return_pct": round(total_return, 2),
        "total_trades": len(trades),
        "win_rate_pct": round(win_rate, 2),
        "max_drawdown_pct": max_drawdown,
        "trade_log": trades[:20],
        "disclaimer": DISCLAIMER,
    }
