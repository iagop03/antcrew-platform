"""antcrew-platform FastAPI application."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import select

from app.core.database import init_db, get_session
from app.core.listener import start_listening, stop_listening
from app.api import runs, tickets, stream, pipeline, api_keys, reviews, templates, workspaces, evals
from app.api import eval_schedules, engine

_STATIC = Path(__file__).parent / "static"
_VERSION = "0.4.0"


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        })


def _setup_logging() -> None:
    level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    handler = logging.StreamHandler()
    if os.environ.get("LOG_FORMAT", "json").lower() == "json":
        handler.setFormatter(_JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

_webhook_task: Optional[asyncio.Task] = None
_scheduler_task: Optional[asyncio.Task] = None
_hitl_cleanup_task: Optional[asyncio.Task] = None
_retention_task: Optional[asyncio.Task] = None

log = logging.getLogger(__name__)


async def _hitl_cleanup_loop() -> None:
    """Mark stale pending reviews as 'timeout' every 5 minutes."""
    import os as _os
    from datetime import timedelta
    from sqlmodel import select
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.core.database import engine as _engine
    from app.models.run import HitlReview

    timeout_s = float(_os.environ.get("HITL_TIMEOUT_S", "3600"))
    log.info("hitl cleanup started (timeout=%.0fs)", timeout_s)
    while True:
        await asyncio.sleep(300)
        try:
            from datetime import datetime, timezone
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_s)
            async with AsyncSession(_engine, expire_on_commit=False) as session:
                result = await session.exec(
                    select(HitlReview).where(
                        HitlReview.status == "pending",
                        HitlReview.created_at <= cutoff,
                    )
                )
                stale = result.all()
                for r in stale:
                    r.status = "timeout"
                    r.resolved_at = datetime.now(timezone.utc)
                    session.add(r)
                if stale:
                    await session.commit()
                    log.info("hitl cleanup: marked %d stale review(s) as timeout", len(stale))
        except Exception as exc:
            log.warning("hitl cleanup error: %s", exc)


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
            cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _webhook_task, _scheduler_task
    _setup_logging()
    cors = os.environ.get("CORS_ORIGINS", "*")
    if cors.strip() == "*":
        log.warning(
            "CORS policy is open (*) — set CORS_ORIGINS env var to restrict origins in production"
        )
    await init_db()
    start_listening()
    from app.core.slack_hitl import maybe_start_from_env as _slack_start
    _slack_start()
    from app.core.slack_hitl import set_main_loop as _set_loop
    _set_loop(asyncio.get_event_loop())
    from app.services.webhook import start_webhook_retry_loop
    _webhook_task = asyncio.create_task(start_webhook_retry_loop(), name="webhook-retry")
    _scheduler_task = asyncio.create_task(_eval_scheduler_loop(), name="eval-scheduler")
    _hitl_cleanup_task = asyncio.create_task(_hitl_cleanup_loop(), name="hitl-cleanup")
    _retention_task = asyncio.create_task(_data_retention_loop(), name="data-retention")
    yield
    stop_listening()
    for task in (_webhook_task, _scheduler_task, _hitl_cleanup_task, _retention_task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    from app.services.runner import shutdown as _runner_shutdown
    _runner_shutdown()
    from app.services.engine_runner import shutdown as _engine_shutdown
    _engine_shutdown()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="antcrew-platform",
    version=_VERSION,
    description="Dashboard and API layer for antcrew pipelines",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(pipeline.router)
app.include_router(runs.router)
app.include_router(tickets.router)
app.include_router(stream.router)
app.include_router(api_keys.router)
app.include_router(reviews.router)
app.include_router(templates.router)
app.include_router(workspaces.router)
app.include_router(evals.router)
app.include_router(eval_schedules.router)
app.include_router(engine.router)

app.mount("/static", StaticFiles(directory=_STATIC), name="static")


# ---------------------------------------------------------------------------
# Utility routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health(session=Depends(get_session)):
    """Liveness + readiness check. Returns 503 if the DB is unreachable."""
    try:
        from app.models.run import Run
        await session.exec(select(Run).limit(1))
        return {"status": "ok", "db": True, "version": _VERSION}
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "db": False, "version": _VERSION, "error": str(exc)},
        )


@app.get("/")
async def dashboard():
    return FileResponse(_STATIC / "index.html")


@app.get("/run/{run_id}")
async def run_detail(run_id: str):
    return FileResponse(_STATIC / "run.html")


@app.get("/tickets")
async def tickets_page():
    return FileResponse(_STATIC / "tickets.html")


@app.get("/reviews")
async def reviews_page():
    return FileResponse(_STATIC / "reviews.html")


@app.get("/evals")
async def evals_page():
    return FileResponse(_STATIC / "evals.html")


@app.get("/webhooks")
async def webhooks_page():
    return FileResponse(_STATIC / "webhooks.html")
