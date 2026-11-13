"""Replay recorded observations from SQLite.

Replay reads observations in the order they were received and rebuilds
candles with the same CandleEngine used live, but in memory
(MemoryCandleBook), so replaying never alters the stored candles. Candle
completion is driven by a simulated clock (each observation's receipt time),
so a replayed candle completes at the same point in the sequence as it did
live, give or take the 1-second housekeeping cadence.
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, Optional

from ..aggregator import Bar, CandleEngine, MemoryCandleBook, Tick
from ..config import Settings
from ..store import Store

Send = Callable[[dict], Awaitable[None]]


def row_to_observation(row):
    if row["kind"] == "tick":
        return Tick(symbol=row["symbol"], source_time_ms=row["source_time_ms"], price=row["price"],
                    day_volume=row["day_volume"], received_ms=row["received_time_ms"])
    return Bar(symbol=row["symbol"], bucket_start_ms=row["source_time_ms"], open=row["bar_open"],
               high=row["bar_high"], low=row["bar_low"], close=row["bar_close"],
               volume=row["bar_volume"], is_complete=_bar_complete(row),
               received_ms=row["received_time_ms"])


def _bar_complete(row) -> bool:
    # obs_key ends with |<0/1> for is_complete (see store.bar_key)
    return row["obs_key"].rsplit("|", 1)[-1] == "1"


def rebuild(store: Store, settings: Settings, symbol: str, from_ms: Optional[int] = None,
            to_ms: Optional[int] = None, end_clock_ms: Optional[int] = None) -> MemoryCandleBook:
    """Synchronous full replay (used by tests and the /api/replay/verify endpoint)."""
    book = MemoryCandleBook()
    engine = CandleEngine(book, settings.interval_ms, settings.interval_label,
                          int(settings.finalize_grace_seconds * 1000))
    for row in store.iter_observations(symbol, from_ms, to_ms):
        obs = row_to_observation(row)
        engine.finalize_due(obs.received_ms)
        if isinstance(obs, Tick):
            engine.apply_tick(obs, row["id"])
        else:
            engine.apply_bar(obs, row["id"])
    engine.finalize_due(end_clock_ms if end_clock_ms is not None else int(time.time() * 1000))
    return book


async def stream_replay(store: Store, settings: Settings, symbol: str, send: Send,
                        from_ms: Optional[int] = None, to_ms: Optional[int] = None,
                        speed: float = 20.0, max_gap_s: float = 1.0) -> dict:
    """Stream a replay to one browser client. speed=20 => 20x real time;
    gaps between observations are capped at max_gap_s so idle periods
    (e.g. a closed market) do not stall the replay."""
    rows = list(store.iter_observations(symbol, from_ms, to_ms))
    book = MemoryCandleBook()
    engine = CandleEngine(book, settings.interval_ms, settings.interval_label,
                          int(settings.finalize_grace_seconds * 1000))
    await send({"type": "replay_started", "symbol": symbol, "observations": len(rows),
                "from_ms": rows[0]["received_time_ms"] if rows else None,
                "to_ms": rows[-1]["received_time_ms"] if rows else None, "speed": speed})
    prev_clock = None
    for row in rows:
        obs = row_to_observation(row)
        clock = obs.received_ms
        if prev_clock is not None and speed > 0:
            await asyncio.sleep(min(max_gap_s, (clock - prev_clock) / 1000 / speed))
        else:
            await asyncio.sleep(0)
        prev_clock = clock
        changes = engine.finalize_due(clock)
        ch = engine.apply_tick(obs, row["id"]) if isinstance(obs, Tick) else engine.apply_bar(obs, row["id"])
        if ch:
            changes.append(ch)
        for c in changes:
            await send({"type": "replay_candle", "reason": c.reason, "candle": c.candle.to_wire(),
                        "replay_clock_ms": clock, "source": row["source"]})
    for c in engine.finalize_due(int(time.time() * 1000)):
        await send({"type": "replay_candle", "reason": c.reason, "candle": c.candle.to_wire(),
                    "replay_clock_ms": prev_clock, "source": "replay"})
    stats = {**engine.counters, "observations": len(rows), "candles": len(book.candles)}
    await send({"type": "replay_finished", "symbol": symbol, "stats": stats})
    return stats
