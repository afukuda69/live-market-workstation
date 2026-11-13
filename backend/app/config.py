"""Runtime configuration, read from environment variables (see .env.example).

No credentials are required: the Yahoo Finance streamer used through yfinance
does not take an API key. Nothing in this file should ever hold a secret.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines) so no extra dependency is needed.
    Values already present in the real environment win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip()  # allow trailing comments
        os.environ.setdefault(key.strip(), value)


_load_dotenv(BACKEND_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_db_path(value: str) -> Path:
    """Relative DB_PATH values are relative to backend/, not the shell's cwd."""
    p = Path(value)
    return p if p.is_absolute() or value == ":memory:" else BACKEND_DIR / p


@dataclass
class Settings:
    # "yahoo" = live Yahoo Finance streamer, "mock" = clearly-labelled synthetic
    # feed for offline tests, "off" = no feed (replay / stored data only).
    feed_mode: str = field(default_factory=lambda: os.environ.get("FEED_MODE", "yahoo").lower())
    symbols: list[str] = field(
        default_factory=lambda: [
            s.strip().upper()
            for s in os.environ.get("SYMBOLS", "BTC-USD,AAPL,SPY").split(",")
            if s.strip()
        ]
    )
    interval_seconds: int = field(default_factory=lambda: int(os.environ.get("INTERVAL_SECONDS", "60")))
    db_path: Path = field(default_factory=lambda: _resolve_db_path(os.environ.get("DB_PATH", "data/market.db")))
    yahoo_ws_url: str = field(
        default_factory=lambda: os.environ.get("YAHOO_WS_URL", "wss://streamer.finance.yahoo.com/?version=2")
    )
    # A candle is marked complete once wall-clock time passes its end + grace.
    finalize_grace_seconds: float = field(
        default_factory=lambda: float(os.environ.get("FINALIZE_GRACE_SECONDS", "5"))
    )
    # Connected but silent for this long => "stale".
    stale_after_seconds: float = field(default_factory=lambda: float(os.environ.get("STALE_AFTER_SECONDS", "30")))
    backfill_history: bool = field(default_factory=lambda: _bool("BACKFILL_HISTORY", True))
    snapshot_candles: int = field(default_factory=lambda: int(os.environ.get("SNAPSHOT_CANDLES", "300")))

    @property
    def interval_ms(self) -> int:
        return self.interval_seconds * 1000

    @property
    def interval_label(self) -> str:
        s = self.interval_seconds
        return f"{s // 60}m" if s % 60 == 0 else f"{s}s"
