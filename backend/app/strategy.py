"""SMA crossover strategy (long-only), shared by the backtester and the live
paper-trading runner.

Rules:
* On an *up* crossover of a completed candle: if flat -> buy (allocation of
  cash); if already long -> skip.
* On a *down* crossover of a completed candle: if long -> sell the whole
  position; if flat -> skip (no short selling).
* Provisional crossovers (from an unfinished candle) are shown but never
  traded.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .indicators import IncrementalSMA, crossover, sign


@dataclass
class StrategyStep:
    fast: Optional[float]
    slow: Optional[float]
    signal: Optional[str]  # 'up' | 'down' | None


class SmaCrossStrategy:
    def __init__(self, fast_n: int, slow_n: int):
        if not (1 <= fast_n < slow_n):
            raise ValueError("need 1 <= fast < slow")
        self.fast_n, self.slow_n = fast_n, slow_n
        self.fast = IncrementalSMA(fast_n)
        self.slow = IncrementalSMA(slow_n)
        self.side = 0  # last non-zero sign of (fast - slow)

    def on_bar_close(self, close: float) -> StrategyStep:
        """Feed one *completed* bar. Uses only this and earlier closes."""
        f, s = self.fast.push(close), self.slow.push(close)
        diff = None if f is None or s is None else f - s
        sig = crossover(self.side, diff)
        if sign(diff) != 0:
            self.side = sign(diff)
        return StrategyStep(f, s, sig)

    def peek(self, provisional_close: float) -> StrategyStep:
        """What the indicators would show if the unfinished bar closed now."""
        f, s = self.fast.peek(provisional_close), self.slow.peek(provisional_close)
        diff = None if f is None or s is None else f - s
        return StrategyStep(f, s, crossover(self.side, diff))


def decide(signal: Optional[str], position_qty: float) -> tuple[Optional[str], str]:
    """Return (order side or None, explanation)."""
    if signal == "up":
        return ("buy", "SMA up-cross, flat -> buy") if position_qty <= 0 else (None, "SMA up-cross, already long -> skip")
    if signal == "down":
        return ("sell", "SMA down-cross, long -> sell all") if position_qty > 0 else (None, "SMA down-cross, no position (long-only) -> skip")
    return None, ""
