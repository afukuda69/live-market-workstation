"""Live smoke check against the real Yahoo streamer (needs internet).

Connects with the same protocol the app uses, prints each decoded tick with
its source time, receipt time and lag, then exits. Use it to record live
evidence for docs/verification.md. Not part of the automated test suite.

    python -m scripts.live_check                    # BTC-USD, 20 s
    python -m scripts.live_check AAPL SPY --seconds 60
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime

from websockets.asyncio.client import connect

from app.config import Settings
from app.normalize import RejectedObservation, decode_yahoo_frame, yahoo_to_tick


async def main(symbols: list[str], seconds: float) -> int:
    url = Settings().yahoo_ws_url
    print(f"connecting to {url} for {symbols} ({seconds:.0f}s)…")
    n = 0
    deadline = time.time() + seconds
    try:
        async with connect(url, open_timeout=10) as ws:
            await ws.send(json.dumps({"subscribe": symbols}))
            while time.time() < deadline:
                try:
                    frame = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.time()))
                except asyncio.TimeoutError:
                    break
                received = int(time.time() * 1000)
                msg = decode_yahoo_frame(json.loads(frame)["message"])
                try:
                    t = yahoo_to_tick(msg, received)
                except RejectedObservation as e:
                    print("rejected:", e, msg)
                    continue
                n += 1
                print(f"{t.symbol:8} {t.price:>12} source={datetime.fromtimestamp(t.source_time_ms/1000):%H:%M:%S.%f}"
                      f" received={datetime.fromtimestamp(received/1000):%H:%M:%S.%f} lag={received - t.source_time_ms} ms"
                      f" day_volume={t.day_volume}")
    except Exception as e:
        print(f"FEED UNAVAILABLE: {type(e).__name__}: {e}")
        return 2
    print(f"{n} ticks received" + ("" if n else " — market closed for these symbols? try BTC-USD"))
    return 0 if n else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("symbols", nargs="*", default=["BTC-USD"])
    p.add_argument("--seconds", type=float, default=20)
    a = p.parse_args()
    raise SystemExit(asyncio.run(main([s.upper() for s in a.symbols], a.seconds)))
