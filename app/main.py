"""antcrew-platform FastAPI application."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import select
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import APP_ENV, VERSION
from app.core.logging import _setup_logging
from app.core.startup import run_startup_checks
from app.core.background import (
    _hitl_cleanup_loop,
    _data_retention_loop,
    _eval_scheduler_loop,
    _run_scheduler_loop,
)
from app.core.database import init_db, get_session
from app.core.listener import start_listening, stop_listening
from app.core.csrf import require_csrf as _require_csrf
from app.api import auth_session as auth_session_api
from app.api import (
    runs, tickets, stream, pipeline, api_keys, reviews, templates,
    workspaces, workspaces_byok, workspaces_members, evals,
)
from app.api import eval_schedules, engine, billing, webhook_mor, pipelines as pipelines_api
from app.api import client_review, compare as compare_api, contract_schemas as contract_schemas_api
from app.api import security_audit as security_audit_api
from app.api import invites as invites_api
from app.api import workspaces_proxy as workspaces_proxy_api
from app.api import run_schedules as run_schedules_api
from app.api import pages as pages_api
from app.api import bootstrap as bootstrap_api
from app.api import admin as admin_api
from app.api import feedback as feedback_api

# Re-export for backward compatibility — tests import these names from app.main
from app.core.startup import _check_auth_mode, _check_slack_config  # noqa: F401
from app.core.background import _do_retention  # noqa: F401

_STATIC = Path(__file__).parent / "static"
_TESTING = os.environ.get("ANTCREW_TESTING") == "1"

log = logging.getLogger(__name__)

_webhook_task: Optional[asyncio.Task] = None
_scheduler_task: Optional[asyncio.Task] = None
_hitl_cleanup_task: Optional[asyncio.Task] = None
_retention_task: Optional[asyncio.Task] = None

_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "font-src 'self' https://cdn.jsdelivr.net data:; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "base-uri 'self'"
)


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject standard security headers on every response."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Content-Security-Policy"] = _CSP
        if APP_ENV not in ("dev", "int"):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _webhook_task, _scheduler_task, _hitl_cleanup_task, _retention_task
    _setup_logging()
    if not _TESTING:
        await init_db()
    await run_startup_checks(_TESTING)
    if not _TESTING:
        start_listening()
    _security_scheduler_task: Optional[asyncio.Task] = None
    if not _TESTING:
        from app.core.slack_hitl import maybe_start_from_env as _slack_start
        _slack_start()
        from app.core.slack_hitl import set_main_loop as _set_loop
        _set_loop(asyncio.get_event_loop())
        from app.services.webhook import start_webhook_retry_loop
        _webhook_task = asyncio.create_task(start_webhook_retry_loop(), name="webhook-retry")
        _scheduler_task = asyncio.create_task(_eval_scheduler_loop(), name="eval-scheduler")
        _hitl_cleanup_task = asyncio.create_task(_hitl_cleanup_loop(), name="hitl-cleanup")
        _retention_task = asyncio.create_task(_data_retention_loop(), name="data-retention")
        _security_scheduler_task = asyncio.create_task(
            security_audit_api.run_schedule_loop(), name="security-audit-scheduler"
        )
        asyncio.create_task(_run_scheduler_loop(), name="run-scheduler")
    yield
    if not _TESTING:
        stop_listening()
    for task in (_webhook_task, _scheduler_task, _hitl_cleanup_task, _retention_task,
                 _security_scheduler_task):
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
    version=VERSION,
    description="Dashboard and API layer for antcrew pipelines",
    lifespan=lifespan,
    docs_url="/docs" if APP_ENV == "dev" else None,
    redoc_url="/redoc" if APP_ENV == "dev" else None,
    openapi_url="/openapi.json" if APP_ENV == "dev" else None,
)

_cors_origins_raw = os.environ.get("CORS_ORIGINS", "").strip()
_cors_origins = (
    _cors_origins_raw.split(",")
    if _cors_origins_raw
    else ["http://localhost:3000", "http://localhost:8000",
          "http://127.0.0.1:3000", "http://127.0.0.1:8000"]
)

app.add_middleware(_SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Api-Key", "X-CSRF-Token"],
)

_csrf = [Depends(_require_csrf)]

app.include_router(auth_session_api.router)
app.include_router(pipeline.router,             dependencies=_csrf)
app.include_router(runs.router,                 dependencies=_csrf)
app.include_router(tickets.router,              dependencies=_csrf)
app.include_router(stream.router)                               # SSE/WebSocket — GET only, no mutations
app.include_router(api_keys.router,             dependencies=_csrf)
app.include_router(reviews.router,              dependencies=_csrf)
app.include_router(templates.router,            dependencies=_csrf)
app.include_router(workspaces.router,           dependencies=_csrf)
app.include_router(workspaces_byok.router,      dependencies=_csrf)
app.include_router(workspaces_proxy_api.router, dependencies=_csrf)
app.include_router(workspaces_members.router,   dependencies=_csrf)
app.include_router(evals.router,                dependencies=_csrf)
app.include_router(eval_schedules.router,       dependencies=_csrf)
app.include_router(engine.router,               dependencies=_csrf)
app.include_router(billing.router,              dependencies=_csrf)
app.include_router(webhook_mor.router)                          # server-to-server, HMAC-signed body
app.include_router(pipelines_api.router,        dependencies=_csrf)
app.include_router(client_review.router,        dependencies=_csrf)
app.include_router(compare_api.router,          dependencies=_csrf)
app.include_router(contract_schemas_api.router, dependencies=_csrf)
app.include_router(security_audit_api.router,          dependencies=_csrf)
app.include_router(security_audit_api.webhook_router)           # HMAC-signed, no CSRF
app.include_router(invites_api.router,                 dependencies=_csrf)
app.include_router(run_schedules_api.router,           dependencies=_csrf)
app.include_router(pages_api.router)
app.include_router(bootstrap_api.router)
app.include_router(admin_api.router,  dependencies=_csrf)
app.include_router(feedback_api.router, dependencies=_csrf)

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
        return {"status": "ok", "db": True, "version": VERSION}
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "db": False, "version": VERSION, "error": str(exc)},
        )
