"""SQLite persistence: observations (with de-duplication), candles and their
revision history, and feed events."""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from typing import Iterator, Optional

from .aggregator import Bar, Candle, Change, Tick


@dataclass
class InsertResult:
    observation_id: int
    duplicate: bool
    duplicate_count: int = 0


def tick_key(source: str, t: Tick) -> str:
    # Identity of a streamed tick: same symbol, source timestamp, price and
    # running volume => the same observation delivered twice.
    return f"{source}|tick|{t.symbol}|{t.source_time_ms}|{t.price!r}|{t.day_volume}"


def bar_key(source: str, interval: str, b: Bar) -> str:
    # Identity of a history bar includes its values, so a bar Yahoo later
    # revises (different close/volume) is a *new* observation and produces a
    # candle revision, while an identical re-download is a duplicate.
    return (
        f"{source}|bar|{interval}|{b.symbol}|{b.bucket_start_ms}|"
        f"{b.open!r}|{b.high!r}|{b.low!r}|{b.close!r}|{b.volume}|{int(b.is_complete)}"
    )


class Store:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.lock = threading.RLock()

    # ---- observations ----------------------------------------------------
    def insert_tick(self, source: str, t: Tick, payload: dict) -> InsertResult:
        return self._insert_observation(
            obs_key=tick_key(source, t), kind="tick", source=source, symbol=t.symbol,
            source_time_ms=t.source_time_ms, received_ms=t.received_ms,
            price=t.price, day_volume=t.day_volume, bar=None, payload=payload,
        )

    def insert_bar(self, source: str, interval: str, b: Bar, payload: dict) -> InsertResult:
        return self._insert_observation(
            obs_key=bar_key(source, interval, b), kind="bar", source=source, symbol=b.symbol,
            source_time_ms=b.bucket_start_ms, received_ms=b.received_ms,
            price=None, day_volume=None, bar=b, payload=payload,
        )

    def _insert_observation(self, *, obs_key, kind, source, symbol, source_time_ms,
                            received_ms, price, day_volume, bar: Optional[Bar], payload) -> InsertResult:
        with self.lock:
            try:
                cur = self.conn.execute(
                    """INSERT INTO observations
                       (obs_key, kind, source, symbol, source_time_ms, received_time_ms, price, day_volume,
                        bar_open, bar_high, bar_low, bar_close, bar_volume, payload_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        obs_key, kind, source, symbol, source_time_ms, received_ms, price, day_volume,
                        bar.open if bar else None, bar.high if bar else None, bar.low if bar else None,
                        bar.close if bar else None, bar.volume if bar else None,
                        json.dumps(payload, default=str, sort_keys=True),
                    ),
                )
                return InsertResult(cur.lastrowid, False)
            except sqlite3.IntegrityError:
                # Same identity seen before: keep the first copy (and its
                # original receipt time), count the repeat.
                self.conn.execute(
                    "UPDATE observations SET duplicate_count = duplicate_count + 1, last_duplicate_ms = ? "
                    "WHERE obs_key = ?",
                    (received_ms, obs_key),
                )
                row = self.conn.execute(
                    "SELECT id, duplicate_count FROM observations WHERE obs_key = ?", (obs_key,)
                ).fetchone()
                return InsertResult(row["id"], True, row["duplicate_count"])

    def iter_observations(self, symbol: str, from_ms: Optional[int] = None,
                          to_ms: Optional[int] = None) -> Iterator[sqlite3.Row]:
        """Recorded observations in the order this backend received them
        (id is assigned on first receipt), so replay reproduces arrival order,
        including any out-of-order delivery."""
        q = "SELECT * FROM observations WHERE symbol = ?"
        args: list = [symbol]
        if from_ms is not None:
            q += " AND received_time_ms >= ?"
            args.append(from_ms)
        if to_ms is not None:
            q += " AND received_time_ms <= ?"
            args.append(to_ms)
        q += " ORDER BY received_time_ms, id"
        with self.lock:
            rows = self.conn.execute(q, args).fetchall()
        return iter(rows)

    # ---- queries ---------------------------------------------------------
    def candles(self, symbol: str, interval: str, limit: int = 300,
                before_ms: Optional[int] = None) -> list[Candle]:
        q = "SELECT * FROM candles WHERE symbol = ? AND interval = ?"
        args: list = [symbol, interval]
        if before_ms is not None:
            q += " AND bucket_start_ms < ?"
            args.append(before_ms)
        q += " ORDER BY bucket_start_ms DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            rows = self.conn.execute(q, args).fetchall()
        return [row_to_candle(r) for r in reversed(rows)]

    def revisions(self, symbol: str, interval: str, bucket_start_ms: int) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                """SELECT r.* FROM candle_revisions r JOIN candles c ON c.id = r.candle_id
                   WHERE c.symbol = ? AND c.interval = ? AND c.bucket_start_ms = ?
                   ORDER BY r.revision""",
                (symbol, interval, bucket_start_ms),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_event(self, ts_ms: int, source: str, state: str, detail: str = "") -> dict:
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO feed_events(ts_ms, source, state, detail) VALUES (?,?,?,?)",
                (ts_ms, source, state, detail),
            )
        return {"id": cur.lastrowid, "ts_ms": ts_ms, "source": source, "state": state, "detail": detail}

    def recent_events(self, limit: int = 50) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM feed_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self.lock:
            obs = self.conn.execute(
                """SELECT COUNT(*) AS n, COALESCE(SUM(duplicate_count),0) AS dups,
                          SUM(kind='tick') AS ticks, SUM(kind='bar') AS bars FROM observations"""
            ).fetchone()
            reasons = self.conn.execute(
                "SELECT reason, COUNT(*) AS n FROM candle_revisions GROUP BY reason"
            ).fetchall()
            candles = self.conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(is_complete),0) AS complete FROM candles"
            ).fetchone()
        return {
            "observations": obs["n"],
            "ticks": obs["ticks"] or 0,
            "bars": obs["bars"] or 0,
            "duplicates": obs["dups"],
            "candles": candles["n"],
            "complete_candles": candles["complete"],
            "revisions_by_reason": {r["reason"]: r["n"] for r in reasons},
        }


def row_to_candle(r: sqlite3.Row) -> Candle:
    return Candle(
        id=r["id"], symbol=r["symbol"], interval=r["interval"],
        bucket_start_ms=r["bucket_start_ms"], bucket_end_ms=r["bucket_end_ms"],
        open=r["open"], high=r["high"], low=r["low"], close=r["close"],
        volume=r["volume"], volume_base=r["volume_base"],
        dv_min=r["dv_min"], dv_max=r["dv_max"],
        first_source_ms=r["first_source_ms"], last_source_ms=r["last_source_ms"],
        tick_count=r["tick_count"], is_complete=bool(r["is_complete"]),
        revision=r["revision"], origin=r["origin"], updated_received_ms=r["updated_received_ms"],
    )


class SqliteCandleBook:
    """CandleBook that persists every change and appends to candle_revisions."""

    def __init__(self, store: Store):
        self.store = store

    def get(self, symbol, interval, bucket_start_ms):
        with self.store.lock:
            r = self.store.conn.execute(
                "SELECT * FROM candles WHERE symbol=? AND interval=? AND bucket_start_ms=?",
                (symbol, interval, bucket_start_ms),
            ).fetchone()
        return row_to_candle(r) if r else None

    def prev_dv_max(self, symbol, interval, bucket_start_ms):
        with self.store.lock:
            r = self.store.conn.execute(
                "SELECT dv_max FROM candles WHERE symbol=? AND interval=? AND bucket_start_ms<? "
                "ORDER BY bucket_start_ms DESC LIMIT 1",
                (symbol, interval, bucket_start_ms),
            ).fetchone()
        return r["dv_max"] if r else None

    def save(self, change: Change, observation_id, now_ms):
        c = change.candle
        conn = self.store.conn
        with self.store.lock:
            conn.execute("BEGIN")
            try:
                conn.execute(
                    """INSERT INTO candles
                       (symbol, interval, bucket_start_ms, bucket_end_ms, open, high, low, close, volume,
                        volume_base, dv_min, dv_max, first_source_ms, last_source_ms, tick_count,
                        is_complete, revision, origin, updated_received_ms)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(symbol, interval, bucket_start_ms) DO UPDATE SET
                         open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
                         volume=excluded.volume, volume_base=excluded.volume_base,
                         dv_min=excluded.dv_min, dv_max=excluded.dv_max,
                         first_source_ms=excluded.first_source_ms, last_source_ms=excluded.last_source_ms,
                         tick_count=excluded.tick_count, is_complete=excluded.is_complete,
                         revision=excluded.revision, origin=excluded.origin,
                         updated_received_ms=excluded.updated_received_ms""",
                    (
                        c.symbol, c.interval, c.bucket_start_ms, c.bucket_end_ms, c.open, c.high, c.low,
                        c.close, c.volume, c.volume_base, c.dv_min, c.dv_max, c.first_source_ms,
                        c.last_source_ms, c.tick_count, int(c.is_complete), c.revision, c.origin,
                        c.updated_received_ms,
                    ),
                )
                if change.material:
                    cid = conn.execute(
                        "SELECT id FROM candles WHERE symbol=? AND interval=? AND bucket_start_ms=?",
                        (c.symbol, c.interval, c.bucket_start_ms),
                    ).fetchone()["id"]
                    conn.execute(
                        """INSERT INTO candle_revisions
                           (candle_id, revision, reason, observation_id, open, high, low, close, volume,
                            is_complete, recorded_ms)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (cid, c.revision, change.reason, observation_id, c.open, c.high, c.low, c.close,
                         c.volume, int(c.is_complete), now_ms),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def open_candles(self):
        with self.store.lock:
            rows = self.store.conn.execute("SELECT * FROM candles WHERE is_complete = 0").fetchall()
        return [row_to_candle(r) for r in rows]
