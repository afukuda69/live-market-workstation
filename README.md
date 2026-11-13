# Live Market Workstation — Part 4.1

A local market dashboard: live Yahoo Finance prices stream through a FastAPI
backend into SQLite and out to a Vue dashboard that draws candlesticks and
volume with TradingView Lightweight Charts. The dashboard always shows where
the data came from, how old it is, and whether the feed is working.

**Part 4.1 scope:** live streaming, persistence, replay, feed status. No
indicators or trading yet (Parts 4.2–4.3). Nothing in this app places orders.

| | |
|---|---|
| Frontend | Vue 3 + Vite, Lightweight Charts 4 (`frontend/`) |
| Backend | FastAPI, `websockets`, `yfinance` (`backend/`) |
| Database | SQLite, file `backend/data/market.db` (created on first run) |
| Live source | Yahoo Finance streamer via the `yfinance` protobuf schema |

Docs: [design notes (data flow + schema)](docs/design.md) ·
[verification](docs/verification.md) · [evidence log](docs/evidence-log.md) ·
[demo script](docs/demo-script.md) · [agent instructions](CLAUDE.md) ·
[AI use](AI-USE.md) · [report](report.md)

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

## Tests

```bash
# backend unit/integration tests (no network needed)
cd backend && python -m pytest -q

# browser tests (Playwright; starts backend in mock mode + Vite automatically)
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
  scripts/init_db.py     database initialisation
  tests/                 pytest suite
frontend/
  src/App.vue            layout, socket handling, replay/verify/outage controls
  src/components/        PriceChart.vue, StatusBar.vue
  e2e/                   Playwright browser tests
docs/                    design, verification, evidence log, demo script
```
