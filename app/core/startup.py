"""Startup health-check coroutines for antcrew-platform.

All ``_check_*`` functions are async guards that raise ``RuntimeError`` on
misconfiguration or log a warning when running locally.  They are composed into
``run_startup_checks`` which is called from the FastAPI lifespan.
"""
from __future__ import annotations

import logging
import os

from sqlmodel import select

from app.core.config import APP_ENV, VERSION
from app.models.run import Workspace

log = logging.getLogger(__name__)


async def _check_app_env() -> None:
    """Log the active environment prominently so it is unmistakable in startup logs."""
    log.info("antcrew-platform v%s  env=%s", VERSION, APP_ENV)


async def _check_database_url() -> None:
    """Block startup if SQLite is used in non-dev environments or on a public host.

    SQLite is single-writer and locks the whole file on writes — unsuitable for
    any concurrent traffic. Non-dev environments also carry a cross-environment
    contamination risk if they share a database instance.
    """
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url or "sqlite" not in db_url.lower():
        return

    if APP_ENV != "dev":
        raise RuntimeError(
            f"DATABASE_URL uses SQLite but APP_ENV={APP_ENV!r}. "
            "Non-dev environments require PostgreSQL to avoid single-writer lock "
            "and cross-environment data contamination. "
            "Set DATABASE_URL to a PostgreSQL connection string "
            f"(e.g. postgresql+asyncpg://user:pass@host/antcrew_{APP_ENV})."
        )

    host = os.environ.get("HOST", "127.0.0.1")
    is_public = host not in ("127.0.0.1", "localhost", "::1")
    if is_public:
        raise RuntimeError(
            f"DATABASE_URL={db_url!r} uses SQLite on public host {host!r}. "
            "SQLite is single-writer and will lock under concurrent traffic. "
            "Set DATABASE_URL to a PostgreSQL connection string."
        )
    log.debug("database: SQLite OK on localhost dev")


async def _check_sandbox_mode() -> None:
    """Block when engine runs would execute code outside Docker.

    ANTCREW_SANDBOX=required is the only safe value for any non-dev environment.
    'auto' on localhost is acceptable only in dev (Docker may be absent).
    int/uat/prod may run on restricted networks but still execute real code with
    real tokens — Docker isolation is non-negotiable regardless of host binding.
    """
    sandbox_mode = os.environ.get("ANTCREW_SANDBOX", "auto").lower()
    host = os.environ.get("HOST", "127.0.0.1")
    is_public = host not in ("127.0.0.1", "localhost", "::1")

    if sandbox_mode == "required":
        log.info("sandbox: ANTCREW_SANDBOX=required — Docker isolation enforced")
        return

    if APP_ENV != "dev":
        raise RuntimeError(
            f"ANTCREW_SANDBOX={sandbox_mode!r} in APP_ENV={APP_ENV!r}. "
            "All non-dev environments must enforce Docker isolation regardless of network "
            "exposure — int/uat run real code with real tokens on potentially shared infra. "
            "Set ANTCREW_SANDBOX=required."
        )

    if is_public:
        raise RuntimeError(
            f"ANTCREW_SANDBOX={sandbox_mode!r} on public host {host!r}. "
            "Engine runs will execute generated code and pip install post-install hooks "
            "directly on the host. Set ANTCREW_SANDBOX=required to enforce Docker isolation."
        )
    log.debug("sandbox: ANTCREW_SANDBOX=%r (localhost dev — Docker optional)", sandbox_mode)


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

    if is_public and os.environ.get("APP_ENV", "dev") == "prod":
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

    if is_public and os.environ.get("APP_ENV", "dev") == "prod":
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


async def _check_platform_api_key_prod() -> None:
    """Block prod startup if PLATFORM_API_KEY is set.

    PLATFORM_API_KEY is a dev/ops master key that bypasses all workspace scoping
    and grants unrestricted admin access to every API. It must never be deployed
    to production — use per-workspace API keys (POST /api-keys/) instead.
    """
    if os.environ.get("PLATFORM_API_KEY") and os.environ.get("APP_ENV") == "prod":
        raise RuntimeError(
            "PLATFORM_API_KEY is set in APP_ENV=prod. "
            "This master key bypasses all workspace scoping and grants unrestricted admin "
            "access without any DB audit trail. "
            "Remove it from your production environment and use per-workspace API keys "
            "(POST /api-keys/) or session-based auth instead."
        )


async def _check_stripe_key_mode() -> None:
    """Block startup if a Stripe test key is used in APP_ENV=prod.

    A test key (sk_test_…) in production means real customer charges silently fail —
    subscriptions are not created, invoices not paid, and the billing system looks
    healthy until a customer reports they were not charged.
    """
    stripe_key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not stripe_key or APP_ENV != "prod":
        return
    if stripe_key.startswith("sk_test_"):
        raise RuntimeError(
            "STRIPE_SECRET_KEY starts with 'sk_test_' but APP_ENV=prod. "
            "A Stripe test key in production silently drops real charges — "
            "subscriptions will not be created and customers will not be billed. "
            "Set STRIPE_SECRET_KEY to your live key (sk_live_…)."
        )
    log.info("billing: Stripe live key active (APP_ENV=prod)")


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

    if is_public and os.environ.get("APP_ENV", "dev") == "prod":
        raise RuntimeError(
            "Customer LLM keys are stored in plaintext but BYOK_ENCRYPTION_KEY is not set. "
            "API keys are high-value credentials. "
            "Generate an encryption key: python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\" and set it as BYOK_ENCRYPTION_KEY."
        )
    log.warning(
        "byok: customer LLM keys stored in plaintext — set BYOK_ENCRYPTION_KEY before production"
    )


