"""REST endpoints for pipeline runs."""
from __future__ import annotations

import io
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, get_workspace_context, WorkspaceContext, require_role
from app.core.database import get_session
from app.models.run import Run, Event as DBEvent
from app.services.runs import cancel_run, get_run, get_run_events, get_run_tickets, get_run_stats, list_runs

router = APIRouter(
    prefix="/runs",
    tags=["runs"],
    dependencies=[Depends(require_api_key)],
)


class RunUpload(BaseModel):
    """Pre-computed run result from a local `antcrew run --push-to` execution."""
    team: str
    request: str
    thread_id: str = "default"
    cost_usd: float = 0.0
    duration_s: Optional[float] = None
    state: Optional[dict] = None


def _assert_run_access(run: Run, ctx: WorkspaceContext) -> None:
    """Raise 403 if the API key is workspace-scoped and doesn't own this run."""
    from app.core.auth import ws_accessible
    if ctx.workspace_ids is not None and not ws_accessible(run.workspace_id, ctx):
        raise HTTPException(403, "This run is not accessible with the current API key")


@router.post("/upload", status_code=201, response_model=Run,
             dependencies=[Depends(require_role("admin", "write"))])
async def upload_run(
    body: RunUpload,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Store a local CLI run result on the platform dashboard.

    Called by ``antcrew run --push-to <platform-url>`` after a successful local run.
    The run appears in the dashboard immediately with status ``success``.
    Tickets in ``state.tickets`` are upserted via the normal ticket pipeline.
    """
    from app.services.runner import AVAILABLE_TEAMS
    from app.services.runs import upsert_tickets_from_run

    if body.team not in AVAILABLE_TEAMS:
        raise HTTPException(422, f"Unknown team {body.team!r}. Available: {AVAILABLE_TEAMS}")
    if not body.request.strip():
        raise HTTPException(422, "request must not be empty")

    run = Run(
        run_id=str(uuid.uuid4()),
        thread_id=body.thread_id,
        team=body.team,
        request=body.request.strip(),
        status="success",
        cost_usd=body.cost_usd,
        duration_s=body.duration_s,
        state=body.state,
        workspace_id=ctx.workspace_id,
        created_by=ctx.created_by,
        finished_at=datetime.now(timezone.utc),
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    if body.state:
        await upsert_tickets_from_run(session, run.run_id, body.state)
        await session.commit()

    return run


@router.get("/stats")
async def stats(
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Aggregate counts and total cost. Scoped to the API key's workspace if set."""
    return await get_run_stats(session, workspace_ids=ctx.workspace_ids)


@router.get("/", response_model=list[Run])
async def index(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    since_id: Optional[int] = Query(None, description="Cursor: return runs with id < since_id"),
    team: Optional[str] = None,
    status: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    return await list_runs(
        session, limit=limit, offset=offset, team=team, status=status,
        since_id=since_id, workspace_ids=ctx.workspace_ids,
    )


@router.get("/{run_id}", response_model=Run)
async def detail(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    run = await get_run(session, run_id)
    if not run:
        raise HTTPException(404, f"Run {run_id!r} not found")
    _assert_run_access(run, ctx)
    return run


@router.post("/{run_id}/cancel", response_model=Run,
             dependencies=[Depends(require_role("admin", "write"))])
async def cancel(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Mark a running run as cancelled. The background thread continues until it finishes
    naturally — this only updates the DB status immediately."""
    existing = await get_run(session, run_id)
    if not existing:
        raise HTTPException(404, f"Run {run_id!r} not found")
    _assert_run_access(existing, ctx)
    run = await cancel_run(session, run_id)
    if run is None:
        raise HTTPException(409, f"Run {run_id!r} is not running (status: {existing.status!r})")
    return run


@router.get("/{run_id}/state")
async def state(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, Any]:
    """Return the full serialized RunResult state for a completed run."""
    run = await get_run(session, run_id)
    if not run:
        raise HTTPException(404, f"Run {run_id!r} not found")
    _assert_run_access(run, ctx)
    if run.state is None:
        raise HTTPException(
            404,
            f"State not available yet — run {run_id!r} is still {run.status!r}",
        )
    return run.state


@router.get("/{run_id}/tickets")
async def tickets(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    """Return tickets produced by a specific run."""
    run = await get_run(session, run_id)
    if not run:
        raise HTTPException(404, f"Run {run_id!r} not found")
    _assert_run_access(run, ctx)
    return await get_run_tickets(session, run_id)


@router.get("/{run_id}/artifacts")
async def artifacts(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Return generated code, devops, doc, and test artifacts for a completed run.

    Each artifact list is empty (not null) when the run hasn't produced that type.
    The run must be in 'success' status; 404 is returned for missing state.
    """
    run = await get_run(session, run_id)
    if not run:
        raise HTTPException(404, f"Run {run_id!r} not found")
    _assert_run_access(run, ctx)
    if run.state is None:
        raise HTTPException(
            404,
            f"State not available — run {run_id!r} is still {run.status!r}",
        )
    s = run.state
    return {
        "run_id": run_id,
        "status": run.status,
        "code_artifacts":   s.get("code_artifacts")   or [],
        "devops_artifacts": s.get("devops_artifacts") or [],
        "doc_artifacts":    s.get("doc_artifacts")    or [],
        "test_artifacts":   s.get("test_artifacts")   or [],
    }


@router.get("/{run_id}/artifacts.zip")
async def artifacts_zip(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> StreamingResponse:
    """Download all code, test, and devops artifacts as a ZIP archive."""
    run = await get_run(session, run_id)
    if not run:
        raise HTTPException(404, f"Run {run_id!r} not found")
    _assert_run_access(run, ctx)
    if run.state is None:
        raise HTTPException(404, f"State not available — run {run_id!r} is still {run.status!r}")

    s = run.state
    all_artifacts = (
        (s.get("code_artifacts") or [])
        + (s.get("test_artifacts") or [])
        + (s.get("devops_artifacts") or [])
        + (s.get("doc_artifacts") or [])
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for art in all_artifacts:
            if isinstance(art, dict):
                path = art.get("file_path") or art.get("path") or ""
                content = art.get("content") or ""
            else:
                path = getattr(art, "file_path", "") or getattr(art, "path", "") or ""
                content = getattr(art, "content", "") or ""
            if path:
                zf.writestr(path.lstrip("/"), content)
    buf.seek(0)

    filename = f"antcrew-{run_id[:12]}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{run_id}/events", response_model=list[DBEvent])
async def events(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    run = await get_run(session, run_id)
    if not run:
        raise HTTPException(404, f"Run {run_id!r} not found")
    _assert_run_access(run, ctx)
    return await get_run_events(session, run_id)
