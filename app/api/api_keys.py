"""API key management — create, list, and revoke platform API keys."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, get_workspace_context, WorkspaceContext, require_role
from app.core.database import get_session
from app.models.run import ApiKey

router = APIRouter(
    prefix="/api-keys",
    tags=["auth"],
    dependencies=[Depends(require_api_key)],
)

_VALID_ROLES = ("admin", "write", "read", "reviewer")


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CreateKeyRequest(BaseModel):
    label: str
    workspace_id: Optional[int] = None
    role: str = "write"

    @field_validator("role")
    @classmethod
    def role_valid(cls, v: str) -> str:
        if v not in _VALID_ROLES:
            raise ValueError(f"role must be one of {_VALID_ROLES}")
        return v


@router.post("/", status_code=201, dependencies=[Depends(require_role("admin"))])
async def create_key(body: CreateKeyRequest, session: AsyncSession = Depends(get_session)):
    """Create a new API key. The raw key is returned once — store it securely.

    Roles:
    - **admin**: full access including key management and workspace admin
    - **write**: trigger runs, create templates and schedules (default)
    - **read**: read-only access to runs, tickets, evals
    - **reviewer**: can only resolve HITL reviews

    Set workspace_id to scope the key to a specific workspace.
    Requires: admin role.
    """
    result = await session.exec(select(ApiKey).where(ApiKey.label == body.label))
    if result.first():
        raise HTTPException(409, f"Key with label {body.label!r} already exists")
    raw = secrets.token_urlsafe(32)
    session.add(ApiKey(
        label=body.label,
        key_hash=_hash(raw),
        workspace_id=body.workspace_id,
        role=body.role,
    ))
    await session.commit()
    return {
        "label": body.label,
        "key": raw,
        "role": body.role,
        "note": "Store this key — it won't be shown again.",
    }


@router.get("/", dependencies=[Depends(require_role("admin"))])
async def list_keys(session: AsyncSession = Depends(get_session)):
    """List all active (non-revoked) API keys. Raw keys are never returned."""
    result = await session.exec(
        select(ApiKey).where(ApiKey.revoked_at == None)  # noqa: E711
    )
    return [
        {"label": k.label, "role": k.role, "workspace_id": k.workspace_id, "created_at": k.created_at}
        for k in result.all()
    ]


@router.delete("/{label}", status_code=204, dependencies=[Depends(require_role("admin"))])
async def revoke_key(label: str, session: AsyncSession = Depends(get_session)):
    """Revoke an API key by label. Requires: admin role."""
    result = await session.exec(select(ApiKey).where(ApiKey.label == label))
    key = result.first()
    if not key:
        raise HTTPException(404, f"Key {label!r} not found")
    key.revoked_at = _utcnow()
    session.add(key)
    await session.commit()
