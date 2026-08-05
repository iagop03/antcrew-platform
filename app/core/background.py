"""Background task loops for antcrew-platform.

All functions run as long-lived asyncio tasks started in the FastAPI lifespan.
"""
from __future__ import annotations

import asyncio
import logging

from sqlmodel import select

log = logging.getLogger(__name__)


async def _do_retention(engine, cutoff) -> tuple[int, int]:
    """Delete stale rows older than *cutoff*. Returns (deliveries_deleted, events_deleted).

    Only terminal webhook deliveries (delivered, failed) are eligible — pending/retrying
    rows are kept regardless of age.
    """
    from sqlmodel import col
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.models.run import WebhookDelivery, Event as DBEvent

    async with AsyncSession(engine, expire_on_commit=False) as session:
        stale_deliveries = (await session.exec(
            select(WebhookDelivery)
            .where(WebhookDelivery.created_at <= cutoff)
            .where(col(WebhookDelivery.status).in_(["delivered", "failed"]))
        )).all()
        for d in stale_deliveries:
            await session.delete(d)

        stale_events = (await session.exec(
            select(DBEvent).where(DBEvent.recorded_at <= cutoff)
        )).all()
        for e in stale_events:
            await session.delete(e)

        if stale_deliveries or stale_events:
            await session.commit()

    return len(stale_deliveries), len(stale_events)


async def _hitl_cleanup_loop() -> None:
    """Mark stale pending reviews as 'timeout' every 5 minutes."""
    import os as _os
    from datetime import timedelta
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.core.database import engine as _engine
    from app.models.run import HitlReview, HitlAuditEntry

    timeout_s = float(_os.environ.get("HITL_TIMEOUT_S", "3600"))
    log.info("hitl cleanup started (timeout=%.0fs)", timeout_s)
    while True:
        await asyncio.sleep(300)
        try:
            from datetime import datetime, timezone
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=timeout_s)
            async with AsyncSession(_engine, expire_on_commit=False) as session:
                result = await session.exec(
                    select(HitlReview).where(
                        HitlReview.status == "pending",
                        HitlReview.created_at <= cutoff,
                    )
                )
                stale = result.all()
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                for r in stale:
                    r.status = "timeout"
                    r.resolved_at = now
                    session.add(r)
                    session.add(HitlAuditEntry(
                        review_id=r.review_id,
                        actor_label=None,
                        action="timed_out",
                        note=f"Auto-timed-out after {timeout_s:.0f}s",
                    ))
                if stale:
                    await session.commit()
                    log.info("hitl cleanup: marked %d stale review(s) as timeout", len(stale))
        except Exception as exc:
            log.warning("hitl cleanup error: %s", exc)


async def _data_retention_loop() -> None:
    """Delete terminal WebhookDelivery and old Event rows on a daily cadence.

    Retention window is configurable via DATA_RETENTION_DAYS (default: 30).
    """
    import os as _os
    from datetime import timedelta
    from app.core.database import engine as _engine

    retention_days = int(_os.environ.get("DATA_RETENTION_DAYS", "30"))
    log.info("data retention started (retention=%dd)", retention_days)
    while True:
        await asyncio.sleep(3600)
        try:
            from datetime import datetime, timezone
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=retention_days)
            deleted_d, deleted_e = await _do_retention(_engine, cutoff)
            if deleted_d or deleted_e:
                log.info(
                    "data retention: deleted %d deliveries, %d events",
                    deleted_d, deleted_e,
                )
        except Exception as exc:
            log.warning("data retention error: %s", exc)


async def _eval_scheduler_loop() -> None:
    """Fire due EvalSchedule entries every 60 seconds."""
    from app.api.eval_schedules import dispatch_due_schedules
    from app.core.database import engine as _engine
    log.info("eval scheduler started")
    while True:
        await asyncio.sleep(60)
        try:
            n = await dispatch_due_schedules(_engine)
            if n:
                log.info("eval scheduler dispatched %d run(s)", n)
        except Exception as exc:
            log.warning("eval scheduler error: %s", exc)


async def _run_scheduler_loop() -> None:
    """Fire due RunSchedule entries every 60 seconds."""
    from app.api.run_schedules import dispatch_due_run_schedules
    from app.core.database import engine as _engine
    log.info("run scheduler started")
    while True:
        await asyncio.sleep(60)
        try:
            n = await dispatch_due_run_schedules(_engine)
            if n:
                log.info("run scheduler dispatched %d engine run(s)", n)
        except Exception as exc:
            log.warning("run scheduler error: %s", exc)


async def _discovery_session_cleanup_loop() -> None:
    """Delete stale DiscoverySession rows every 6 hours.

    A session is eligible if updated_at has not changed in DISCOVERY_SESSION_TTL_DAYS
    (default 7). Covers both abandoned in-progress sessions and completed ones.
    """
    import os as _os
    from datetime import datetime, timedelta, timezone
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.core.database import engine as _engine
    from app.models.discovery import DiscoverySession

    ttl_days = int(_os.environ.get("DISCOVERY_SESSION_TTL_DAYS", "7"))
    log.info("discovery session cleanup started (ttl=%dd)", ttl_days)
    while True:
        await asyncio.sleep(21600)  # 6 hours
        try:
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=ttl_days)
            async with AsyncSession(_engine, expire_on_commit=False) as session:
                stale = (await session.exec(
                    select(DiscoverySession).where(DiscoverySession.updated_at <= cutoff)
                )).all()
                for s in stale:
                    await session.delete(s)
                if stale:
                    await session.commit()
                    log.info("discovery cleanup: deleted %d stale session(s)", len(stale))
        except Exception as exc:
            log.warning("discovery cleanup error: %s", exc)