async def _check_proxy_config() -> None:
    """Warn when proxy-mode workspaces exist but BYOK_ENCRYPTION_KEY is not set.

    Proxy tokens are encrypted with the same key as BYOK keys. If the key is
    absent, the tokens are stored in plaintext — same risk as BYOK without encryption.
    Only emits a warning; does not block startup (no proxy rows = no risk).
    """
    enc_key = os.environ.get("BYOK_ENCRYPTION_KEY")
    if enc_key:
        return  # already verified by _check_byok_config

    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.core.database import engine as _engine

    try:
        async with AsyncSession(_engine, expire_on_commit=False) as session:
            any_proxy = (await session.exec(
                select(Workspace).where(Workspace.llm_key_mode == "proxy").limit(1)
            )).first()
    except Exception:
        return

    if any_proxy:
        host = os.environ.get("HOST", "127.0.0.1")
        is_public = host not in ("127.0.0.1", "localhost", "::1")
        if is_public and os.environ.get("APP_ENV", "dev") == "prod":
            raise RuntimeError(
                "Proxy-mode workspaces exist but BYOK_ENCRYPTION_KEY is not set. "
                "Proxy tokens would be stored in plaintext — set BYOK_ENCRYPTION_KEY "
                "to encrypt them at rest."
            )
        log.warning("proxy: proxy tokens stored in plaintext — set BYOK_ENCRYPTION_KEY before production")


async def _check_mor_config() -> None:
    """Warn or block when Lemon Squeezy webhooks are accepted without signature verification.

    Accepting MoR webhooks without verifying the HMAC-SHA256 X-Signature header lets
    anyone forge subscription events (mark unpaid subscriptions as active, cancel rivals).
    In production this is a hard error; locally it is a warning.
    """
    webhook_secret = os.environ.get("LEMON_SQUEEZY_WEBHOOK_SECRET")
    if webhook_secret:
        log.info("mor: Lemon Squeezy webhook secret set — signature verification active")
        return

    host = os.environ.get("HOST", "127.0.0.1")
    is_public = host not in ("127.0.0.1", "localhost", "::1")

    if is_public and os.environ.get("APP_ENV", "dev") == "prod":
        raise RuntimeError(
            "LEMON_SQUEEZY_WEBHOOK_SECRET is not set. "
            "Starting in production without webhook signature verification would allow "
            "anyone to forge subscription events (activate cancelled plans, block active ones). "
            "Set LEMON_SQUEEZY_WEBHOOK_SECRET from your Lemon Squeezy webhook settings."
        )
    log.warning(
        "mor: LEMON_SQUEEZY_WEBHOOK_SECRET not set — webhook signatures will not be verified "
        "(dev mode only, not suitable for production)"
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
    if is_public and os.environ.get("APP_ENV", "dev") == "prod":
        raise RuntimeError(
            "CORS_ORIGINS=* is not allowed on a public host. "
            "Set CORS_ORIGINS to a comma-separated list of allowed origins "
            "(e.g. https://app.yourdomain.com) or unset it to allow localhost only."
        )
    log.warning(
        "CORS: allow_origins=* — localhost only, set CORS_ORIGINS for production"
    )


async def _check_rate_limit_workers() -> None:
    """Warn when multiple workers are configured alongside the in-memory rate limiter.

    The rate limiter uses a per-process sliding window — each worker process has
    its own independent bucket, so the effective limit is RATE_LIMIT_RPM × workers.
    This is only a warning (not a hard error) because single-process deployments
    (Fly.io with min_machines=1) are the current production topology; replace the
    limiter with a Redis-backed implementation before enabling horizontal scaling.
    """
    rpm = int(os.environ.get("RATE_LIMIT_RPM", "60"))
    if rpm <= 0:
        return
    workers = int(os.environ.get("ANTCREW_WORKERS", "1"))
    if workers > 1:
        log.warning(
            "rate_limit: ANTCREW_WORKERS=%d but rate limiter is in-memory — "
            "effective limit is %d RPM per process (%d × %d). "
            "Replace with a Redis-backed implementation before horizontal scaling.",
            workers, rpm, rpm, workers,
        )


async def run_startup_checks(testing: bool) -> None:
    """Run all startup health checks in order.

    ``_check_app_env`` is always called (even during testing) to log the version.
    All other checks are skipped when ``testing=True``.
    """
    await _check_app_env()
    if not testing:
        await _check_database_url()
        await _check_auth_mode()
        await _check_cors_config()
        await _check_sandbox_mode()
        await _check_platform_api_key_prod()
        await _check_stripe_config()
        await _check_stripe_key_mode()
        await _check_mor_config()
        await _check_slack_config()
        await _check_byok_config()
        await _check_proxy_config()
        await _check_rate_limit_workers()
