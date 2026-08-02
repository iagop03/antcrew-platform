"""FastAPI dependency that enforces platform-admin access.

Usage:
    from app.core.admin_auth import require_platform_admin

    @router.get("/admin/something")
    async def something(admin_user=Depends(require_platform_admin)):
        ...

The dependency reads the `antcrew_session` cookie, resolves it to a User,
and raises 401/403 if the user is not a platform admin.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request
from sqlmodel import select

from app.core.database import get_session
from app.models.auth import User, UserSession

log = logging.getLogger(__name__)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def require_platform_admin(
    request: Request,
    session=Depends(get_session),
) -> User:
    """Return the authenticated admin User or raise 401/403."""
    raw_token: Optional[str] = request.cookies.get("antcrew_session")
    if not raw_token:
        raise HTTPException(401, "Authentication required")

    token_hash = _hash_token(raw_token)
    user_session: Optional[UserSession] = (await session.exec(
        select(UserSession)
        .where(UserSession.token_hash == token_hash)
        .where(UserSession.revoked.is_(False))
    )).first()

    if user_session is None or user_session.user_id is None:
        raise HTTPException(401, "Invalid or expired session")

    user: Optional[User] = await session.get(User, user_session.user_id)
    if user is None:
        raise HTTPException(401, "User not found")

    if not user.is_platform_admin:
        raise HTTPException(403, "Admin access required")

    return user
