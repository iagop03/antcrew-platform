"""API key management — create, list, revoke, and update platform API keys."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import (
    require_api_key, require_role, _hash, _key_prefix,
    get_workspace_context, WorkspaceContext, ws_filter, ws_accessible,
)
from app.core.database import get_session
from app.models.run import ApiKey

router = APIRouter(
    prefix="/api-keys",
    tags=["auth"],
    dependencies=[Depends(require_api_key)],
)

_VALID_ROLES = ("admin", "write", "read", "reviewer")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CreateKeyRequest(BaseModel):
    label: str
    workspace_id: Optional[int] = None
    role: str = "write"
    email: Optional[str] = None
    slack_user_id: Optional[str] = None      # Slack member ID for DM notifications
    telegram_chat_id: Optional[str] = None   # Telegram chat ID for bot notifications

    @field_validator("role")
    @classmethod
    def role_valid(cls, v: str) -> str:
        if v not in _VALID_ROLES:
            raise ValueError(f"role must be one of {_VALID_ROLES}")
        return v


class UpdateKeyRequest(BaseModel):
    email: Optional[str] = None
    role: Optional[str] = None
    slack_user_id: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    @field_validator("role")
    @classmethod
    def role_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _VALID_ROLES:
            raise ValueError(f"role must be one of {_VALID_ROLES}")
        return v


@router.post("/", status_code=201, dependencies=[Depends(require_role("admin"))])
async def create_key(
    body: CreateKeyRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Create a new API key. The raw key is returned once — store it securely.

    Roles:
    - **admin**: full access including key management and workspace admin
    - **write**: trigger runs, create templates and schedules (default)
    - **read**: read-only access to runs, tickets, evals
    - **reviewer**: can only resolve HITL reviews

    Set workspace_id to scope the key to a specific workspace.
    Requires: admin role.
    """
    if body.workspace_id is not None and not ws_accessible(body.workspace_id, ctx):
        raise HTTPException(403, "workspace_id is not accessible with the current API key")
    ws_id = body.workspace_id or ctx.workspace_id
    result = await session.exec(
        select(ApiKey).where(ApiKey.label == body.label, ApiKey.workspace_id == ws_id)
    )
    if result.first():
        raise HTTPException(409, f"Key with label {body.label!r} already exists in this workspace")
    raw = secrets.token_urlsafe(32)
    session.add(ApiKey(
        label=body.label,
        key_hash=_hash(raw),
        key_prefix=_key_prefix(raw),
        workspace_id=body.workspace_id,
        role=body.role,
        email=body.email,
        slack_user_id=body.slack_user_id,
        telegram_chat_id=body.telegram_chat_id,
    ))
    await session.commit()
    return {
        "label": body.label,
        "key": raw,
        "role": body.role,
        "note": "Store this key — it won't be shown again.",
    }


@router.get("/", dependencies=[Depends(require_role("admin"))])
async def list_keys(
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """List active (non-revoked) API keys scoped to the caller's workspace."""
    stmt = select(ApiKey).where(ApiKey.revoked_at == None)  # noqa: E711
    stmt = ws_filter(stmt, ApiKey.workspace_id, ctx)
    result = await session.exec(stmt)
    return [
        {
            "label": k.label,
            "role": k.role,
            "workspace_id": k.workspace_id,
            "email": k.email,
            "slack_user_id": k.slack_user_id,
            "telegram_chat_id": k.telegram_chat_id,
            "created_at": k.created_at,
        }
        for k in result.all()
    ]


@router.patch("/{label}", dependencies=[Depends(require_role("admin"))])
async def update_key(
    label: str,
    body: UpdateKeyRequest,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Update mutable fields on an API key (email, role). Requires: admin role."""
    stmt = select(ApiKey).where(ApiKey.label == label, ApiKey.revoked_at == None)  # noqa: E711
    stmt = ws_filter(stmt, ApiKey.workspace_id, ctx)
    key = (await session.exec(stmt)).first()
    if not key:
        raise HTTPException(404, f"Key {label!r} not found or already revoked")
    if body.email is not None:
        key.email = body.email
    if body.role is not None:
        key.role = body.role
    if body.slack_user_id is not None:
        key.slack_user_id = body.slack_user_id or None
    if body.telegram_chat_id is not None:
        key.telegram_chat_id = body.telegram_chat_id or None
    session.add(key)
    await session.commit()
    return {
        "label": key.label,
        "role": key.role,
        "email": key.email,
        "slack_user_id": key.slack_user_id,
        "telegram_chat_id": key.telegram_chat_id,
    }


@router.delete("/{label}", status_code=204, dependencies=[Depends(require_role("admin"))])
async def revoke_key(
    label: str,
    ctx: WorkspaceContext = Depends(get_workspace_context),
    session: AsyncSession = Depends(get_session),
):
    """Revoke an API key by label. Requires: admin role."""
    stmt = ws_filter(select(ApiKey).where(ApiKey.label == label), ApiKey.workspace_id, ctx)
    key = (await session.exec(stmt)).first()
    if not key:
        raise HTTPException(404, f"Key {label!r} not found")
    key.revoked_at = _utcnow()
    session.add(key)
    await session.commit()
