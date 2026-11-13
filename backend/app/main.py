"""FastAPI application: REST endpoints, browser WebSocket, and feed lifecycle.

Run from backend/:  uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import Settings
from .db import connect, init_db
from .feeds.history import backfill
from .feeds.mock import MockFeed
from .feeds.replay import rebuild, stream_replay
from .feeds.yahoo_live import YahooLiveFeed
from .hub import Hub
from .service import MarketService, now_ms
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("market.api")

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class FaultRequest(BaseModel):
    enabled: bool


def create_app(settings: Optional[Settings] = None, start_feed: bool = True) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = connect(settings.db_path)
        init_db(conn)
        store = Store(conn)
        hub = Hub()
        service = MarketService(settings, store, hub)
        app.state.settings, app.state.store, app.state.hub, app.state.service = settings, store, hub, service
        app.state.feed = None
        tasks: list[asyncio.Task] = [asyncio.create_task(service.housekeeping_loop(), name="housekeeping")]
        store.record_event(now_ms(), "app", "startup",
                           f"mode={settings.feed_mode} symbols={','.join(settings.symbols)} db={settings.db_path.name}")
        if start_feed:
            if settings.feed_mode == "yahoo":
                feed = YahooLiveFeed(service, settings.symbols, settings.yahoo_ws_url)
                app.state.feed = feed
                tasks.append(asyncio.create_task(feed.run(), name="yahoo-feed"))
                if settings.backfill_history:
                    tasks.append(asyncio.create_task(backfill(service, settings.symbols), name="backfill"))
            elif settings.feed_mode == "mock":
                feed = MockFeed(service, settings.symbols)
                app.state.feed = feed
                tasks.append(asyncio.create_task(feed.run(), name="mock-feed"))
        app.state.tasks = tasks
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            store.record_event(now_ms(), "app", "shutdown", "")
            conn.close()

    app = FastAPI(title="Live Market Workstation", version="4.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"], allow_headers=["*"],
    )

    # ---- REST --------------------------------------------------------------
    @app.get("/api/health")
    def health():
        return {"ok": True, "server_time_ms": now_ms()}

    @app.get("/api/config")
    def get_config():
        s = app.state.settings
        return {"symbols": s.symbols, "interval": s.interval_label, "interval_seconds": s.interval_seconds,
                "feed_mode": s.feed_mode, "finalize_grace_seconds": s.finalize_grace_seconds}

    @app.get("/api/status")
    def get_status():
        return app.state.service.status_snapshot()

    @app.get("/api/candles")
    def get_candles(symbol: str, limit: int = Query(300, ge=1, le=5000), before_ms: Optional[int] = None):
        s = app.state.settings
        candles = app.state.store.candles(symbol.upper(), s.interval_label, limit, before_ms)
        return {"symbol": symbol.upper(), "interval": s.interval_label, "candles": [c.to_wire() for c in candles]}

    @app.get("/api/candles/{symbol}/{bucket_start_ms}/revisions")
    def get_revisions(symbol: str, bucket_start_ms: int):
        s = app.state.settings
        revs = app.state.store.revisions(symbol.upper(), s.interval_label, bucket_start_ms)
        if not revs:
            raise HTTPException(404, "no such candle")
        return {"symbol": symbol.upper(), "bucket_start_ms": bucket_start_ms, "revisions": revs}

    @app.get("/api/observations")
    def get_observations(symbol: str, limit: int = Query(100, ge=1, le=2000)):
        with app.state.store.lock:
            rows = app.state.store.conn.execute(
                "SELECT id, obs_key, kind, source, symbol, source_time_ms, received_time_ms, price, day_volume, "
                "bar_open, bar_high, bar_low, bar_close, bar_volume, duplicate_count "
                "FROM observations WHERE symbol = ? ORDER BY id DESC LIMIT ?",
                (symbol.upper(), limit),
            ).fetchall()
        return {"symbol": symbol.upper(), "observations": [dict(r) for r in rows]}

    @app.get("/api/events")
    def get_events(limit: int = Query(50, ge=1, le=500)):
        return {"events": app.state.store.recent_events(limit)}

    @app.get("/api/stats")
    def get_stats():
        return {"db": app.state.store.stats(), "session": app.state.service.all_counters()}

    @app.get("/api/replay/verify")
    def verify_replay(symbol: str):
        """Rebuild candles from recorded observations and compare with stored candles."""
        s = app.state.settings
        store = app.state.store
        # Bring stored candles up to date first (the housekeeping loop does the
        # same every second) so both sides are compared at the same clock.
        clock = now_ms()
        app.state.service.engine.finalize_due(clock)
        book = rebuild(store, s, symbol.upper(), end_clock_ms=clock)
        stored = {c.bucket_start_ms: c for c in store.candles(symbol.upper(), s.interval_label, limit=100000)}
        rebuilt = {c.bucket_start_ms: c for c in book.all(symbol.upper())}
        mismatches = []
        for b in sorted(set(stored) | set(rebuilt)):
            a, r = stored.get(b), rebuilt.get(b)
            if a is None or r is None or a.ohlcv() != r.ohlcv() or a.is_complete != r.is_complete:
                mismatches.append({"bucket_start_ms": b,
                                   "stored": a.to_wire() if a else None,
                                   "replayed": r.to_wire() if r else None})
        return {"symbol": symbol.upper(), "stored": len(stored), "replayed": len(rebuilt),
                "matches": len(stored) - len([m for m in mismatches if m["stored"]]),
                "mismatches": mismatches[:20], "mismatch_count": len(mismatches)}

    @app.post("/api/feed/fault")
    async def set_fault(req: FaultRequest):
        feed = app.state.feed
        if not isinstance(feed, YahooLiveFeed):
            raise HTTPException(409, "fault injection applies to the live Yahoo feed only")
        await feed.set_fault(req.enabled)
        return {"fault_injected": req.enabled}

    # ---- WebSocket ---------------------------------------------------------
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        hub: Hub = app.state.hub
        service: MarketService = app.state.service
        s: Settings = app.state.settings
        client = hub.add(ws)
        await hub.send(client, {"type": "hello", "symbols": s.symbols, "interval": s.interval_label,
                                "interval_seconds": s.interval_seconds, "status": service.status_snapshot(),
                                "events": app.state.store.recent_events(30)})
        try:
            while True:
                try:
                    msg = json.loads(await ws.receive_text())
                except json.JSONDecodeError:
                    await hub.send(client, {"type": "error", "error": "invalid JSON"})
                    continue
                kind = msg.get("type")
                if kind == "subscribe":
                    symbol = str(msg.get("symbol", "")).upper()
                    if symbol not in s.symbols:
                        await hub.send(client, {"type": "error", "error": f"unknown symbol {symbol}"})
                        continue
                    client.symbol = symbol
                    candles = app.state.store.candles(symbol, s.interval_label, s.snapshot_candles)
                    await hub.send(client, {"type": "snapshot", "symbol": symbol, "interval": s.interval_label,
                                            "candles": [c.to_wire() for c in candles],
                                            "status": service.status_snapshot()})
                elif kind == "replay_start":
                    symbol = str(msg.get("symbol", client.symbol or "")).upper()
                    if client.replay_task and not client.replay_task.done():
                        client.replay_task.cancel()
                    speed = float(msg.get("speed", 20))

                    async def _send(m, c=client):
                        await hub.send(c, m)

                    client.replay_task = asyncio.create_task(
                        stream_replay(app.state.store, s, symbol, _send,
                                      from_ms=msg.get("from_ms"), to_ms=msg.get("to_ms"), speed=speed))
                elif kind == "replay_stop":
                    if client.replay_task and not client.replay_task.done():
                        client.replay_task.cancel()
                    await hub.send(client, {"type": "replay_stopped"})
                elif kind == "ping":
                    await hub.send(client, {"type": "pong", "server_time_ms": now_ms()})
        except WebSocketDisconnect:
            pass
        finally:
            hub.remove(client)

    if FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return app


app = create_app()
