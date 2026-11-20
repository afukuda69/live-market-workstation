# Live Market Workstation — Parts 4.1–4.2

A local market dashboard: live Yahoo Finance prices stream through a FastAPI
backend into SQLite and out to a Vue dashboard that draws candlesticks and
volume with TradingView Lightweight Charts. The dashboard always shows where
the data came from, how old it is, and whether the feed is working.

**Part 4.1:** live streaming, persistence, replay, feed status.
**Part 4.2:** SMA 10/30 and their crossover on the chart, a long-only
crossover strategy trading a **simulated** account live, and a backtester.
All trading is simulated in SQLite — nothing in this app places real orders.

| | |
|---|---|
| Frontend | Vue 3 + Vite, Lightweight Charts 4 (`frontend/`) |
| Backend | FastAPI, `websockets`, `yfinance` (`backend/`) |
| Database | SQLite, file `backend/data/market.db` (created on first run) |
| Live source | Yahoo Finance streamer via the `yfinance` protobuf schema |

Docs: [design notes (data flow + schema)](docs/design.md) ·
[verification](docs/verification.md) · [evidence log](docs/evidence-log.md) ·
[demo script](docs/demo-script.md) · [agent instructions](CLAUDE.md) ·
[AI use](AI-USE.md) · [report (current part)](report.md) · [4.1 report](docs/reports/report-4.1.md)

## Prerequisites

* Python 3.11+ and Node.js 20+
* Internet access for live mode (no API keys or accounts needed)

## Setup (clean checkout)

macOS / Linux:

```bash
git clone <repo-url> live-market-workstation
cd live-market-workstation

# backend
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env           # optional; defaults work without it
python -m scripts.init_db      # creates data/market.db and prints the tables

# frontend (new terminal)
cd ../frontend
npm install
```

Windows PowerShell differences: `py -m venv .venv`, `.venv\Scripts\Activate.ps1`,
`copy .env.example .env`.

## Run

Terminal 1 — backend (from `backend/`, venv active):

```bash
uvicorn app.main:app --reload --port 8000
```

Terminal 2 — frontend (from `frontend/`):

```bash
npm run dev
```

Open <http://localhost:5173>. Vite proxies `/api` and `/ws` to port 8000.

### Feed modes (`FEED_MODE` in `backend/.env` or the environment)

| Mode | What it does | When to use |
|---|---|---|
| `yahoo` (default) | Live Yahoo streamer + today's 1m history backfill | Real use and the demo |
| `mock` | Labelled **SIMULATED** random-walk ticks (includes duplicates and out-of-order delivery) | Offline development, browser tests |
| `off` | No feed; stored data and replay only | Inspecting recorded data |

Example: `FEED_MODE=mock uvicorn app.main:app --port 8000`
(PowerShell: `$env:FEED_MODE="mock"; uvicorn app.main:app --port 8000`).

### Market hours

US equities (AAPL, SPY) only stream during trading hours (and some
extended hours); outside them their feed goes quiet and the dashboard shows
**STALE** with a growing data age. **BTC-USD trades 24/7** and is the default
symbol for showing live streaming at any time. When nothing is live, use
**Replay** to re-run recorded observations from SQLite.

### Showing an unavailable feed

* **Real:** turn Wi-Fi off while the backend runs → feed goes `UNAVAILABLE`,
  red banner, retry countdown in the detail line; turn it back on → `LIVE`.
* **On demand:** the *Outage test → Simulate feed outage* button
  (`POST /api/feed/fault`) closes the Yahoo connection and blocks reconnects;
  the UI marks this **INJECTED** so it is not confused with a real outage.
* Stopping the backend shows **BACKEND OFFLINE** in the browser.

## Part 4.2 features

**Live tab**
* Orange SMA 10 and blue SMA 30 over the candles. Values computed only from
  completed candles; the faded line end and grey "?" markers include the
  still-forming candle and are never traded.
* Green/red arrows = confirmed crossovers; blue **B** / orange **S** = simulated fills.
* *Simulated account* panel: equity, cash, position and average cost, mark
  price, unrealized/realized P&L, fees, recent fills and signals (with the
  reason each was acted on or skipped), *Reset account*.
