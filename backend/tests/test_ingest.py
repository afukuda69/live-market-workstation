"""Ingest path with SQLite: identity, duplicates, revisions, out-of-order, rejects."""
import asyncio

from app.aggregator import Bar

from .conftest import T0, ymsg

M = 60_000


def run(coro):
    return asyncio.run(coro)


def rows(svc, sql, *args):
    return [dict(r) for r in svc.store.conn.execute(sql, args).fetchall()]


def test_observation_keeps_source_and_receipt_times(service):
    run(service.ingest_yahoo_message(ymsg("AAPL", T0 + 1_000, 230.12, 5_000), received_ms=T0 + 1_450))
    (o,) = rows(service, "SELECT * FROM observations")
    assert o["source"] == "yahoo-ws" and o["kind"] == "tick"
    assert o["source_time_ms"] == T0 + 1_000 and o["received_time_ms"] == T0 + 1_450
    assert o["price"] == 230.12 and o["day_volume"] == 5_000
    assert '"id": "AAPL"' in o["payload_json"]


def test_float32_price_is_rounded_to_price_hint(service):
    run(service.ingest_yahoo_message(ymsg("AAPL", T0 + 1_000, 189.30999755859375), received_ms=T0 + 1_100))
    assert rows(service, "SELECT price FROM observations")[0]["price"] == 189.31


def test_duplicate_is_stored_once_and_counted(service):
    m = ymsg("AAPL", T0 + 1_000, 230.0, 5_000)
    first = run(service.ingest_yahoo_message(m, received_ms=T0 + 1_100))
    again = run(service.ingest_yahoo_message(dict(m), received_ms=T0 + 2_500))
    assert first is not None and again is None
    (o,) = rows(service, "SELECT * FROM observations")
    assert o["duplicate_count"] == 1
    assert o["received_time_ms"] == T0 + 1_100  # first receipt time is preserved
    assert o["last_duplicate_ms"] == T0 + 2_500
    assert len(rows(service, "SELECT * FROM candle_revisions")) == 1  # duplicate caused no revision
    assert service.counters["duplicates"] == 1


def test_same_time_different_price_is_not_a_duplicate(service):
    run(service.ingest_yahoo_message(ymsg("AAPL", T0 + 1_000, 230.0), received_ms=T0 + 1_100))
    run(service.ingest_yahoo_message(ymsg("AAPL", T0 + 1_000, 230.5), received_ms=T0 + 1_200))
    assert len(rows(service, "SELECT * FROM observations")) == 2


def test_every_material_change_appends_a_revision(service):
    for i, p in enumerate([100.0, 101.0, 101.0, 99.0]):
        run(service.ingest_yahoo_message(ymsg("AAPL", T0 + 1_000 * (i + 1), p), received_ms=T0 + 1_000 * (i + 1) + 50))
    revs = service.store.revisions("AAPL", "1m", T0)
    assert [r["revision"] for r in revs] == [1, 2, 3]  # 101.0 repeat changed nothing
    assert [r["reason"] for r in revs] == ["new", "update", "update"]
    c = service.store.candles("AAPL", "1m")[0]
    assert c.revision == 3 and c.tick_count == 4 and (c.high, c.low, c.close) == (101.0, 99.0, 99.0)


def test_out_of_order_and_late_are_recorded(service):
    svc = service
    run(svc.ingest_yahoo_message(ymsg("AAPL", T0 + 10_000, 100.0), received_ms=T0 + 10_100))
    run(svc.ingest_yahoo_message(ymsg("AAPL", T0 + 40_000, 101.0), received_ms=T0 + 40_100))
    run(svc.ingest_yahoo_message(ymsg("AAPL", T0 + 20_000, 104.0), received_ms=T0 + 41_000))  # out of order
    svc.engine.finalize_due(T0 + M + 5_000)
    run(svc.ingest_yahoo_message(ymsg("AAPL", T0 + 59_000, 97.0), received_ms=T0 + M + 8_000))  # late
    reasons = [r["reason"] for r in svc.store.revisions("AAPL", "1m", T0)]
    assert reasons == ["new", "update", "out_of_order", "finalize", "late"]
    c = svc.store.candles("AAPL", "1m")[0]
    assert c.is_complete and (c.open, c.high, c.low, c.close) == (100.0, 104.0, 97.0, 97.0)  # late tick is newest => close


def test_revised_history_bar_creates_backfill_revision(service):
    b1 = Bar("AAPL", T0, 10, 11, 9, 10.5, 100, False, T0 + 30_000)
    b1_again = Bar("AAPL", T0, 10, 11, 9, 10.5, 100, False, T0 + 40_000)
    b2 = Bar("AAPL", T0, 10, 11.5, 9, 11.2, 180, True, T0 + 70_000)
    run(service.ingest_bar(b1, {}))
    assert run(service.ingest_bar(b1_again, {})) is None  # identical re-download => duplicate
    run(service.ingest_bar(b2, {}))
    revs = service.store.revisions("AAPL", "1m", T0)
    assert [r["reason"] for r in revs] == ["backfill", "backfill_revision"]
    assert revs[-1]["close"] == 11.2 and revs[-1]["is_complete"] == 1
    assert len(rows(service, "SELECT * FROM observations WHERE kind='bar'")) == 2


def test_malformed_messages_are_rejected_and_logged(service):
    bad = [{"price": 1.0, "time": str(T0)}, ymsg("AAPL", T0, -5.0), ymsg("AAPL", T0, float("nan")),
           {"id": "AAPL", "price": 1.0, "time": "not-a-number"}]
    for m in bad:
        assert run(service.ingest_yahoo_message(m, received_ms=T0 + 1_000)) is None
    assert service.counters["rejected"] == 4
    assert rows(service, "SELECT COUNT(*) n FROM observations")[0]["n"] == 0
    assert any(e["state"] == "rejected" for e in service.store.recent_events())


def test_future_timestamp_rejected(service):
    assert run(service.ingest_yahoo_message(ymsg("AAPL", T0 + 10 * M, 1.0), received_ms=T0)) is None


def test_stats_summarise_database(service):
    m = ymsg("AAPL", T0 + 1_000, 1.0)
    run(service.ingest_yahoo_message(m, received_ms=T0 + 1_100))
    run(service.ingest_yahoo_message(m, received_ms=T0 + 1_200))
    st = service.store.stats()
    assert st["observations"] == 1 and st["duplicates"] == 1 and st["candles"] == 1
    assert st["revisions_by_reason"] == {"new": 1}
