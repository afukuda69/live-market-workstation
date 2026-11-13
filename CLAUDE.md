# Agent instructions — Live Market Workstation

Read this before changing code. Keep it current when conventions change.

## Project
Course project (Assignment 4) built in cumulative parts. Part 4.1 = live
Yahoo Finance data → FastAPI → SQLite → Vue chart, with replay and visible feed
status. Later parts add indicators, simulated trading, backtesting and AI —
**all trading must remain simulated**; never add code that places real orders.

## Stack and commands
- Backend: Python 3.11+, FastAPI, `backend/`. Run `uvicorn app.main:app --reload --port 8000` from `backend/`.
- Frontend: Vue 3 + Vite + Lightweight Charts 4, `frontend/`. Run `npm run dev`.
- DB init: `python -m scripts.init_db` (from `backend/`).
- Tests: `cd backend && python -m pytest -q`; `cd frontend && npm run test:e2e`.
- Offline: `FEED_MODE=mock` (SIMULATED data) or `FEED_MODE=off`.
- The developer works on macOS (zsh) and sometimes Windows PowerShell; give commands for the shell in use.

## Invariants — do not break
1. **One ingest path.** Every source (Yahoo WS, history, mock) goes through `MarketService.ingest_*`. Do not write candles from anywhere else.
2. **Candle rules live in `aggregator.py` only** and stay free of I/O, so live and replay share them. Replay uses `MemoryCandleBook` and must never write to `candles`.
3. **Keep both times.** Observations store `source_time_ms` and `received_time_ms`. Buckets use source time. Never replace one with the other.
4. **Never silently overwrite history.** Every material candle change appends to `candle_revisions` with a reason. Duplicates increment `duplicate_count`; they do not create rows or revisions.
5. **Provisional vs complete must be visible** in data (`is_complete`) and in the UI.
6. **Truthful status.** The UI must show source, feed state and data age (based on source timestamps, not redraw time). Simulated or injected conditions must be labelled (SIMULATED, INJECTED).
7. **No credentials in the repo.** Config comes from env / `backend/.env` (git-ignored). If a later part adds API keys, add them to `.env.example` as empty placeholders only.

## When changing things
- Schema change → update `db.py`, `docs/design.md` §4, and add/adjust tests.
- New candle rule → test in `tests/test_aggregator.py` and a replay-equivalence check in `tests/test_replay.py`.
- New UI state → `data-testid` on the element and a Playwright check in `frontend/e2e/`.
- Distinguish mocked tests from live observations in `docs/verification.md`.
- Add an entry to `docs/evidence-log.md` for notable decisions or failed approaches.
- Run both test suites before committing.

## Style
Small, readable modules; docstrings explain *why*. Prefer explicit names over cleverness. The developer must be able to explain every file.
