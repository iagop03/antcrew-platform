"""REST endpoints for the ticket kanban."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, get_workspace_context, WorkspaceContext, ws_accessible
from app.core.database import get_session
from app.models.run import Ticket
from app.services.runs import list_tickets

router = APIRouter(prefix="/tickets", tags=["tickets"], dependencies=[Depends(require_api_key)])

_VALID_STATUSES = ("open", "in_progress", "done", "blocked")


class StatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in _VALID_STATUSES:
            raise ValueError(f"status must be one of {_VALID_STATUSES}")
        return v


@router.get("/export-targets")
async def export_targets() -> dict:
    """Return which export integrations are currently configured."""
    from app.services.export import available_targets
    return {"targets": available_targets()}


@router.get("/", response_model=list[Ticket])
async def index(
    status: Optional[str] = None,
    search: Optional[str] = Query(None, description="Filter by title, description, or PRD"),
    limit: int = Query(200, le=500),
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    return await list_tickets(session, status=status, search=search, limit=limit, workspace_ids=ctx.workspace_ids)


@router.patch("/{ticket_id}/status", response_model=Ticket)
async def update_status(
    ticket_id: str,
    body: StatusUpdate,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
):
    result = await session.exec(select(Ticket).where(Ticket.ticket_id == ticket_id))
    ticket = result.first()
    if not ticket:
        raise HTTPException(404, f"Ticket {ticket_id!r} not found")
    if ctx.workspace_ids is not None:
        from app.models.run import Run
        run_result = await session.exec(select(Run).where(Run.run_id == ticket.run_id))
        run = run_result.first()
        if run and not ws_accessible(run.workspace_id, ctx):
            raise HTTPException(403, "This ticket is not accessible with the current API key")
    ticket.status = body.status
    ticket.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)
    return ticket


@router.post("/{ticket_id}/export")
async def export_ticket(
    ticket_id: str,
    target: str = Query(..., description="Export target: 'jira' or 'linear'"),
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> dict:
    """Export a ticket to an external project management tool."""
    result = await session.exec(select(Ticket).where(Ticket.ticket_id == ticket_id))
    ticket = result.first()
    if not ticket:
        raise HTTPException(404, f"Ticket {ticket_id!r} not found")
    if ctx.workspace_ids is not None:
        from app.models.run import Run
        run_result = await session.exec(select(Run).where(Run.run_id == ticket.run_id))
        run = run_result.first()
        if run and not ws_accessible(run.workspace_id, ctx):
            raise HTTPException(403, "This ticket is not accessible with the current API key")

    from app.services.export import export_to_jira, export_to_linear
    try:
        if target == "jira":
            url = await export_to_jira(ticket)
        elif target == "linear":
            url = await export_to_linear(ticket)
        else:
            raise HTTPException(422, f"Unknown target {target!r}. Use 'jira' or 'linear'.")
        return {"url": url, "target": target, "ticket_id": ticket_id}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Export failed: {exc}")
