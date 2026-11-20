"""Simulated account: orders, fills, cash, positions and profit/loss.

Pure logic, no I/O. Used by the backtester and by the live paper-trading
runner (which persists orders/fills in SQLite and rebuilds the account from
them).

Execution and accounting assumptions (docs/design.md §11):
* Market orders only, long-only (no short selling, no leverage, no margin).
* Fill price = reference price adjusted by slippage against the trader:
  buy at ref * (1 + slippage_bps/10000), sell at ref * (1 - slippage_bps/10000).
* Fee = fee_bps/10000 of the filled notional, paid in cash on every fill.
* Fractional quantities are allowed (rounded down to `qty_decimals`).
* Average-cost method: a buy updates the average cost (fees excluded);
  a sell realises (fill price - average cost) * qty.
* realized_pnl and unrealized_pnl are price P&L *before* fees; fees are
  tracked separately. Identity checked in tests:
      equity - initial_cash == realized_pnl + unrealized_pnl - fees
* Unrealized P&L marks the position to a mark price supplied by the caller
  (latest close; in live mode that close may be provisional — the API says so).
* An order that cannot be filled (not enough cash, nothing to sell,
  quantity rounds to zero) is rejected, not partially filled.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class Fill:
    order_id: int
    time_ms: int
    side: str           # 'buy' | 'sell'
    qty: float
    ref_price: float    # price before slippage
    price: float        # executed price
    fee: float
    realized_pnl: float = 0.0  # for sells: (price - avg_cost) * qty

    def notional(self) -> float:
        return self.qty * self.price


@dataclass
class Order:
    id: int
    created_ms: int
    side: str
    qty: Optional[float]        # None = size at fill time (buy: allocation of cash, sell: whole position)
    status: str = "pending"     # pending | filled | rejected
    reason: str = ""            # why the order exists (e.g. 'SMA up-cross @ 12:34')
    reject_reason: str = ""
    fill: Optional[Fill] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class Position:
    qty: float = 0.0
    avg_cost: float = 0.0


@dataclass
class AccountConfig:
    initial_cash: float = 100_000.0
    fee_bps: float = 10.0        # 0.10 % per fill
    slippage_bps: float = 5.0    # 0.05 % against the trader
    allocation: float = 1.0      # fraction of available cash used per buy
    qty_decimals: int = 6


@dataclass
class Account:
    config: AccountConfig = field(default_factory=AccountConfig)
    cash: float = 0.0
    position: Position = field(default_factory=Position)
    realized_pnl: float = 0.0
    fees: float = 0.0
    orders: list[Order] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    _next_id: int = 1

    def __post_init__(self):
        if not self.cash:
            self.cash = self.config.initial_cash

    # ---- prices ------------------------------------------------------------
    def exec_price(self, side: str, ref_price: float) -> float:
        adj = self.config.slippage_bps / 10_000
        return ref_price * (1 + adj) if side == "buy" else ref_price * (1 - adj)

    def _round_qty(self, q: float) -> float:
        f = 10 ** self.config.qty_decimals
        return math.floor(q * f + 1e-9) / f

    def buy_qty_for(self, ref_price: float) -> float:
        """Largest quantity affordable with `allocation` of cash, fees included."""
        price = self.exec_price("buy", ref_price)
        budget = self.cash * self.config.allocation
        return self._round_qty(budget / (price * (1 + self.config.fee_bps / 10_000)))

    # ---- orders -----------------------------------------------------------
    def new_order(self, created_ms: int, side: str, qty: Optional[float] = None, reason: str = "") -> Order:
        o = Order(self._next_id, created_ms, side, qty, reason=reason)
        self._next_id += 1
        self.orders.append(o)
        return o

    def fill_order(self, order: Order, time_ms: int, ref_price: float) -> Order:
        if order.status != "pending":
            raise ValueError(f"order {order.id} is {order.status}")
        if order.qty:
            qty = self._round_qty(order.qty)
        elif order.side == "buy":
            qty = self.buy_qty_for(ref_price)
        else:
            qty = self.position.qty
        price = self.exec_price(order.side, ref_price)
        if qty <= 0:
            return self._reject(order, "quantity rounds to zero")
        notional = qty * price
        fee = notional * self.config.fee_bps / 10_000
        pos = self.position
        if order.side == "buy":
            if notional + fee > self.cash + 1e-9:
                return self._reject(order, f"insufficient cash: need {notional + fee:.2f}, have {self.cash:.2f}")
        elif order.side == "sell":
            if qty > pos.qty + 1e-12:
                return self._reject(order, f"cannot sell {qty}, holding {pos.qty} (long-only)")
        else:
            raise ValueError(f"unknown side {order.side}")
        f = self.apply_fill(Fill(order.id, time_ms, order.side, qty, ref_price, price, fee))
        order.qty = qty
        order.status = "filled"
        order.fill = f
        return order

    def apply_fill(self, f: Fill) -> Fill:
        """Update cash, position, P&L and fees for one fill. Also used to
        rebuild a persisted account from its stored fills."""
        pos = self.position
        notional = f.qty * f.price
        if f.side == "buy":
            new_qty = pos.qty + f.qty
            pos.avg_cost = (pos.qty * pos.avg_cost + f.qty * f.price) / new_qty
            pos.qty = new_qty
            self.cash -= notional + f.fee
            f.realized_pnl = 0.0
        else:
            f.realized_pnl = (f.price - pos.avg_cost) * f.qty
            remaining = pos.qty - f.qty
            pos.qty = self._round_qty(remaining) if remaining > 1e-12 else 0.0
            if pos.qty == 0:
                pos.avg_cost = 0.0
            self.cash += notional - f.fee
            self.realized_pnl += f.realized_pnl
        self.fees += f.fee
        self.fills.append(f)
        return f

    def _reject(self, order: Order, why: str) -> Order:
        order.status = "rejected"
        order.reject_reason = why
        return order

    # ---- valuation ----------------------------------------------------------
    def unrealized_pnl(self, mark: Optional[float]) -> float:
        if mark is None or self.position.qty == 0:
            return 0.0
        return (mark - self.position.avg_cost) * self.position.qty

    def equity(self, mark: Optional[float]) -> float:
        value = self.position.qty * (mark if mark is not None else self.position.avg_cost)
        return self.cash + value

    def snapshot(self, mark: Optional[float]) -> dict:
        eq = self.equity(mark)
        return {
            "initial_cash": self.config.initial_cash,
            "cash": self.cash,
            "position_qty": self.position.qty,
            "avg_cost": self.position.avg_cost,
            "mark_price": mark,
            "market_value": self.position.qty * (mark or 0.0),
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl(mark),
            "fees": self.fees,
            "equity": eq,
            "total_return_pct": (eq / self.config.initial_cash - 1) * 100,
            "fills": len(self.fills),
        }
