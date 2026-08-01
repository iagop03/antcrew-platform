"""CRUD for recurring engine run schedules."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import get_workspace_context, WorkspaceContext, require_role
from app.core.database import get_session, engine as _db_engine
from app.models.run import RunSchedule

router = APIRouter(
    prefix="/run-schedules",
    tags=["run-schedules"],
    dependencies=[Depends(get_workspace_context)],
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _next_run(cron_expr: str) -> datetime:
    """Compute next fire time from a cron expression using croniter."""
    from croniter import croniter
    return croniter(cron_expr, _utcnow()).get_next(datetime)


class _ScheduleCreate(BaseModel):
    name: str
    goal: str
    model: str = "claude"
    conditions: Optional[list[str]] = None
    full: bool = True
    max_cost_usd: Optional[float] = None
    cron_expr: str


class _ScheduleUpdate(BaseModel):
    name: Optional[str] = None
    goal: Optional[str] = None
    model: Optional[str] = None
    conditions: Optional[list[str]] = None
    full: Optional[bool] = None
    max_cost_usd: Optional[float] = None
    cron_expr: Optional[str] = None
    enabled: Optional[bool] = None


@router.post("/", status_code=201, dependencies=[Depends(require_role("admin", "write"))])
async def create_schedule(
    body: _ScheduleCreate,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> RunSchedule:
    """Create a recurring engine run schedule."""
    from croniter import croniter
    if not croniter.is_valid(body.cron_expr):
        raise HTTPException(422, f"Invalid cron expression: {body.cron_expr!r}")

    schedule = RunSchedule(
        workspace_id=ctx.workspace_id,
        name=body.name.strip(),
        goal=body.goal.strip(),
        model=body.model,
        conditions=json.dumps(body.conditions) if body.conditions else None,
        full=body.full,
        max_cost_usd=body.max_cost_usd,
        cron_expr=body.cron_expr,
        enabled=True,
        next_run_at=_next_run(body.cron_expr),
        created_by=ctx.created_by,
        created_at=_utcnow(),
    )
    session.add(schedule)
    await session.commit()
    await session.refresh(schedule)
    return schedule


@router.get("/")
async def list_schedules(
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> list[RunSchedule]:
    result = await session.exec(
        select(RunSchedule)
        .where(RunSchedule.workspace_id == ctx.workspace_id)
        .order_by(RunSchedule.created_at.desc())  # type: ignore[attr-defined]
    )
    return list(result.all())


@router.get("/{schedule_id}")
async def get_schedule(
    schedule_id: int,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> RunSchedule:
    s = (await session.exec(select(RunSchedule).where(RunSchedule.id == schedule_id))).first()
    if not s or s.workspace_id != ctx.workspace_id:
        raise HTTPException(404, "Schedule not found")
    return s


@router.patch("/{schedule_id}", dependencies=[Depends(require_role("admin", "write"))])
async def update_schedule(
    schedule_id: int,
    body: _ScheduleUpdate,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> RunSchedule:
    s = (await session.exec(select(RunSchedule).where(RunSchedule.id == schedule_id))).first()
    if not s or s.workspace_id != ctx.workspace_id:
        raise HTTPException(404, "Schedule not found")

    if body.name is not None:
        s.name = body.name.strip()
    if body.goal is not None:
        s.goal = body.goal.strip()
    if body.model is not None:
        s.model = body.model
    if body.conditions is not None:
        s.conditions = json.dumps(body.conditions)
    if body.full is not None:
        s.full = body.full
    if body.max_cost_usd is not None:
        s.max_cost_usd = body.max_cost_usd
    if body.cron_expr is not None:
        from croniter import croniter
        if not croniter.is_valid(body.cron_expr):
            raise HTTPException(422, f"Invalid cron expression: {body.cron_expr!r}")
        s.cron_expr = body.cron_expr
        s.next_run_at = _next_run(body.cron_expr)
    if body.enabled is not None:
        s.enabled = body.enabled

    session.add(s)
    await session.commit()
    await session.refresh(s)
    return s


@router.delete("/{schedule_id}", status_code=204, dependencies=[Depends(require_role("admin"))])
async def delete_schedule(
    schedule_id: int,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> None:
    s = (await session.exec(select(RunSchedule).where(RunSchedule.id == schedule_id))).first()
    if not s or s.workspace_id != ctx.workspace_id:
        raise HTTPException(404, "Schedule not found")
    await session.delete(s)
    await session.commit()


# ---------------------------------------------------------------------------
# Background dispatcher — called from main.py lifespan loop
# ---------------------------------------------------------------------------

async def dispatch_due_run_schedules(db_engine) -> int:
    """Fire all enabled RunSchedule entries whose next_run_at <= now. Returns count dispatched."""
    import json as _json
    from sqlmodel.ext.asyncio.session import AsyncSession as _AsyncSession
    from app.services.engine_runner import dispatch_engine

    now = _utcnow()
    dispatched = 0

    async with _AsyncSession(db_engine, expire_on_commit=False) as session:
        due = (await session.exec(
            select(RunSchedule).where(
                RunSchedule.enabled == True,  # noqa: E712
                RunSchedule.next_run_at <= now,
            )
        )).all()

        for sched in due:
            try:
                conditions = _json.loads(sched.conditions) if sched.conditions else []
                run_id = await dispatch_engine(
                    goal=sched.goal,
                    model=sched.model,
                    conditions=conditions,
                    full=sched.full,
                    max_cost_usd=sched.max_cost_usd,
                    workspace_id=sched.workspace_id,
                    created_by=sched.created_by or "scheduler",
                )
                sched.last_run_id = run_id
                sched.next_run_at = _next_run(sched.cron_expr)
                session.add(sched)
                dispatched += 1
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "run scheduler: failed to dispatch schedule %d: %s", sched.id, exc
                )

        if dispatched:
            await session.commit()

    return dispatched
