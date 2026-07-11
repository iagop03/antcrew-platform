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
from app.api import eval_schedules, engine, billing

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


async def _check_database_url() -> None:
    """Block startup if SQLite is used on a public-facing host.

    SQLite is single-writer and locks the whole file on writes — under any real
    concurrent load it will cause 500s.  On a public host this is always wrong.
    """
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url or "sqlite" not in db_url.lower():
        return
    host = os.environ.get("HOST", "127.0.0.1")
    is_public = host not in ("127.0.0.1", "localhost", "::1")
    if is_public:
        raise RuntimeError(
            f"DATABASE_URL={db_url!r} uses SQLite on public host {host!r}. "
            "SQLite is single-writer and will lock under concurrent traffic. "
            "Set DATABASE_URL to a PostgreSQL connection string for production deployments."
        )
    log.debug("database: SQLite OK on localhost")


async def _check_sandbox_mode() -> None:
    """Block or loudly warn when engine runs would execute code outside Docker on a public host.

    The safe production value is ANTCREW_SANDBOX=required.  'auto' is acceptable
    on localhost (Docker may be absent) but on any public interface it means generated
    code and pip install post-install hooks execute unsandboxed on the host.
    """
    sandbox_mode = os.environ.get("ANTCREW_SANDBOX", "auto").lower()
    host = os.environ.get("HOST", "127.0.0.1")
    is_public = host not in ("127.0.0.1", "localhost", "::1")

    if sandbox_mode == "required":
        log.info("sandbox: ANTCREW_SANDBOX=required — Docker isolation enforced")
        return

    if is_public and sandbox_mode != "required":
        raise RuntimeError(
            f"ANTCREW_SANDBOX={sandbox_mode!r} on public host {host!r}. "
            "Engine runs will execute generated code and pip install post-install hooks "
            "directly on the host. Set ANTCREW_SANDBOX=required to enforce Docker isolation, "
            "or set HOST to a loopback address for local-only deployments."
        )
    log.debug("sandbox: ANTCREW_SANDBOX=%r (localhost — Docker optional)", sandbox_mode)


async def _check_stripe_config() -> None:
    """Block startup when Stripe is configured without a webhook secret in production.

    Accepting Stripe webhooks without signature verification lets anyone forge
    subscription events (cancel a rival's subscription, falsely mark invoices paid).
    In production this is a hard error; locally it's a warning.
    """
    stripe_key     = os.environ.get("STRIPE_SECRET_KEY")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not stripe_key:
        return  # Stripe not configured — billing is a no-op, nothing to enforce
    if webhook_secret:
        log.info("billing: Stripe configured with webhook secret — signature verification active")
        return

    host      = os.environ.get("HOST", "127.0.0.1")
    is_public = host not in ("127.0.0.1", "localhost", "::1")

    if is_public:
        raise RuntimeError(
            "STRIPE_SECRET_KEY is set but STRIPE_WEBHOOK_SECRET is missing. "
            "Starting in production without webhook signature verification would allow "
            "anyone to forge subscription events. "
            "Set STRIPE_WEBHOOK_SECRET (from your Stripe webhook dashboard) or "
            "unset STRIPE_SECRET_KEY if billing is not yet active."
        )
    log.warning(
        "billing: STRIPE_SECRET_KEY set but STRIPE_WEBHOOK_SECRET missing — "
        "webhook events will be rejected (403). Set STRIPE_WEBHOOK_SECRET for local testing."
    )


async def _check_slack_config() -> None:
    """Block startup when Slack is configured without token encryption on a public host.

    A Slack bot token (xoxb-…) stored in plaintext in the DB is a high-value
    credential — it allows posting to channels and reading message history.
    On a public-facing host, require SLACK_TOKEN_ENCRYPTION_KEY to be set so
    tokens are Fernet-encrypted at rest. Locally, warn only.
    """
    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    if not bot_token:
        return  # Slack not configured — nothing to enforce
    enc_key = os.environ.get("SLACK_TOKEN_ENCRYPTION_KEY")
    if enc_key:
        log.info("slack: token encryption active (SLACK_TOKEN_ENCRYPTION_KEY set)")
        return

    host = os.environ.get("HOST", "127.0.0.1")
    is_public = host not in ("127.0.0.1", "localhost", "::1")

    if is_public:
        raise RuntimeError(
            "SLACK_BOT_TOKEN is set but SLACK_TOKEN_ENCRYPTION_KEY is missing. "
            "The Slack bot token would be stored in plaintext in the database, "
            "exposing a credential that allows posting to and reading from your Slack workspace. "
            "Generate a key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\" "
            "and set it as SLACK_TOKEN_ENCRYPTION_KEY, or unset SLACK_BOT_TOKEN if Slack is not yet active."
        )
    log.warning(
        "slack: SLACK_BOT_TOKEN set but SLACK_TOKEN_ENCRYPTION_KEY missing — "
        "bot token stored in plaintext (dev mode only, not suitable for production)"
    )


