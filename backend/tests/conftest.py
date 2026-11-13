import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.hub import Hub  # noqa: E402
from app.service import MarketService  # noqa: E402
from app.store import Store  # noqa: E402

T0 = 1_763_000_000_000 - (1_763_000_000_000 % 60_000)  # a minute boundary (Nov 2025)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    for k in ["FEED_MODE", "SYMBOLS", "INTERVAL_SECONDS", "DB_PATH", "FINALIZE_GRACE_SECONDS",
              "STALE_AFTER_SECONDS", "BACKFILL_HISTORY"]:
        monkeypatch.delenv(k, raising=False)
    s = Settings()
    s.feed_mode = "off"
    s.symbols = ["AAPL", "BTC-USD"]
    s.db_path = tmp_path / "test.db"
    s.finalize_grace_seconds = 5
    s.backfill_history = False
    return s


@pytest.fixture
def service(settings):
    conn = connect(settings.db_path)
    init_db(conn)
    svc = MarketService(settings, Store(conn), Hub())
    yield svc
    conn.close()


def ymsg(symbol, t_ms, price, day_volume=None, **extra):
    """A decoded Yahoo streamer message (field names/types as yfinance returns them)."""
    m = {"id": symbol, "price": price, "time": str(t_ms), "price_hint": "2"}
    if day_volume is not None:
        m["day_volume"] = str(day_volume)
    m.update(extra)
    return m


def encode_frame(symbol, t_ms, price, day_volume=None) -> str:
    """A raw frame exactly as Yahoo's streamer sends it: JSON with a base64 protobuf."""
    import json

    from yfinance.pricing_pb2 import PricingData

    pd = PricingData(id=symbol, price=price, time=t_ms, price_hint=2)
    if day_volume is not None:
        pd.day_volume = day_volume
    return json.dumps({"type": "pricing", "message": base64.b64encode(pd.SerializeToString()).decode()})
