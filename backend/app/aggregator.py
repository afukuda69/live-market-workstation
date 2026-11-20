"""Turns observations (ticks and history bars) into candles.

This module is pure logic: it knows nothing about SQLite, WebSockets or
Yahoo. The same `CandleEngine` runs in two places:

* live ingest, backed by `store.SqliteCandleBook` (persists every change), and
* replay, backed by `MemoryCandleBook` (rebuilds candles from recorded
  observations without touching the stored candles).

Because both paths share this code, a replay of the recorded observations
must reproduce the stored candles — that property is tested in
tests/test_replay.py.

Rules (also documented in docs/design.md):
* bucket = floor(source_time / interval). Source time decides the candle,
  not arrival time.
* high/low take every tick. close comes from the tick with the latest source
  time, open from the tick with the earliest source time, so an out-of-order
  tick cannot overwrite a newer close.
* A candle becomes complete when wall-clock time passes bucket end + grace.
  A tick that arrives after that is applied as a 'late' revision; the candle
  stays complete and its revision number increases.
* Every material change (OHLCV or completeness) increments `revision`.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Protocol


@dataclass(frozen=True)
class Tick:
    symbol: str
    source_time_ms: int
    price: float
    day_volume: Optional[int]
    received_ms: int


@dataclass(frozen=True)
class Bar:
    symbol: str
    bucket_start_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: int
    is_complete: bool
    received_ms: int


@dataclass
class Candle:
    symbol: str
    interval: str
    bucket_start_ms: int
    bucket_end_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    volume_base: int = 0
    dv_min: Optional[int] = None
    dv_max: Optional[int] = None
    first_source_ms: Optional[int] = None
    last_source_ms: Optional[int] = None
    tick_count: int = 0
    is_complete: bool = False
    revision: int = 1
    origin: str = "ticks"
    updated_received_ms: int = 0
    id: Optional[int] = None

    def ohlcv(self) -> tuple:
        return (self.open, self.high, self.low, self.close, self.volume)

    def to_wire(self) -> dict:
        """Shape sent to the browser. `time` is UTC seconds (Lightweight Charts)."""
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "time": self.bucket_start_ms // 1000,
            "bucket_start_ms": self.bucket_start_ms,
            "bucket_end_ms": self.bucket_end_ms,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "is_complete": self.is_complete,
            "revision": self.revision,
            "origin": self.origin,
            "tick_count": self.tick_count,
            "last_source_ms": self.last_source_ms,
            "updated_received_ms": self.updated_received_ms,
        }


@dataclass
class Change:
    candle: Candle
    reason: str  # new | update | out_of_order | late | finalize | backfill | backfill_revision
    material: bool  # True if OHLCV or completeness changed (=> new revision row)


class CandleBook(Protocol):
    def get(self, symbol: str, interval: str, bucket_start_ms: int) -> Optional[Candle]: ...
    def prev_dv_max(self, symbol: str, interval: str, bucket_start_ms: int) -> Optional[int]: ...
    def save(self, change: Change, observation_id: Optional[int], now_ms: int) -> None: ...
    def open_candles(self) -> list[Candle]: ...


def compute_volume(c: Candle, prev_dv_max: Optional[int]) -> int:
    """Per-candle volume from Yahoo's cumulative `day_volume`.

    Yahoo ticks carry the session's running volume, not the size of each trade,
    so a candle's volume is (largest day_volume in the bucket) minus (the
    running total at the end of the previous bucket). If there is no usable
    previous bucket (first candle, or a session reset where the counter went
    down), the smallest day_volume inside the bucket is the baseline.
    Candles that started from a history bar keep the bar's volume and add only
    what arrives afterwards.
    """
    if c.dv_max is None:
        return c.volume_base
    if c.volume_base > 0 or prev_dv_max is None or prev_dv_max > (c.dv_min or 0):
        baseline = c.dv_min
    else:
        baseline = prev_dv_max
    return c.volume_base + max(0, c.dv_max - (baseline or 0))


class CandleEngine:
    def __init__(self, book: CandleBook, interval_ms: int, interval_label: str, grace_ms: int):
        self.book = book
        self.interval_ms = interval_ms
        self.interval = interval_label
        self.grace_ms = grace_ms
        self.counters = {
            "ticks": 0,
            "bars": 0,
            "out_of_order": 0,
            "late": 0,
            "revisions": 0,
            "ignored_bars": 0,
        }

    def bucket_of(self, t_ms: int) -> int:
        return (t_ms // self.interval_ms) * self.interval_ms

    # ---- ticks -----------------------------------------------------------
    def apply_tick(self, tick: Tick, observation_id: Optional[int] = None) -> Change:
        self.counters["ticks"] += 1
        bucket = self.bucket_of(tick.source_time_ms)
        existing = self.book.get(tick.symbol, self.interval, bucket)
        prev_dv = self.book.prev_dv_max(tick.symbol, self.interval, bucket)

        if existing is None:
            c = Candle(
                symbol=tick.symbol,
                interval=self.interval,
                bucket_start_ms=bucket,
                bucket_end_ms=bucket + self.interval_ms,
                open=tick.price, high=tick.price, low=tick.price, close=tick.price,
                dv_min=tick.day_volume, dv_max=tick.day_volume,
                first_source_ms=tick.source_time_ms, last_source_ms=tick.source_time_ms,
                tick_count=1, origin="ticks", updated_received_ms=tick.received_ms,
            )
            c.volume = compute_volume(c, prev_dv)
            change = Change(c, "new", True)
        else:
            before = (existing.ohlcv(), existing.is_complete)
            c = replace(existing)
            out_of_order = c.last_source_ms is not None and tick.source_time_ms < c.last_source_ms
            c.high = max(c.high, tick.price)
            c.low = min(c.low, tick.price)
            if c.last_source_ms is None or tick.source_time_ms >= c.last_source_ms:
                c.close = tick.price
                c.last_source_ms = tick.source_time_ms
            if c.first_source_ms is None or tick.source_time_ms < c.first_source_ms:
                c.open = tick.price
                c.first_source_ms = tick.source_time_ms
            if tick.day_volume is not None:
                c.dv_min = tick.day_volume if c.dv_min is None else min(c.dv_min, tick.day_volume)
                c.dv_max = tick.day_volume if c.dv_max is None else max(c.dv_max, tick.day_volume)
            c.volume = compute_volume(c, prev_dv)
            c.tick_count += 1
            c.updated_received_ms = tick.received_ms
            if existing.is_complete:
                reason = "late"
                self.counters["late"] += 1
            elif out_of_order:
                reason = "out_of_order"
                self.counters["out_of_order"] += 1
            else:
                reason = "update"
            material = (c.ohlcv(), c.is_complete) != before
            if material:
                c.revision = existing.revision + 1
            change = Change(c, reason, material)

        if change.material:
            self.counters["revisions"] += 1
        self.book.save(change, observation_id, tick.received_ms)
        return change

    # ---- history bars ----------------------------------------------------
    def apply_bar(self, bar: Bar, observation_id: Optional[int] = None) -> Optional[Change]:
        """History bars fill gaps. They never overwrite a candle built from live
        ticks (the ticks are the primary record); they can revise an earlier
        history bar, e.g. when Yahoo updates the still-forming last bar."""
        self.counters["bars"] += 1
        existing = self.book.get(bar.symbol, self.interval, bar.bucket_start_ms)
        if existing is None:
            c = Candle(
                symbol=bar.symbol, interval=self.interval,
                bucket_start_ms=bar.bucket_start_ms,
                bucket_end_ms=bar.bucket_start_ms + self.interval_ms,
                open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                volume=bar.volume, volume_base=bar.volume,
                first_source_ms=bar.bucket_start_ms, last_source_ms=bar.bucket_start_ms,
                is_complete=bar.is_complete, origin="history",
                updated_received_ms=bar.received_ms,
            )
            change = Change(c, "backfill", True)
        elif existing.origin == "history":
            c = replace(existing)
            c.open, c.high, c.low, c.close = bar.open, bar.high, bar.low, bar.close
            c.volume_base = bar.volume
            c.dv_min = c.dv_max = None
            c.volume = bar.volume
            c.is_complete = existing.is_complete or bar.is_complete
            c.updated_received_ms = bar.received_ms
            material = (c.ohlcv(), c.is_complete) != (existing.ohlcv(), existing.is_complete)
            if not material:
                return None
            c.revision = existing.revision + 1
            change = Change(c, "backfill_revision", True)
        else:
            self.counters["ignored_bars"] += 1
            return None
        self.counters["revisions"] += 1
        self.book.save(change, observation_id, bar.received_ms)
        return change

    # ---- completion ------------------------------------------------------
    def finalize_due(self, now_ms: int) -> list[Change]:
        changes = []
        # oldest first, so downstream consumers (indicators) see candles complete in order
        for c in sorted(self.book.open_candles(), key=lambda c: (c.symbol, c.bucket_start_ms)):
            if c.bucket_end_ms + self.grace_ms <= now_ms:
                done = replace(c, is_complete=True, revision=c.revision + 1)
                ch = Change(done, "finalize", True)
                self.book.save(ch, None, now_ms)
                self.counters["revisions"] += 1
                changes.append(ch)
        return changes


class MemoryCandleBook:
    """In-memory CandleBook used by replay and unit tests."""

    def __init__(self):
        self.candles: dict[tuple, Candle] = {}
        self.revisions: list[tuple[str, Candle]] = []

    def get(self, symbol, interval, bucket_start_ms):
        c = self.candles.get((symbol, interval, bucket_start_ms))
        return replace(c) if c else None

    def prev_dv_max(self, symbol, interval, bucket_start_ms):
        best_key = None
        for (s, i, b) in self.candles:
            if s == symbol and i == interval and b < bucket_start_ms:
                if best_key is None or b > best_key[2]:
                    best_key = (s, i, b)
        return self.candles[best_key].dv_max if best_key else None

    def save(self, change, observation_id, now_ms):
        c = change.candle
        self.candles[(c.symbol, c.interval, c.bucket_start_ms)] = replace(c)
        if change.material:
            self.revisions.append((change.reason, replace(c)))

    def open_candles(self):
        return [replace(c) for c in self.candles.values() if not c.is_complete]

    def all(self, symbol: Optional[str] = None) -> list[Candle]:
        out = [c for c in self.candles.values() if symbol is None or c.symbol == symbol]
        return sorted(out, key=lambda c: (c.symbol, c.bucket_start_ms))
