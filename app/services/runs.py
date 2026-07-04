"""Run persistence and query helpers."""
from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, desc

from app.models.run import Run, Event as DBEvent, Ticket


async def list_runs(
    session: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    team: Optional[str] = None,
    status: Optional[str] = None,
) -> list[Run]:
    stmt = select(Run).order_by(desc(Run.created_at)).offset(offset).limit(limit)
    if team:
        stmt = stmt.where(Run.team == team)
    if status:
        stmt = stmt.where(Run.status == status)
    result = await session.exec(stmt)
    return list(result.all())


async def get_run(session: AsyncSession, run_id: str) -> Optional[Run]:
    result = await session.exec(select(Run).where(Run.run_id == run_id))
    return result.first()


async def get_run_events(session: AsyncSession, run_id: str) -> list[DBEvent]:
    result = await session.exec(
        select(DBEvent)
        .where(DBEvent.run_id == run_id)
        .order_by(DBEvent.timestamp)
    )
    return list(result.all())


async def upsert_tickets_from_run(
    session: AsyncSession,
    run_id: str,
    run_state: dict,
) -> int:
    """Extract tickets from a RunResult state and upsert by ticket_id.

    Uses the deterministic TICKET-<sha256[:8]> IDs from antcrew v0.14.0,
    so re-running the same PRD updates existing tickets instead of
    creating duplicates.
    """
    from sqlmodel import select
    raw_tickets = run_state.get("tickets") or []
    prd = run_state.get("prd") or {}
    prd_title = (prd.get("title") or "") if isinstance(prd, dict) else getattr(prd, "title", "")
    count = 0
    for t in raw_tickets:
        if isinstance(t, dict):
            tid = t.get("id", "")
            title = t.get("title", "")
            desc = t.get("description", "")
            priority = t.get("priority", "medium")
            status = t.get("status", "open")
        else:
            tid = getattr(t, "id", "")
            title = getattr(t, "title", "")
            desc = getattr(t, "description", "")
            priority = str(getattr(t, "priority", "medium"))
            status = str(getattr(t, "status", "open"))

        if not tid:
            continue

        result = await session.exec(select(Ticket).where(Ticket.ticket_id == tid))
        existing = result.first()
        if existing:
            existing.title = title
            existing.description = desc
            existing.priority = priority
            existing.status = status
            existing.run_id = run_id
            session.add(existing)
        else:
            session.add(Ticket(
                ticket_id=tid, run_id=run_id,
                title=title, description=desc,
                priority=priority, status=status,
                prd_title=prd_title,
            ))
        count += 1
    await session.commit()
    return count


async def list_tickets(
    session: AsyncSession,
    *,
    status: Optional[str] = None,
    limit: int = 200,
) -> list[Ticket]:
    stmt = select(Ticket).order_by(desc(Ticket.updated_at)).limit(limit)
    if status:
        stmt = stmt.where(Ticket.status == status)
    result = await session.exec(stmt)
    return list(result.all())
