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

## 3. Live Yahoo observations — 2026-11-12, macOS, ~8:13–9:45 PM ET

Recorded from `backend/data/market.db` and the dashboard after the session.
These are observations of the real provider, not automated tests.

| Observation | Result |
|---|---|
| Live ticks recorded | 1,279 (BTC-USD 891, SPY 229, AAPL 159) from `yahoo-ws` |
| History bars recorded | 1,753 (`yahoo-history`, today's 1-minute bars) |
| BTC-USD source → backend delay | mean ~3.0 s, min 0.8 s; larger only right after reconnects |
| AAPL / SPY after market close | ticks carried source times up to ~4.5 h old; dashboard data age showed them as old |
| Duplicate ticks from Yahoo | 22 (stored once each, `duplicate_count` incremented) |
| Duplicate history bars | 3,480 (same bars re-downloaded on each backend restart) |
| Candle revisions by reason | new 216, update 996, finalize 216, late 3, backfill 1,748, backfill_revision 4 |
| Out-of-order ticks in real data | 0 observed (rule covered by automated tests only) |
| Replay = stored, real data | BTC-USD 162/162, AAPL 839/839, SPY 963/963 candles, 0 mismatches |

**Unavailable-feed conditions observed for real (from `feed_events`):**

- 8:13–8:15 PM — `SSLCertVerificationError: certificate verify failed` on every connect (python.org Python 3.14 without its certificate bundle). Shown as UNAVAILABLE with backoff 2, 4, 8, 16, 30 s. Fixed by running *Install Certificates.command*; next start went LIVE.
- 9:03–9:11 PM — Wi-Fi off: `gaierror: [Errno 8] nodename nor servname provided` with retries #2–#7; LIVE again at 9:11:19 PM after Wi-Fi returned, with no restart.
- 8:35–9:43 PM — 7 disconnects `ConnectionClosedError: keepalive ping timeout`, each shown as UNAVAILABLE and reconnected in ~2–5 s; quiet gaps also triggered STALE (no messages for 30+ s) followed by "messages resumed".
- Backend stopped while the page was open → browser showed BACKEND OFFLINE ("data shown is frozen").
- 9:43 PM — labelled INJECTED outage via the *Simulate feed outage* button (used in the demo video).

Demo video: <https://drive.google.com/file/d/1niD55L3WWFwusmE2XaevG6ldWMGernB5/view?usp=drive_link>

## 4. Not verified / limitations of the evidence

* Automated tests use fake WebSocket servers; they cannot show Yahoo's real message rate, field availability or outages.
* Real Yahoo data produced duplicates and late ticks but no out-of-order ticks in this session; out-of-order handling is verified only by forced cases in the mock feed and unit tests.
* Browser tests run headless Chrome on Linux; Safari was not tested.

---

# Part 4.2 verification

## 5. Requirement → evidence map (4.2)

| Requirement | Automated check | Manual / live evidence |
|---|---|---|
| Two SMAs of different lengths displayed | e2e `part42.spec.js` "SMA lines appear…" | video, screenshots |
| Crossover displayed | `test_series_marks_crossovers_on_completed_candles`; chart arrows | video |
| SMA verified against a trusted reference | `test_batch_matches_pandas_on_real_spy_data` (pandas `rolling(n).mean()`, n = 1, 5, 10, 30, 50, real SPY data, rel. tol 1e-12) | — |
| Batch vs incremental | `test_incremental_equals_batch`; `test_indicator_matches_batch_after_live_updates`; live **Verify live SMA = batch** button (`/api/indicators/verify`); e2e check | screenshot of ✓ match on live data |
| Unfinished candles handled | `test_unfinished_candle_gives_provisional_point_only`, `test_provisional_crossover_can_disappear`, `test_no_provisional_point_while_previous_candle_is_unfinished`, `test_provisional_cross_is_shown_but_not_traded` | faded line end + "?" marker in video |
| Strategy applied in a simulated account | `test_crossovers_place_and_fill_simulated_orders`, `test_no_trading_on_candles_completed_before_account_existed` | account panel in video |
| Orders, fills, cash, positions, realized/unrealized P&L | `test_accounting.py` (11 tests, hand-worked examples incl. fees 0.10 %, slippage, average cost, partial sell, rejections, rebuild-from-fills, P&L identity on 50 random sequences) | — |
| Account state persisted | `test_account_survives_restart`, `test_changed_settings_start_a_new_account`, `test_reset_keeps_old_account` | — |
| Backtest without look-ahead | `test_signal_at_close_fills_at_next_open`, `test_no_look_ahead_truncation` (truncate and replace future bars), `test_signal_on_last_bar_is_not_executed` | — |
| Backtest results shown | `test_real_fixture_run_is_consistent`, `test_api_42.py`, e2e "backtest on the real SPY fixture…" | video |
| Data limits handled | `test_yahoo_refresh_drops_unfinished_bar_and_caches`, `test_backtest_yahoo_offline_without_cache`, `test_backtest_validation`, e2e "not enough data" | — |
| Browser tests + GitHub Actions on push | `.github/workflows/ci.yml` runs pytest and Playwright | link a green run in report.md |

## 6. Results recorded (2026-11-19, development sandbox)

* `python -m pytest -q` → **92 passed** (52 new for 4.2).
* `npx playwright test` → **8 passed**; repeated 5× in a row with CI settings after the fix in evidence log E8 (it failed 3 of 5 before).
* Live SIMULATED feed (5-second candles, SMA 3/8): the runner placed a buy on an up-cross and a sell on the next down-cross; a down-cross while flat was recorded as "skip"; `/api/indicators/verify` → match.
* Backtest of SMA 10/30 on the real SPY fixture (390 one-minute bars, 2026-11-12 regular session, no gaps):

| Costs | Strategy | Buy & hold | Round trips | Wins | Max DD | Fees | Slippage | In market |
|---|---|---|---|---|---|---|---|---|
| fee 10 bps + slippage 5 bps | **−1.73 %** | −0.22 % | 6 | 1 | 2.39 % | 1,193.04 | 596.52 | 40 % |
| no costs | +0.05 % | −0.07 % | 6 | 1 | 0.76 % | 0 | 0 | 40 % |

  Interpretation: on this one day the crossover's gross edge was about zero;
  trading costs (≈ 0.3 % per round trip) turned it into a loss. This shows the
  cost model matters more than the signal at this timescale. It does **not**
  show how the strategy performs on other days, instruments or windows.

* Stored candles from the 4.1 live session (BTC-USD, 701 candles, 193 gaps
  up to 13 h): −9.74 %. The gaps make this run unreliable; it is kept as an
  example of why the data description (gaps) is shown next to every result.

## 7. Live Yahoo observations for 4.2 — 2026-11-19, macOS, 4:00–8:47 PM ET

From `backend/data/market.db` (tables `sim_signals`, `sim_fills`, `observations`, `feed_events`) after the session. Observations of the real provider, not automated tests.

| Observation | Result |
|---|---|
| History loaded at startup | BTC-USD 1,261 · AAPL 633 · SPY 694 one-minute bars → SMA 10/30 defined immediately |
| Live ticks recorded | 884 (BTC-USD 258, SPY 479, AAPL 147); 15 duplicate ticks from Yahoo |
| Source → backend delay | BTC-USD mean 2.9 s; after-hours AAPL/SPY quotes 40–50 s old |
| Candle revisions in session | new 73 · update 584 · finalize 71 · late 161 · out_of_order 1 · backfill 2,588 |
| Simulated accounts | one per symbol, created 4:00:30 PM, SMA 10/30, fee 10 bps, slippage 5 bps |

**Confirmed crossovers and actions (live):**

| Symbol | Candle | Signal | SMA 10 / SMA 30 | Action | Fill |
|---|---|---|---|---|---|
| SPY | 4:00 PM | down | 716.80 / 716.83 | skip — no position (long-only) | — |
| BTC-USD | 4:03 PM | down | 62,032.72 / 62,053.31 | skip — no position (long-only) | — |
| AAPL | 4:08 PM | down | 348.83 / 348.83 | skip — no position (long-only) | — |
| AAPL | 4:12 PM | **up** | 348.86 / 348.84 | **buy** | 286.021698 @ 349.27 (last tick 349.10 + 5 bps slippage), fee 99.90, decided 4:13:05 PM, price 4.1 s old |

The AAPL buy shows the full path: a completed candle → confirmed SMA values → crossover → decision → simulated fill → account state.

**Feed conditions during the session:** INJECTED outage at 4:07 and 4:17 PM (button, labelled in the UI); `keepalive ping timeout` disconnects at 4:19 and 4:23 PM; `TimeoutError` during handshake; from 4:23 PM `gaierror` (no network / DNS) with retries backing off to every 30 s; automatic recovery around 8:46 PM with no restart.

Demo video: <https://drive.google.com/file/d/128cJwyx7vLFpiVFsPK5RAOUPNbxrV3Zl/view?usp=sharing>
