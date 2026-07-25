"""CSRF protection — double-submit cookie pattern.

A random token is set as a non-HttpOnly cookie (csrf_token) alongside the
session cookie.  JS reads it and re-sends it as X-CSRF-Token on every
state-changing request.  The server validates the two match.

Why this works: a cross-origin attacker cannot read the csrf_token cookie
(SameSite=Strict + browser cookie isolation), so they cannot forge the header.

Scope: only session-cookie-authenticated (browser) requests are validated.
API-key-authenticated requests (X-Api-Key header) are not susceptible to CSRF
and are explicitly skipped.
"""
from __future__ import annotations

import hmac
import secrets

from fastapi import HTTPException, Request
from fastapi.responses import Response

CSRF_COOKIE = "csrf_token"
_MAX_AGE = 2592000  # 30 days, mirrors session cookie lifetime


def generate() -> str:
    return secrets.token_urlsafe(32)


def set_cookie(response: Response, token: str, *, secure: bool = False) -> None:
    response.set_cookie(
        key=CSRF_COOKIE,
        value=token,
        httponly=False,   # JS must be able to read this
        secure=secure,
        samesite="strict",
        max_age=_MAX_AGE,
    )


def clear_cookie(response: Response, *, secure: bool = False) -> None:
    response.set_cookie(
        key=CSRF_COOKIE,
        value="",
        httponly=False,
        secure=secure,
        samesite="strict",
        max_age=0,
    )


async def require_csrf(request: Request) -> None:
    """FastAPI dependency: validate CSRF token for session-authenticated mutations.

    No-op for:
      - Safe HTTP methods (GET, HEAD, OPTIONS, TRACE)
      - Requests with X-Api-Key or Authorization header (API key auth)
      - Requests without antcrew_session cookie (not yet authenticated)
    """
    if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return
    if request.headers.get("X-Api-Key") or request.headers.get("Authorization"):
        return
    if not request.cookies.get("antcrew_session"):
        return

    cookie_token = request.cookies.get(CSRF_COOKIE, "")
    header_token = request.headers.get("X-CSRF-Token", "")

    if not cookie_token or not header_token:
        raise HTTPException(
            403,
            "CSRF token missing — include X-CSRF-Token header (read from csrf_token cookie)",
        )
    if not hmac.compare_digest(cookie_token, header_token):
        raise HTTPException(403, "CSRF token invalid")
