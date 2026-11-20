"""Historical bars for backtests, from three sources:

* 'stored'  — completed 1-minute candles this app recorded live (SQLite `candles`).
* 'yahoo'   — bars downloaded with yfinance `Ticker.history`, cached in
              `history_bars` so a run can be repeated offline.
* 'fixture' — a CSV of real bars committed to the repo
              (backend/data/fixtures/), used by tests and offline demos.

Every source returns only *completed* bars: an incomplete stored candle or a
still-forming last Yahoo bar is dropped, because a decision must not use a
bar that has not finished.
"""
from __future__ import annotations

import asyncio
import csv
import datetime
import time
from pathlib import Path
from typing import Optional

from .backtest import HistBar
from .config import BACKEND_DIR
from .store import Store

FIXTURE_DIR = BACKEND_DIR / "data" / "fixtures"

# yfinance limits on how far back each interval goes
YAHOO_INTERVALS = {
    "1m": ("7d", 60_000),
    "5m": ("60d", 300_000),
    "15m": ("60d", 900_000),
    "1h": ("730d", 3_600_000),
    "1d": ("5y", 86_400_000),
}


def now_ms() -> int:
    return int(time.time() * 1000)


def list_fixtures() -> list[str]:
    return sorted(p.name for p in FIXTURE_DIR.glob("*.csv"))


def load_fixture(name: str) -> list[HistBar]:
    path = (FIXTURE_DIR / name).resolve()
    if path.parent != FIXTURE_DIR.resolve() or not path.exists():
        raise FileNotFoundError(f"unknown fixture {name}")
    with path.open() as f:
        return [HistBar(int(r["time_ms"]), float(r["open"]), float(r["high"]), float(r["low"]),
                        float(r["close"]), float(r.get("volume") or 0)) for r in csv.DictReader(f)]


def load_stored(store: Store, symbol: str, interval: str = "1m") -> list[HistBar]:
    with store.lock:
        rows = store.conn.execute(
            "SELECT bucket_start_ms, open, high, low, close, volume FROM candles "
            "WHERE symbol = ? AND interval = ? AND is_complete = 1 ORDER BY bucket_start_ms",
            (symbol, interval),
        ).fetchall()
    return [HistBar(*tuple(r)) for r in rows]


def _yahoo_fetch(symbol: str, interval: str):
    import yfinance as yf

    period = YAHOO_INTERVALS[interval][0]
    return yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False, prepost=False)


async def refresh_yahoo(store: Store, symbol: str, interval: str, fetch=_yahoo_fetch) -> dict:
    """Download bars and upsert them into history_bars. Returns fetch info;
    never raises for network problems (the cache may still be usable)."""
    if interval not in YAHOO_INTERVALS:
        raise ValueError(f"interval must be one of {list(YAHOO_INTERVALS)}")
    step = YAHOO_INTERVALS[interval][1]
    fetched = now_ms()
    try:
        df = await asyncio.to_thread(fetch, symbol, interval)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "fetched_ms": fetched, "rows": 0}
    if df is None or len(df) == 0:
        return {"ok": False, "error": "Yahoo returned no bars", "fetched_ms": fetched, "rows": 0}
    n, dropped = 0, 0
    with store.lock:
        for ts, row in df.iterrows():
            start = int(ts.timestamp() * 1000)
            if interval != "1d" and start + step > fetched:
                dropped += 1  # still forming
                continue
            if interval == "1d" and ts.date() >= datetime.datetime.now(ts.tz).date():
                dropped += 1
                continue
            vol = row["Volume"]
            store.conn.execute(
                """INSERT INTO history_bars(symbol, interval, time_ms, open, high, low, close, volume, fetched_ms)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(symbol, interval, time_ms) DO UPDATE SET
                     open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
                     volume=excluded.volume, fetched_ms=excluded.fetched_ms""",
                (symbol, interval, start, float(row["Open"]), float(row["High"]), float(row["Low"]),
                 float(row["Close"]), float(vol) if vol == vol else 0.0, fetched),
            )
            n += 1
    return {"ok": True, "error": None, "fetched_ms": fetched, "rows": n, "dropped_incomplete": dropped}


def load_cached_yahoo(store: Store, symbol: str, interval: str,
                      start_ms: Optional[int] = None, end_ms: Optional[int] = None) -> list[HistBar]:
    q = "SELECT time_ms, open, high, low, close, volume FROM history_bars WHERE symbol=? AND interval=?"
    args: list = [symbol, interval]
    if start_ms is not None:
        q += " AND time_ms >= ?"
        args.append(start_ms)
    if end_ms is not None:
        q += " AND time_ms <= ?"
        args.append(end_ms)
    with store.lock:
        rows = store.conn.execute(q + " ORDER BY time_ms", args).fetchall()
    return [HistBar(*tuple(r)) for r in rows]


def describe(bars: list[HistBar], step_ms: Optional[int]) -> dict:
    """Range and gaps of a bar list (a gap = more than 1.5 intervals between bars)."""
    gaps = 0
    largest = 0
    if step_ms and len(bars) > 1:
        for a, b in zip(bars, bars[1:]):
            d = b.time_ms - a.time_ms
            if d > 1.5 * step_ms:
                gaps += 1
                largest = max(largest, d)
    return {"bars": len(bars), "start_ms": bars[0].time_ms if bars else None,
            "end_ms": bars[-1].time_ms if bars else None, "gaps": gaps, "largest_gap_ms": largest}
