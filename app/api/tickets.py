"""REST endpoints for the ticket kanban."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.exceptions import TicketNotFoundError
from pydantic import BaseModel, field_validator
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import require_api_key, get_workspace_context, WorkspaceContext, ws_accessible, require_role
from app.core.database import get_session
from app.models.run import Run, Ticket
from app.services.runs import list_tickets

router = APIRouter(prefix="/tickets", tags=["tickets"], dependencies=[Depends(require_api_key)])

_VALID_STATUSES = ("open", "in_progress", "done", "blocked")
_VALID_TICKET_TYPES = ("task", "manual_action", "bug")


class StatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in _VALID_STATUSES:
            raise ValueError(f"status must be one of {_VALID_STATUSES}")
        return v


class ManualActionCreate(BaseModel):
    """Body for POST /tickets/ — create a manual-action blocking ticket."""
    run_id: str
    title: str
    description: str = ""
    assignee: Optional[str] = None
    priority: str = "high"


@router.post(
    "/",
    status_code=201,
    response_model=Ticket,
    dependencies=[Depends(require_role("admin", "write"))],
)
async def create_manual_action(
    body: ManualActionCreate,
    session: AsyncSession = Depends(get_session),
    ctx: WorkspaceContext = Depends(get_workspace_context),
) -> Ticket:
    """Create a manual-action blocking ticket for any run.

    The associated run is immediately set to ``blocked``. The run resumes
    (status → ``running``) when the ticket is resolved via PATCH /tickets/{id}/status
    with ``status="done"``.

    Use this endpoint when an external agent or the platform itself determines
    that a human step is needed outside of an engine capability graph.
    """
    # Verify the run is accessible
    run_result = await session.exec(select(Run).where(Run.run_id == body.run_id))
    run = run_result.first()
    if run is None:
        raise HTTPException(404, f"Run {body.run_id!r} not found")
    if ctx.workspace_ids is not None and not ws_accessible(run.workspace_id, ctx):
        raise HTTPException(403, "Run is not accessible with the current API key")

    ticket_id = str(uuid.uuid4())
    ticket = Ticket(
        ticket_id=ticket_id,
        run_id=body.run_id,
        title=body.title,
        description=body.description,
        ticket_type="manual_action",
        blocking=True,
        assignee=body.assignee,
        priority=body.priority,
        status="open",
    )
    session.add(ticket)

    # Block the run
    if run.status not in ("error", "success", "cancelled"):
        run.status = "blocked"
        session.add(run)

    await session.commit()
    await session.refresh(ticket)
    return ticket


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
        raise TicketNotFoundError(ticket_id)

    run = None
    if ctx.workspace_ids is not None:
        run_result = await session.exec(select(Run).where(Run.run_id == ticket.run_id))
        run = run_result.first()
        if run is None or not ws_accessible(run.workspace_id, ctx):
            raise HTTPException(403, "This ticket is not accessible with the current API key")

    ticket.status = body.status
    ticket.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(ticket)

    # Unblock the run when the last blocking ticket is resolved
    if ticket.blocking and body.status == "done":
        if run is None:
            run_result = await session.exec(select(Run).where(Run.run_id == ticket.run_id))
            run = run_result.first()

        if run and run.status == "blocked":
            # Check if there are other open blocking tickets for this run
            other_blocking = (await session.exec(
                select(Ticket).where(
                    Ticket.run_id == ticket.run_id,
                    Ticket.blocking == True,  # noqa: E712
                    Ticket.ticket_id != ticket_id,
                    Ticket.status != "done",
                )
            )).first()
            if not other_blocking:
                run.status = "running"
                session.add(run)

        # Wake up the engine thread if it's blocking on this ticket
        from app.services.engine_runner import resolve_manual_action
        resolve_manual_action(ticket_id)

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
        raise TicketNotFoundError(ticket_id)
    if ctx.workspace_ids is not None:
        from app.models.run import Run
        run_result = await session.exec(select(Run).where(Run.run_id == ticket.run_id))
        run = run_result.first()
        if run is None or not ws_accessible(run.workspace_id, ctx):
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
