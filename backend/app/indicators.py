"""Simple moving averages and crossover detection.

Two implementations of the same SMA are kept on purpose:

* `sma_batch(closes, n)` recomputes every value from a list (used for
  snapshots, backtests and as the check), and
* `IncrementalSMA` keeps a rolling window and updates one bar at a time
  (used by the live strategy runner).

tests/test_indicators.py checks both against pandas' `rolling(n).mean()` and
against each other.

Definitions (docs/design.md §10):
* SMA(n) at bar i = mean of the closes of bars i-n+1 .. i. Undefined (None)
  until n bars exist.
* diff = SMA_fast - SMA_slow. An *up* crossover happens on the bar where diff
  becomes > 0 after the last non-zero diff was < 0; a *down* crossover is the
  mirror. A bar where diff == 0 exactly is not a crossover by itself (the
  previous non-zero side is remembered), so touching without crossing does
  not produce a signal.
* Only completed candles produce *confirmed* values. A value that includes
  the still-forming candle is *provisional* and may change or disappear.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence


def _mean(values: Iterable[float]) -> float:
    # math.fsum: exact rounding, so the result does not depend on summation order
    vals = list(values)
    return math.fsum(vals) / len(vals)


def sma_batch(closes: Sequence[float], n: int) -> list[Optional[float]]:
    if n < 1:
        raise ValueError("SMA window must be >= 1")
    out: list[Optional[float]] = []
    for i in range(len(closes)):
        out.append(None if i + 1 < n else _mean(closes[i + 1 - n: i + 1]))
    return out


class IncrementalSMA:
    def __init__(self, n: int):
        if n < 1:
            raise ValueError("SMA window must be >= 1")
        self.n = n
        self.window: deque[float] = deque(maxlen=n)

    def push(self, close: float) -> Optional[float]:
        """Add one completed bar's close and return the new SMA (None until n bars)."""
        self.window.append(close)
        return self.value

    @property
    def value(self) -> Optional[float]:
        return _mean(self.window) if len(self.window) == self.n else None

    def peek(self, provisional_close: float) -> Optional[float]:
        """SMA *if* the unfinished bar closed at this price. Does not change state."""
        if len(self.window) + 1 < self.n:
            return None
        vals = list(self.window)[-(self.n - 1):] if self.n > 1 else []
        return _mean(vals + [provisional_close])

    def reset(self) -> None:
        self.window.clear()


def sign(x: Optional[float]) -> int:
    if x is None or x == 0:
        return 0
    return 1 if x > 0 else -1


def crossover(last_nonzero_side: int, diff: Optional[float]) -> Optional[str]:
    """'up' / 'down' / None given the side before this bar and this bar's diff."""
    s = sign(diff)
    if s == 0 or last_nonzero_side == 0 or s == last_nonzero_side:
        return None
    return "up" if s > 0 else "down"


@dataclass
class IndicatorPoint:
    time: int               # bucket start, UTC seconds (chart time)
    bucket_start_ms: int
    fast: Optional[float]
    slow: Optional[float]
    signal: Optional[str]   # 'up' | 'down' | None
    provisional: bool       # True if this point uses an unfinished candle

    def to_wire(self) -> dict:
        return {"time": self.time, "bucket_start_ms": self.bucket_start_ms, "fast": self.fast,
                "slow": self.slow, "signal": self.signal, "provisional": self.provisional}


def indicator_series(candles: Sequence, fast_n: int, slow_n: int) -> list[IndicatorPoint]:
    """Batch indicator series for a candle list (oldest first).

    Completed candles produce confirmed points. If the *last* candle is not
    complete, its point is computed from the confirmed history plus its
    current close and marked provisional. (Only the newest candle can be
    incomplete; an incomplete candle elsewhere is treated the same way and
    excluded from later confirmed values, and no provisional point is made
    until the candle before the forming one is complete.)
    """
    if fast_n >= slow_n:
        raise ValueError("fast window must be shorter than slow window")
    complete = [c for c in candles if c.is_complete]
    closes = [c.close for c in complete]
    fast = sma_batch(closes, fast_n)
    slow = sma_batch(closes, slow_n)
    points: list[IndicatorPoint] = []
    side = 0
    for c, f, s in zip(complete, fast, slow):
        diff = None if f is None or s is None else f - s
        sig = crossover(side, diff)
        if sign(diff) != 0:
            side = sign(diff)
        points.append(IndicatorPoint(c.bucket_start_ms // 1000, c.bucket_start_ms, f, s, sig, False))
    last = candles[-1] if candles else None
    # A provisional point is only meaningful if every earlier candle is already
    # complete; while the previous candle is still in its grace period the
    # "window" would skip it, so no provisional value is shown then.
    prev_done = len(candles) < 2 or candles[-2].is_complete
    if last is not None and not last.is_complete and prev_done:
        f_inc, s_inc = IncrementalSMA(fast_n), IncrementalSMA(slow_n)
        for x in closes[-slow_n:]:
            f_inc.push(x)
            s_inc.push(x)
        # IncrementalSMA windows only keep the last n closes; the fast one needs
        # the last fast_n, which the slow_n tail contains.
        f, s = f_inc.peek(last.close), s_inc.peek(last.close)
        diff = None if f is None or s is None else f - s
        points.append(IndicatorPoint(last.bucket_start_ms // 1000, last.bucket_start_ms, f, s,
                                     crossover(side, diff), True))
    return points
