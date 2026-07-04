"""Antcrew event bus listener — persists every event to the DB.

Subscribe once at app startup. Every pipeline.start/end, agent.start/end,
feedback.*, router.dispatch, etc. is written to the events table and
used to update the runs table in real time.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from antcrew.core.events import bus
from app.core.database import AsyncSessionLocal
from app.models.run import Run, Event as DBEvent

if TYPE_CHECKING:
    from antcrew.core.events import Event

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sync_handler(event: "Event") -> None:
    """Fire-and-forget: schedule DB write on the running event loop."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_persist_event(event))
    except RuntimeError:
        pass  # no event loop in sync test context


async def _persist_event(event: "Event") -> None:
    try:
        async with AsyncSessionLocal() as session:
            # Always record the raw event
            db_event = DBEvent(
                run_id=event.run_id,
                thread_id=event.thread_id,
                event_type=event.type,
                payload=dict(event.payload),
                timestamp=event.timestamp,
            )
            session.add(db_event)

            # Update or create Run row on lifecycle events
            if event.type == "pipeline.start" and event.run_id:
                run = Run(
                    run_id=event.run_id,
                    thread_id=event.thread_id or "default",
                    team=event.payload.get("team", "unknown"),
                    request=event.payload.get("request", ""),
                    status="running",
                )
                await session.merge(run)

            elif event.type == "pipeline.end" and event.run_id:
                from sqlmodel import select
                stmt = select(Run).where(Run.run_id == event.run_id)
                result = await session.exec(stmt)
                run = result.first()
                if run:
                    run.status = "success" if event.payload.get("success") else "error"
                    run.cost_usd = event.payload.get("cost_usd", 0.0)
                    run.finished_at = _utcnow()
                    session.add(run)

            await session.commit()
    except Exception as exc:
        log.warning("platform listener: DB write failed: %s", exc)


def start_listening() -> None:
    """Subscribe the platform listener to the global antcrew bus."""
    bus.subscribe("*", _sync_handler)
    log.info("antcrew-platform: listening to antcrew event bus")


def stop_listening() -> None:
    bus.unsubscribe("*", _sync_handler)
