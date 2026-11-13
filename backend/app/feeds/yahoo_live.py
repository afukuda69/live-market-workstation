"""Live Yahoo Finance streamer client.

Why not `yfinance.AsyncWebSocket.listen()` directly? Its listen loop catches
connection errors, sleeps and reconnects internally, so the application could
not see (or show) that the feed was down. This client speaks the same protocol
yfinance documents — send {"subscribe": [...]} and re-send it every 15 s,
receive JSON frames whose "message" is a base64 protobuf `PricingData` — and
decodes frames with the protobuf schema shipped in yfinance. Connection
state changes are reported to MarketService, which records them in
feed_events and pushes them to the browser.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable, Optional

from websockets.asyncio.client import connect as ws_connect

from ..normalize import decode_yahoo_frame

log = logging.getLogger("market.yahoo")

HEARTBEAT_SECONDS = 15
MAX_BACKOFF_SECONDS = 30


class InjectedFault(ConnectionError):
    pass


class YahooLiveFeed:
    source = "yahoo-ws"

    def __init__(self, service, symbols: list[str], url: str,
                 connect: Callable = ws_connect, backoff_cap: float = MAX_BACKOFF_SECONDS):
        self.service = service
        self.symbols = symbols
        self.url = url
        self._connect = connect
        self.backoff_cap = backoff_cap
        self._ws = None
        self._wake = asyncio.Event()
        self._stopped = False

    # Fault injection lets the unavailable-feed path be demonstrated on demand
    # (it is labelled as injected in the UI and in feed_events). Pulling the
    # network cable / turning Wi-Fi off exercises the same path for real.
    async def set_fault(self, enabled: bool) -> None:
        self.service.status.fault_injected = enabled
        if enabled and self._ws is not None:
            await self._ws.close()
        self._wake.set()

    async def stop(self) -> None:
        self._stopped = True
        self._wake.set()
        if self._ws is not None:
            await self._ws.close()

    async def _sleep_or_wake(self, seconds: float) -> None:
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _heartbeat(self, ws) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await ws.send(json.dumps({"subscribe": self.symbols}))

    async def run(self) -> None:
        svc = self.service
        attempts = 0
        while not self._stopped:
            await svc.set_state("connecting", f"{self.url} symbols={','.join(self.symbols)}", self.source)
            hb: Optional[asyncio.Task] = None
            try:
                if svc.status.fault_injected:
                    raise InjectedFault("injected fault: simulated outage (toggle off to recover)")
                async with self._connect(self.url, open_timeout=10) as ws:
                    self._ws = ws
                    await ws.send(json.dumps({"subscribe": self.symbols}))
                    attempts = 0
                    svc.status.reconnect_attempts = 0
                    await svc.set_state("live", "connected; waiting for price updates", self.source)
                    hb = asyncio.create_task(self._heartbeat(ws))
                    async for frame in ws:
                        await self._handle_frame(frame)
                if svc.status.fault_injected:
                    raise InjectedFault("injected fault: connection closed by fault toggle")
                raise ConnectionError("server closed the connection")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # network errors, DNS, TLS, closed socket, injected fault
                if self._stopped:
                    break
                attempts += 1
                svc.status.reconnect_attempts = attempts
                delay = min(self.backoff_cap, 2 ** min(attempts, 5))
                await svc.set_state("unavailable", f"{type(e).__name__}: {e} — retry #{attempts} in {delay:.0f}s",
                                    self.source)
                await self._sleep_or_wake(delay)
            finally:
                self._ws = None
                if hb:
                    hb.cancel()
        await svc.set_state("stopped", "feed stopped", self.source)

    async def _handle_frame(self, frame) -> None:
        try:
            data = json.loads(frame)
            msg = decode_yahoo_frame(data.get("message", ""))
        except Exception as e:
            self.service.counters["rejected"] += 1
            log.warning("undecodable frame: %s", e)
            return
        await self.service.ingest_yahoo_message(msg, source=self.source)
