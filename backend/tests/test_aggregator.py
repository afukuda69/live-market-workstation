"""Pure candle-building rules (no database)."""
from app.aggregator import Bar, CandleEngine, MemoryCandleBook, Tick, compute_volume, Candle

from .conftest import T0

M = 60_000


def engine():
    return CandleEngine(MemoryCandleBook(), M, "1m", grace_ms=5_000)


def tick(t, price, dv=None, recv=None, sym="AAPL"):
    return Tick(sym, t, price, dv, recv if recv is not None else t + 200)


def test_first_tick_creates_provisional_candle():
    e = engine()
    ch = e.apply_tick(tick(T0 + 1_000, 100.0))
    c = ch.candle
    assert ch.reason == "new" and ch.material
    assert (c.open, c.high, c.low, c.close) == (100.0, 100.0, 100.0, 100.0)
    assert c.bucket_start_ms == T0 and c.bucket_end_ms == T0 + M
    assert c.is_complete is False and c.revision == 1


def test_ticks_update_high_low_close_and_revision():
    e = engine()
    e.apply_tick(tick(T0 + 1_000, 100.0))
    e.apply_tick(tick(T0 + 2_000, 103.0))
    ch = e.apply_tick(tick(T0 + 3_000, 99.0))
    c = ch.candle
    assert (c.open, c.high, c.low, c.close) == (100.0, 103.0, 99.0, 99.0)
    assert c.revision == 3 and c.tick_count == 3


def test_unchanged_price_is_not_a_new_revision():
    e = engine()
    e.apply_tick(tick(T0 + 1_000, 100.0))
    ch = e.apply_tick(tick(T0 + 2_000, 100.0))
    assert ch.material is False and ch.candle.revision == 1 and ch.candle.tick_count == 2


def test_out_of_order_tick_does_not_overwrite_newer_close():
    e = engine()
    e.apply_tick(tick(T0 + 10_000, 100.0))
    e.apply_tick(tick(T0 + 30_000, 101.0))
    ch = e.apply_tick(tick(T0 + 20_000, 105.0, recv=T0 + 31_000))  # older source time, arrives later
    c = ch.candle
    assert ch.reason == "out_of_order"
    assert c.close == 101.0  # close still from the newest source time
    assert c.high == 105.0   # but the price did trade, so high includes it
    assert e.counters["out_of_order"] == 1


def test_out_of_order_tick_earlier_than_first_sets_open():
    e = engine()
    e.apply_tick(tick(T0 + 10_000, 100.0))
    ch = e.apply_tick(tick(T0 + 2_000, 98.0, recv=T0 + 11_000))
    assert ch.candle.open == 98.0 and ch.candle.close == 100.0


def test_bucket_is_decided_by_source_time_not_arrival():
    e = engine()
    e.apply_tick(tick(T0 + M + 1_000, 200.0))  # next minute
    ch = e.apply_tick(tick(T0 + 59_000, 199.0, recv=T0 + M + 2_000))  # previous minute, arrives late
    assert ch.candle.bucket_start_ms == T0


def test_finalize_waits_for_grace():
    e = engine()
    e.apply_tick(tick(T0 + 1_000, 100.0))
    assert e.finalize_due(T0 + M + 4_999) == []
    done = e.finalize_due(T0 + M + 5_000)
    assert len(done) == 1 and done[0].candle.is_complete and done[0].reason == "finalize"
    assert done[0].candle.revision == 2


def test_late_tick_revises_complete_candle_and_keeps_it_complete():
    e = engine()
    e.apply_tick(tick(T0 + 1_000, 100.0))
    e.finalize_due(T0 + M + 5_000)
    ch = e.apply_tick(tick(T0 + 59_500, 90.0, recv=T0 + M + 9_000))
    assert ch.reason == "late" and ch.material
    assert ch.candle.is_complete is True
    assert ch.candle.low == 90.0 and ch.candle.revision == 3
    assert e.counters["late"] == 1


def test_volume_from_cumulative_day_volume():
    e = engine()
    e.apply_tick(tick(T0 + 1_000, 100.0, dv=1_000))
    e.apply_tick(tick(T0 + 30_000, 100.5, dv=1_400))
    c1 = e.book.get("AAPL", "1m", T0)
    assert c1.volume == 400  # first candle: max - min within bucket
    e.apply_tick(tick(T0 + M + 1_000, 101.0, dv=1_650))
    c2 = e.book.get("AAPL", "1m", T0 + M)
    assert c2.volume == 250  # 1650 - previous candle's last running total 1400


def test_volume_handles_session_reset():
    prev_max = 5_000_000
    c = Candle("AAPL", "1m", T0, T0 + M, 1, 1, 1, 1, dv_min=100, dv_max=300)
    assert compute_volume(c, prev_max) == 200  # counter went down => use in-bucket baseline


def test_history_bar_fills_gap_and_can_be_revised():
    e = engine()
    b1 = Bar("AAPL", T0, 1, 2, 0.5, 1.5, 1000, False, T0 + 30_000)
    ch = e.apply_bar(b1)
    assert ch.reason == "backfill" and ch.candle.origin == "history" and not ch.candle.is_complete
    b2 = Bar("AAPL", T0, 1, 2.5, 0.5, 2.2, 1500, True, T0 + 70_000)
    ch2 = e.apply_bar(b2)
    assert ch2.reason == "backfill_revision" and ch2.candle.revision == 2
    assert ch2.candle.close == 2.2 and ch2.candle.volume == 1500 and ch2.candle.is_complete


def test_history_bar_never_overwrites_tick_candle():
    e = engine()
    e.apply_tick(tick(T0 + 1_000, 100.0))
    assert e.apply_bar(Bar("AAPL", T0, 1, 2, 0.5, 1.5, 1000, True, T0 + 70_000)) is None
    assert e.book.get("AAPL", "1m", T0).close == 100.0
    assert e.counters["ignored_bars"] == 1


def test_live_tick_extends_history_candle():
    e = engine()
    e.apply_bar(Bar("AAPL", T0, 100, 101, 99, 100.5, 1000, False, T0 + 20_000))
    ch = e.apply_tick(tick(T0 + 40_000, 102.0, dv=50_000))
    c = ch.candle
    assert c.open == 100 and c.high == 102.0 and c.close == 102.0
    e.apply_tick(tick(T0 + 50_000, 101.5, dv=50_300))
    assert e.book.get("AAPL", "1m", T0).volume == 1000 + 300
