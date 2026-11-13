"""One-shot history backfill from Yahoo Finance (yfinance `Ticker.history`).

Fills the chart with today's 1-minute bars on startup so the dashboard is
not empty before live ticks arrive. Each bar is stored as an observation
(kind='bar', source='yahoo-history') and applied through the same engine.
"""
from __future__ import annotations

import asyncio
import logging

from ..aggregator import Bar
from ..service import MarketService, now_ms

log = logging.getLogger("market.history")

SOURCE = "yahoo-history"


def _fetch(symbol: str):
    import yfinance as yf  # imported lazily so tests do not need network

    return yf.Ticker(symbol).history(period="1d", interval="1m", prepost=True, auto_adjust=False)


async def backfill(service: MarketService, symbols: list[str], fetch=_fetch) -> dict:
    results = {}
    if service.settings.interval_seconds != 60:
        service.store.record_event(now_ms(), SOURCE, "history_skipped", "backfill supports 1m candles only")
        return results
    for symbol in symbols:
        try:
            df = await asyncio.to_thread(fetch, symbol)
        except Exception as e:
            ev = service.store.record_event(now_ms(), SOURCE, "history_unavailable", f"{symbol}: {e}")
            await service.hub.broadcast({"type": "event", "event": ev})
            results[symbol] = 0
            continue
        if df is None or len(df) == 0:
            ev = service.store.record_event(now_ms(), SOURCE, "history_unavailable",
                                            f"{symbol}: Yahoo returned no 1m bars")
            await service.hub.broadcast({"type": "event", "event": ev})
            results[symbol] = 0
            continue
        received = now_ms()
        n = 0
        for ts, row in df.iterrows():
            start = int(ts.timestamp() * 1000)
            bar = Bar(
                symbol=symbol, bucket_start_ms=start,
                open=round(float(row["Open"]), 6), high=round(float(row["High"]), 6),
                low=round(float(row["Low"]), 6), close=round(float(row["Close"]), 6),
                volume=int(row["Volume"]) if row["Volume"] == row["Volume"] else 0,
                is_complete=start + service.settings.interval_ms <= received,
                received_ms=received,
            )
            payload = {"ts": ts.isoformat(), "Open": bar.open, "High": bar.high, "Low": bar.low,
                       "Close": bar.close, "Volume": bar.volume}
            await service.ingest_bar(bar, payload, SOURCE)
            n += 1
        ev = service.store.record_event(now_ms(), SOURCE, "history_loaded", f"{symbol}: {n} bars")
        await service.hub.broadcast({"type": "event", "event": ev})
        results[symbol] = n
    return results
