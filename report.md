# Assignment 4.1 — Market Data and the Dashboard

**Student:** Alessio Fukuda
**Repository:** <https://github.com/afukuda69/live-market-workstation> _(confirm URL)_
**Submitted commit:** `REPLACE_WITH_COMMIT_SHA` (`git rev-parse HEAD`)
**Video (30–60 s):** <https://drive.google.com/file/d/1niD55L3WWFwusmE2XaevG6ldWMGernB5/view?usp=drive_link>

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
- Real unavailable feed: in a network that blocks Yahoo the app reported `UNAVAILABLE` with the proxy error and increasing retries (verification §2).
- **Live Yahoo run, 2026-11-12, ~8:13–9:45 PM ET (macOS)** — details in [verification §3](docs/verification.md):
  - Recorded **1,279 live ticks** (BTC-USD 891, SPY 229, AAPL 159) and **1,753 history bars**. BTC-USD source→backend delay averaged **~3.0 s** (min 0.8 s).
  - After US market close, AAPL/SPY ticks carried source timestamps up to ~4.5 hours old; the data-age display showed them as old rather than current, which is the behaviour the age indicator is for.
  - **Real duplicates:** Yahoo re-sent **22 identical ticks**, each stored once and counted. A further **3,480** duplicate history bars came from re-downloading the same day on each backend restart — also recognised and not stored twice.
  - **Real revisions:** 3 `late` revisions (ticks arriving after their candle was marked complete) and 4 `backfill_revision`s (Yahoo changed a history bar). No out-of-order ticks were observed in real data; that rule is covered by automated tests.
  - **Replay check on the real recorded data:** rebuilding all candles from stored observations matched the stored candles exactly — BTC-USD 162/162, AAPL 839/839, SPY 963/963, **0 mismatches**.
  - **Unavailable feed, observed for real three ways:** (1) on first start the feed was `UNAVAILABLE` with `SSLCertVerificationError` until Python's certificates were installed; (2) with Wi-Fi off (9:03–9:11 PM) it reported `gaierror` DNS failures with backoff retries and recovered to LIVE automatically; (3) Yahoo closed the connection 7 times with `keepalive ping timeout`, each shown as UNAVAILABLE and reconnected within ~2–5 s. The demo video also uses the labelled INJECTED outage toggle.

Mocked versus live evidence is separated in [docs/verification.md](docs/verification.md).

## Remaining limitations

- Only 1-minute candles; history backfill covers the current day only.
- Yahoo's streamer is unofficial; volume comes from the cumulative `day_volume` field, so the first candle after startup under-counts volume and instruments without that field show zero volume.
- History bars never replace tick-built candles.
- Completion depends on the backend's clock.
- Browser tests use the simulated feed; real-feed behaviour is shown by manual observation and the video.
- Yahoo's stream drops the connection every few minutes during quiet periods (`keepalive ping timeout`); the app recovers automatically but briefly shows UNAVAILABLE/STALE. A future fix is to tune the WebSocket ping settings.
- The status detail line keeps saying "waiting for price updates" after ticks arrive, and the "Duplicates ignored" counter combines duplicate ticks with re-downloaded history bars. Both are display issues to fix in Part 4.2.
