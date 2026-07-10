"""API key authentication for antcrew-platform.

Auth modes (evaluated in order):
  1. PLATFORM_API_KEY env set → single-key mode (no DB hit)
  2. ApiKey rows in DB → multi-key mode (sha256 hash lookup by index)
  3. Neither → open mode (dev/local, no auth required)
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Optional, Any

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from app.core.database import get_session

_KEY_HEADER = APIKeyHeader(name="X-Api-Key", auto_error=False)


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


_VALID_ROLES = frozenset({"admin", "write", "read", "reviewer"})


@dataclass
class WorkspaceContext:
    """Auth context propagated to route handlers."""
    workspace_id: Optional[int]
    created_by: Optional[str]  # API key label or "env_key"
    role: str = "admin"         # admin | write | read | reviewer
    membership_ids: list[int] = field(default_factory=list)  # extra workspace IDs from memberships

    @property
    def workspace_ids(self) -> Optional[list[int]]:
        """Effective workspace ID set for list/filter queries.

        Returns None when the key is unrestricted (admin without a workspace scope).
        Returns a list of accessible workspace IDs otherwise.
        """
        ids: list[int] = list(self.membership_ids)
        if self.workspace_id is not None and self.workspace_id not in ids:
            ids.insert(0, self.workspace_id)
        return ids if ids else None


async def _authenticate(raw_key: Optional[str], session) -> WorkspaceContext:
    """Auth lookup using the provided session (injectable → testable).

    Uses indexed lookups — never fetches all keys to compare in Python.
    """
    from sqlmodel import select
    from app.models.run import ApiKey

    if raw_key:
        hashed = _hash(raw_key)
        key = (await session.exec(
            select(ApiKey).where(
                ApiKey.key_hash == hashed,
                ApiKey.revoked_at == None,  # noqa: E711
            )
        )).first()
        if key is not None:
            from app.models.run import WorkspaceMembership
            memberships = (await session.exec(
                select(WorkspaceMembership).where(WorkspaceMembership.api_key_id == key.id)
            )).all()
            return WorkspaceContext(
                workspace_id=key.workspace_id,
                created_by=key.label,
                role=key.role if key.role in _VALID_ROLES else "write",
                membership_ids=[m.workspace_id for m in memberships],
            )
        # Key provided but not found — check if multi-key mode is active
        any_key = (await session.exec(
            select(ApiKey).where(ApiKey.revoked_at == None).limit(1)  # noqa: E711
        )).first()
        if any_key is not None:
            raise HTTPException(401, "Invalid X-Api-Key")
        return WorkspaceContext(workspace_id=None, created_by=None)  # open mode
    else:
        any_key = (await session.exec(
            select(ApiKey).where(ApiKey.revoked_at == None).limit(1)  # noqa: E711
        )).first()
        if any_key is None:
            return WorkspaceContext(workspace_id=None, created_by=None)  # open mode
        raise HTTPException(401, "X-Api-Key header required")


async def get_workspace_context(
    request: Request,
    x_api_key: str | None = Security(_KEY_HEADER),
    session=Depends(get_session),
) -> WorkspaceContext:
    """FastAPI dependency: authenticate, rate-limit, and return workspace context.

    Returns WorkspaceContext with workspace_id (from the API key's scope) and
    created_by (key label). In open mode both are None.
    Uses the injected session so tests can override it via get_session.
    """
    from app.core import rate_limit

    env_key = os.environ.get("PLATFORM_API_KEY")
    if env_key:
        if x_api_key != env_key:
            raise HTTPException(401, "Invalid or missing X-Api-Key header")
        ctx = WorkspaceContext(workspace_id=None, created_by="env_key", role="admin")
        await rate_limit.check(request, ctx.workspace_id, ctx.created_by)
        return ctx
    try:
        ctx = await _authenticate(x_api_key, session)
    except HTTPException:
        raise
    except Exception:
        return WorkspaceContext(workspace_id=None, created_by=None)  # DB unavailable → fail open
    await rate_limit.check(request, ctx.workspace_id, ctx.created_by)
    return ctx


# Alias so routers can keep `dependencies=[Depends(require_api_key)]`.
# Same function reference → FastAPI caches the dep result when both router-level
# dep and route-level Depends(get_workspace_context) are used in the same request.
require_api_key = get_workspace_context


def require_role(*roles: str):
    """FastAPI dependency factory: raise 403 unless the caller has one of the given roles.

    Usage::
        @router.post("/", dependencies=[Depends(require_role("admin", "write"))])
    """
    from fastapi import Depends as _Depends

    async def _check(ctx: WorkspaceContext = _Depends(get_workspace_context)):
        if ctx.role not in roles:
            raise HTTPException(
                403,
                f"This action requires role {' or '.join(roles)!r}. "
                f"Your key has role {ctx.role!r}.",
            )
        return ctx

    return _check


def ws_filter(stmt: Any, column: Any, ctx: WorkspaceContext) -> Any:
    """Apply workspace scoping to a SQLModel select statement.

    Uses IN() when the key has a multi-workspace scope, equality for single-workspace,
    and returns the statement unmodified when the key is unrestricted (workspace_ids=None).
    """
    ids = ctx.workspace_ids
    if ids is None:
        return stmt
    if len(ids) == 1:
        return stmt.where(column == ids[0])
    return stmt.where(column.in_(ids))


def ws_accessible(workspace_id: Optional[int], ctx: WorkspaceContext) -> bool:
    """Return True when *workspace_id* is accessible under *ctx*.

    Used for per-row ownership checks (403 guards).
    """
    ids = ctx.workspace_ids
    if ids is None:
        return True  # unrestricted
    return workspace_id in ids


async def check_ws_api_key(api_key: str | None) -> bool:
    """Validate an API key for WebSocket connections (no DI — uses engine directly)."""
    env_key = os.environ.get("PLATFORM_API_KEY")
    if env_key:
        return api_key == env_key

    try:
        from sqlmodel import select
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.core.database import engine
        from app.models.run import ApiKey

        async with AsyncSession(engine, expire_on_commit=False) as session:
            any_key = (await session.exec(
                select(ApiKey).where(ApiKey.revoked_at == None).limit(1)  # noqa: E711
            )).first()
            if any_key is None:
                return True  # open mode

            if not api_key:
                return False

            hashed = _hash(api_key)
            key = (await session.exec(
                select(ApiKey).where(
                    ApiKey.key_hash == hashed,
                    ApiKey.revoked_at == None,  # noqa: E711
                )
            )).first()
            return key is not None
    except Exception:
        return True  # DB unavailable → fail open
