"""REST endpoints for the ticket kanban."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.auth import require_api_key
from app.core.database import get_session
from app.models.run import Ticket
from app.services.runs import list_tickets

router = APIRouter(prefix="/tickets", tags=["tickets"], dependencies=[Depends(require_api_key)])


@router.get("/", response_model=list[Ticket])
async def index(
    status: Optional[str] = None,
    limit: int = Query(200, le=500),
    session: AsyncSession = Depends(get_session),
):
    return await list_tickets(session, status=status, limit=limit)


@router.patch("/{ticket_id}/status")
async def update_status(
    ticket_id: str,
    body: dict,
    session: AsyncSession = Depends(get_session),
):
    result = await session.exec(select(Ticket).where(Ticket.ticket_id == ticket_id))
    ticket = result.first()
    if not ticket:
        raise HTTPException(404, f"Ticket {ticket_id!r} not found")
    new_status = body.get("status")
    if new_status not in ("open", "in_progress", "done", "blocked"):
        raise HTTPException(422, f"Invalid status: {new_status!r}")
    ticket.status = new_status
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)
    return ticket
