"""SIMULATED feed for development, tests and demos without network access.

It produces messages with the same fields as decoded Yahoo frames and sends
them through the same ingest path. Its source is recorded as 'mock' and the
UI labels it SIMULATED, so it can never be mistaken for market data.
It deliberately re-sends some messages (duplicates) and delays others
(out-of-order) so those code paths are visibly exercised.
"""
from __future__ import annotations

import asyncio
import random

from ..service import MarketService, now_ms

START_PRICES = {"BTC-USD": 90000.0, "AAPL": 230.0, "SPY": 600.0}


class MockFeed:
    source = "mock"

    def __init__(self, service: MarketService, symbols: list[str], rate_hz: float = 2.0,
                 duplicate_every: int = 25, out_of_order_every: int = 40, seed: int | None = None):
        self.service = service
        self.symbols = symbols
        self.rate_hz = rate_hz
        self.duplicate_every = duplicate_every
        self.out_of_order_every = out_of_order_every
        self.rng = random.Random(seed)
        self.prices = {s: START_PRICES.get(s, 100.0) for s in symbols}
        self.day_volume = {s: 1_000_000 for s in symbols}
        self.n = 0

    def _message(self, symbol: str, t_ms: int) -> dict:
        p = self.prices[symbol] * (1 + self.rng.gauss(0, 0.00012))
        self.prices[symbol] = p
        self.day_volume[symbol] += self.rng.randint(100, 5000)
        return {"id": symbol, "price": round(p, 2), "time": str(t_ms), "price_hint": "2",
                "day_volume": str(self.day_volume[symbol]), "exchange": "SIM", "market_hours": 1}

    async def run(self) -> None:
        svc = self.service
        await svc.set_state("live", "SIMULATED data — not from any market", self.source)
        delayed: list[dict] = []
        while True:
            for symbol in self.symbols:
                self.n += 1
                msg = self._message(symbol, now_ms())
                if self.out_of_order_every and self.n % self.out_of_order_every == 0:
                    delayed.append(msg)  # deliver later => arrives out of order
                    continue
                await svc.ingest_yahoo_message(msg, source=self.source)
                if self.duplicate_every and self.n % self.duplicate_every == 0:
                    await svc.ingest_yahoo_message(dict(msg), source=self.source)  # exact re-send
                if delayed and self.n % 7 == 0:
                    await svc.ingest_yahoo_message(delayed.pop(0), source=self.source)
            await asyncio.sleep(1 / self.rate_hz)