async def _check_auth_mode() -> None:
    """Warn or block when the platform starts in open (unauthenticated) mode.

    Open mode is intentional for local dev but dangerous if exposed publicly.
    Set ANTCREW_REQUIRE_AUTH=true to block startup when no credentials are configured.
    """
    env_key = os.environ.get("PLATFORM_API_KEY")
    require_auth = os.environ.get("ANTCREW_REQUIRE_AUTH", "").lower() in ("1", "true", "yes")

    if env_key:
        log.info("auth: single-key mode (PLATFORM_API_KEY set)")
        return

    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.core.database import engine as _engine
    from app.models.run import ApiKey

    try:
        async with AsyncSession(_engine, expire_on_commit=False) as session:
            any_key = (await session.exec(
                select(ApiKey).where(ApiKey.revoked_at == None).limit(1)  # noqa: E711
            )).first()
        if any_key is not None:
            log.info("auth: multi-key mode (%d+ API keys in DB)", 1)
            return
    except Exception as exc:
        log.warning("auth: could not query ApiKey table (%s) — defaulting to open mode", exc)

    # No credentials configured — open mode
    if require_auth:
        raise RuntimeError(
            "ANTCREW_REQUIRE_AUTH=true but no API keys exist and PLATFORM_API_KEY is not set. "
            "Create at least one API key via POST /api-keys/ or set PLATFORM_API_KEY, "
            "then restart. Unset ANTCREW_REQUIRE_AUTH to allow open mode for local dev."
        )

    host = os.environ.get("HOST", "127.0.0.1")
    is_public = host not in ("127.0.0.1", "localhost", "::1")

    border = "=" * 72
    msg = (
        f"\n{border}\n"
        "  ANTCREW-PLATFORM STARTING IN OPEN (UNAUTHENTICATED) MODE\n"
        "     All API endpoints are accessible without any credentials.\n"
        "\n"
        "  To enable authentication:\n"
        "    Option A — set PLATFORM_API_KEY env var (single key)\n"
        "    Option B — POST /api-keys to create scoped keys in the DB\n"
        "  To block startup when no credentials exist: ANTCREW_REQUIRE_AUTH=true\n"
    )
    if is_public:
        msg += (
            f"\n  HOST={host!r} — this server is reachable beyond localhost.\n"
            "     Running without auth on a public interface is a security risk.\n"
        )
    msg += f"{border}\n"

    if is_public:
        log.error("auth: OPEN MODE on public host %r — no credentials required", host)
    else:
        log.warning("auth: open mode (no PLATFORM_API_KEY, no DB keys) — local dev only")

    print(msg, flush=True)


async def _hitl_cleanup_loop() -> None:
    """Mark stale pending reviews as 'timeout' every 5 minutes."""
    import os as _os
    from datetime import timedelta
    from sqlmodel import select
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


async def _check_byok_config() -> None:
    """Warn or block when customer LLM keys are stored without encryption.

    If any LLMProviderKey rows exist but BYOK_ENCRYPTION_KEY is not set on a
    public host, those keys are in plaintext — block startup.
    """
    enc_key = os.environ.get("BYOK_ENCRYPTION_KEY")
    if enc_key:
        log.info("byok: key encryption active (BYOK_ENCRYPTION_KEY set)")
        return

    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.core.database import engine as _engine
    from app.models.run import LLMProviderKey

    try:
        async with AsyncSession(_engine, expire_on_commit=False) as session:
            any_key = (await session.exec(select(LLMProviderKey).limit(1))).first()
    except Exception:
        return  # Table not yet created (pre-migration) — safe to proceed

    if not any_key:
        return  # No BYOK keys stored yet

    host = os.environ.get("HOST", "127.0.0.1")
    is_public = host not in ("127.0.0.1", "localhost", "::1")

    if is_public:
        raise RuntimeError(
            "Customer LLM keys are stored in plaintext but BYOK_ENCRYPTION_KEY is not set. "
            "API keys are high-value credentials. "
            "Generate an encryption key: python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\" and set it as BYOK_ENCRYPTION_KEY."
        )
    log.warning(
        "byok: customer LLM keys stored in plaintext — set BYOK_ENCRYPTION_KEY before production"
    )


async def _check_cors_config() -> None:
    """Block startup when CORS_ORIGINS=* is used on a public-facing host.

    The safe default (no CORS_ORIGINS set) restricts cross-origin access to
    localhost only.  Explicitly setting CORS_ORIGINS=* in production is a
    hard error — it lets any website make credentialed requests to the API.
    """
    cors = os.environ.get("CORS_ORIGINS", "").strip()
    if cors != "*":
        if not cors:
            log.debug("CORS: no CORS_ORIGINS set — defaulting to localhost only")
        else:
            log.info("CORS: origins=%r", cors)
        return

    host = os.environ.get("HOST", "127.0.0.1")
    is_public = host not in ("127.0.0.1", "localhost", "::1")
    if is_public:
        raise RuntimeError(
            "CORS_ORIGINS=* is not allowed on a public host. "
            "Set CORS_ORIGINS to a comma-separated list of allowed origins "
            "(e.g. https://app.yourdomain.com) or unset it to allow localhost only."
        )
    log.warning(
        "CORS: allow_origins=* — localhost only, set CORS_ORIGINS for production"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _webhook_task, _scheduler_task
    _setup_logging()
    await init_db()
    await _check_database_url()
    await _check_auth_mode()
    await _check_cors_config()
    await _check_sandbox_mode()
    await _check_stripe_config()
    await _check_slack_config()
    await _check_byok_config()
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

_cors_origins_raw = os.environ.get("CORS_ORIGINS", "").strip()
_cors_origins = (
    _cors_origins_raw.split(",")
    if _cors_origins_raw
    else ["http://localhost:3000", "http://localhost:8000",
          "http://127.0.0.1:3000", "http://127.0.0.1:8000"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
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
app.include_router(billing.router)

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


@app.get("/onboard")
async def onboard_page():
    return FileResponse(_STATIC / "onboard.html")
