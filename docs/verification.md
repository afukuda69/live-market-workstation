# Verification — Part 4.1

Three kinds of evidence are kept separate here:

* **Automated, mocked** — pytest and Playwright. They run without network or open markets and show that *our* code behaves as designed. They do not prove how Yahoo behaves.
* **Live-provider observation** — what the real Yahoo streamer did on a given date, recorded by hand (screenshots, `scripts/live_check.py` output, video).
* **Environment observation** — what happened when a network really blocked Yahoo.

## 1. Requirement → evidence map

| Requirement | Automated check (mocked) | Live / manual evidence |
|---|---|---|
| Connect to Yahoo Finance | `test_yahoo_feed.py::test_frames_are_decoded_stored_and_candled` (real protobuf frames, fake socket) | §3 live log + video |
| Updating candlesticks + volume | `e2e/dashboard.spec.js` "streams candles…" | video |
| WebSockets for real-time | `test_api.py::test_websocket_snapshot_and_live_push` | browser DevTools → Network → WS |
| Source, age, feed status visible | e2e "shows source, feed state and data age"; `test_health_config_status` | video |
| Store observations in SQLite | `test_ingest.py::test_observation_keeps_source_and_receipt_times` | `sqlite3` query, §3 |
| Retrieve and replay | `test_replay.py` (4 tests), `test_api.py::test_websocket_replay`, e2e "replay…" | video |
| Observation identity | `test_duplicate_is_stored_once_and_counted`, `test_same_time_different_price_is_not_a_duplicate` | |
| Source and receipt times | `test_observation_keeps_source_and_receipt_times` | |
| Revisions | `test_every_material_change_appends_a_revision`, `test_revised_history_bar_creates_backfill_revision`, `/revisions` endpoint test | |
| Complete vs provisional candle | `test_first_tick_creates_provisional_candle`, `test_finalize_waits_for_grace`, `test_late_tick_revises_complete_candle_and_keeps_it_complete` | legend tag in video |
| Duplicates handled | `test_duplicate_is_stored_once_and_counted`, mock-feed tests, e2e counter test | |
| Out-of-order handled | `test_out_of_order_tick_does_not_overwrite_newer_close`, `test_bucket_is_decided_by_source_time_not_arrival`, `test_out_of_order_and_late_are_recorded` | |
| Unavailable feed | `test_unreachable_server_reports_unavailable_and_retries`, `test_server_closing_connection_is_an_outage`, `test_injected_fault_and_recovery`, `test_silent_live_feed_becomes_stale_then_recovers` | §2 real blocked network; Wi-Fi-off in video |
| Malformed data | `test_malformed_messages_are_rejected_and_logged`, `test_future_timestamp_rejected` | |
| Replay reproduces stored data | `test_replay_reproduces_stored_candles`; `GET /api/replay/verify`; e2e "verify matches" | verify button in video |

## 2. Results recorded so far

**2026-11-12 — automated (development sandbox, Linux, Python 3.13):**
`python -m pytest -q` → **40 passed**. `npx playwright test` (mock feed, Chrome) → **4 passed**.

**2026-11-12 — real network block (environment observation, not injected):**
the development sandbox's proxy refuses connections to Yahoo. Running the
backend in `yahoo` mode there produced, without any code change or fault toggle:

```
yahoo-ws connecting wss://streamer.finance.yahoo.com/?version=2 symbols=BTC-USD,AAPL,SPY
yahoo-history history_unavailable AAPL: Failed to perform, curl: (7) CONNECT tunnel failed, response 403 …
yahoo-ws unavailable InvalidProxyStatus: proxy rejected connection: HTTP 403 — retry #2 in 4s
yahoo-ws unavailable InvalidProxyStatus: proxy rejected connection: HTTP 403 — retry #3 in 8s
yahoo-ws unavailable InvalidProxyStatus: proxy rejected connection: HTTP 403 — retry #4 in 16s
yahoo-history history_unavailable SPY: Yahoo returned no 1m bars
```

`/api/status` reported `"state": "unavailable"`, `reconnect_attempts: 4`. Note
that yfinance's history call returned an *empty* result for SPY rather than an
error — the app logs that as `history_unavailable` too.

## 3. Live Yahoo observations — TO RECORD on your machine

Fill these in when you run against the real feed; keep them honest (date, time, what you saw).

- [ ] `python -m scripts.live_check BTC-USD --seconds 30` output pasted here (ticks, lag in ms, whether `day_volume` is present).
- [ ] Same for an equity during market hours (e.g. `AAPL SPY`), and outside hours (expect 0 ticks).
- [ ] Screenshot: dashboard LIVE with data age < 5 s.
- [ ] Screenshot: Wi-Fi off → UNAVAILABLE banner; Wi-Fi on → LIVE again.
- [ ] Screenshot: AAPL after close → STALE.
- [ ] `sqlite3 backend/data/market.db "select source, count(*), sum(duplicate_count) from observations group by source;"`
- [ ] `sqlite3 backend/data/market.db "select reason, count(*) from candle_revisions group by reason;"` (did real data produce `out_of_order` or `late`?)
- [ ] `/api/replay/verify?symbol=BTC-USD` result on real recorded data.
- [ ] Observed message rate and any gaps for BTC-USD.

## 4. Not verified / limitations of the evidence

* Automated tests use fake WebSocket servers; they cannot show Yahoo's real message rate, field availability or outages.
* Duplicates and out-of-order delivery are *forced* by the mock feed; whether the real Yahoo feed produces them is an open observation (§3).
* Browser tests run headless Chrome on Linux; Safari was not tested.
