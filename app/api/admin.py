"""Platform admin API.

All endpoints require is_platform_admin=True on the authenticated User.
Bootstrap endpoint (POST /admin/make-admin) requires PLATFORM_ADMIN_TOKEN env var.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Integer, case, extract, func
from sqlalchemy import select as sa_select
from sqlmodel import select

from app.core.admin_auth import require_platform_admin
from app.core.database import get_session
from app.models.admin import Campaign, PlatformConfig
from app.models.auth import User
from app.models.feedback import UserFeedback
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
    discount_days: Optional[int] = None
    max_participants: Optional[int] = None

    @classmethod
    def _strip_tz(cls, dt: datetime) -> datetime:
        return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt

    def clean_dates(self) -> "CampaignCreate":
        self.starts_at = self._strip_tz(self.starts_at)
        self.ends_at = self._strip_tz(self.ends_at)
        return self


class CampaignPatch(BaseModel):
    name: Optional[str] = None
    multiplier: Optional[float] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    target: Optional[str] = None
    active: Optional[bool] = None
    discount_days: Optional[int] = None
    max_participants: Optional[int] = None


class CampaignRow(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    multiplier: float
    starts_at: datetime
    ends_at: datetime
    target: str
    active: bool
    discount_days: Optional[int] = None
    max_participants: Optional[int] = None
    created_at: datetime


class BillingRates(BaseModel):
    managed_cost_multiplier: float
    byok_service_multiplier: float
    proxy_service_multiplier: float
    managed_enabled: bool
    byok_enabled: bool
    proxy_enabled: bool


class BillingRatesPatch(BaseModel):
    managed_cost_multiplier: Optional[float] = None
    byok_service_multiplier: Optional[float] = None
    proxy_service_multiplier: Optional[float] = None
    managed_enabled: Optional[bool] = None
    byok_enabled: Optional[bool] = None
    proxy_enabled: Optional[bool] = None


class FeedbackRow(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    user_id: Optional[int]
    workspace_id: Optional[int]
    context: str
    helpful: Optional[bool]
    message: Optional[str]
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
    use_case: Optional[str]
    team_size: Optional[str]
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
    if body.multiplier <= 0:
        raise HTTPException(422, "multiplier must be greater than 0")
    body.clean_dates()
    if body.ends_at <= body.starts_at:
        raise HTTPException(422, "ends_at must be after starts_at")

    campaign = Campaign(
        name=body.name,
        multiplier=body.multiplier,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        target=body.target,
        active=True,
        discount_days=body.discount_days,
        max_participants=body.max_participants,
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
        if isinstance(value, datetime) and value.tzinfo is not None:
            value = value.replace(tzinfo=None)
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
# Billing rates
# ---------------------------------------------------------------------------

_DEFAULT_RATES = BillingRates(
    managed_cost_multiplier=3.0,
    byok_service_multiplier=0.4,
    proxy_service_multiplier=0.7,
    managed_enabled=True,
    byok_enabled=True,
    proxy_enabled=True,
)


def _rates_from_cfg(cfg: PlatformConfig) -> BillingRates:
    return BillingRates(
        managed_cost_multiplier=cfg.managed_cost_multiplier,
        byok_service_multiplier=cfg.byok_service_multiplier,
        proxy_service_multiplier=cfg.proxy_service_multiplier,
        managed_enabled=cfg.managed_enabled,
        byok_enabled=cfg.byok_enabled,
        proxy_enabled=cfg.proxy_enabled,
    )


@router.get("/billing-rates", response_model=BillingRates)
async def get_billing_rates(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    cfg = await session.get(PlatformConfig, 1)
    return _rates_from_cfg(cfg) if cfg else _DEFAULT_RATES


@router.patch("/billing-rates", response_model=BillingRates)
async def patch_billing_rates(
    body: BillingRatesPatch,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    for field, value in body.model_dump(exclude_unset=True).items():
        if isinstance(value, float) and value <= 0:
            raise HTTPException(422, f"{field} must be greater than 0")

    cfg = await session.get(PlatformConfig, 1)
    if cfg is None:
        cfg = PlatformConfig(id=1)
        session.add(cfg)

    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(cfg, field, value)

    from app.models._utils import _utcnow
    cfg.updated_at = _utcnow()
    session.add(cfg)
    await session.commit()
    await session.refresh(cfg)
    return _rates_from_cfg(cfg)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

@router.get("/analytics")
async def admin_analytics(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    """Time-series platform analytics for the last 12 months."""
    from app.models.run import Run, Ticket

    cutoff = datetime.utcnow() - timedelta(days=365)

    def _by_month(rows):
        return [
            {"period": f"{int(r.yr):04d}-{int(r.mo):02d}", "count": int(r.cnt)}
            for r in rows
        ]

    yr_ws = extract("year", Workspace.created_at)
    mo_ws = extract("month", Workspace.created_at)
    ws_rows = (await session.execute(
        sa_select(yr_ws.label("yr"), mo_ws.label("mo"), func.count().label("cnt"))
        .where(Workspace.created_at >= cutoff)
        .group_by(yr_ws, mo_ws)
        .order_by(yr_ws, mo_ws)
    )).all()

    yr_u = extract("year", User.created_at)
    mo_u = extract("month", User.created_at)
    user_rows = (await session.execute(
        sa_select(yr_u.label("yr"), mo_u.label("mo"), func.count().label("cnt"))
        .where(User.created_at >= cutoff)
        .group_by(yr_u, mo_u)
        .order_by(yr_u, mo_u)
    )).all()

    yr_r = extract("year", Run.created_at)
    mo_r = extract("month", Run.created_at)
    run_rows = (await session.execute(
        sa_select(
            yr_r.label("yr"),
            mo_r.label("mo"),
            func.count().label("cnt"),
            func.sum(case((Run.status == "success", 1), else_=0)).label("success"),
        )
        .where(Run.created_at >= cutoff)
        .group_by(yr_r, mo_r)
        .order_by(yr_r, mo_r)
    )).all()

    tickets_by_status_rows = (await session.execute(
        sa_select(Ticket.status, func.count().label("cnt")).group_by(Ticket.status)
    )).all()
    tickets_by_status = {r.status: int(r.cnt) for r in tickets_by_status_rows}

    use_case_rows = (await session.execute(
        sa_select(User.use_case, func.count().label("cnt"))
        .where(User.use_case.isnot(None))
        .group_by(User.use_case)
        .order_by(func.count().desc())
    )).all()

    team_size_rows = (await session.execute(
        sa_select(User.team_size, func.count().label("cnt"))
        .where(User.team_size.isnot(None))
        .group_by(User.team_size)
        .order_by(func.count().desc())
    )).all()

    fb_total = (await session.exec(select(func.count()).select_from(UserFeedback))).one()
    fb_positive = (await session.exec(
        select(func.count()).select_from(UserFeedback).where(UserFeedback.helpful.is_(True))
    )).one()
    fb_negative = (await session.exec(
        select(func.count()).select_from(UserFeedback).where(UserFeedback.helpful.is_(False))
    )).one()

    # ── Usage breakdown: by team, by LLM mode, by model ─────────────────────
    from app.models.run import Run as _Run, Ticket as _Ticket

    team_rows = (await session.execute(
        sa_select(
            _Run.team,
            func.count().label("runs"),
            func.coalesce(func.sum(_Run.cost_usd), 0.0).label("cost"),
        )
        .where(_Run.created_at >= cutoff)
        .group_by(_Run.team)
        .order_by(func.coalesce(func.sum(_Run.cost_usd), 0.0).desc())
    )).all()

    # Tickets per team via run_id join
    ticket_team_rows = (await session.execute(
        sa_select(_Run.team, func.count(_Ticket.id).label("tickets"))
        .join(_Ticket, _Run.run_id == _Ticket.run_id)
        .where(_Run.created_at >= cutoff)
        .group_by(_Run.team)
    )).all()
    tickets_by_team = {r.team: int(r.tickets) for r in ticket_team_rows}

    _effective_mode = func.coalesce(_Run.llm_key_mode, Workspace.llm_key_mode).label("effective_mode")
    mode_rows = (await session.execute(
        sa_select(
            _effective_mode,
            func.count(_Run.id).label("runs"),
            func.coalesce(func.sum(_Run.cost_usd), 0.0).label("cost"),
        )
        .join(Workspace, _Run.workspace_id == Workspace.id, isouter=True)
        .where(_Run.created_at >= cutoff)
        .group_by(func.coalesce(_Run.llm_key_mode, Workspace.llm_key_mode))
        .order_by(func.coalesce(func.sum(_Run.cost_usd), 0.0).desc())
    )).all()

    model_rows = (await session.execute(
        sa_select(
            _Run.model,
            func.count().label("runs"),
            func.coalesce(func.sum(_Run.cost_usd), 0.0).label("cost"),
        )
        .where(_Run.model.isnot(None))
        .where(_Run.created_at >= cutoff)
        .group_by(_Run.model)
        .order_by(func.coalesce(func.sum(_Run.cost_usd), 0.0).desc())
    )).all()

    runs_with_success = [
        {
            "period": f"{int(r.yr):04d}-{int(r.mo):02d}",
            "count": int(r.cnt),
            "success": int(r.success or 0),
        }
        for r in run_rows
    ]

    return {
        "workspaces_by_month": _by_month(ws_rows),
        "users_by_month": _by_month(user_rows),
        "runs_by_month": runs_with_success,
        "tickets_by_status": tickets_by_status,
        "use_cases": [{"use_case": r.use_case, "count": int(r.cnt)} for r in use_case_rows],
        "team_sizes": [{"team_size": r.team_size, "count": int(r.cnt)} for r in team_size_rows],
        "feedback": {"total": int(fb_total), "positive": int(fb_positive), "negative": int(fb_negative)},
        "by_team": [
            {
                "team": r.team,
                "runs": int(r.runs),
                "tickets": tickets_by_team.get(r.team, 0),
                "cost_usd": round(float(r.cost), 4),
            }
            for r in team_rows
        ],
        "by_llm_mode": [
            {
                "mode": r.effective_mode,
                "runs": int(r.runs),
                "cost_usd": round(float(r.cost), 4),
            }
            for r in mode_rows
        ],
        "by_model": [
            {
                "model": r.model,
                "runs": int(r.runs),
                "cost_usd": round(float(r.cost), 4),
            }
            for r in model_rows
        ],
    }


# ---------------------------------------------------------------------------
# Feedback (admin read-only)
# ---------------------------------------------------------------------------

@router.get("/feedback", response_model=list[FeedbackRow])
async def list_feedback(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
    limit: int = 100,
    offset: int = 0,
):
    rows = (await session.exec(
        select(UserFeedback).order_by(UserFeedback.created_at.desc()).offset(offset).limit(limit)
    )).all()
    return rows


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


# ---------------------------------------------------------------------------
# Analytics: HITL resolution time
# ---------------------------------------------------------------------------

@router.get("/analytics/hitl-resolution")
async def hitl_resolution_stats(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    """Average HITL review resolution time and count of resolved reviews."""
    from app.models.run import HitlReview as _HitlReview

    result = (await session.execute(
        sa_select(
            func.avg(
                extract("epoch", _HitlReview.resolved_at - _HitlReview.created_at) / 60.0
            ).label("avg_min"),
            func.count().label("resolved_count"),
        )
        .where(_HitlReview.resolved_at.isnot(None))
    )).one()

    return {
        "avg_resolution_min": round(float(result.avg_min or 0), 2),
        "resolved_count": int(result.resolved_count),
    }


# ---------------------------------------------------------------------------
# Analytics: workspace churn
# ---------------------------------------------------------------------------

@router.get("/analytics/churn")
async def workspace_churn(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
    days: int = 30,
):
    """Workspaces that have run at least once but made no runs in the last *days* days."""
    from app.models.run import Run as _ChurnRun

    cutoff = datetime.utcnow() - timedelta(days=days)

    subq = (
        sa_select(
            _ChurnRun.workspace_id,
            func.max(_ChurnRun.created_at).label("last_run"),
        )
        .where(_ChurnRun.workspace_id.isnot(None))
        .group_by(_ChurnRun.workspace_id)
        .subquery()
    )

    rows = (await session.execute(
        sa_select(
            Workspace.id,
            Workspace.name,
            Workspace.slug,
            subq.c.last_run,
        )
        .join(subq, Workspace.id == subq.c.workspace_id)
        .where(subq.c.last_run < cutoff)
        .order_by(subq.c.last_run.asc())
    )).all()

    now = datetime.utcnow()
    return {
        "window_days": days,
        "count": len(rows),
        "churned_workspaces": [
            {
                "workspace_id": r.id,
                "name": r.name,
                "slug": r.slug,
                "last_run_at": r.last_run.isoformat() if r.last_run else None,
                "days_since_last_run": (now - r.last_run).days if r.last_run else None,
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Analytics: engine capability usage
# ---------------------------------------------------------------------------

@router.get("/analytics/capabilities")
async def capability_analytics(
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
    days: int = 30,
):
    """Engine capability usage analytics.

    Aggregates agent.start (dispatched) and agent.end (completed) events from
    the event table. Payload fields: agent_name, succeeded, duration_s, cost_usd.
    """
    from sqlalchemy import text as sa_text

    cutoff = datetime.utcnow() - timedelta(days=max(1, min(days, 365)))

    rows = (await session.execute(
        sa_text("""
            SELECT
                payload->>'agent_name'                                          AS capability,
                COUNT(*) FILTER (WHERE event_type = 'agent.start')              AS dispatched,
                COUNT(*) FILTER (WHERE event_type = 'agent.end')                AS completed,
                COUNT(*) FILTER (
                    WHERE event_type = 'agent.end'
                      AND (payload->>'succeeded')::boolean IS TRUE
                )                                                               AS succeeded,
                AVG(
                    CASE WHEN event_type = 'agent.end'
                         THEN (payload->>'duration_s')::float END
                )                                                               AS avg_duration_s,
                SUM(
                    CASE WHEN event_type = 'agent.end'
                         THEN (payload->>'cost_usd')::float END
                )                                                               AS total_cost_usd
            FROM event
            WHERE event_type IN ('agent.start', 'agent.end')
              AND payload->>'agent_name' IS NOT NULL
              AND recorded_at >= :cutoff
            GROUP BY payload->>'agent_name'
            ORDER BY dispatched DESC
        """),
        {"cutoff": cutoff},
    )).all()

    capabilities = []
    for r in rows:
        dispatched = int(r.dispatched or 0)
        completed  = int(r.completed  or 0)
        succeeded  = int(r.succeeded  or 0)
        capabilities.append({
            "capability":     r.capability,
            "dispatched":     dispatched,
            "completed":      completed,
            "succeeded":      succeeded,
            "success_rate":   round(succeeded / completed, 4) if completed else None,
            "avg_duration_s": round(float(r.avg_duration_s), 2) if r.avg_duration_s is not None else None,
            "total_cost_usd": round(float(r.total_cost_usd), 6) if r.total_cost_usd is not None else 0.0,
        })

    return {
        "window_days": days,
        "capabilities": capabilities,
    }


@router.post("/users/{user_id}/erase", status_code=200)
async def erase_user_data(
    user_id: int,
    _admin=Depends(require_platform_admin),
    session=Depends(get_session),
):
    """GDPR Art. 17 right to erasure.

    Anonymises the user's PII and replaces run.request content across all workspaces
    they own or are a member of with a datestamped placeholder. Billing records (run rows)
    are retained for legal obligation but their content is erased. Irreversible.
    """
    from app.models.auth import UserSession, ApiKey, WorkspaceMembership
    from app.models.workspace import Workspace
    from app.models.run import Run
    from app.models.discovery import DiscoverySession

    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if user.email.startswith("erased_") and user.email.endswith("@erased.antcrew"):
        raise HTTPException(409, "User data has already been erased")

    erased_at = datetime.utcnow().replace(microsecond=0).isoformat()
    placeholder = f"[erased {erased_at}]"

    # Anonymise user PII — keep the row for FK integrity
    user.email = f"erased_{user_id}@erased.antcrew"
    user.display_name = "[erased]"
    user.totp_secret = None
    user.mfa_enabled = False
    user.password_hash = "erased"
    session.add(user)

    # Collect all workspace IDs the user owns or has membership in
    owned_ids = {
        ws.id for ws in (await session.exec(
            select(Workspace).where(Workspace.owner_user_id == user_id)
        )).all()
        if ws.id is not None
    }
    member_ids = {
        m.workspace_id for m in (await session.exec(
            select(WorkspaceMembership).where(WorkspaceMembership.user_id == user_id)
        )).all()
    }
    all_ws_ids = owned_ids | member_ids

    # Erase run.request across all affected workspaces
    runs_erased = 0
    for ws_id in all_ws_ids:
        for run in (await session.exec(select(Run).where(Run.workspace_id == ws_id))).all():
            if run.request and not run.request.startswith("[erased"):
                run.request = placeholder
                session.add(run)
                runs_erased += 1

    # Delete DiscoverySessions (raw conversation turns)
    disc_count = 0
    for ws_id in all_ws_ids:
        for ds in (await session.exec(
            select(DiscoverySession).where(DiscoverySession.workspace_id == ws_id)
        )).all():
            await session.delete(ds)
            disc_count += 1

    # Revoke API keys
    keys = (await session.exec(
        select(ApiKey).where(ApiKey.user_id == user_id).where(ApiKey.revoked_at == None)  # noqa: E711
    )).all()
    now_dt = datetime.utcnow()
    for key in keys:
        key.revoked_at = now_dt
        session.add(key)

    # Delete browser sessions
    user_sessions = (await session.exec(
        select(UserSession).where(UserSession.user_id == user_id)
    )).all()
    for us in user_sessions:
        await session.delete(us)

    await session.commit()

    return {
        "erased_at": erased_at,
        "user_id": user_id,
        "email_anonymised": f"erased_{user_id}@erased.antcrew",
        "runs_request_erased": runs_erased,
        "discovery_sessions_deleted": disc_count,
        "api_keys_revoked": len(keys),
        "browser_sessions_deleted": len(user_sessions),
        "workspaces_affected": sorted(all_ws_ids),
    }
