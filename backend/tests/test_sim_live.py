"""Live indicators + paper trading driven by real ingest/finalise events."""
import asyncio
import time

import pandas as pd
import pytest

from app.history_data import load_cached_yahoo, refresh_yahoo
from app.sim_live import LiveSimRunner

from .conftest import ymsg

M = 60_000
# recent, minute-aligned base time (live code compares against the account's creation time)
BASE = (int(time.time() * 1000) // M) * M - 40 * M
DOWN_UP_DOWN = [10, 9, 8, 7, 6, 7, 8, 9, 10, 11, 10, 9, 8, 7, 6]


def run(c):
    return asyncio.run(c)


@pytest.fixture
def sim(service, settings):
    settings.sma_fast, settings.sma_slow = 2, 4
    settings.sim_fee_bps, settings.sim_slippage_bps = 0, 0
    settings.sim_initial_cash = 1_000

    def price(sym):
        st = service.status.symbols.get(sym) or {}
        return st.get("last_price"), st.get("last_source_ms")

    r = LiveSimRunner(settings, service.store, service.hub, price)
    r.start()
    r.books["AAPL"].account_created_ms = 0  # treat every candle in the test as "after start"
    service.listeners.append(r.on_change)
    return r


async def candle(svc, i, close):
    """One candle: a tick at its start, then a tick 30 s in with the close."""
    t = BASE + i * M
    await svc.ingest_yahoo_message(ymsg("AAPL", t + 1_000, close), received_ms=t + 1_100)
    await svc.ingest_yahoo_message(ymsg("AAPL", t + 30_000, close), received_ms=t + 30_100)


async def finalize(svc, t):
    for ch in svc.engine.finalize_due(t):
        await svc._broadcast_change(ch)


async def play(svc, closes):
    for i, c in enumerate(closes):
        await candle(svc, i, c)
        # first tick of the next minute arrives, then the grace period passes
        await finalize(svc, BASE + (i + 1) * M + 5_000)


def rows(svc, sql, *a):
    return [dict(r) for r in svc.store.conn.execute(sql, a).fetchall()]


def test_crossovers_place_and_fill_simulated_orders(service, sim):
    run(play(service, DOWN_UP_DOWN))
    sigs = rows(service, "SELECT signal, action, bucket_start_ms FROM sim_signals ORDER BY id")
    assert [(s["signal"], s["action"]) for s in sigs] == [("up", "buy"), ("down", "sell")]
    fills = rows(service, "SELECT * FROM sim_fills ORDER BY id")
    assert [f["side"] for f in fills] == ["buy", "sell"]
    # filled at the latest observed price when the signal was confirmed
    up_bucket = sigs[0]["bucket_start_ms"]
    assert fills[0]["ref_price"] == DOWN_UP_DOWN[(up_bucket - BASE) // M]
    assert fills[0]["ref_source_ms"] is not None
    view = sim.account_view("AAPL")
    snap = view["snapshot"]
    assert snap["position_qty"] == 0
    assert snap["equity"] == pytest.approx(1_000 + snap["realized_pnl"])
    assert view["stats"]["orders"] == 2


def test_indicator_matches_batch_after_live_updates(service, sim):
    run(play(service, DOWN_UP_DOWN))
    v = sim.verify("AAPL")
    assert v["match"] is True and v["completed_candles"] == len(DOWN_UP_DOWN)


def test_provisional_cross_is_shown_but_not_traded(service, sim):
    msgs = []

    async def capture(m, symbol=None):
        msgs.append(m)

    service.hub.broadcast = capture
    run(play(service, [10, 9, 8, 7, 6]))
    # forming candle jumps up => provisional up-cross, no order
    run(service.ingest_yahoo_message(ymsg("AAPL", BASE + 5 * M + 1_000, 20), received_ms=BASE + 5 * M + 1_100))
    prov = [m for m in msgs if m.get("type") == "indicator" and m["point"]["provisional"]]
    assert prov and prov[-1]["point"]["signal"] == "up"
    assert rows(service, "SELECT COUNT(*) n FROM sim_orders")[0]["n"] == 0
    # price falls back before the candle completes: no confirmed signal
    run(service.ingest_yahoo_message(ymsg("AAPL", BASE + 5 * M + 50_000, 5), received_ms=BASE + 5 * M + 50_100))
    run(finalize(service, BASE + 6 * M + 5_000))
    assert rows(service, "SELECT COUNT(*) n FROM sim_signals")[0]["n"] == 0


def test_no_trading_on_candles_completed_before_account_existed(service, sim):
    sim.books["AAPL"].account_created_ms = BASE + 100 * M  # account "created" after all these candles
    run(play(service, DOWN_UP_DOWN))
    assert rows(service, "SELECT COUNT(*) n FROM sim_signals")[0]["n"] == 0
    assert sim.verify("AAPL")["match"] is True  # indicators still computed


def test_late_revision_triggers_rebuild(service, sim):
    async def scenario():
        await play(service, DOWN_UP_DOWN[:8])
        # late tick for an already-complete candle changes its close
        await service.ingest_yahoo_message(ymsg("AAPL", BASE + 3 * M + 59_000, 3.0), received_ms=BASE + 9 * M)
        await asyncio.sleep(0.7)  # debounced rebuild

    run(scenario())
    assert sim.books["AAPL"].rebuilds >= 1
    assert sim.verify("AAPL")["match"] is True


def test_account_survives_restart(service, sim, settings):
    run(play(service, DOWN_UP_DOWN[:9]))  # ends long
    before = sim.account_view("AAPL")["snapshot"]
    assert before["position_qty"] > 0
    again = LiveSimRunner(settings, service.store, service.hub, lambda s: (None, None))
    again.start()
    a = again.books["AAPL"].account
    assert a.cash == pytest.approx(before["cash"]) and a.position.qty == pytest.approx(before["position_qty"])
    assert again.books["AAPL"].account_id == sim.books["AAPL"].account_id


def test_changed_settings_start_a_new_account(service, sim, settings):
    old_id = sim.books["AAPL"].account_id
    settings.sma_slow = 5
    again = LiveSimRunner(settings, service.store, service.hub)
    again.start()
    assert again.books["AAPL"].account_id != old_id
    act = rows(service, "SELECT id, active FROM sim_accounts WHERE symbol='AAPL' ORDER BY id")
    assert act[-2] == {"id": old_id, "active": 0} and act[-1]["active"] == 1


def test_reset_keeps_old_account(service, sim):
    old = sim.books["AAPL"].account_id
    view = run(sim.reset("AAPL"))
    assert view["account_id"] != old
    assert rows(service, "SELECT active FROM sim_accounts WHERE id=?", old)[0]["active"] == 0


def test_yahoo_refresh_drops_unfinished_bar_and_caches(service):
    now = int(time.time() * 1000)
    start = (now // 300_000) * 300_000
    idx = pd.to_datetime([start - 600_000, start - 300_000, start], unit="ms", utc=True)
    df = pd.DataFrame({"Open": [1, 2, 3], "High": [1, 2, 3], "Low": [1, 2, 3], "Close": [1, 2, 3],
                       "Volume": [10, 20, 30]}, index=idx)
    info = run(refresh_yahoo(service.store, "SPY", "5m", fetch=lambda s, i: df))
    assert info["ok"] and info["rows"] == 2 and info["dropped_incomplete"] == 1
    assert [b.close for b in load_cached_yahoo(service.store, "SPY", "5m")] == [1, 2]

    def boom(s, i):
        raise ConnectionError("offline")

    info = run(refresh_yahoo(service.store, "SPY", "5m", fetch=boom))
    assert not info["ok"] and "offline" in info["error"]
    assert len(load_cached_yahoo(service.store, "SPY", "5m")) == 2  # cache still usable


def test_candle_filled_in_behind_the_latest_is_handled(service, sim):
    """A delayed tick can create an *older* candle after a newer one was used
    (mock feed does this). The SMA state must be rebuilt to include it."""
    async def scenario():
        await play(service, [10, 9, 8])          # candles 0,1,2 complete
        # candle 4 arrives and completes before any tick for candle 3 exists
        t4 = BASE + 4 * M
        await service.ingest_yahoo_message(ymsg("AAPL", t4 + 1_000, 7), received_ms=t4 + 1_100)
        await finalize(service, BASE + 5 * M + 5_000)
        # now a delayed tick creates candle 3, and it is finalised
        await service.ingest_yahoo_message(ymsg("AAPL", BASE + 3 * M + 10_000, 6), received_ms=BASE + 5 * M + 6_000)
        await finalize(service, BASE + 5 * M + 7_000)
        return sim.verify("AAPL")   # checked immediately, before the debounced rebuild

    v = run(scenario())
    assert v["match"] is True and v["completed_candles"] == 5 and v["rebuilt_before_check"] is True
