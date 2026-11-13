# Assignment 4.1 — Market Data and the Dashboard

**Student:** Alessio Fukuda
**Repository:** <https://github.com/afukuda69/live-market-workstation> _(confirm URL)_
**Submitted commit:** `REPLACE_WITH_COMMIT_SHA` (`git rev-parse HEAD`)
**Video (30–60 s):** REPLACE_WITH_VIDEO_LINK _(check it opens in a private/incognito window)_

## Summary of what was completed

A local client-server market dashboard (Vue 3 + Lightweight Charts, FastAPI,
SQLite):

- **Live visualization.** The backend holds a WebSocket connection to Yahoo Finance's streamer (protocol and protobuf schema from `yfinance`) for BTC-USD, AAPL and SPY, builds 1-minute candles from ticks, and pushes every change to the browser over its own WebSocket (`/ws`). The chart shows candlesticks with volume; today's 1-minute history is backfilled at startup. A status bar always shows the **source**, **feed state** (LIVE / STALE / UNAVAILABLE / CONNECTING / BACKEND OFFLINE, SIMULATED in mock mode), **data age** measured from the newest tick's *source timestamp*, and source→backend latency.
- **Persistence and replay.** Every observation is stored once in SQLite with its identity key, source, source time and receipt time, and raw payload. Candles carry `is_complete` and a `revision` number; every material change is appended to `candle_revisions` with a reason. Recorded observations can be replayed into the chart at 5×–100× speed, and `/api/replay/verify` rebuilds all candles from observations and compares them with the stored ones.
- **Duplicate, revised and out-of-order data.** Duplicates are recognised by identity and counted, not re-applied. Out-of-order ticks can extend high/low but never overwrite a newer close. Ticks arriving after a candle completed are applied as visible `late` revisions. Changed history bars become `backfill_revision`s.
- **Unavailable feed.** Connection failures, server disconnects and silent feeds are detected, logged to `feed_events`, shown in the UI with a banner and retry countdown, and recovered from automatically with backoff. A labelled fault toggle can force an outage on demand.

Supporting documents: [README](README.md) (setup, DB initialisation, startup) ·
[design notes: data flow and SQLite schema](docs/design.md) ·
[verification](docs/verification.md) · [evidence log](docs/evidence-log.md) ·
[agent instructions](CLAUDE.md) · [AI use](AI-USE.md).

## Key design decisions

1. **One ingest path for all sources** (`MarketService`): Yahoo live ticks, history bars and the simulated feed are normalised, de-duplicated, stored and turned into candles by the same code. Candle rules live in a pure module (`aggregator.py`) that replay reuses, which is what makes replay-equals-stored checkable.
2. **Own WebSocket client instead of `yfinance.AsyncWebSocket.listen()`**, because that loop reconnects internally and hides outages from the application (evidence log E2).
3. **Source time decides the candle; receipt time is kept for audit and replay order.** Data age uses the source time so a redrawing page cannot look "fresh" on old data.
4. **Completeness by clock + grace (5 s)**, with late ticks applied as visible revisions rather than dropped.
5. **BTC-USD as the default symbol** so live streaming can be shown outside US market hours; equities show STALE when markets are closed.

## Verification results

- Backend: `python -m pytest -q` → **40 passed** (aggregation rules, de-duplication, revisions, out-of-order/late, rejected messages, replay equivalence, Yahoo client against fake servers incl. outage and recovery, REST/WebSocket API).
- Browser: Playwright against the SIMULATED feed → **4 passed** (chart renders and updates; source/state/age visible; symbol switch; replay + verify; duplicate counter).
- CI: GitHub Actions runs both on each push. _Link a green run: REPLACE_WITH_ACTIONS_RUN_URL_
- Real unavailable feed: in a network that blocks Yahoo the app reported `UNAVAILABLE` with the proxy error and increasing retries (verification §2).
- Live Yahoo observations: _fill in from verification §3 — date/time, tick rate, lag, whether real data produced duplicates or out-of-order ticks, replay verify result._

Mocked versus live evidence is separated in [docs/verification.md](docs/verification.md).

## Remaining limitations

- Only 1-minute candles; history backfill covers the current day only.
- Yahoo's streamer is unofficial; volume comes from the cumulative `day_volume` field, so the first candle after startup under-counts volume and instruments without that field show zero volume.
- History bars never replace tick-built candles.
- Completion depends on the backend's clock.
- Browser tests use the simulated feed; real-feed behaviour is shown by manual observation and the video.
