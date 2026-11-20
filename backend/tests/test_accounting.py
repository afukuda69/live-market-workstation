"""Simulated accounting checked against hand-worked examples."""
import random

import pytest

from app.accounting import Account, AccountConfig


def acct(cash=10_000, fee=0, slip=0, alloc=1.0):
    return Account(AccountConfig(initial_cash=cash, fee_bps=fee, slippage_bps=slip, allocation=alloc))


def test_buy_then_sell_no_costs():
    a = acct()
    a.fill_order(a.new_order(0, "buy", 10), 1, 100.0)
    assert a.cash == 9_000 and a.position.qty == 10 and a.position.avg_cost == 100
    assert a.unrealized_pnl(105) == 50 and a.equity(105) == 10_050
    a.fill_order(a.new_order(2, "sell", 10), 3, 110.0)
    assert a.cash == 10_100 and a.position.qty == 0
    assert a.realized_pnl == 100 and a.unrealized_pnl(110) == 0


def test_fees_worked_example():
    # 0.10% fee. Buy 10 @ 100: notional 1000, fee 1.00 -> cash 8999.
    # Sell 10 @ 110: notional 1100, fee 1.10 -> cash 10097.90.
    a = acct(fee=10)
    a.fill_order(a.new_order(0, "buy", 10), 1, 100.0)
    assert a.cash == pytest.approx(8_999.0)
    a.fill_order(a.new_order(2, "sell", 10), 3, 110.0)
    assert a.cash == pytest.approx(10_097.90)
    assert a.realized_pnl == pytest.approx(100.0)   # price P&L before fees
    assert a.fees == pytest.approx(2.10)
    assert a.equity(110) - 10_000 == pytest.approx(97.90)  # net of fees


def test_slippage_moves_price_against_trader():
    a = acct(slip=50)  # 0.50%
    o = a.fill_order(a.new_order(0, "buy", 1), 1, 200.0)
    assert o.fill.price == pytest.approx(201.0) and o.fill.ref_price == 200.0
    o = a.fill_order(a.new_order(2, "sell", 1), 3, 200.0)
    assert o.fill.price == pytest.approx(199.0)
    assert a.realized_pnl == pytest.approx(-2.0)


def test_average_cost_and_partial_sell():
    a = acct(cash=100_000)
    a.fill_order(a.new_order(0, "buy", 10), 1, 100.0)
    a.fill_order(a.new_order(0, "buy", 10), 2, 120.0)
    assert a.position.avg_cost == pytest.approx(110.0)
    a.fill_order(a.new_order(0, "sell", 5), 3, 130.0)
    assert a.realized_pnl == pytest.approx(5 * 20.0)
    assert a.position.qty == 15 and a.position.avg_cost == pytest.approx(110.0)
    assert a.unrealized_pnl(100.0) == pytest.approx(15 * -10.0)


def test_sized_buy_uses_allocation_and_covers_fee():
    a = acct(cash=10_000, fee=10, alloc=0.5)
    o = a.fill_order(a.new_order(0, "buy"), 1, 100.0)  # size at fill
    # budget 5000; qty = 5000 / (100 * 1.001) = 49.950049..., rounded down to 6 dp
    assert o.status == "filled" and o.fill.qty == pytest.approx(49.950049, abs=1e-6)
    assert a.cash == pytest.approx(10_000 - 49.950049 * 100 * 1.001, abs=1e-4)
    assert a.cash >= 5_000 - 1e-6


def test_full_allocation_buy_never_overdraws():
    a = acct(cash=1_000, fee=10, slip=5)
    a.fill_order(a.new_order(0, "buy"), 1, 333.33)
    assert 0 <= a.cash < 1.0


def test_sell_without_position_is_rejected():
    a = acct()
    o = a.fill_order(a.new_order(0, "sell", 1), 1, 100.0)
    assert o.status == "rejected" and "long-only" in o.reject_reason
    assert a.cash == 10_000 and not a.fills


def test_insufficient_cash_is_rejected():
    a = acct(cash=100)
    o = a.fill_order(a.new_order(0, "buy", 2), 1, 100.0)
    assert o.status == "rejected" and "insufficient cash" in o.reject_reason


def test_cannot_fill_twice():
    a = acct()
    o = a.fill_order(a.new_order(0, "buy", 1), 1, 100.0)
    with pytest.raises(ValueError):
        a.fill_order(o, 2, 100.0)


def test_rebuild_from_fills_gives_same_account():
    a = acct(fee=10, slip=5)
    for side, px in [("buy", 100), ("sell", 104), ("buy", 99), ("sell", 97), ("buy", 101)]:
        a.fill_order(a.new_order(0, side), 1, px)
    b = acct(fee=10, slip=5)
    for f in a.fills:
        from dataclasses import replace
        b.apply_fill(replace(f))
    assert b.cash == pytest.approx(a.cash) and b.position == a.position
    assert b.realized_pnl == pytest.approx(a.realized_pnl) and b.fees == pytest.approx(a.fees)


def test_pnl_identity_holds_for_random_sequences():
    rng = random.Random(7)
    for _ in range(50):
        a = acct(cash=50_000, fee=rng.choice([0, 5, 10, 25]), slip=rng.choice([0, 5, 20]))
        price = 100.0
        for _ in range(40):
            price *= 1 + rng.gauss(0, 0.01)
            side = "buy" if a.position.qty == 0 else rng.choice(["sell", "buy"])
            a.fill_order(a.new_order(0, side), 1, price)
            identity = a.realized_pnl + a.unrealized_pnl(price) - a.fees
            assert a.equity(price) - 50_000 == pytest.approx(identity, abs=1e-6)
            assert a.cash >= -1e-6 and a.position.qty >= 0
