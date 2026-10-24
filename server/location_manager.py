"""Armazenamento de localização: última posição + histórico circular + broadcast."""
import asyncio
import time
from collections import deque


class LocationStore:
    def __init__(self, history_max: int = 500):
        self.last: dict[str, dict] = {}
        self.history: dict[str, deque] = {}
        self._max = history_max
        self.viewers: set[asyncio.Queue] = set()

    def upsert(self, report: dict) -> dict:
        data = dict(report)
        data["ts"] = time.time()
        dev = data["device"]
        self.last[dev] = data
        h = self.history.setdefault(dev, deque(maxlen=self._max))
        h.append(data)
        return data

    async def broadcast(self, data: dict) -> None:
        for q in list(self.viewers):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                self.viewers.discard(q)  # viewer lento: cortado

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=100)
        self.viewers.add(q)
        for d in list(self.last.values()):  # replay do estado atual
            try:
                q.put_nowait(d)
            except asyncio.QueueFull:
                break
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.viewers.discard(q)


store = LocationStore()
