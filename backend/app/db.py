"""SQLite schema and connection helper.

Run `python -m scripts.init_db` from backend/ to create the database explicitly;
the app also calls `init_db()` on startup (the schema is idempotent).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Every observation received from a source, exactly once per identity.
-- kind = 'tick' (one streamed price update) or 'bar' (one history bar).
CREATE TABLE IF NOT EXISTS observations (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    obs_key                TEXT    NOT NULL UNIQUE,   -- identity used for de-duplication
    kind                   TEXT    NOT NULL CHECK (kind IN ('tick', 'bar')),
    source                 TEXT    NOT NULL,          -- 'yahoo-ws', 'yahoo-history', 'mock'
    symbol                 TEXT    NOT NULL,
    source_time_ms         INTEGER NOT NULL,          -- time stamped by the source
    received_time_ms       INTEGER NOT NULL,          -- time this backend first received it
    price                  REAL,                      -- tick price (NULL for bars)
    day_volume             INTEGER,                   -- cumulative session volume on ticks
    bar_open REAL, bar_high REAL, bar_low REAL, bar_close REAL, bar_volume INTEGER,
    payload_json           TEXT    NOT NULL,          -- decoded source message, for audit
    duplicate_count        INTEGER NOT NULL DEFAULT 0,-- later copies of the same identity
    last_duplicate_ms      INTEGER
);
CREATE INDEX IF NOT EXISTS ix_obs_symbol_received ON observations(symbol, received_time_ms, id);
CREATE INDEX IF NOT EXISTS ix_obs_symbol_source   ON observations(symbol, source_time_ms);

-- Current state of each candle. One row per (symbol, interval, bucket).
CREATE TABLE IF NOT EXISTS candles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol              TEXT    NOT NULL,
    interval            TEXT    NOT NULL,
    bucket_start_ms     INTEGER NOT NULL,
    bucket_end_ms       INTEGER NOT NULL,
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
    volume              INTEGER NOT NULL DEFAULT 0,
    volume_base         INTEGER NOT NULL DEFAULT 0,  -- volume taken from a history bar
    dv_min              INTEGER,                     -- smallest day_volume seen in bucket
    dv_max              INTEGER,                     -- largest day_volume seen in bucket
    first_source_ms     INTEGER,                     -- source time of the tick that set open
    last_source_ms      INTEGER,                     -- source time of the tick that set close
    tick_count          INTEGER NOT NULL DEFAULT 0,
    is_complete         INTEGER NOT NULL DEFAULT 0 CHECK (is_complete IN (0, 1)),
    revision            INTEGER NOT NULL DEFAULT 1,
    origin              TEXT    NOT NULL,            -- 'ticks' or 'history'
    updated_received_ms INTEGER NOT NULL,
    UNIQUE (symbol, interval, bucket_start_ms)
);

-- Append-only history: one row every time a candle row changes.
CREATE TABLE IF NOT EXISTS candle_revisions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    candle_id        INTEGER NOT NULL REFERENCES candles(id),
    revision         INTEGER NOT NULL,
    reason           TEXT    NOT NULL,  -- new | update | out_of_order | late | finalize | backfill | backfill_revision
    observation_id   INTEGER REFERENCES observations(id),
    open REAL, high REAL, low REAL, close REAL, volume INTEGER,
    is_complete      INTEGER NOT NULL,
    recorded_ms      INTEGER NOT NULL,
    UNIQUE (candle_id, revision)
);

-- ===== Part 4.2 ==========================================================

-- Historical bars downloaded for backtests (cache, so runs are reproducible
-- and work offline). Separate from `candles`, which hold live-built data.
CREATE TABLE IF NOT EXISTS history_bars (
    symbol      TEXT    NOT NULL,
    interval    TEXT    NOT NULL,      -- '1m', '5m', '15m', '1h', '1d'
    time_ms     INTEGER NOT NULL,      -- bar start
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
    volume      REAL    NOT NULL DEFAULT 0,
    fetched_ms  INTEGER NOT NULL,      -- when this version of the bar was downloaded
    PRIMARY KEY (symbol, interval, time_ms)
);

-- Saved backtest runs: inputs, data used, and results.
CREATE TABLE IF NOT EXISTS backtest_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ms    INTEGER NOT NULL,
    symbol        TEXT    NOT NULL,
    source        TEXT    NOT NULL,    -- 'stored' | 'yahoo' | 'fixture'
    interval      TEXT    NOT NULL,
    params_json   TEXT    NOT NULL,
    data_json     TEXT    NOT NULL,    -- range, bar count, gaps, fetch info
    metrics_json  TEXT    NOT NULL,
    result_json   TEXT    NOT NULL     -- trades, round trips, signals, equity, indicators, notes
);

-- Live simulated (paper) accounts, one active per symbol.
CREATE TABLE IF NOT EXISTS sim_accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT    NOT NULL,
    created_ms    INTEGER NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    fast          INTEGER NOT NULL,
    slow          INTEGER NOT NULL,
    initial_cash  REAL    NOT NULL,
    fee_bps       REAL    NOT NULL,
    slippage_bps  REAL    NOT NULL,
    allocation    REAL    NOT NULL
);

-- Every confirmed crossover the live strategy saw, and what it did.
CREATE TABLE IF NOT EXISTS sim_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      INTEGER NOT NULL REFERENCES sim_accounts(id),
    bucket_start_ms INTEGER NOT NULL,  -- the completed candle that produced it
    decided_ms      INTEGER NOT NULL,
    signal          TEXT    NOT NULL,  -- 'up' | 'down'
    fast REAL, slow REAL, close REAL,
    action          TEXT    NOT NULL,  -- 'buy' | 'sell' | 'skip'
    note            TEXT,
    order_id        INTEGER
);

CREATE TABLE IF NOT EXISTS sim_orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      INTEGER NOT NULL REFERENCES sim_accounts(id),
    created_ms      INTEGER NOT NULL,
    side            TEXT    NOT NULL CHECK (side IN ('buy','sell')),
    order_type      TEXT    NOT NULL DEFAULT 'market',
    status          TEXT    NOT NULL,  -- 'filled' | 'rejected'
    reason          TEXT,
    reject_reason   TEXT
);

CREATE TABLE IF NOT EXISTS sim_fills (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id         INTEGER NOT NULL REFERENCES sim_orders(id),
    account_id       INTEGER NOT NULL REFERENCES sim_accounts(id),
    time_ms          INTEGER NOT NULL,
    side             TEXT    NOT NULL,
    qty              REAL    NOT NULL,
    ref_price        REAL    NOT NULL,  -- last observed price used as the market price
    ref_source_ms    INTEGER,           -- source time of that price (shows its age)
    price            REAL    NOT NULL,  -- after slippage
    fee              REAL    NOT NULL,
    realized_pnl     REAL    NOT NULL
);

-- Feed lifecycle: connects, disconnects, outages, recoveries, rejected messages.
CREATE TABLE IF NOT EXISTS feed_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms     INTEGER NOT NULL,
    source    TEXT    NOT NULL,
    state     TEXT    NOT NULL,
    detail    TEXT
);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO schema_meta(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
