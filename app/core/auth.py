"""Simple API key auth. Set PLATFORM_API_KEY env var to enable.

If the env var is not set, the platform runs in open mode (dev/local use).
"""
from __future__ import annotations

import os

from fastapi import Header, HTTPException, Security
from fastapi.security import APIKeyHeader

_KEY_HEADER = APIKeyHeader(name="X-Api-Key", auto_error=False)


async def require_api_key(x_api_key: str | None = Security(_KEY_HEADER)) -> None:
    expected = os.environ.get("PLATFORM_API_KEY")
    if not expected:
        return  # open mode — no key required
    if x_api_key != expected:
        raise HTTPException(401, "Invalid or missing X-Api-Key header")
