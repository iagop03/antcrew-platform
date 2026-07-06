"""WebSocket endpoint — real-time event stream for the dashboard."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from antcrew.core.events import bus, Event
from app.core.auth import check_ws_api_key

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["stream"])

_WS_QUEUE_SIZE = 100
_WS_PING_INTERVAL = 30  # seconds


class _Connection:
    def __init__(self, ws: WebSocket, run_id: Optional[str] = None):
        self.ws = ws
        self.run_id = run_id
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=_WS_QUEUE_SIZE)

    def enqueue(self, event: Event) -> None:
        if self.run_id and event.run_id != self.run_id:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            log.debug("WS queue full for run_id=%s — dropping event %s", self.run_id, event.type)

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


async def _ping_loop(ws: WebSocket) -> None:
    """Send a keepalive ping every _WS_PING_INTERVAL seconds."""
    while True:
        await asyncio.sleep(_WS_PING_INTERVAL)
        try:
            await ws.send_text('{"type":"ping"}')
        except Exception:
            break


@router.websocket("/events")
async def events_stream(
    ws: WebSocket,
    run_id: Optional[str] = None,
    api_key: Optional[str] = None,
):
    """Stream all bus events (or events for a specific run_id) over WebSocket.

    Pass api_key as a query param when PLATFORM_API_KEY or DB keys are configured:
    ws://host/ws/events?api_key=<key>&run_id=<optional>
    """
    await ws.accept()

    if not await check_ws_api_key(api_key):
        await ws.close(code=4001, reason="Unauthorized")
        return

    conn = _Connection(ws, run_id=run_id)

    def _handler(event: Event) -> None:
        conn.enqueue(event)

    bus.subscribe("*", _handler)
    ping_task = asyncio.create_task(_ping_loop(ws))
    try:
        await conn.send_loop()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        ping_task.cancel()
        bus.unsubscribe("*", _handler)
        log.debug("WS client disconnected (run_id=%s)", run_id)
