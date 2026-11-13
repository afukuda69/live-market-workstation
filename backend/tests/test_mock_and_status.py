"""Simulated feed and stale-feed detection."""
import asyncio

from app.feeds.mock import MockFeed
from app.service import now_ms


def test_mock_feed_is_labelled_and_exercises_duplicates_and_out_of_order(service):
    async def scenario():
        feed = MockFeed(service, ["AAPL", "BTC-USD"], rate_hz=200, duplicate_every=5,
                        out_of_order_every=6, seed=1)
        task = asyncio.create_task(feed.run())
        await asyncio.sleep(0.4)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert service.status.state == "live" and "SIMULATED" in service.status.detail
    assert service.counters["duplicates"] > 0
    srcs = {r[0] for r in service.store.conn.execute("SELECT DISTINCT source FROM observations")}
    assert srcs == {"mock"}


def test_silent_live_feed_becomes_stale_then_recovers(service):
    from .conftest import ymsg

    async def scenario():
        await service.set_state("live", "connected")
        service.status.stale_after_s = 0.5
        service.status.last_message_received_ms = now_ms() - 2_000
        hk = asyncio.create_task(service.housekeeping_loop())
        await asyncio.sleep(0.2)
        assert service.status.state == "stale"
        await service.ingest_yahoo_message(ymsg("AAPL", now_ms(), 1.0))
        assert service.status.state == "live"
        hk.cancel()
        await asyncio.gather(hk, return_exceptions=True)

    asyncio.run(scenario())
    states = [e["state"] for e in reversed(service.store.recent_events())]
    assert states[-2:] == ["stale", "live"]
