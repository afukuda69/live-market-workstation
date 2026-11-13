"""Browser WebSocket connections and fan-out."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Optional

from fastapi import WebSocket


@dataclass(eq=False)
class Client:
    ws: WebSocket
    symbol: Optional[str] = None
    replay_task: Optional[asyncio.Task] = field(default=None, repr=False)


class Hub:
    def __init__(self):
        self.clients: set[Client] = set()

    def add(self, ws: WebSocket) -> Client:
        c = Client(ws)
        self.clients.add(c)
        return c

    def remove(self, c: Client) -> None:
        self.clients.discard(c)
        if c.replay_task and not c.replay_task.done():
            c.replay_task.cancel()

    async def send(self, c: Client, message: dict) -> bool:
        try:
            await c.ws.send_text(json.dumps(message, default=str))
            return True
        except Exception:
            self.remove(c)
            return False

    async def broadcast(self, message: dict, symbol: Optional[str] = None) -> None:
        targets = [c for c in list(self.clients) if symbol is None or c.symbol == symbol]
        if targets:
            await asyncio.gather(*(self.send(c, message) for c in targets))
