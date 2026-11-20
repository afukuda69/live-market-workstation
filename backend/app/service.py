"""MarketService: the single ingest path.

    source message -> normalise -> store observation (dedupe)
                   -> CandleEngine (persist candle + revision) -> broadcast

Every feed (Yahoo live, history backfill, mock) goes through these methods,
so de-duplication, revision and completeness rules are applied identically.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from .aggregator import Bar, CandleEngine, Change
from .config import Settings
from .hub import Hub
from .normalize import RejectedObservation, yahoo_to_tick
from .store import SqliteCandleBook, Store

log = logging.getLogger("market")

SOURCE_LABELS = {
    "yahoo": "Yahoo Finance streamer (via yfinance)",
    "mock": "SIMULATED mock feed (not market data)",
    "off": "No live feed (stored data only)",
}


def now_ms() -> int:
    return int(time.time() * 1000)


class FeedStatus:
    """What the UI shows: source, state, data age inputs, and counters."""

    def __init__(self, mode: str, symbols: list[str], stale_after_s: float):
        self.mode = mode
        self.source_label = SOURCE_LABELS.get(mode, mode)
        self.state = "stopped" if mode == "off" else "starting"
        self.state_since_ms = now_ms()
        self.detail = ""
        self.fault_injected = False
        self.reconnect_attempts = 0
        self.last_message_received_ms: Optional[int] = None
        self.stale_after_s = stale_after_s
        self.symbols = {s: {"last_source_ms": None, "last_received_ms": None, "last_price": None, "ticks": 0}
                        for s in symbols}

    def snapshot(self, counters: dict) -> dict:
        return {
            "mode": self.mode,
            "source_label": self.source_label,
            "state": self.state,
            "state_since_ms": self.state_since_ms,
            "detail": self.detail,
            "fault_injected": self.fault_injected,
            "reconnect_attempts": self.reconnect_attempts,
            "last_message_received_ms": self.last_message_received_ms,
            "stale_after_s": self.stale_after_s,
            "server_time_ms": now_ms(),
            "symbols": self.symbols,
            "counters": counters,
        }


class MarketService:
    def __init__(self, settings: Settings, store: Store, hub: Hub):
        self.settings = settings
        self.store = store
        self.hub = hub
        self.engine = CandleEngine(
            SqliteCandleBook(store),
            interval_ms=settings.interval_ms,
            interval_label=settings.interval_label,
            grace_ms=int(settings.finalize_grace_seconds * 1000),
        )
        self.status = FeedStatus(settings.feed_mode, settings.symbols, settings.stale_after_seconds)
        self.counters = {"duplicates": 0, "rejected": 0}
        # async callbacks run after every candle change (Part 4.2: indicators + paper trading)
        self.listeners: list = []
        self._last_reject_event_ms = 0

    # ---- status ----------------------------------------------------------
    def all_counters(self) -> dict:
        return {**self.engine.counters, **self.counters}

    def status_snapshot(self) -> dict:
        return self.status.snapshot(self.all_counters())

    async def set_state(self, state: str, detail: str = "", source: Optional[str] = None) -> None:
        changed = state != self.status.state or detail != self.status.detail
        if state != self.status.state:
            self.status.state_since_ms = now_ms()
        self.status.state = state
        self.status.detail = detail
        if changed:
            ev = self.store.record_event(now_ms(), source or self.status.mode, state, detail)
            log.info("feed %s: %s %s", ev["source"], state, detail)
            await self.hub.broadcast({"type": "event", "event": ev})
            await self.hub.broadcast({"type": "status", "status": self.status_snapshot()})

    # ---- ingest ----------------------------------------------------------
    async def ingest_yahoo_message(self, msg: dict[str, Any], source: str = "yahoo-ws",
                                   received_ms: Optional[int] = None) -> Optional[Change]:
        received = received_ms if received_ms is not None else now_ms()
        self.status.last_message_received_ms = received
        try:
            tick = yahoo_to_tick(msg, received)
        except RejectedObservation as e:
            self.counters["rejected"] += 1
            if received - self._last_reject_event_ms > 10_000:  # rate-limit the log
                self._last_reject_event_ms = received
                ev = self.store.record_event(received, source, "rejected", str(e))
                await self.hub.broadcast({"type": "event", "event": ev})
            return None

        sym = self.status.symbols.setdefault(
            tick.symbol, {"last_source_ms": None, "last_received_ms": None, "last_price": None, "ticks": 0})
        sym["ticks"] += 1
        sym["last_received_ms"] = received
        if sym["last_source_ms"] is None or tick.source_time_ms >= sym["last_source_ms"]:
            sym["last_source_ms"] = tick.source_time_ms
            sym["last_price"] = tick.price

        if self.status.state == "stale":
            await self.set_state("live", "messages resumed")

        res = self.store.insert_tick(source, tick, msg)
        if res.duplicate:
            self.counters["duplicates"] += 1
            return None

        change = self.engine.apply_tick(tick, res.observation_id)
        await self.hub.broadcast(
            {"type": "tick", "symbol": tick.symbol, "price": tick.price,
             "source_time_ms": tick.source_time_ms, "received_ms": received},
            symbol=tick.symbol,
        )
        await self._broadcast_change(change)
        return change

    async def ingest_bar(self, bar: Bar, payload: dict, source: str = "yahoo-history") -> Optional[Change]:
        res = self.store.insert_bar(source, self.settings.interval_label, bar, payload)
        if res.duplicate:
            self.counters["duplicates"] += 1
            return None
        change = self.engine.apply_bar(bar, res.observation_id)
        if change:
            await self._broadcast_change(change)
        return change

    async def _broadcast_change(self, change: Change) -> None:
        await self.hub.broadcast(
            {"type": "candle", "reason": change.reason, "material": change.material,
             "candle": change.candle.to_wire()},
            symbol=change.candle.symbol,
        )
        for listener in self.listeners:
            try:
                await listener(change)
            except Exception:
                log.exception("candle listener failed")

    # ---- background loops ------------------------------------------------
    async def housekeeping_loop(self) -> None:
        """Every second: finalise due candles, detect a silent feed, push status."""
        tick_n = 0
        while True:
            try:
                for ch in self.engine.finalize_due(now_ms()):
                    await self._broadcast_change(ch)
                st = self.status
                if st.state == "live" and st.last_message_received_ms is not None:
                    silent_s = (now_ms() - st.last_message_received_ms) / 1000
                    if silent_s > st.stale_after_s:
                        await self.set_state("stale", f"no messages for {int(silent_s)}s "
                                                      "(market may be closed or feed stalled)")
                elif st.state == "live" and st.last_message_received_ms is None:
                    if (now_ms() - st.state_since_ms) / 1000 > st.stale_after_s:
                        await self.set_state("stale", "connected but no messages received yet")
                tick_n += 1
                if tick_n % 2 == 0:
                    await self.hub.broadcast({"type": "status", "status": self.status_snapshot()})
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("housekeeping error")
            await asyncio.sleep(1)
