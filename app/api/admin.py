"""Platform admin API.

All endpoints require is_platform_admin=True on the authenticated User.
Bootstrap endpoint (POST /admin/make-admin) requires PLATFORM_ADMIN_TOKEN env var.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from app.core.admin_auth import require_platform_admin
from app.core.database import get_session
from app.models.admin import Campaign
from app.models.auth import User
from app.models.workspace import Workspace

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class WorkspacePatch(BaseModel):
    cost_multiplier_override: Optional[float] = None
    multiplier_locked: Optional[bool] = None
    is_trial: Optional[bool] = None
    max_cost_usd: Optional[float] = None


class WorkspaceRow(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    slug: str
    llm_key_mode: str
    is_trial: bool
    cost_multiplier_override: Optional[float]
    multiplier_locked: bool
    total_cost_usd: float
    max_cost_usd: Optional[float]
    subscription_status: Optional[str]
    created_at: datetime


class CampaignCreate(BaseModel):
    name: str
    multiplier: float
    starts_at: datetime
    ends_at: datetime
    target: str = "all"  # all | new

    def validate_target(self) -> "CampaignCreate":
        if self.target not in ("all", "new"):
            raise ValueError("target must be 'all' or 'new'")
        return self


class CampaignPatch(BaseModel):
    name: Optional[str] = None
    multiplier: Optional[float] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    target: Optional[str] = None
    active: Optional[bool] = None


class CampaignRow(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    multiplier: float
    starts_at: datetime
    ends_at: datetime
    target: str
    active: bool
    created_at: datetime


class MakeAdminRequest(BaseModel):
    email: str
    token: str  # must match PLATFORM_ADMIN_TOKEN env var


class AdminUserRow(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    email: str
    display_name: Optional[str]
    is_platform_admin: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# Bootstrap: grant first admin via env token
# ---------------------------------------------------------------------------

@router.post("/make-admin", response_model=AdminUserRow)
async def make_admin(
    body: MakeAdminRequest,
    session=Depends(get_session),
):
    """Grant is_platform_admin to a user. Protected by PLATFORM_ADMIN_TOKEN env var."""
    expected = os.environ.get("PLATFORM_ADMIN_TOKEN", "")
    if not expected or body.token != expected:
        raise HTTPException(403, "Invalid PLATFORM_ADMIN_TOKEN")

    user: Optional[User] = (await session.exec(
        select(User).where(User.email == body.email)
    )).first()
    if user is None:
        raise HTTPException(404, f"No user with email {body.email!r}")

    user.is_platform_admin = True
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@router.get("/stats")
async def admin_stats(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    from sqlalchemy import func
    from app.models.run import Run

    total_workspaces = (await session.exec(
        select(func.count()).select_from(Workspace)
    )).one()
    trial_workspaces = (await session.exec(
        select(func.count()).select_from(Workspace).where(Workspace.is_trial.is_(True))
    )).one()
    paid_workspaces = (await session.exec(
        select(func.count()).select_from(Workspace).where(Workspace.is_trial.is_(False))
    )).one()
    total_runs = (await session.exec(
        select(func.count()).select_from(Run)
    )).one()
    total_cost = (await session.exec(
        select(func.coalesce(func.sum(Workspace.total_cost_usd), 0.0))
    )).one()

    return {
        "workspaces": {"total": total_workspaces, "trial": trial_workspaces, "paid": paid_workspaces},
        "runs": {"total": total_runs},
        "revenue": {"total_cost_usd": float(total_cost)},
    }


# ---------------------------------------------------------------------------
# Workspaces
# ---------------------------------------------------------------------------

@router.get("/workspaces", response_model=list[WorkspaceRow])
async def list_workspaces(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
    limit: int = 100,
    offset: int = 0,
):
    rows = (await session.exec(
        select(Workspace).order_by(Workspace.created_at.desc()).offset(offset).limit(limit)
    )).all()
    return rows


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceRow)
async def patch_workspace(
    workspace_id: int,
    body: WorkspacePatch,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    ws: Optional[Workspace] = await session.get(Workspace, workspace_id)
    if ws is None:
        raise HTTPException(404, "Workspace not found")

    if body.cost_multiplier_override is not None:
        ws.cost_multiplier_override = body.cost_multiplier_override
    if body.multiplier_locked is not None:
        ws.multiplier_locked = body.multiplier_locked
    if body.is_trial is not None:
        ws.is_trial = body.is_trial
    if body.max_cost_usd is not None:
        ws.max_cost_usd = body.max_cost_usd

    # Clearing override: pass null explicitly via JSON
    if "cost_multiplier_override" in (body.model_fields_set or set()) and body.cost_multiplier_override is None:
        ws.cost_multiplier_override = None

    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------

@router.get("/campaigns", response_model=list[CampaignRow])
async def list_campaigns(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    rows = (await session.exec(
        select(Campaign).order_by(Campaign.starts_at.desc())
    )).all()
    return rows


@router.post("/campaigns", response_model=CampaignRow, status_code=201)
async def create_campaign(
    body: CampaignCreate,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    if body.target not in ("all", "new"):
        raise HTTPException(422, "target must be 'all' or 'new'")
    if body.ends_at <= body.starts_at:
        raise HTTPException(422, "ends_at must be after starts_at")

    campaign = Campaign(
        name=body.name,
        multiplier=body.multiplier,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        target=body.target,
        active=True,
    )
    session.add(campaign)
    await session.commit()
    await session.refresh(campaign)
    return campaign


@router.patch("/campaigns/{campaign_id}", response_model=CampaignRow)
async def patch_campaign(
    campaign_id: int,
    body: CampaignPatch,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    camp: Optional[Campaign] = await session.get(Campaign, campaign_id)
    if camp is None:
        raise HTTPException(404, "Campaign not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(camp, field, value)

    session.add(camp)
    await session.commit()
    await session.refresh(camp)
    return camp


@router.delete("/campaigns/{campaign_id}", status_code=204)
async def delete_campaign(
    campaign_id: int,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    camp: Optional[Campaign] = await session.get(Campaign, campaign_id)
    if camp is None:
        raise HTTPException(404, "Campaign not found")
    await session.delete(camp)
    await session.commit()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@router.get("/users", response_model=list[AdminUserRow])
async def list_users(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
    limit: int = 100,
    offset: int = 0,
):
    rows = (await session.exec(
        select(User).order_by(User.created_at.desc()).offset(offset).limit(limit)
    )).all()
    return rows


@router.patch("/users/{user_id}/admin", response_model=AdminUserRow)
async def set_user_admin(
    user_id: int,
    is_admin: bool,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    user: Optional[User] = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    user.is_platform_admin = is_admin
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user