* *Verify live SMA = batch* compares the running incremental SMAs with a full
  recomputation from SQLite.

**Backtest tab** — choose data (real SPY fixture, this app's stored candles,
or Yahoo history at 1m–1d), SMA windows, cash, fee and slippage; see return
vs buy-and-hold, drawdown, round trips, the chart with trades, the equity
curve, notes on what the result does not show, and earlier runs.

Strategy, execution and accounting assumptions: [design notes §10–15](docs/design.md#part-42--indicators-simulated-trading-backtesting).

To see crossovers and trades quickly without waiting for 1-minute candles
(e.g. for a demo), run the simulated feed with short candles:

```bash
FEED_MODE=mock INTERVAL_SECONDS=5 SMA_FAST=3 SMA_SLOW=8 FINALIZE_GRACE_SECONDS=1 \
  DB_PATH=data/demo.db uvicorn app.main:app --port 8000
```

## Tests

```bash
# backend unit/integration tests (no network needed) — 92 tests
cd backend && python -m pytest -q

# browser tests (Playwright; starts backend in mock mode with 2 s candles + Vite) — 8 tests
cd frontend
npx playwright install chromium    # first time only
npm run test:e2e
```

GitHub Actions (`.github/workflows/ci.yml`) runs both on every push.
What is mocked versus observed live is listed in [docs/verification.md](docs/verification.md).

## Useful endpoints

* `GET /api/status` — feed state, per-symbol last source/receipt time, counters
* `GET /api/candles?symbol=BTC-USD&limit=300`
* `GET /api/candles/BTC-USD/<bucket_start_ms>/revisions` — a candle's revision history
* `GET /api/observations?symbol=BTC-USD` — raw recorded observations
* `GET /api/events` — feed lifecycle log
* `GET /api/replay/verify?symbol=BTC-USD` — rebuild from observations and compare with stored candles
* `GET /api/indicators?symbol=BTC-USD`, `GET /api/indicators/verify?symbol=BTC-USD`
* `GET /api/sim/account?symbol=BTC-USD`, `POST /api/sim/reset {"symbol": "BTC-USD"}`
* `POST /api/backtest {"source": "fixture"|"stored"|"yahoo", "symbol", "interval", "fast", "slow", "fee_bps", "slippage_bps"}`, `GET /api/backtest/runs`

Inspect the database directly: `sqlite3 backend/data/market.db ".tables"`.

## Configuration

See `backend/.env.example`. No credentials are used or required; `.env` is
git-ignored anyway. The database file is git-ignored (it is local recorded data).

## Project layout

```
backend/
  app/main.py            FastAPI app, REST + /ws, feed lifecycle
  app/service.py         single ingest path, feed status, housekeeping loop
  app/aggregator.py      candle rules (pure logic, shared by live + replay)
  app/store.py           SQLite access, de-duplication, revisions
  app/normalize.py       decode + validate Yahoo messages
  app/db.py              schema
  app/feeds/             yahoo_live.py, history.py, mock.py, replay.py
  app/indicators.py      SMA (batch + incremental), crossover, provisional points   (4.2)
  app/accounting.py      simulated account: orders, fills, cash, P&L               (4.2)
  app/strategy.py        SMA crossover rules (long-only)                            (4.2)
  app/backtest.py        event-driven backtester                                    (4.2)
  app/history_data.py    backtest data: fixture / stored / Yahoo + cache            (4.2)
  app/sim_live.py        live SMAs + paper trading on completed candles             (4.2)
  data/fixtures/         real SPY 1-minute bars for offline backtests and tests     (4.2)
  scripts/init_db.py     database initialisation
  tests/                 pytest suite
frontend/
  src/App.vue            layout, socket handling, replay/verify/outage controls
  src/components/        PriceChart.vue, StatusBar.vue, AccountPanel.vue, BacktestView.vue
  e2e/                   Playwright browser tests
docs/                    design, verification, evidence log, demo script
```
