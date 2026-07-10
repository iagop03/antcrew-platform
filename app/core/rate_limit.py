"""In-memory sliding-window rate limiter for FastAPI.

Keyed by workspace_id (preferred) › API key label › client IP.
All state lives in the process — single instance only.  For multi-instance
deployments replace with a Redis-backed implementation.

Environment variables:
  RATE_LIMIT_RPM   max requests per minute per key  (default: 60, 0 = disabled)
"""
from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from typing import Optional

from fastapi import HTTPException, Request

_RPM: int = int(os.environ.get("RATE_LIMIT_RPM", "60"))
_WINDOW: float = 60.0

_windows: dict[str, deque[float]] = {}
_lock: Optional[asyncio.Lock] = None


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def _ident(request: Request, workspace_id: Optional[int], created_by: Optional[str]) -> str:
    if workspace_id is not None:
        return f"ws:{workspace_id}"
    if created_by:
        return f"key:{created_by}"
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"


def reset() -> None:
    """Clear all sliding windows. Call between test cases to prevent cross-test leakage."""
    _windows.clear()


async def check(
    request: Request,
    workspace_id: Optional[int],
    created_by: Optional[str],
) -> None:
    """Raise HTTP 429 if the caller exceeds RATE_LIMIT_RPM requests per minute.

    No-op when RATE_LIMIT_RPM=0 (disabled).
    """
    if _RPM <= 0:
        return

    ident = _ident(request, workspace_id, created_by)
    now = time.monotonic()

    async with _get_lock():
        window = _windows.setdefault(ident, deque())
        while window and window[0] < now - _WINDOW:
            window.popleft()
        if len(window) >= _RPM:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Rate limit exceeded: {_RPM} requests/min. "
                    "Retry after 60 seconds."
                ),
                headers={"Retry-After": "60"},
            )
        window.append(now)
