"""REST endpoints for pipeline runs."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_api_key
from app.core.database import get_session
from app.models.run import Run, Event as DBEvent
from app.services.runs import get_run, get_run_events, list_runs

router = APIRouter(
    prefix="/runs",
    tags=["runs"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/", response_model=list[Run])
async def index(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    team: Optional[str] = None,
    status: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    return await list_runs(session, limit=limit, offset=offset, team=team, status=status)


@router.get("/{run_id}", response_model=Run)
async def detail(run_id: str, session: AsyncSession = Depends(get_session)):
    run = await get_run(session, run_id)
    if not run:
        raise HTTPException(404, f"Run {run_id!r} not found")
    return run


@router.get("/{run_id}/state")
async def state(run_id: str, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Return the full serialized RunResult state for a completed run.

    The state is stored after the pipeline finishes and includes all agent
    outputs (prd, tickets, code_artifacts, etc.) with private keys (_run_id,
    _thread_id, …) excluded.
    """
    run = await get_run(session, run_id)
    if not run:
        raise HTTPException(404, f"Run {run_id!r} not found")
    if run.state is None:
        raise HTTPException(
            404,
            f"State not available yet — run {run_id!r} is still {run.status!r}",
        )
    return run.state


@router.get("/{run_id}/events", response_model=list[DBEvent])
async def events(run_id: str, session: AsyncSession = Depends(get_session)):
    return await get_run_events(session, run_id)
