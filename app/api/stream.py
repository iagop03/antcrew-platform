"""WebSocket endpoint — real-time event stream for the dashboard."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from antcrew import bus, Event
from app.core.auth import resolve_ws_api_key, resolve_ws_session_token

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["stream"])

_WS_QUEUE_SIZE = 100
_WS_PING_INTERVAL = 30  # seconds

# run_id → workspace_id for active runs; populated by runners on start, removed on completion.
_run_workspace: dict[str, int] = {}


def register_run(run_id: str, workspace_id: int) -> None:
    """Register a run→workspace mapping so WS connections can filter events by workspace."""
    _run_workspace[run_id] = workspace_id


def deregister_run(run_id: str) -> None:
    """Remove the run→workspace mapping when a run completes."""
    _run_workspace.pop(run_id, None)


class _Connection:
    def __init__(
        self,
        ws: WebSocket,
        run_id: Optional[str] = None,
        workspace_ids: Optional[frozenset] = None,
    ):
        self.ws = ws
        self.run_id = run_id
        self.workspace_ids = workspace_ids  # None = global access; frozenset = restricted
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=_WS_QUEUE_SIZE)

    def enqueue(self, event: Event) -> None:
        if self.run_id and event.run_id != self.run_id:
            return
        # Workspace isolation: drop events from runs outside this connection's workspaces.
        if self.workspace_ids is not None and event.run_id:
            ws_id = _run_workspace.get(event.run_id)
            if ws_id is not None and ws_id not in self.workspace_ids:
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

    Auth options (in priority order):
    1. Query param: ws://host/ws/events?api_key=<key>  — simple but key appears in
       access logs. Avoid on production proxies that log full request URLs.
    2. First-message auth: connect without api_key, then send JSON
       {"auth": "<key>"} as the first message. The server waits up to 10 s for it
       before closing the connection. Preferred for programmatic clients that want
       to keep the key out of server access logs.
    """
    await ws.accept()

    # Resolve key: query param takes priority; fall back to first-message auth.
    resolved_key = api_key
    if resolved_key is None:
        # Wait briefly for the client to send {"auth": "<key>"} as first message.
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
            msg = json.loads(raw)
            if isinstance(msg, dict) and "auth" in msg:
                resolved_key = msg["auth"]
                run_id = run_id or msg.get("run_id")
        except (asyncio.TimeoutError, json.JSONDecodeError, Exception):
            pass  # no first-message auth — proceed to key check (will reject if required)

    # Also accept antcrew_session cookie (parsed from the Upgrade request headers).
    if resolved_key is None:
        cookie_header = ws.headers.get("cookie", "")
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith("antcrew_session="):
                session_token = part[len("antcrew_session="):]
                auth = await resolve_ws_session_token(session_token)
                if auth is not None:
                    conn = _Connection(ws, run_id=run_id, workspace_ids=auth.workspace_ids)

                    def _session_handler(event: Event) -> None:
                        conn.enqueue(event)

                    bus.subscribe("*", _session_handler)
                    ping_task = asyncio.create_task(_ping_loop(ws))
                    try:
                        await conn.send_loop()
                    except (WebSocketDisconnect, RuntimeError):
                        pass
                    finally:
                        ping_task.cancel()
                        bus.unsubscribe("*", _session_handler)
                        log.debug("WS session client disconnected (run_id=%s)", run_id)
                    return
                break

    auth = await resolve_ws_api_key(resolved_key)
    if auth is None:
        await ws.close(code=4001, reason="Unauthorized")
        return

    conn = _Connection(ws, run_id=run_id, workspace_ids=auth.workspace_ids)

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
