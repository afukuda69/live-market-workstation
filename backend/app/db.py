"""SQLite schema and connection helper.

Run `python -m scripts.init_db` from backend/ to create the database explicitly;
the app also calls `init_db()` on startup (the schema is idempotent).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

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
