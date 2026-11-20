"""Event-driven backtest of the SMA crossover strategy.

For each historical bar, in time order:

1. If an order was decided at the previous bar's close, fill it at *this*
   bar's open (the first price observable after the decision).
2. The bar closes: update the SMAs with this close only, detect a crossover,
   and if the strategy acts, create an order to be filled at the next open.
3. Record equity marked at this bar's close.

So the decision at bar i uses bars 0..i, and its execution uses bar i+1's
open — never anything later. tests/test_backtest.py checks this by
truncating the data and confirming earlier decisions do not change.
A signal on the final bar has no next open; it is reported as unexecuted.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional, Sequence

from .accounting import Account, AccountConfig
from .strategy import SmaCrossStrategy, decide


@dataclass(frozen=True)
class HistBar:
    time_ms: int   # bar start
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class BacktestResult:
    params: dict
    bars: int
    start_ms: Optional[int]
    end_ms: Optional[int]
    metrics: dict
    trades: list[dict]          # fills, in order
    round_trips: list[dict]
    signals: list[dict]         # every crossover and what was done
    equity: list[dict]          # per bar: time_ms, equity, close, qty
    indicators: list[dict]      # per bar: time_ms, fast, slow
    ohlc: list[list] = field(default_factory=list)  # per bar: [time_ms, open, high, low, close, volume]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def max_drawdown_pct(values: Sequence[float]) -> float:
    peak, mdd = None, 0.0
    for v in values:
        peak = v if peak is None or v > peak else peak
        if peak and peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return mdd * 100


def run_backtest(bars: Sequence[HistBar], fast_n: int, slow_n: int,
                 config: Optional[AccountConfig] = None) -> BacktestResult:
    cfg = config or AccountConfig()
    acct = Account(cfg)
    strat = SmaCrossStrategy(fast_n, slow_n)
    pending = None
    equity, indicators, signals, notes = [], [], [], []
    bars = sorted(bars, key=lambda b: b.time_ms)
    for i, bar in enumerate(bars):
        if pending is not None:
            acct.fill_order(pending, bar.time_ms, bar.open)
            if pending.status == "rejected":
                notes.append(f"order {pending.id} rejected: {pending.reject_reason}")
            pending = None
        step = strat.on_bar_close(bar.close)
        indicators.append({"time_ms": bar.time_ms, "fast": step.fast, "slow": step.slow})
        if step.signal:
            side, why = decide(step.signal, acct.position.qty)
            rec = {"time_ms": bar.time_ms, "signal": step.signal, "fast": step.fast, "slow": step.slow,
                   "close": bar.close, "action": side or "skip", "note": why}
            if side:
                pending = acct.new_order(bar.time_ms, side, None, why)
                rec["order_id"] = pending.id
            signals.append(rec)
        equity.append({"time_ms": bar.time_ms, "equity": acct.equity(bar.close), "close": bar.close,
                       "qty": acct.position.qty, "cash": acct.cash})
    if pending is not None:
        notes.append(f"signal on the last bar ({pending.side}) was not executed: no later bar to fill at")

    last_close = bars[-1].close if bars else None
    trades = [{**asdict(f), "notional": f.qty * f.price} for f in acct.fills]
    round_trips = _round_trips(acct.fills)
    snap = acct.snapshot(last_close)
    bh, bh_curve = _buy_and_hold(bars, slow_n, cfg)
    for e, v in zip(equity, bh_curve):
        e["buy_hold_equity"] = v
    eq_values = [e["equity"] for e in equity]
    wins = [r for r in round_trips if r["return_pct"] > 0]
    metrics = {
        **snap,
        "bars": len(bars),
        "warmup_bars": slow_n - 1,
        "max_drawdown_pct": max_drawdown_pct(eq_values),
        "round_trips": len(round_trips),
        "win_rate_pct": (len(wins) / len(round_trips) * 100) if round_trips else None,
        "avg_trade_return_pct": (sum(r["return_pct"] for r in round_trips) / len(round_trips)) if round_trips else None,
        "exposure_pct": (sum(1 for e in equity if e["qty"] > 0) / len(equity) * 100) if equity else 0.0,
        "signals": len(signals),
        "buy_and_hold_return_pct": bh,
        "open_position_at_end": acct.position.qty > 0,
        "slippage_cost": sum(abs(f.price - f.ref_price) * f.qty for f in acct.fills),
    }
    if acct.position.qty > 0:
        notes.append("position still open at the end; it is marked to the last close (unrealized)")
    if len(bars) < slow_n + 1:
        notes.append(f"only {len(bars)} bars: not enough for SMA({slow_n}) plus one execution bar")
    return BacktestResult(
        params={"fast": fast_n, "slow": slow_n, **asdict(cfg)},
        bars=len(bars), start_ms=bars[0].time_ms if bars else None, end_ms=bars[-1].time_ms if bars else None,
        metrics=metrics, trades=trades, round_trips=round_trips, signals=signals,
        equity=equity, indicators=indicators,
        ohlc=[[b.time_ms, b.open, b.high, b.low, b.close, b.volume] for b in bars], notes=notes,
    )


def _round_trips(fills) -> list[dict]:
    out, entry = [], None
    for f in fills:
        if f.side == "buy" and entry is None:
            entry = f
        elif f.side == "sell" and entry is not None:
            cost = entry.qty * entry.price + entry.fee
            proceeds = f.qty * f.price - f.fee
            out.append({"entry_ms": entry.time_ms, "exit_ms": f.time_ms, "qty": f.qty,
                        "entry_price": entry.price, "exit_price": f.price,
                        "pnl": proceeds - cost, "return_pct": (proceeds / cost - 1) * 100})
            entry = None
    return out


def _buy_and_hold(bars, slow_n, cfg: AccountConfig):
    """Benchmark: buy at the open of the first bar the strategy could hold
    (index slow_n, right after the first possible signal), same costs, hold to
    the last close. Returns (return %, equity per bar)."""
    if len(bars) <= slow_n:
        return None, []
    a = Account(AccountConfig(**{**asdict(cfg), "allocation": 1.0}))
    o = a.new_order(bars[slow_n].time_ms, "buy")
    a.fill_order(o, bars[slow_n].time_ms, bars[slow_n].open)
    if o.status != "filled":
        return None, []
    curve = [cfg.initial_cash] * slow_n + [a.equity(b.close) for b in bars[slow_n:]]
    return (a.equity(bars[-1].close) / cfg.initial_cash - 1) * 100, curve
