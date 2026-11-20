"""Live indicators and paper trading.

For every configured symbol this runner keeps:

* an incremental SMA(fast)/SMA(slow) state fed with *completed* candles, in
  order, as they are finalised (`SmaCrossStrategy`);
* a simulated account (`Account`) persisted as rows in sim_accounts,
  sim_signals, sim_orders and sim_fills, and rebuilt from sim_fills on start.

Flow for each candle change coming from MarketService:

* candle became complete (reason 'finalize') and is newer than the last one
  used -> push its close, compute confirmed SMAs, detect a crossover; if there
  is one, record the signal and, if the strategy acts, place a market order
  and fill it immediately at the latest observed price (+ slippage, + fee).
* candle still forming -> provisional SMAs/crossover via `peek` (shown, never
  traded).
* a candle that was already used changed (late tick, history revision) ->
  rebuild the SMA state from SQLite. Decisions already taken stay as they
  were; the signal row keeps the SMA values that were used.
* history candles that arrive already complete (backfill on startup) update
  the indicators but never trade — they describe the past.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from .accounting import Account, AccountConfig, Fill
from .aggregator import Change
from .config import Settings
from .hub import Hub
from .indicators import indicator_series
from .store import Store
from .strategy import SmaCrossStrategy, decide

log = logging.getLogger("market.sim")


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class SymbolSim:
    symbol: str
    account_id: int
    account_created_ms: int
    account: Account
    strategy: SmaCrossStrategy
    last_bucket: Optional[int] = None
    last_point: Optional[dict] = None
    provisional_point: Optional[dict] = None
    rebuilds: int = 0
    gaps: int = 0
    dirty: bool = False                       # SMA state must be rebuilt before next use
    rebuild_task: Optional[asyncio.Task] = None
    counters: dict = field(default_factory=lambda: {"signals": 0, "orders": 0, "rejected": 0, "skipped": 0})


class LiveSimRunner:
    def __init__(self, settings: Settings, store: Store, hub: Hub, price_lookup=None):
        self.s = settings
        self.store = store
        self.hub = hub
        # price_lookup(symbol) -> (price, source_time_ms) of the latest observed tick
        self.price_lookup = price_lookup or (lambda sym: (None, None))
        self.books: dict[str, SymbolSim] = {}

    # ---- setup -----------------------------------------------------------
    def account_config(self) -> AccountConfig:
        s = self.s
        return AccountConfig(initial_cash=s.sim_initial_cash, fee_bps=s.sim_fee_bps,
                             slippage_bps=s.sim_slippage_bps, allocation=s.sim_allocation)

    def start(self) -> None:
        for sym in self.s.symbols:
            self.books[sym] = self._load_symbol(sym)

    def _active_account_row(self, symbol: str):
        s, cfg = self.s, self.account_config()
        with self.store.lock:
            row = self.store.conn.execute(
                "SELECT * FROM sim_accounts WHERE symbol=? AND active=1 ORDER BY id DESC LIMIT 1", (symbol,)
            ).fetchone()
            params = (s.sma_fast, s.sma_slow, cfg.initial_cash, cfg.fee_bps, cfg.slippage_bps, cfg.allocation)
            if row and (row["fast"], row["slow"], row["initial_cash"], row["fee_bps"], row["slippage_bps"],
                        row["allocation"]) == params:
                return row
            if row:  # settings changed: keep the old account for the record, start a new one
                self.store.conn.execute("UPDATE sim_accounts SET active=0 WHERE id=?", (row["id"],))
            cur = self.store.conn.execute(
                "INSERT INTO sim_accounts(symbol, created_ms, active, fast, slow, initial_cash, fee_bps, "
                "slippage_bps, allocation) VALUES (?,?,1,?,?,?,?,?,?)",
                (symbol, now_ms(), *params),
            )
            return self.store.conn.execute("SELECT * FROM sim_accounts WHERE id=?", (cur.lastrowid,)).fetchone()

    def _load_symbol(self, symbol: str) -> SymbolSim:
        row = self._active_account_row(symbol)
        acct = Account(AccountConfig(initial_cash=row["initial_cash"], fee_bps=row["fee_bps"],
                                     slippage_bps=row["slippage_bps"], allocation=row["allocation"]))
        with self.store.lock:
            fills = self.store.conn.execute(
                "SELECT * FROM sim_fills WHERE account_id=? ORDER BY id", (row["id"],)).fetchall()
        for f in fills:  # rebuild the account from its stored fills
            acct.apply_fill(Fill(f["order_id"], f["time_ms"], f["side"], f["qty"], f["ref_price"], f["price"], f["fee"]))
        ss = SymbolSim(symbol, row["id"], row["created_ms"], acct, SmaCrossStrategy(row["fast"], row["slow"]))
        self._warm(ss)
        return ss

    def _complete_candles(self, symbol: str, limit: int):
        with self.store.lock:
            rows = self.store.conn.execute(
                "SELECT bucket_start_ms, close FROM candles WHERE symbol=? AND interval=? AND is_complete=1 "
                "ORDER BY bucket_start_ms DESC LIMIT ?", (symbol, self.s.interval_label, limit)).fetchall()
        return list(reversed(rows))

    def _warm(self, ss: SymbolSim):
        """(Re)build the incremental SMA state from stored completed candles.
        A long tail is used so the 'last side' of fast vs slow is known.
        Returns the step for the newest candle (or None)."""
        ss.strategy = SmaCrossStrategy(ss.strategy.fast_n, ss.strategy.slow_n)
        ss.last_bucket, ss.last_point, step = None, None, None
        for r in self._complete_candles(ss.symbol, ss.strategy.slow_n * 10 + 50):
            step = ss.strategy.on_bar_close(r["close"])
            ss.last_bucket = r["bucket_start_ms"]
            ss.last_point = self._point(r["bucket_start_ms"], step.fast, step.slow, step.signal, False)
        ss.dirty = False
        return step

    def _schedule_rebuild(self, ss: SymbolSim, reason: str) -> None:
        """Coalesce bursts of revisions (e.g. hundreds of backfilled history
        bars at startup) into one rebuild + one chart refresh."""
        ss.dirty = True
        if ss.rebuild_task and not ss.rebuild_task.done():
            return

        async def run():
            await asyncio.sleep(0.5)
            if ss.dirty:
                ss.rebuilds += 1
                self._warm(ss)
            await self.hub.broadcast({"type": "indicators", "symbol": ss.symbol,
                                      "points": self.indicator_points(ss.symbol), "reason": reason},
                                     symbol=ss.symbol)

        ss.rebuild_task = asyncio.create_task(run())

    @staticmethod
    def _point(bucket_ms, fast, slow, signal, provisional) -> dict:
        return {"time": bucket_ms // 1000, "bucket_start_ms": bucket_ms, "fast": fast, "slow": slow,
                "signal": signal, "provisional": provisional}

    # ---- candle events ---------------------------------------------------
    async def on_change(self, change: Change) -> None:
        c = change.candle
        ss = self.books.get(c.symbol)
        if ss is None or c.interval != self.s.interval_label:
            return
        if c.is_complete:
            if ss.last_bucket is None or c.bucket_start_ms > ss.last_bucket:
                if ss.last_bucket is not None and c.bucket_start_ms - ss.last_bucket > self.s.interval_ms:
                    ss.gaps += 1
                if ss.dirty:
                    ss.rebuilds += 1
                    step = self._warm(ss)  # the stored candles already include this one
                else:
                    step = ss.strategy.on_bar_close(c.close)
                ss.last_bucket = c.bucket_start_ms
                ss.last_point = self._point(c.bucket_start_ms, step.fast, step.slow, step.signal, False)
                ss.provisional_point = None
                await self.hub.broadcast({"type": "indicator", "symbol": c.symbol, "point": ss.last_point},
                                         symbol=c.symbol)
                if step.signal and change.reason == "finalize" and c.bucket_end_ms >= ss.account_created_ms:
                    await self._act(ss, c, step)
            elif change.material:
                # a candle the SMAs already used (or an older gap-filling one)
                # changed: recompute from SQLite. Past decisions are not redone.
                self._schedule_rebuild(ss, change.reason)
        else:
            # provisional values only when the previous candle is already confirmed
            # (adjacent); otherwise the window would silently skip a candle
            adjacent = ss.last_bucket is not None and c.bucket_start_ms - ss.last_bucket == self.s.interval_ms
            if adjacent and not ss.dirty:
                step = ss.strategy.peek(c.close)
                ss.provisional_point = self._point(c.bucket_start_ms, step.fast, step.slow, step.signal, True)
                await self.hub.broadcast({"type": "indicator", "symbol": c.symbol, "point": ss.provisional_point},
                                         symbol=c.symbol)

    async def _act(self, ss: SymbolSim, candle, step) -> None:
        ss.counters["signals"] += 1
        decided = now_ms()
        side, why = decide(step.signal, ss.account.position.qty)
        order_id = None
        fill_row = None
        if side:
            price, price_src_ms = self.price_lookup(ss.symbol)
            if price is None:
                price, price_src_ms = candle.close, candle.last_source_ms
            order = ss.account.new_order(decided, side, None, why)
            with self.store.lock:
                cur = self.store.conn.execute(
                    "INSERT INTO sim_orders(account_id, created_ms, side, status, reason) VALUES (?,?,?,?,?)",
                    (ss.account_id, decided, side, "pending", why))
            order.id = order_id = cur.lastrowid
            ss.account.fill_order(order, decided, price)
            with self.store.lock:
                self.store.conn.execute("UPDATE sim_orders SET status=?, reject_reason=? WHERE id=?",
                                        (order.status, order.reject_reason or None, order.id))
                if order.status == "filled":
                    f = order.fill
                    self.store.conn.execute(
                        "INSERT INTO sim_fills(order_id, account_id, time_ms, side, qty, ref_price, ref_source_ms, "
                        "price, fee, realized_pnl) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (order.id, ss.account_id, f.time_ms, f.side, f.qty, f.ref_price, price_src_ms,
                         f.price, f.fee, f.realized_pnl))
                    fill_row = {"order_id": order.id, "time_ms": f.time_ms, "side": f.side, "qty": f.qty,
                                "ref_price": f.ref_price, "ref_source_ms": price_src_ms, "price": f.price,
                                "fee": f.fee, "realized_pnl": f.realized_pnl}
            ss.counters["orders" if order.status == "filled" else "rejected"] += 1
        else:
            ss.counters["skipped"] += 1
        with self.store.lock:
            self.store.conn.execute(
                "INSERT INTO sim_signals(account_id, bucket_start_ms, decided_ms, signal, fast, slow, close, action, "
                "note, order_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ss.account_id, candle.bucket_start_ms, decided, step.signal, step.fast, step.slow, candle.close,
                 side or "skip", why, order_id))
        detail = f"{ss.symbol} {step.signal}-cross @ candle {time.strftime('%H:%M', time.localtime(candle.bucket_start_ms / 1000))}: {why}"
        if fill_row:
            detail += f" — filled {fill_row['qty']} @ {fill_row['price']:.2f}"
        ev = self.store.record_event(decided, "sim", f"signal_{step.signal}", detail)
        await self.hub.broadcast({"type": "event", "event": ev})
        await self.hub.broadcast({"type": "trade", "symbol": ss.symbol, "signal": step.signal, "action": side or "skip",
                                  "note": why, "fill": fill_row, "account": self.account_view(ss.symbol)},
                                 symbol=ss.symbol)

    # ---- views -------------------------------------------------------------
    def indicator_points(self, symbol: str, limit: Optional[int] = None) -> list[dict]:
        """Batch-computed series over the stored candles (plus the provisional
        point for a forming last candle)."""
        ss = self.books.get(symbol)
        fast_n, slow_n = (ss.strategy.fast_n, ss.strategy.slow_n) if ss else (self.s.sma_fast, self.s.sma_slow)
        limit = limit or self.s.snapshot_candles
        candles = self.store.candles(symbol, self.s.interval_label, limit + slow_n)
        pts = [p.to_wire() for p in indicator_series(candles, fast_n, slow_n)]
        visible = {c.bucket_start_ms for c in candles[-limit:]}
        return [p for p in pts if p["bucket_start_ms"] in visible]

    def account_view(self, symbol: str, history: int = 50) -> dict:
        ss = self.books.get(symbol)
        if ss is None:
            return {}
        price, src = self.price_lookup(symbol)
        snap = ss.account.snapshot(price)
        with self.store.lock:
            fills = [dict(r) for r in self.store.conn.execute(
                "SELECT f.*, o.reason FROM sim_fills f JOIN sim_orders o ON o.id=f.order_id "
                "WHERE f.account_id=? ORDER BY f.id DESC LIMIT ?", (ss.account_id, history))]
            orders = [dict(r) for r in self.store.conn.execute(
                "SELECT * FROM sim_orders WHERE account_id=? ORDER BY id DESC LIMIT ?", (ss.account_id, history))]
            signals = [dict(r) for r in self.store.conn.execute(
                "SELECT * FROM sim_signals WHERE account_id=? ORDER BY id DESC LIMIT ?", (ss.account_id, history))]
        return {
            "symbol": symbol, "account_id": ss.account_id, "created_ms": ss.account_created_ms,
            "params": {"fast": ss.strategy.fast_n, "slow": ss.strategy.slow_n,
                       "fee_bps": ss.account.config.fee_bps, "slippage_bps": ss.account.config.slippage_bps,
                       "allocation": ss.account.config.allocation},
            "snapshot": snap,
            "mark_source_ms": src,
            "mark_basis": "latest observed trade price (the current candle may be unfinished)",
            "fills": fills, "orders": orders, "signals": signals,
            "indicator": {"confirmed": ss.last_point, "provisional": ss.provisional_point},
            "stats": {**ss.counters, "rebuilds": ss.rebuilds, "gaps": ss.gaps},
        }

    def verify(self, symbol: str) -> dict:
        """Compare the live incremental SMA state with a batch recomputation
        from the stored completed candles."""
        ss = self.books[symbol]
        rebuilt = False
        if ss.dirty:
            # a revision/gap-fill is waiting for its (debounced) rebuild; apply it now
            ss.rebuilds += 1
            self._warm(ss)
            rebuilt = True
        complete = [c for c in self.store.candles(symbol, self.s.interval_label, 100000) if c.is_complete]
        # compare at the same candle: one may have completed a moment ago and
        # not reached the incremental state yet
        pending = [c for c in complete if ss.last_bucket is not None and c.bucket_start_ms > ss.last_bucket]
        upto = [c for c in complete if ss.last_bucket is None or c.bucket_start_ms <= ss.last_bucket]
        batch = indicator_series(upto, ss.strategy.fast_n, ss.strategy.slow_n)
        last = batch[-1] if batch else None
        inc_fast, inc_slow = ss.strategy.fast.value, ss.strategy.slow.value

        def close(a, b):
            return (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= 1e-9 * max(1, abs(b)))

        ok = last is not None and last.bucket_start_ms == ss.last_bucket and close(inc_fast, last.fast) and close(inc_slow, last.slow)
        return {"symbol": symbol, "match": bool(ok) if last else None,
                "incremental": {"bucket_start_ms": ss.last_bucket, "fast": inc_fast, "slow": inc_slow},
                "batch": last.to_wire() if last else None,
                "completed_candles": len(batch), "pending_candles": len(pending), "rebuilds": ss.rebuilds,
                "rebuilt_before_check": rebuilt}

    async def reset(self, symbol: str) -> dict:
        with self.store.lock:
            self.store.conn.execute("UPDATE sim_accounts SET active=0 WHERE symbol=? AND active=1", (symbol,))
        old = self.books.get(symbol)
        self.books[symbol] = self._load_symbol(symbol)
        ev = self.store.record_event(now_ms(), "sim", "account_reset",
                                     f"{symbol}: new simulated account #{self.books[symbol].account_id}"
                                     + (f" (previous #{old.account_id} kept)" if old else ""))
        await self.hub.broadcast({"type": "event", "event": ev})
        return self.account_view(symbol)

    async def account_loop(self) -> None:
        """Push account snapshots (mark-to-market changes with every tick)."""
        while True:
            try:
                for sym in self.books:
                    await self.hub.broadcast({"type": "account", "symbol": sym, "account": self.account_view(sym, 20)},
                                             symbol=sym)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("account loop error")
            await asyncio.sleep(2)
