"""Validate and normalise decoded source messages into `Tick`s."""
from __future__ import annotations

import base64
import math
from typing import Any

from google.protobuf.json_format import MessageToDict
from yfinance.pricing_pb2 import PricingData  # protobuf schema shipped with yfinance

from .aggregator import Tick

MAX_FUTURE_SKEW_MS = 5 * 60 * 1000  # reject timestamps > 5 min ahead of receipt


class RejectedObservation(ValueError):
    """Raised when a message cannot become a trustworthy observation."""


def decode_yahoo_frame(encoded: str) -> dict[str, Any]:
    """Decode one base64 protobuf payload from Yahoo's streamer.

    This mirrors what yfinance's own WebSocket client does
    (yfinance/live.py: BaseWebSocket._decode_message) but raises instead of
    printing, so the caller can count and log rejected frames.
    """
    raw = base64.b64decode(encoded)
    msg = PricingData()
    msg.ParseFromString(raw)
    return MessageToDict(msg, preserving_proto_field_name=True)


def yahoo_to_tick(msg: dict[str, Any], received_ms: int) -> Tick:
    """Yahoo fields: id (symbol), price (float32), time (ms since epoch, as a
    string because protobuf int64 -> JSON string), day_volume (cumulative,
    string), price_hint (decimal places)."""
    try:
        symbol = str(msg["id"]).upper()
        source_time_ms = int(msg["time"])
        price = float(msg["price"])
    except (KeyError, TypeError, ValueError) as e:
        raise RejectedObservation(f"missing or malformed field: {e}") from e
    if not symbol:
        raise RejectedObservation("empty symbol")
    if not math.isfinite(price) or price <= 0:
        raise RejectedObservation(f"invalid price {price!r}")
    if source_time_ms <= 0:
        raise RejectedObservation(f"invalid source time {source_time_ms}")
    if source_time_ms > received_ms + MAX_FUTURE_SKEW_MS:
        raise RejectedObservation("source time is more than 5 minutes in the future")
    # price arrives as float32 (e.g. 189.30999755859375); round to the
    # precision Yahoo says the instrument is quoted in.
    hint = int(msg.get("price_hint", 2) or 2)
    price = round(price, max(2, min(hint, 8)))
    dv = msg.get("day_volume")
    day_volume = int(dv) if dv not in (None, "") else None
    return Tick(symbol=symbol, source_time_ms=source_time_ms, price=price,
                day_volume=day_volume, received_ms=received_ms)
