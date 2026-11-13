"""Live Yahoo feed client against fake WebSocket servers (no network).

These are mocked checks of our client logic. They show how the app behaves
when the connection fails or frames arrive; they do not prove Yahoo's real
streamer behaves this way — that is shown in the live demo video.
"""
import asyncio
import json

from app.feeds.yahoo_live import YahooLiveFeed

from .conftest import T0, encode_frame


class FakeWS:
    def __init__(self, frames, hold_open=False):
        self.frames = list(frames)
        self.sent = []
        self.hold_open = hold_open
        self.closed = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, data):
        self.sent.append(json.loads(data))

    async def close(self):
        self.closed.set()

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for f in self.frames:
            yield f
        if self.hold_open:
            await self.closed.wait()


async def wait_for(pred, timeout=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


def test_unreachable_server_reports_unavailable_and_retries(service):
    calls = []

    def connect(url, **kw):
        calls.append(url)
        raise OSError("Name or service not known")

    async def scenario():
        feed = YahooLiveFeed(service, ["AAPL"], "wss://example.invalid", connect=connect, backoff_cap=0.05)
        task = asyncio.create_task(feed.run())
        await wait_for(lambda: len(calls) >= 2)
        assert service.status.state in ("unavailable", "connecting")
        await feed.stop()
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())
    events = service.store.recent_events()
    unavailable = [e for e in events if e["state"] == "unavailable"]
    assert unavailable and "OSError" in unavailable[-1]["detail"]
    assert service.status.reconnect_attempts >= 2
    assert service.status.state == "stopped"


def test_frames_are_decoded_stored_and_candled(service):
    frames = [encode_frame("AAPL", T0 + 1_000, 230.25, 1000),
              encode_frame("AAPL", T0 + 2_000, 230.75, 1300),
              "not json"]
    ws = FakeWS(frames, hold_open=True)

    async def scenario():
        feed = YahooLiveFeed(service, ["AAPL"], "wss://fake", connect=lambda url, **kw: ws, backoff_cap=0.05)
        task = asyncio.create_task(feed.run())
        await wait_for(lambda: service.counters["rejected"] == 1)
        assert service.status.state == "live"
        assert ws.sent[0] == {"subscribe": ["AAPL"]}
        await feed.stop()
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())
    c = service.store.candles("AAPL", "1m")[0]
    assert (c.open, c.close, c.tick_count) == (230.25, 230.75, 2)
    src = service.store.conn.execute("SELECT DISTINCT source FROM observations").fetchall()
    assert [r[0] for r in src] == ["yahoo-ws"]


def test_server_closing_connection_is_an_outage(service):
    async def scenario():
        feed = YahooLiveFeed(service, ["AAPL"], "wss://fake", connect=lambda url, **kw: FakeWS([]),
                             backoff_cap=0.05)
        task = asyncio.create_task(feed.run())
        await wait_for(lambda: any(e["state"] == "unavailable" for e in service.store.recent_events()))
        await feed.stop()
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())
    assert any("server closed" in (e["detail"] or "") for e in service.store.recent_events())


def test_injected_fault_and_recovery(service):
    sockets = []

    def connect(url, **kw):
        ws = FakeWS([], hold_open=True)
        sockets.append(ws)
        return ws

    async def scenario():
        feed = YahooLiveFeed(service, ["AAPL"], "wss://fake", connect=connect, backoff_cap=0.05)
        task = asyncio.create_task(feed.run())
        await wait_for(lambda: service.status.state == "live")
        await feed.set_fault(True)
        await wait_for(lambda: service.status.state == "unavailable")
        assert "injected fault" in service.status.detail
        assert service.status.fault_injected is True
        await feed.set_fault(False)
        await wait_for(lambda: service.status.state == "live" and len(sockets) >= 2)
        await feed.stop()
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())
    states = [e["state"] for e in reversed(service.store.recent_events())]
    i = states.index("unavailable")
    assert "live" in states[i:]  # recovered after the outage
