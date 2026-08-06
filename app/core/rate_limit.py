"""Sliding-window rate limiter — Redis-backed when REDIS_URL is set, in-process fallback otherwise.

When REDIS_URL is configured (required for any multi-worker deployment), requests are
counted in Redis using an atomic Lua sliding window shared across all processes and servers.

When REDIS_URL is not set, the limiter falls back to a per-process in-memory dict.
The effective limit in that mode is RATE_LIMIT_RPM × (number of uvicorn workers) —
acceptable for local dev with a single worker, a misconfiguration in production.

Environment variables:
  RATE_LIMIT_RPM   max requests per minute per key  (default: 60, 0 = disabled)
  REDIS_URL        Redis connection string           (e.g. redis://localhost:6379/0)
                   When unset, per-process fallback is used with a startup warning.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from collections import deque
from typing import Optional

from fastapi import HTTPException, Request

log = logging.getLogger(__name__)

_RPM: int = int(os.environ.get("RATE_LIMIT_RPM", "60"))
_WINDOW: float = 60.0
_REDIS_URL: Optional[str] = os.environ.get("REDIS_URL") or None

# ---------------------------------------------------------------------------
# Redis backend
# ---------------------------------------------------------------------------

_redis_client = None  # redis.asyncio.Redis, initialised lazily on first request


def _get_redis():
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as aioredis
        _redis_client = aioredis.from_url(_REDIS_URL, decode_responses=True)
    return _redis_client


# Atomic sliding-window: remove stale entries, check count, conditionally record this request.
# Returns 0 if allowed, 1 if rate-limited.
_LUA_SLIDING_WINDOW = """
local key   = KEYS[1]
local now   = tonumber(ARGV[1])
local win   = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local uid   = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - win)
local n = redis.call('ZCARD', key)
if n < limit then
    redis.call('ZADD', key, now, uid)
    redis.call('PEXPIRE', key, math.ceil(win * 1000) + 1000)
    return 0
end
return 1
"""


async def _check_redis(ident: str) -> bool:
    """Return True if this request should be blocked. Fails open on Redis errors."""
    client = _get_redis()
    now = time.time()
    uid = f"{now:.6f}:{secrets.token_hex(4)}"
    try:
        result = await client.eval(_LUA_SLIDING_WINDOW, 1, f"rl:{ident}", now, _WINDOW, _RPM, uid)
        return bool(result)
    except Exception as exc:
        log.warning("rate_limit: Redis unavailable, passing request through: %s", exc)
        return False  # fail open — don't block legitimate traffic on Redis downtime


# ---------------------------------------------------------------------------
# In-process fallback (single worker only)
# ---------------------------------------------------------------------------

_windows: dict[str, deque[float]] = {}
_lock: Optional[asyncio.Lock] = None


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


async def _check_memory(ident: str) -> bool:
    """Return True if this request should be blocked (single-process, not cross-worker)."""
    now = time.monotonic()
    async with _get_lock():
        window = _windows.setdefault(ident, deque())
        while window and window[0] < now - _WINDOW:
            window.popleft()
        if len(window) >= _RPM:
            return True
        window.append(now)
        return False


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def _ident(request: Request, workspace_id: Optional[int], created_by: Optional[str]) -> str:
    if workspace_id is not None:
        return f"ws:{workspace_id}"
    if created_by:
        return f"key:{created_by}"
    # Behind Cloudflare or nginx, request.client.host is the proxy IP — read the real IP
    # from X-Forwarded-For (first entry, which is the original client when the proxy is trusted).
    xff = request.headers.get("x-forwarded-for")
    host = xff.split(",")[0].strip() if xff else (request.client.host if request.client else "unknown")
    return f"ip:{host}"


def reset() -> None:
    """Clear the in-process sliding windows. Call between test cases to prevent leakage.

    Has no effect on the Redis backend — Redis state is isolated per test by using
    unique key prefixes or flushing the test database directly.
    """
    _windows.clear()


async def check(
    request: Request,
    workspace_id: Optional[int],
    created_by: Optional[str],
) -> None:
    """Raise HTTP 429 if the caller exceeds RATE_LIMIT_RPM requests per minute.

    Uses Redis when REDIS_URL is set (cross-process, multi-worker safe).
    Falls back to per-process in-memory dict when REDIS_URL is unset.
    No-op when RATE_LIMIT_RPM=0.
    """
    if _RPM <= 0:
        return

    ident = _ident(request, workspace_id, created_by)
    limited = await _check_redis(ident) if _REDIS_URL else await _check_memory(ident)

    if limited:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {_RPM} requests/min. Retry after 60 seconds.",
            headers={"Retry-After": "60"},
        )
