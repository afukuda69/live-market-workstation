"""Part 4.2 HTTP endpoints."""
from fastapi.testclient import TestClient

from app.main import create_app


def client_for(settings):
    settings.finalize_grace_seconds = 10 * 365 * 24 * 3600
    return TestClient(create_app(settings, start_feed=False))


def test_backtest_fixture_saved_and_listed(settings):
    with client_for(settings) as c:
        opts = c.get("/api/backtest/options").json()
        assert "spy_1m_2026-11-12.csv" in opts["fixtures"] and "5m" in opts["yahoo_intervals"]
        r = c.post("/api/backtest", json={"source": "fixture", "fast": 10, "slow": 30})
        assert r.status_code == 200
        d = r.json()
        assert d["data"]["bars"] == 390 and d["data"]["symbol"] == "SPY" and d["data"]["gaps"] == 0
        assert d["metrics"]["round_trips"] >= 1 and len(d["equity"]) == 390
        runs = c.get("/api/backtest/runs").json()["runs"]
        assert runs[0]["id"] == d["id"] and runs[0]["params"]["slow"] == 30
        full = c.get(f"/api/backtest/runs/{d['id']}").json()
        assert full["metrics"] == d["metrics"]


def test_backtest_validation(settings):
    with client_for(settings) as c:
        assert c.post("/api/backtest", json={"source": "fixture", "fast": 30, "slow": 10}).status_code == 422
        assert c.post("/api/backtest", json={"source": "nope"}).status_code == 422
        r = c.post("/api/backtest", json={"source": "stored", "symbol": "AAPL"})
        assert r.status_code == 422 and "completed bars" in r.json()["detail"]
        assert c.post("/api/backtest", json={"source": "fixture", "fixture": "../../etc/passwd"}).status_code == 404


def test_backtest_yahoo_offline_without_cache(settings, monkeypatch):
    import app.main as main

    async def fake_refresh(store, symbol, interval):
        return {"ok": False, "error": "ConnectionError: offline", "fetched_ms": 0, "rows": 0}

    monkeypatch.setattr(main, "refresh_yahoo", fake_refresh)
    with client_for(settings) as c:
        r = c.post("/api/backtest", json={"source": "yahoo", "symbol": "SPY", "interval": "5m"})
        assert r.status_code == 422 and "offline" in r.json()["detail"]


def test_account_indicators_and_reset(settings):
    with client_for(settings) as c:
        acct = c.get("/api/sim/account", params={"symbol": "AAPL"}).json()
        assert acct["snapshot"]["equity"] == settings.sim_initial_cash
        assert acct["params"]["fast"] == settings.sma_fast
        ind = c.get("/api/indicators", params={"symbol": "AAPL"}).json()
        assert ind["points"] == [] and ind["slow"] == settings.sma_slow
        v = c.get("/api/indicators/verify", params={"symbol": "AAPL"}).json()
        assert v["completed_candles"] == 0
        new = c.post("/api/sim/reset", json={"symbol": "AAPL"}).json()
        assert new["account_id"] != acct["account_id"]
        assert c.get("/api/sim/account", params={"symbol": "NOPE"}).status_code == 404


def test_snapshot_includes_indicators_and_account(settings):
    with client_for(settings) as c, c.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "subscribe", "symbol": "AAPL"})
        m = ws.receive_json()
        while m["type"] != "snapshot":
            m = ws.receive_json()
        assert "indicators" in m and m["sma"] == {"fast": settings.sma_fast, "slow": settings.sma_slow}
        assert m["account"]["snapshot"]["cash"] == settings.sim_initial_cash


def test_replay_verify_finalisation_reaches_live_indicators(settings):
    """Regression: /api/replay/verify used to finalise candles without telling
    listeners, so the live SMA state silently missed those candles."""
    import time

    from .conftest import ymsg

    settings.sma_fast, settings.sma_slow = 2, 4
    settings.finalize_grace_seconds = 3600  # the housekeeping loop won't finalise during the test
    with TestClient(create_app(settings, start_feed=False)) as c:
        svc = c.app.state.service
        base = (int(time.time() * 1000) // 60_000) * 60_000 - 10 * 60_000
        for i, px in enumerate([10, 9, 8, 7, 8, 9]):
            t = base + i * 60_000
            c.portal.call(lambda t=t, px=px: svc.ingest_yahoo_message(ymsg("AAPL", t + 1_000, px), received_ms=t + 1_100))
        settings.finalize_grace_seconds = 0
        svc.engine.grace_ms = 0
        assert c.get("/api/replay/verify", params={"symbol": "AAPL"}).json()["mismatch_count"] == 0
        v = c.get("/api/indicators/verify", params={"symbol": "AAPL"}).json()
        assert v["match"] is True and v["completed_candles"] == 6
