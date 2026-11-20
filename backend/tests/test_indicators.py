"""SMA and crossover: hand-worked examples, pandas as the trusted reference,
batch vs incremental, and provisional (unfinished-candle) handling."""
import math

import pandas as pd
import pytest

from app.aggregator import Candle
from app.history_data import load_fixture
from app.indicators import IncrementalSMA, crossover, indicator_series, sma_batch

FIXTURE = "spy_1m_2026-11-12.csv"


def close_enough(a, b):
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-9)


def test_sma_hand_example():
    # SMA(3) of 1..6: undefined, undefined, (1+2+3)/3, (2+3+4)/3, ...
    assert sma_batch([1, 2, 3, 4, 5, 6], 3) == [None, None, 2.0, 3.0, 4.0, 5.0]
    assert sma_batch([10, 20], 1) == [10.0, 20.0]


def test_sma_rejects_bad_window():
    with pytest.raises(ValueError):
        sma_batch([1, 2], 0)


@pytest.mark.parametrize("n", [1, 5, 10, 30, 50])
def test_batch_matches_pandas_on_real_spy_data(n):
    closes = [b.close for b in load_fixture(FIXTURE)]
    ours = sma_batch(closes, n)
    ref = pd.Series(closes).rolling(n).mean().tolist()  # trusted reference
    assert len(ours) == len(ref) == 390
    for a, b in zip(ours, ref):
        if isinstance(b, float) and math.isnan(b):
            assert a is None
        else:
            assert close_enough(a, b)


@pytest.mark.parametrize("n", [3, 10, 30])
def test_incremental_equals_batch(n):
    closes = [b.close for b in load_fixture(FIXTURE)]
    inc = IncrementalSMA(n)
    for i, (x, expected) in enumerate(zip(closes, sma_batch(closes, n))):
        assert close_enough(inc.push(x), expected), i


def test_peek_does_not_change_state():
    inc = IncrementalSMA(3)
    for x in [1, 2, 3]:
        inc.push(x)
    assert inc.value == 2.0
    assert inc.peek(9) == pytest.approx((2 + 3 + 9) / 3)
    assert inc.value == 2.0
    assert inc.push(4) == 3.0  # peek did not consume anything


def test_crossover_rules():
    assert crossover(-1, 0.5) == "up"
    assert crossover(1, -0.5) == "down"
    assert crossover(1, 0.5) is None
    assert crossover(0, 0.5) is None   # first defined value is not a crossover
    assert crossover(-1, 0.0) is None  # touching is not crossing
    assert crossover(-1, None) is None


def test_touch_then_cross_signals_once():
    # fast-slow diffs: -1, 0 (touch), +1 => one up-cross, on the +1 bar
    side, sigs = 0, []
    for d in [-1.0, 0.0, 1.0, 0.0, 2.0]:
        sigs.append(crossover(side, d))
        if d != 0:
            side = 1 if d > 0 else -1
    assert sigs == [None, None, "up", None, None]


def _candles(closes, complete_upto=None):
    out = []
    for i, c in enumerate(closes):
        done = complete_upto is None or i < complete_upto
        out.append(Candle("X", "1m", i * 60_000, (i + 1) * 60_000, c, c, c, c, is_complete=done))
    return out


def test_series_marks_crossovers_on_completed_candles():
    closes = [5, 4, 3, 2, 1, 2, 3, 4, 5, 6]  # falls then rises
    pts = indicator_series(_candles(closes), 2, 4)
    sigs = [(i, p.signal) for i, p in enumerate(pts) if p.signal]
    # SMA2 vs SMA4: below while falling; crosses above on the rise
    assert sigs == [(6, "up")]
    assert all(not p.provisional for p in pts)


def test_unfinished_candle_gives_provisional_point_only():
    closes = [5, 4, 3, 2, 1, 2, 3, 9]
    pts = indicator_series(_candles(closes, complete_upto=7), 2, 4)
    assert pts[-1].provisional is True
    assert all(not p.provisional for p in pts[:-1])
    # confirmed values never include the unfinished close
    confirmed = indicator_series(_candles(closes[:7]), 2, 4)
    assert [p.fast for p in pts[:-1]] == [p.fast for p in confirmed]
    # the provisional value is what the SMA would be if the candle closed now
    assert pts[-1].fast == pytest.approx((3 + 9) / 2)


def test_provisional_crossover_can_disappear():
    base = [5, 4, 3, 2, 1, 1, 1]
    up = indicator_series(_candles(base + [6], complete_upto=7), 2, 4)[-1]
    flat = indicator_series(_candles(base + [1], complete_upto=7), 2, 4)[-1]
    assert up.provisional and up.signal == "up"
    assert flat.provisional and flat.signal is None  # price came back: signal gone


def test_no_provisional_point_while_previous_candle_is_unfinished():
    closes = [5, 4, 3, 2, 1, 2, 3, 9]
    cs = _candles(closes, complete_upto=6)  # last two candles both still open
    pts = indicator_series(cs, 2, 4)
    assert all(not p.provisional for p in pts) and len(pts) == 6
