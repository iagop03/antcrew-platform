"""Database setup using SQLModel + aiosqlite (SQLite) or asyncpg (PostgreSQL).

Migration strategy:
  SQLite  (dev / test) — create_all on fresh DB + inline _migrate_* helpers
  PostgreSQL (production) — alembic upgrade head via subprocess in executor
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

log = logging.getLogger(__name__)

DB_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./platform.db")

engine = create_async_engine(DB_URL, echo=False)

_PROJECT_ROOT = Path(__file__).parent.parent.parent  # app/core/database.py → project root


async def _run_alembic_upgrade() -> None:
    """Run `alembic upgrade head` for the current DATABASE_URL.

    Runs in a thread-pool executor so the async event loop is not blocked and
    there is no nested-asyncio issue (alembic's env.py calls asyncio.run()
    internally, which requires a fresh event loop in a separate thread).
    """
    env = {**os.environ, "DATABASE_URL": DB_URL}
    loop = asyncio.get_running_loop()
    result: subprocess.CompletedProcess = await loop.run_in_executor(
        None,
        lambda: subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            env=env,
        ),
    )
    if result.stdout.strip():
        log.info("alembic: %s", result.stdout.strip())
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade head failed (exit {result.returncode}):\n{result.stderr}"
        )


async def _migrate_webhook_events(eng) -> None:
    """One-time idempotent migration: move WebhookConfig.events JSON → webhook_event rows.

    The old 'events' column (JSON string) is no longer in the SQLModel definition
    but may still exist in existing SQLite databases. This reads it via raw SQL
    and populates the webhook_event table. Safe to run on fresh DBs (no-op).
    """
    try:
        async with eng.begin() as conn:
            rows = (await conn.execute(
                text(
                    "SELECT id, events FROM webhook_config "
                    "WHERE events IS NOT NULL AND events != '' AND events != '[]'"
                )
            )).fetchall()
    except Exception:
        return  # events column absent (fresh DB) or table doesn't exist yet

    if not rows:
        return

    async with eng.begin() as conn:
        for webhook_id, events_json in rows:
            try:
                events_list = json.loads(events_json)
            except Exception:
                continue
            for event_type in (e for e in events_list if isinstance(e, str)):
                existing = (await conn.execute(
                    text(
                        "SELECT id FROM webhook_event "
                        "WHERE webhook_id = :wid AND event_type = :et"
                    ),
                    {"wid": webhook_id, "et": event_type},
                )).first()
                if not existing:
                    await conn.execute(
                        text(
                            "INSERT INTO webhook_event (webhook_id, event_type) "
                            "VALUES (:wid, :et)"
                        ),
                        {"wid": webhook_id, "et": event_type},
                    )


async def _migrate_drop_budget_exceeded(eng) -> None:
    """Idempotent migration: drop workspace.budget_exceeded column if present."""
    try:
        async with eng.begin() as conn:
            cols = (await conn.execute(text("PRAGMA table_info(workspace)"))).fetchall()
            col_names = [row[1] for row in cols]
            if "budget_exceeded" in col_names:
                await conn.execute(text("ALTER TABLE workspace DROP COLUMN budget_exceeded"))
    except Exception:
        pass  # PostgreSQL or table absent — skip


async def _migrate_eval_run_id(eng) -> None:
    """Idempotent migration: add eval_run.run_id column if absent."""
    try:
        async with eng.begin() as conn:
            cols = (await conn.execute(text("PRAGMA table_info(eval_run)"))).fetchall()
            col_names = [row[1] for row in cols]
            if "run_id" not in col_names:
                await conn.execute(text("ALTER TABLE eval_run ADD COLUMN run_id TEXT"))
    except Exception:
        pass  # PostgreSQL or table absent — skip


async def _migrate_workspace_membership(eng) -> None:
    """Idempotent migration: create workspace_membership table if absent."""
    try:
        async with eng.begin() as conn:
            tables = (await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='workspace_membership'")
            )).fetchall()
            if not tables:
                await conn.execute(text(
                    "CREATE TABLE workspace_membership ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "api_key_id INTEGER NOT NULL, "
                    "workspace_id INTEGER NOT NULL, "
                    "created_at DATETIME"
                    ")"
                ))
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_workspace_membership_api_key_id "
                    "ON workspace_membership(api_key_id)"
                ))
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_workspace_membership_workspace_id "
                    "ON workspace_membership(workspace_id)"
                ))
    except Exception:
        pass  # PostgreSQL handled by Alembic or create_all


async def _migrate_stripe_fields(eng) -> None:
    """Idempotent migration: add/rename billing columns on workspace if absent."""
    try:
        async with eng.begin() as conn:
            cols = (await conn.execute(text("PRAGMA table_info(workspace)"))).fetchall()
            col_names = {row[1] for row in cols}

            # Stripe-specific fields (named; kept as-is)
            for col_def, col_name in [
                ("stripe_customer_id TEXT", "stripe_customer_id"),
                ("stripe_subscription_id TEXT", "stripe_subscription_id"),
            ]:
                if col_name not in col_names:
                    await conn.execute(text(f"ALTER TABLE workspace ADD COLUMN {col_def}"))

            if "stripe_customer_id" not in col_names:
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_workspace_stripe_customer_id "
                    "ON workspace(stripe_customer_id)"
                ))

            # Rename legacy stripe_subscription_status → subscription_status
            if "stripe_subscription_status" in col_names and "subscription_status" not in col_names:
                await conn.execute(text(
                    "ALTER TABLE workspace RENAME COLUMN "
                    "stripe_subscription_status TO subscription_status"
                ))
                col_names.discard("stripe_subscription_status")
                col_names.add("subscription_status")

            # Provider-neutral and MoR lane fields
            for col_def, col_name in [
                ("subscription_status TEXT", "subscription_status"),
                ("billing_provider TEXT NOT NULL DEFAULT 'mor'", "billing_provider"),
                ("mor_customer_id TEXT", "mor_customer_id"),
                ("mor_subscription_id TEXT", "mor_subscription_id"),
            ]:
                if col_name not in col_names:
                    await conn.execute(text(f"ALTER TABLE workspace ADD COLUMN {col_def}"))
    except Exception:
        pass  # PostgreSQL or table absent — skip


async def _migrate_workspace_is_trial(eng) -> None:
    """Idempotent migration: add is_trial column to workspace if absent."""
    try:
        async with eng.begin() as conn:
            cols = (await conn.execute(text("PRAGMA table_info(workspace)"))).fetchall()
            col_names = {row[1] for row in cols}
            if "is_trial" not in col_names:
                # Default 0 (False) for existing workspaces — only new ones start in trial.
                await conn.execute(text(
                    "ALTER TABLE workspace ADD COLUMN is_trial BOOLEAN NOT NULL DEFAULT 0"
                ))
    except Exception:
        pass  # PostgreSQL or table absent — skip


async def _migrate_llm_base_url(eng) -> None:
    """Idempotent migration: add base_url column to llm_provider_key if absent."""
    try:
        async with eng.begin() as conn:
            cols = (await conn.execute(text("PRAGMA table_info(llm_provider_key)"))).fetchall()
            col_names = {row[1] for row in cols}
            if "base_url" not in col_names:
                await conn.execute(text("ALTER TABLE llm_provider_key ADD COLUMN base_url TEXT"))
    except Exception:
        pass  # PostgreSQL or table absent — skip


async def init_db() -> None:
    if "postgresql" in DB_URL:
        await _run_alembic_upgrade()
        return
    # SQLite: create_all for fresh DBs + inline helpers for existing ones
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    await _migrate_webhook_events(engine)
    await _migrate_drop_budget_exceeded(engine)
    await _migrate_eval_run_id(engine)
    await _migrate_workspace_membership(engine)
    await _migrate_stripe_fields(engine)
    await _migrate_workspace_is_trial(engine)
    await _migrate_llm_base_url(engine)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
