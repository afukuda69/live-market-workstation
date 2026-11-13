"""HTTP + WebSocket API, end to end inside the app (feeds disabled, ticks injected)."""
from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import T0, ymsg

M = 60_000


def make_client(settings):
    # Long grace so the background housekeeping loop cannot finalise the
    # (historical) test candles mid-test; finalisation is tested elsewhere.
    settings.finalize_grace_seconds = 10 * 365 * 24 * 3600
    return TestClient(create_app(settings, start_feed=False))


def inject(client, msg, received_ms):
    svc = client.app.state.service
    return client.portal.call(lambda: svc.ingest_yahoo_message(msg, received_ms=received_ms))


def test_health_config_status(settings):
    with make_client(settings) as client:
        assert client.get("/api/health").json()["ok"] is True
        cfg = client.get("/api/config").json()
        assert cfg["symbols"] == ["AAPL", "BTC-USD"] and cfg["interval"] == "1m"
        st = client.get("/api/status").json()
        assert st["mode"] == "off" and st["state"] == "stopped"
        assert "server_time_ms" in st and "source_label" in st


def test_websocket_snapshot_and_live_push(settings):
    with make_client(settings) as client:
        inject(client, ymsg("AAPL", T0 + 1_000, 100.0, 10), T0 + 1_100)
        with client.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello" and hello["symbols"] == ["AAPL", "BTC-USD"]
            ws.send_json({"type": "subscribe", "symbol": "aapl"})
            snap = ws.receive_json()
            while snap["type"] != "snapshot":
                snap = ws.receive_json()
            assert snap["symbol"] == "AAPL" and len(snap["candles"]) == 1
            assert snap["candles"][0]["time"] == T0 // 1000

            inject(client, ymsg("AAPL", T0 + 2_000, 101.0, 20), T0 + 2_100)
            got = {}
            while "candle" not in got:
                m = ws.receive_json()
                got[m["type"]] = m
            assert got["candle"]["candle"]["close"] == 101.0
            assert got["candle"]["candle"]["is_complete"] is False
            assert got["candle"]["reason"] == "update"


def test_websocket_rejects_unknown_symbol(settings):
    with make_client(settings) as client, client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "subscribe", "symbol": "NOPE"})
        m = ws.receive_json()
        while m["type"] != "error":
            m = ws.receive_json()
        assert "unknown symbol" in m["error"]


def test_rest_retrieval_revisions_and_verify(settings):
    with make_client(settings) as client:
        inject(client, ymsg("AAPL", T0 + 1_000, 100.0), T0 + 1_100)
        inject(client, ymsg("AAPL", T0 + 5_000, 102.0), T0 + 5_100)
        inject(client, ymsg("AAPL", T0 + 5_000, 102.0), T0 + 5_200)  # duplicate
        candles = client.get("/api/candles", params={"symbol": "AAPL"}).json()["candles"]
        assert len(candles) == 1 and candles[0]["revision"] == 2
        revs = client.get(f"/api/candles/AAPL/{T0}/revisions").json()["revisions"]
        assert [r["reason"] for r in revs] == ["new", "update"]
        obs = client.get("/api/observations", params={"symbol": "AAPL"}).json()["observations"]
        assert len(obs) == 2 and max(o["duplicate_count"] for o in obs) == 1
        v = client.get("/api/replay/verify", params={"symbol": "AAPL"}).json()
        assert v["mismatch_count"] == 0 and v["stored"] == v["replayed"] == 1
        stats = client.get("/api/stats").json()
        assert stats["db"]["duplicates"] == 1
        assert client.get(f"/api/candles/AAPL/{T0 + M}/revisions").status_code == 404


def test_websocket_replay(settings):
    with make_client(settings) as client:
        inject(client, ymsg("AAPL", T0 + 1_000, 100.0), T0 + 1_100)
        inject(client, ymsg("AAPL", T0 + M + 1_000, 101.0), T0 + M + 1_100)
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "replay_start", "symbol": "AAPL", "speed": 0})
            types = []
            while "replay_finished" not in types:
                types.append(ws.receive_json()["type"])
            assert types.count("replay_candle") >= 2


def test_fault_endpoint_requires_live_feed(settings):
    with make_client(settings) as client:
        assert client.post("/api/feed/fault", json={"enabled": True}).status_code == 409


def test_events_logged_on_startup(settings):
    with make_client(settings) as client:
        ev = client.get("/api/events").json()["events"]
        assert any(e["state"] == "startup" for e in ev)
