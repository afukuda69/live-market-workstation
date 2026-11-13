# 60-second demo script (Part 4.1 video)

Record with QuickTime (File → New Screen Recording) or OBS. Run in `yahoo` mode
with BTC-USD selected so data is live at any hour. Start the backend 2–3
minutes earlier so a few complete candles exist.

| Time | Show | Say |
|---|---|---|
| 0–10 s | Dashboard, BTC-USD, green **LIVE**, source label, data age ticking under ~3 s, provisional candle updating | "Live BTC-USD from Yahoo's streamer, through my FastAPI backend over a WebSocket. Source, feed state and data age are always visible." |
| 10–18 s | Hover a past candle (COMPLETE, rev N) vs the current one (PROVISIONAL) | "Hollow dashed candles are still forming; solid ones are complete. Each change is a stored revision." |
| 18–28 s | Terminal: `sqlite3 backend/data/market.db "select source, received_time_ms - source_time_ms, price from observations order by id desc limit 3;"` | "Every observation is stored once with source and receipt time." |
| 28–40 s | Turn Wi-Fi off → red **UNAVAILABLE** banner, retry message, data age growing. Wi-Fi on → LIVE | "When the feed drops, the app says so instead of showing old prices as current." |
| 40–55 s | Click **Replay BTC-USD** (20×) → purple REPLAY banner; Back to live → **Verify replay = stored** ✓ | "Replay rebuilds candles from recorded observations with the same code; verify confirms they match what was stored." |
| 55–60 s | Data-quality panel | "Design decision: one ingest path, so duplicates and out-of-order ticks are handled the same for live, history and replay." |

Backup if Wi-Fi toggling is awkward: use **Simulate feed outage** (shown as INJECTED).
