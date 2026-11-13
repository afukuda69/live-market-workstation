"""Replay: recorded observations rebuild the stored candles exactly."""
import asyncio

from app.aggregator import Bar
from app.feeds.replay import rebuild, stream_replay

from .conftest import T0, ymsg

M = 60_000


def run(c):
    return asyncio.run(c)


def record_messy_session(svc):
    """History bars, ticks with duplicates, out-of-order and late delivery,
    across three minutes — with live finalisation in between."""
    run(svc.ingest_bar(Bar("AAPL", T0 - M, 99, 100, 98, 99.5, 900, True, T0 + 100), {}))
    seq = [
        (T0 + 1_000, 100.0, 10_000, T0 + 1_200),
        (T0 + 15_000, 101.0, 10_400, T0 + 15_100),
        (T0 + 15_000, 101.0, 10_400, T0 + 15_900),  # duplicate
        (T0 + 45_000, 100.2, 10_900, T0 + 45_100),
        (T0 + 30_000, 102.5, 10_700, T0 + 46_000),  # out of order
        (T0 + M + 2_000, 100.9, 11_200, T0 + M + 2_100),
    ]
    for t, p, dv, recv in seq:
        run(svc.ingest_yahoo_message(ymsg("AAPL", t, p, dv), received_ms=recv))
    svc.engine.finalize_due(T0 + M + 5_000)  # minute 0 completes
    run(svc.ingest_yahoo_message(ymsg("AAPL", T0 + 59_000, 99.0, 11_000), received_ms=T0 + M + 7_000))  # late
    run(svc.ingest_yahoo_message(ymsg("AAPL", T0 + 2 * M + 1_000, 101.4, 11_600), received_ms=T0 + 2 * M + 1_100))
    svc.engine.finalize_due(T0 + 2 * M + 5_000)  # minute 1 completes


def test_replay_reproduces_stored_candles(service, settings):
    record_messy_session(service)
    stored = service.store.candles("AAPL", "1m")
    book = rebuild(service.store, settings, "AAPL", end_clock_ms=T0 + 2 * M + 10_000)
    replayed = book.all("AAPL")
    assert [c.bucket_start_ms for c in replayed] == [c.bucket_start_ms for c in stored]
    for a, b in zip(stored, replayed):
        assert a.ohlcv() == b.ohlcv(), a.bucket_start_ms
        assert a.is_complete == b.is_complete
        assert a.revision == b.revision
    # replay must not write to the stored candles
    assert service.store.candles("AAPL", "1m")[-1].revision == stored[-1].revision


def test_replay_preserves_arrival_order_reasons(service, settings):
    record_messy_session(service)
    book = rebuild(service.store, settings, "AAPL", end_clock_ms=T0 + 2 * M + 10_000)
    reasons = [r for r, _ in book.revisions]
    assert "out_of_order" in reasons and "late" in reasons and "backfill" in reasons


def test_stream_replay_sends_ordered_messages(service, settings):
    record_messy_session(service)
    sent = []

    async def send(m):
        sent.append(m)

    stats = run(stream_replay(service.store, settings, "AAPL", send, speed=0))
    assert sent[0]["type"] == "replay_started" and sent[-1]["type"] == "replay_finished"
    candles = [m for m in sent if m["type"] == "replay_candle"]
    assert candles and all("reason" in m and "candle" in m for m in candles)
    assert stats["observations"] == 8  # 1 bar + 7 unique ticks (duplicate stored once)
    clocks = [m["replay_clock_ms"] for m in candles]
    assert clocks == sorted(clocks)


def test_replay_of_empty_symbol(service, settings):
    sent = []

    async def send(m):
        sent.append(m)

    run(stream_replay(service.store, settings, "BTC-USD", send, speed=0))
    assert [m["type"] for m in sent] == ["replay_started", "replay_finished"]
