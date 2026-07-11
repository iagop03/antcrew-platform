"""API key authentication for antcrew-platform.

Auth modes (evaluated in order):
  1. PLATFORM_API_KEY env set → single-key mode (no DB hit)
  2. ApiKey rows in DB → multi-key mode (prefix-indexed bcrypt lookup)
  3. Neither → open mode (dev/local, no auth required)

Lookup strategy:
  New keys: WHERE key_prefix = sha256(raw)[:16] → 1 row → 1 bcrypt.checkpw()  O(1)
  Legacy keys (no prefix yet): scan sha256 keys, set prefix + rehash on success  O(n) → shrinks
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from dataclasses import dataclass, field
from typing import Optional, Any

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from app.core.database import get_session

log = logging.getLogger(__name__)

_KEY_HEADER = APIKeyHeader(name="X-Api-Key", auto_error=False)


def _key_prefix(raw_key: str) -> str:
    """Fast lookup index: first 16 hex chars of SHA256(raw_key). Stable, collision-resistant."""
    return hashlib.sha256(raw_key.encode()).hexdigest()[:16]


def _hash(key: str) -> str:
    """Hash a new API key with bcrypt (cost factor 12)."""
    import bcrypt
    return bcrypt.hashpw(key.encode(), bcrypt.gensalt(rounds=12)).decode()


def _verify(raw_key: str, stored_hash: str) -> bool:
    """Verify key against stored hash. Accepts bcrypt and legacy sha256."""
    if stored_hash.startswith(("$2b$", "$2a$", "$2y$")):
        try:
            import bcrypt as _bcrypt
            return _bcrypt.checkpw(raw_key.encode(), stored_hash.encode())
        except Exception:
            return False
    return hmac.compare_digest(stored_hash, hashlib.sha256(raw_key.encode()).hexdigest())


def _is_legacy_hash(stored_hash: str) -> bool:
    return not stored_hash.startswith(("$2b$", "$2a$", "$2y$"))


_VALID_ROLES = frozenset({"admin", "write", "read", "reviewer"})


@dataclass
class WorkspaceContext:
    """Auth context propagated to route handlers."""
    workspace_id: Optional[int]
    created_by: Optional[str]  # API key label or "env_key"
    role: str = "write"         # admin | write | read | reviewer — never "admin" by accident
    membership_ids: list[int] = field(default_factory=list)

    @property
    def workspace_ids(self) -> Optional[list[int]]:
        ids: list[int] = list(self.membership_ids)
        if self.workspace_id is not None and self.workspace_id not in ids:
            ids.insert(0, self.workspace_id)
        return ids if ids else None


async def _authenticate(raw_key: Optional[str], session) -> WorkspaceContext:
    """Auth lookup using the provided session (injectable → testable).

    Fast path (new keys with key_prefix set): 1 indexed SELECT + 1 bcrypt.checkpw().
    Slow path (legacy keys without prefix): SHA256 scan, then upgrades on success.
    """
    from sqlmodel import select
    from app.models.run import ApiKey

    if raw_key:
        prefix = _key_prefix(raw_key)
        key: Optional[ApiKey] = None

        # Fast path — indexed prefix lookup (O(1))
        candidates = (await session.exec(
            select(ApiKey).where(
                ApiKey.key_prefix == prefix,
                ApiKey.revoked_at == None,  # noqa: E711
            )
        )).all()
        if candidates:
            key = next((k for k in candidates if _verify(raw_key, k.key_hash)), None)

        # Slow path — legacy keys without prefix (shrinks as they log in)
        if key is None:
            legacy = (await session.exec(
                select(ApiKey).where(
                    ApiKey.key_prefix == None,  # noqa: E711
                    ApiKey.revoked_at == None,  # noqa: E711
                )
            )).all()
            if legacy:
                key = next((k for k in legacy if _verify(raw_key, k.key_hash)), None)

        if key is not None:
            # Snapshot attrs before any commit
            key_id = key.id
            workspace_id = key.workspace_id
            label = key.label
            role = key.role

            # Upgrade legacy key: set prefix and/or rehash sha256 → bcrypt
            if key.key_prefix is None or _is_legacy_hash(key.key_hash):
                try:
                    key.key_prefix = prefix
                    if _is_legacy_hash(key.key_hash):
                        key.key_hash = _hash(raw_key)
                    session.add(key)
                    await session.commit()
                except Exception as exc:
                    log.warning("auth: could not upgrade legacy key %r: %s", label, exc)
                    try:
                        await session.rollback()
                    except Exception:
                        pass

            from app.models.run import WorkspaceMembership
            memberships = (await session.exec(
                select(WorkspaceMembership).where(WorkspaceMembership.api_key_id == key_id)
            )).all()
            return WorkspaceContext(
                workspace_id=workspace_id,
                created_by=label,
                role=role if role in _VALID_ROLES else "write",
                membership_ids=[m.workspace_id for m in memberships],
            )

        # Key provided but not found — check if multi-key mode is active
        any_key = (await session.exec(
            select(ApiKey).where(ApiKey.revoked_at == None).limit(1)  # noqa: E711
        )).first()
        if any_key is not None:
            raise HTTPException(401, "Invalid X-Api-Key")
        return WorkspaceContext(workspace_id=None, created_by=None, role="admin")  # open mode
    else:
        any_key = (await session.exec(
            select(ApiKey).where(ApiKey.revoked_at == None).limit(1)  # noqa: E711
        )).first()
        if any_key is None:
            return WorkspaceContext(workspace_id=None, created_by=None, role="admin")  # open mode
        raise HTTPException(401, "X-Api-Key header required")


async def get_workspace_context(
    request: Request,
    x_api_key: str | None = Security(_KEY_HEADER),
    session=Depends(get_session),
) -> WorkspaceContext:
    """FastAPI dependency: authenticate, rate-limit, and return workspace context."""
    from app.core import rate_limit

    env_key = os.environ.get("PLATFORM_API_KEY")
    if env_key:
        if not hmac.compare_digest(x_api_key or "", env_key):
            raise HTTPException(401, "Invalid or missing X-Api-Key header")
        ctx = WorkspaceContext(workspace_id=None, created_by="env_key", role="admin")
        await rate_limit.check(request, ctx.workspace_id, ctx.created_by)
        return ctx
    try:
        ctx = await _authenticate(x_api_key, session)
    except HTTPException:
        raise
    except Exception as exc:
        log.error("auth: DB error during authentication: %s", exc)
        raise HTTPException(503, "Authentication service temporarily unavailable")
    await rate_limit.check(request, ctx.workspace_id, ctx.created_by)
    return ctx


require_api_key = get_workspace_context


def require_role(*roles: str):
    """FastAPI dependency factory: raise 403 unless the caller has one of the given roles."""
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
    """Apply workspace scoping to a SQLModel select statement."""
    ids = ctx.workspace_ids
    if ids is None:
        return stmt
    if len(ids) == 1:
        return stmt.where(column == ids[0])
    return stmt.where(column.in_(ids))


def ws_accessible(workspace_id: Optional[int], ctx: WorkspaceContext) -> bool:
    """Return True when workspace_id is accessible under ctx."""
    ids = ctx.workspace_ids
    if ids is None:
        return True
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

            prefix = _key_prefix(api_key)

            # Fast path
            candidates = (await session.exec(
                select(ApiKey).where(
                    ApiKey.key_prefix == prefix,
                    ApiKey.revoked_at == None,  # noqa: E711
                )
            )).all()
            matched = next((k for k in candidates if _verify(api_key, k.key_hash)), None)

            # Slow path for legacy keys
            if matched is None:
                legacy = (await session.exec(
                    select(ApiKey).where(
                        ApiKey.key_prefix == None,  # noqa: E711
                        ApiKey.revoked_at == None,  # noqa: E711
                    )
                )).all()
                matched = next((k for k in legacy if _verify(api_key, k.key_hash)), None)

            if matched is not None and (matched.key_prefix is None or _is_legacy_hash(matched.key_hash)):
                try:
                    matched.key_prefix = prefix
                    if _is_legacy_hash(matched.key_hash):
                        matched.key_hash = _hash(api_key)
                    session.add(matched)
                    await session.commit()
                except Exception:
                    pass
            return matched is not None
    except Exception:
        return False  # fail closed for WebSocket auth
