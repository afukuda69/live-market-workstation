"""Backtest: execution timing, no look-ahead, metrics, real-data run."""
import pytest

from app.accounting import AccountConfig
from app.backtest import HistBar, max_drawdown_pct, run_backtest
from app.history_data import describe, load_fixture

FIXTURE = "spy_1m_2026-11-12.csv"
M = 60_000


def bars_from_closes(closes, opens=None):
    opens = opens or closes
    return [HistBar(i * M, o, max(o, c), min(o, c), c) for i, (o, c) in enumerate(zip(opens, closes))]


NO_COST = AccountConfig(initial_cash=1_000, fee_bps=0, slippage_bps=0)


def test_signal_at_close_fills_at_next_open():
    closes = [5, 4, 3, 2, 1, 2, 3, 4, 5, 6, 7]
    opens = [c + 0.5 for c in closes]  # opens differ from closes so we can tell them apart
    r = run_backtest(bars_from_closes(closes, opens), 2, 4, NO_COST)
    up = [s for s in r.signals if s["signal"] == "up"][0]
    assert up["time_ms"] == 6 * M and up["action"] == "buy"
    buy = r.trades[0]
    assert buy["time_ms"] == 7 * M           # the next bar
    assert buy["ref_price"] == opens[7]      # at its open, not the signal bar's close


def test_hand_worked_round_trip():
    # up-cross on bar 6, buy at bar 7 open (4); down-cross later, sell at next open
    closes = [5, 4, 3, 2, 1, 2, 3, 4, 5, 6, 5, 4, 3, 2, 1, 1]
    r = run_backtest(bars_from_closes(closes), 2, 4, NO_COST)
    assert [t["side"] for t in r.trades] == ["buy", "sell"]
    buy, sell = r.trades
    assert buy["price"] == 4 and buy["qty"] == 250            # 1000 cash / 4
    down = [s for s in r.signals if s["signal"] == "down"][0]
    assert sell["time_ms"] == down["time_ms"] + M
    assert sell["price"] == closes[sell["time_ms"] // M]       # open == close in this data
    expected = 1_000 + 250 * (sell["price"] - 4)
    assert r.metrics["equity"] == pytest.approx(expected)
    assert r.round_trips[0]["pnl"] == pytest.approx(expected - 1_000)


def test_no_look_ahead_truncation():
    """Decisions and fills before bar k must not change when bars after k are
    removed or altered."""
    bars = load_fixture(FIXTURE)
    full = run_backtest(bars, 10, 30)
    for k in (60, 150, 240, 389):
        cut = run_backtest(bars[:k], 10, 30)
        t_k = bars[k - 1].time_ms
        assert [s for s in full.signals if s["time_ms"] <= t_k] == cut.signals
        assert [t for t in full.trades if t["time_ms"] <= t_k] == cut.trades
        # and wildly different future bars do not change the past either
        future = [HistBar(b.time_ms, b.open * 3, b.high * 3, b.low * 3, b.close * 3) for b in bars[k:]]
        mixed = run_backtest(bars[:k] + future, 10, 30)
        assert [s for s in mixed.signals if s["time_ms"] <= t_k - M] == \
               [s for s in cut.signals if s["time_ms"] <= t_k - M]


def test_signal_on_last_bar_is_not_executed():
    closes = [5, 4, 3, 2, 1, 2, 3]  # up-cross on the last bar
    r = run_backtest(bars_from_closes(closes), 2, 4, NO_COST)
    assert r.signals[-1]["signal"] == "up" and r.trades == []
    assert any("not executed" in n for n in r.notes)


def test_max_drawdown():
    assert max_drawdown_pct([100, 120, 90, 130, 65]) == pytest.approx(50.0)
    assert max_drawdown_pct([1, 2, 3]) == 0


def test_real_fixture_run_is_consistent():
    bars = load_fixture(FIXTURE)
    assert describe(bars, M)["gaps"] == 0 and len(bars) == 390
    r = run_backtest(bars, 10, 30)
    m = r.metrics
    assert m["bars"] == 390 and m["warmup_bars"] == 29
    # no indicator value before the window is full
    assert all(p["slow"] is None for p in r.indicators[:29]) and r.indicators[29]["slow"] is not None
    # every fill happens at the open of the bar after its signal
    times = {b.time_ms: b for b in bars}
    for t in r.trades:
        assert t["ref_price"] == times[t["time_ms"]].open
    # accounting identity at the end
    assert m["equity"] - m["initial_cash"] == pytest.approx(m["realized_pnl"] + m["unrealized_pnl"] - m["fees"])
    # alternating buy/sell (long-only, one position at a time)
    sides = [t["side"] for t in r.trades]
    assert all(a != b for a, b in zip(sides, sides[1:])) and (not sides or sides[0] == "buy")
    assert m["buy_and_hold_return_pct"] is not None
    assert len(r.equity) == 390


def test_costs_reduce_returns():
    bars = load_fixture(FIXTURE)
    free = run_backtest(bars, 10, 30, AccountConfig(fee_bps=0, slippage_bps=0)).metrics["equity"]
    costly = run_backtest(bars, 10, 30, AccountConfig(fee_bps=10, slippage_bps=5)).metrics["equity"]
    assert costly < free


def test_too_few_bars_is_reported():
    r = run_backtest(bars_from_closes([1, 2, 3]), 2, 4, NO_COST)
    assert r.trades == [] and any("not enough" in n for n in r.notes)
