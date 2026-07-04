"""WebSocket endpoint — real-time event stream for the dashboard."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from antcrew.core.events import bus, Event

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["stream"])


class _Connection:
    def __init__(self, ws: WebSocket, run_id: Optional[str] = None):
        self.ws = ws
        self.run_id = run_id  # None = all runs
        self._queue: asyncio.Queue = asyncio.Queue()

    def enqueue(self, event: Event) -> None:
        if self.run_id and event.run_id != self.run_id:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

    async def send_loop(self) -> None:
        while True:
            event = await self._queue.get()
            msg = {
                "type": event.type,
                "run_id": event.run_id,
                "thread_id": event.thread_id,
                "timestamp": event.timestamp,
                "payload": event.payload,
            }
            await self.ws.send_text(json.dumps(msg))


@router.websocket("/events")
async def events_stream(ws: WebSocket, run_id: Optional[str] = None):
    """Stream all bus events (or events for a specific run_id) over WebSocket."""
    await ws.accept()
    conn = _Connection(ws, run_id=run_id)

    def _handler(event: Event) -> None:
        conn.enqueue(event)

    bus.subscribe("*", _handler)
    try:
        await conn.send_loop()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe("*", _handler)
        log.debug("WS client disconnected (run_id=%s)", run_id)
