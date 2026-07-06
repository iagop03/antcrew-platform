"""CRUD endpoints for recurring eval schedules."""
from __future__ import annotations

import asyncio
import functools
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, get_workspace_context, WorkspaceContext, ws_filter, ws_accessible
from app.core.database import get_session
from app.models.run import EvalSchedule, EvalRun

router = APIRouter(
    prefix="/eval-schedules",
    tags=["eval-schedules"],
    dependencies=[Depends(require_api_key)],
)


class ScheduleCreate(BaseModel):
    name: str
    team: str
    request: str
    interval_hours: float = 24.0
    model: str = ""
    judge_model: str = ""
    expect_min_tickets: int = 0
    expect_min_code_files: int = 0
    expect_review_verdict: str = ""


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.post("/", response_model=EvalSchedule, status_code=201)
async def create_schedule(
    body: ScheduleCreate,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    sched = EvalSchedule(
        name=body.name,
        team=body.team,
        request=body.request,
        interval_hours=body.interval_hours,
        model=body.model,
        judge_model=body.judge_model,
        expect_min_tickets=body.expect_min_tickets,
        expect_min_code_files=body.expect_min_code_files,
        expect_review_verdict=body.expect_review_verdict,
        workspace_id=ctx.workspace_id,
        next_run_at=datetime.now(timezone.utc) + timedelta(hours=body.interval_hours),
    )
    session.add(sched)
    await session.commit()
    await session.refresh(sched)
    return sched


@router.get("/", response_model=list[EvalSchedule])
async def list_schedules(
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    q = select(EvalSchedule)
    q = ws_filter(q, EvalSchedule.workspace_id, ctx)
    q = q.order_by(EvalSchedule.created_at.desc())
    return (await session.exec(q)).all()


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule(
    schedule_id: int,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    result = await session.exec(select(EvalSchedule).where(EvalSchedule.id == schedule_id))
    sched = result.first()
    if not sched:
        raise HTTPException(404, f"Schedule {schedule_id} not found")
    if not ws_accessible(sched.workspace_id, ctx):
        raise HTTPException(403, "Not accessible with the current API key")
    await session.delete(sched)
    await session.commit()


@router.patch("/{schedule_id}/toggle", response_model=EvalSchedule)
async def toggle_schedule(
    schedule_id: int,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    result = await session.exec(select(EvalSchedule).where(EvalSchedule.id == schedule_id))
    sched = result.first()
    if not sched:
        raise HTTPException(404, f"Schedule {schedule_id} not found")
    if not ws_accessible(sched.workspace_id, ctx):
        raise HTTPException(403, "Not accessible with the current API key")
    sched.enabled = not sched.enabled
    session.add(sched)
    await session.commit()
    await session.refresh(sched)
    return sched


# ---------------------------------------------------------------------------
# Scheduler dispatch (called from main.py background loop)
# ---------------------------------------------------------------------------

async def dispatch_due_schedules(engine) -> int:
    """Fire all enabled schedules whose next_run_at is in the past. Returns count dispatched."""
    import logging
    from app.services.eval_runner import EvalRunConfig, run_eval_sync, _executor
    from sqlmodel.ext.asyncio.session import AsyncSession as _Sess

    log = logging.getLogger("eval_scheduler")
    now = datetime.now(timezone.utc)

    async with _Sess(engine, expire_on_commit=False) as session:
        result = await session.exec(
            select(EvalSchedule).where(
                EvalSchedule.enabled == True,
                EvalSchedule.next_run_at <= now,
            )
        )
        schedules = result.all()

        if not schedules:
            return 0

        loop = asyncio.get_running_loop()
        dispatched = 0

        for sched in schedules:
            eval_id = str(uuid.uuid4())
            row = EvalRun(
                eval_id=eval_id,
                team=sched.team,
                request=sched.request,
                name=f"{sched.name} (scheduled)",
                model=sched.model,
                judge_model=sched.judge_model,
                status="running",
                workspace_id=sched.workspace_id,
            )
            session.add(row)

            cfg = EvalRunConfig(
                team=sched.team,
                request=sched.request,
                name=sched.name,
                model=sched.model,
                judge_model=sched.judge_model,
                expect_min_tickets=sched.expect_min_tickets,
                expect_min_code_files=sched.expect_min_code_files,
                expect_review_verdict=sched.expect_review_verdict,
            )
            loop.run_in_executor(_executor, functools.partial(run_eval_sync, eval_id, cfg, loop))

            sched.next_run_at = now + timedelta(hours=sched.interval_hours)
            sched.last_eval_id = eval_id
            session.add(sched)
            dispatched += 1
            log.info("eval_scheduler: dispatched eval_id=%s for schedule %r", eval_id, sched.name)

        await session.commit()
        return dispatched
