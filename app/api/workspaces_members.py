"""Workspace membership — multi-workspace access per API key."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, require_role
from app.core.database import get_session
from app.models.run import Workspace, ApiKey, WorkspaceMembership

router = APIRouter(
    prefix="/workspaces",
    tags=["workspaces"],
    dependencies=[Depends(require_api_key)],
)


class MembershipCreate(BaseModel):
    api_key_id: int


class MembershipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    api_key_id: int
    workspace_id: int
    key_label: Optional[str] = None
    created_at: datetime


@router.get("/{workspace_id}/members", response_model=list[MembershipOut],
            dependencies=[Depends(require_role("admin"))])
async def list_members(
    workspace_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[MembershipOut]:
    """List API keys that have membership access to this workspace."""
    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws:
        raise HTTPException(404, f"Workspace {workspace_id} not found")
    memberships = (await session.exec(
        select(WorkspaceMembership).where(WorkspaceMembership.workspace_id == workspace_id)
    )).all()
    key_ids = [m.api_key_id for m in memberships]
    keys: dict[int, str] = {}
    if key_ids:
        key_rows = (await session.exec(
            select(ApiKey).where(col(ApiKey.id).in_(key_ids))
        )).all()
        keys = {k.id: k.label for k in key_rows if k.id is not None}
    return [
        MembershipOut(
            id=m.id,  # type: ignore[arg-type]
            api_key_id=m.api_key_id,
            workspace_id=m.workspace_id,
            key_label=keys.get(m.api_key_id),
            created_at=m.created_at,
        )
        for m in memberships
    ]


@router.post("/{workspace_id}/members", status_code=201, response_model=MembershipOut,
             dependencies=[Depends(require_role("admin"))])
async def add_member(
    workspace_id: int,
    body: MembershipCreate,
    session: AsyncSession = Depends(get_session),
) -> MembershipOut:
    """Grant an API key access to a workspace (multi-workspace membership).

    Unlike the key's primary workspace_id, memberships let one key read across
    multiple workspaces without changing the key's primary scope for writes.
    """
    ws = (await session.exec(select(Workspace).where(Workspace.id == workspace_id))).first()
    if not ws:
        raise HTTPException(404, f"Workspace {workspace_id} not found")
    key = (await session.exec(select(ApiKey).where(ApiKey.id == body.api_key_id))).first()
    if not key:
        raise HTTPException(404, f"ApiKey {body.api_key_id} not found")
    existing = (await session.exec(
        select(WorkspaceMembership)
        .where(WorkspaceMembership.api_key_id == body.api_key_id)
        .where(WorkspaceMembership.workspace_id == workspace_id)
    )).first()
    if existing:
        return MembershipOut(
            id=existing.id,  # type: ignore[arg-type]
            api_key_id=existing.api_key_id,
            workspace_id=existing.workspace_id,
            key_label=key.label,
            created_at=existing.created_at,
        )
    m = WorkspaceMembership(api_key_id=body.api_key_id, workspace_id=workspace_id)
    session.add(m)
    await session.commit()
    await session.refresh(m)
    return MembershipOut(
        id=m.id,  # type: ignore[arg-type]
        api_key_id=m.api_key_id,
        workspace_id=m.workspace_id,
        key_label=key.label,
        created_at=m.created_at,
    )


@router.delete("/{workspace_id}/members/{api_key_id}", status_code=204,
               dependencies=[Depends(require_role("admin"))])
async def remove_member(
    workspace_id: int,
    api_key_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Revoke a key's multi-workspace membership for this workspace."""
    m = (await session.exec(
        select(WorkspaceMembership)
        .where(WorkspaceMembership.api_key_id == api_key_id)
        .where(WorkspaceMembership.workspace_id == workspace_id)
    )).first()
    if not m:
        raise HTTPException(404, "Membership not found")
    await session.delete(m)
    await session.commit()
