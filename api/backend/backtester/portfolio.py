"""
Portfolio simulator and Trade dataclass for the backtester.

Pure Python — no database, no Redis. The simulator is fed one trading day
at a time via process_day() in chronological order. It models entry/exit
execution at next-day open prices, intraday stop-loss/take-profit fills,
and realistic Zerodha-style costs (₹20 brokerage, 0.05% slippage, 0.1% STT
on sell).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional


BROKERAGE_PER_ORDER = 20.0  # flat ₹20 per buy or sell (Zerodha delivery)
SLIPPAGE_PCT = 0.0005  # 0.05% of order value
STT_SELL_PCT = 0.001  # 0.1% on sell side only


@dataclass
class Trade:
    trade_id: int
    symbol: str
    entry_date: Optional[date]
    entry_price: Optional[float]
    quantity: int
    position_size_inr: float
    brokerage: float = 0.0
    slippage: float = 0.0
    stt_buy: float = 0.0
    exit_date: Optional[date] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    gross_pnl: Optional[float] = None
    net_pnl: Optional[float] = None
    return_pct: Optional[float] = None
    holding_days: Optional[int] = None
    skipped: bool = False
    skip_reason: Optional[str] = None
    # Internal — peak price since entry, for trailing stops if ever needed
    peak_price: Optional[float] = field(default=None, repr=False)
    brokerage_sell: float = 0.0
    slippage_sell: float = 0.0
    stt_sell: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("peak_price", None)
        for k in ("entry_date", "exit_date"):
            v = d.get(k)
            if v is not None:
                d[k] = v.isoformat() if hasattr(v, "isoformat") else str(v)
        return d


@dataclass
class PortfolioSnapshot:
    date: date
    cash: float
    holdings_value: float
    total_value: float
    open_positions: int

    def to_dict(self) -> dict:
        return {
            "date": self.date.isoformat() if hasattr(self.date, "isoformat") else str(self.date),
            "cash": round(self.cash, 2),
            "holdings_value": round(self.holdings_value, 2),
            "total_value": round(self.total_value, 2),
            "open_positions": self.open_positions,
        }


class PortfolioSimulator:
    """Stateful day-by-day portfolio simulator."""

    def __init__(
        self,
        starting_capital: float,
        symbol: str,
        position_size_inr: Optional[float] = None,
        position_size_pct: Optional[float] = None,
        max_positions: int = 10,
        allow_averaging: bool = True,
        stop_loss_pct: Optional[float] = None,
        take_profit_pct: Optional[float] = None,
    ):
        if position_size_inr is None and position_size_pct is None:
            raise ValueError("Either position_size_inr or position_size_pct must be set")
        self.symbol = symbol
        self.starting_capital = float(starting_capital)
        self.cash = float(starting_capital)
        self.position_size_inr = position_size_inr
        self.position_size_pct = position_size_pct
        self.max_positions = max_positions
        self.allow_averaging = allow_averaging
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct

        self._open_trades: list[Trade] = []
        self._closed_trades: list[Trade] = []
        self._snapshots: list[PortfolioSnapshot] = []
        self._next_trade_id = 1
        # Pending entry: a signal fired yesterday → execute at today's open
        self._pending_entry: bool = False

    # ------------------------------------------------------------------
    # Sizing
    # ------------------------------------------------------------------
    def _size_for_trade(self, current_total_value: float) -> float:
        if self.position_size_inr is not None:
            return float(self.position_size_inr)
        return current_total_value * (float(self.position_size_pct) / 100.0)

    # ------------------------------------------------------------------
    # Core day loop
    # ------------------------------------------------------------------
    def process_day(
        self,
        d: date,
        open_price: float,
        high_price: float,
        low_price: float,
        close_price: float,
        entry_signal_today: bool,
        exit_signal_today: bool,
    ) -> PortfolioSnapshot:
        """
        Process a single trading day.

        entry_signal_today, exit_signal_today are the signals that fired on
        THIS day's close. They are not acted on until tomorrow's open.
        Pending entries from YESTERDAY get filled at today's open.
        Intraday stop-loss / take-profit are checked against today's high/low.
        """
        # 1. Fill any pending entry queued by yesterday's signal
        if self._pending_entry:
            self._execute_entry(d, open_price)
            self._pending_entry = False

        # 2. Intraday stop / target / pending exit fills (at next-day open or
        #    at the stop/target trigger price). Pending exits queued by
        #    yesterday's exit signal fill at today's open.
        self._fill_pending_exits(d, open_price)

        for trade in list(self._open_trades):
            # Update peak for any future trailing-stop logic
            if trade.peak_price is None or high_price > trade.peak_price:
                trade.peak_price = high_price

            # Stop-loss takes precedence over take-profit when both hit same day
            if self.stop_loss_pct is not None:
                stop_price = trade.entry_price * (1 - self.stop_loss_pct / 100.0)
                if low_price <= stop_price:
                    self._close_trade(trade, d, stop_price, "stop_loss")
                    continue

            if self.take_profit_pct is not None:
                tgt_price = trade.entry_price * (1 + self.take_profit_pct / 100.0)
                if high_price >= tgt_price:
                    self._close_trade(trade, d, tgt_price, "take_profit")
                    continue

        # 3. Queue exit for next day's open if exit signal fired today
        if exit_signal_today and self._open_trades:
            for trade in self._open_trades:
                if trade.exit_reason is None:  # mark for tomorrow's open
                    trade.exit_reason = "_pending_exit_signal"

        # 4. Queue entry for next day's open if entry signal fired today
        if entry_signal_today:
            self._pending_entry = True

        # 5. Mark to market
        holdings_value = sum(t.quantity * close_price for t in self._open_trades)
        total_value = self.cash + holdings_value
        snap = PortfolioSnapshot(
            date=d,
            cash=self.cash,
            holdings_value=holdings_value,
            total_value=total_value,
            open_positions=len(self._open_trades),
        )
        self._snapshots.append(snap)
        return snap

    # ------------------------------------------------------------------
    # Entry / exit execution
    # ------------------------------------------------------------------
    def _execute_entry(self, d: date, open_price: float) -> None:
        if open_price is None or open_price <= 0:
            return
        if len(self._open_trades) >= self.max_positions:
            self._closed_trades.append(Trade(
                trade_id=self._next_trade_id,
                symbol=self.symbol,
                entry_date=None,
                entry_price=None,
                quantity=0,
                position_size_inr=0.0,
                skipped=True,
                skip_reason="max_positions_reached",
            ))
            self._next_trade_id += 1
            return

        # Disallow averaging into the same name when configured
        if not self.allow_averaging and self._open_trades:
            self._closed_trades.append(Trade(
                trade_id=self._next_trade_id,
                symbol=self.symbol,
                entry_date=None,
                entry_price=None,
                quantity=0,
                position_size_inr=0.0,
                skipped=True,
                skip_reason="averaging_disabled",
            ))
            self._next_trade_id += 1
            return

        target_value = self._size_for_trade(self.cash + sum(t.quantity * open_price for t in self._open_trades))
        qty = int(target_value // open_price)
        if qty <= 0:
            self._closed_trades.append(Trade(
                trade_id=self._next_trade_id,
                symbol=self.symbol,
                entry_date=d,
                entry_price=open_price,
                quantity=0,
                position_size_inr=target_value,
                skipped=True,
                skip_reason="position_size_below_one_share",
            ))
            self._next_trade_id += 1
            return

        gross = qty * open_price
        slippage = gross * SLIPPAGE_PCT
        brokerage = BROKERAGE_PER_ORDER
        total_cost = gross + slippage + brokerage

        if total_cost > self.cash:
            # Try to scale down to whatever cash allows
            affordable_qty = int((self.cash - brokerage) / (open_price * (1 + SLIPPAGE_PCT)))
            if affordable_qty <= 0:
                self._closed_trades.append(Trade(
                    trade_id=self._next_trade_id,
                    symbol=self.symbol,
                    entry_date=d,
                    entry_price=open_price,
                    quantity=0,
                    position_size_inr=target_value,
                    skipped=True,
                    skip_reason="insufficient_cash",
                ))
                self._next_trade_id += 1
                return
            qty = affordable_qty
            gross = qty * open_price
            slippage = gross * SLIPPAGE_PCT
            total_cost = gross + slippage + brokerage

        self.cash -= total_cost
        trade = Trade(
            trade_id=self._next_trade_id,
            symbol=self.symbol,
            entry_date=d,
            entry_price=float(open_price),
            quantity=qty,
            position_size_inr=float(gross),
            brokerage=brokerage,
            slippage=slippage,
            stt_buy=0.0,
            peak_price=float(open_price),
        )
        self._next_trade_id += 1
        self._open_trades.append(trade)

    def _fill_pending_exits(self, d: date, open_price: float) -> None:
        """Realise any trades flagged with '_pending_exit_signal' at today's open."""
        for trade in list(self._open_trades):
            if trade.exit_reason == "_pending_exit_signal":
                self._close_trade(trade, d, open_price, "exit_signal")

    def _close_trade(self, trade: Trade, d: date, exit_price: float,
                     reason: str) -> None:
        gross = trade.quantity * exit_price
        slippage_sell = gross * SLIPPAGE_PCT
        brokerage_sell = BROKERAGE_PER_ORDER
        stt_sell = gross * STT_SELL_PCT
        net_proceeds = gross - slippage_sell - brokerage_sell - stt_sell
        self.cash += net_proceeds

        trade.exit_date = d
        trade.exit_price = float(exit_price)
        trade.exit_reason = reason
        trade.brokerage_sell = brokerage_sell
        trade.slippage_sell = slippage_sell
        trade.stt_sell = stt_sell
        trade.gross_pnl = (exit_price - trade.entry_price) * trade.quantity
        total_costs = (trade.brokerage + trade.slippage + trade.stt_buy
                       + brokerage_sell + slippage_sell + stt_sell)
        trade.net_pnl = trade.gross_pnl - total_costs
        trade.return_pct = (trade.net_pnl / trade.position_size_inr * 100.0
                             if trade.position_size_inr > 0 else 0.0)
        if trade.entry_date is not None:
            trade.holding_days = (d - trade.entry_date).days

        self._open_trades.remove(trade)
        self._closed_trades.append(trade)

    # ------------------------------------------------------------------
    # End-of-test cleanup
    # ------------------------------------------------------------------
    def close_all_open(self, d: date, close_price: float,
                        reason: str = "end_of_period") -> None:
        for trade in list(self._open_trades):
            self._close_trade(trade, d, close_price, reason)

    def mark_open_at_close(self, d: date, close_price: float) -> None:
        """Leave open trades open but tag them with mark-to-market info."""
        for trade in self._open_trades:
            trade.exit_date = None
            trade.exit_reason = "open"
            trade.holding_days = ((d - trade.entry_date).days
                                   if trade.entry_date else None)
            trade.gross_pnl = (close_price - trade.entry_price) * trade.quantity
            trade.net_pnl = trade.gross_pnl - (trade.brokerage + trade.slippage)
            trade.return_pct = (trade.net_pnl / trade.position_size_inr * 100.0
                                 if trade.position_size_inr > 0 else 0.0)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------
    def get_equity_curve(self) -> list[PortfolioSnapshot]:
        return list(self._snapshots)

    def get_trades(self) -> list[Trade]:
        return list(self._closed_trades) + list(self._open_trades)

    def get_open_trades(self) -> list[Trade]:
        return list(self._open_trades)

    def get_final_cash(self) -> float:
        return self.cash

    def get_final_value(self) -> float:
        if not self._snapshots:
            return self.cash
        return self._snapshots[-1].total_value
