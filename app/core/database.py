"""SQLite database setup using SQLModel + aiosqlite."""
from __future__ import annotations

import json
import os
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

DB_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./platform.db")

engine = create_async_engine(DB_URL, echo=False)


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
    """Idempotent migration: add Stripe billing columns to workspace if absent."""
    try:
        async with eng.begin() as conn:
            cols = (await conn.execute(text("PRAGMA table_info(workspace)"))).fetchall()
            col_names = {row[1] for row in cols}
            for col_def, col_name in [
                ("stripe_customer_id TEXT", "stripe_customer_id"),
                ("stripe_subscription_id TEXT", "stripe_subscription_id"),
                ("stripe_subscription_status TEXT", "stripe_subscription_status"),
            ]:
                if col_name not in col_names:
                    await conn.execute(
                        text(f"ALTER TABLE workspace ADD COLUMN {col_def}")
                    )
            if "stripe_customer_id" not in col_names:
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_workspace_stripe_customer_id "
                    "ON workspace(stripe_customer_id)"
                ))
    except Exception:
        pass  # PostgreSQL or table absent — skip


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    await _migrate_webhook_events(engine)
    await _migrate_drop_budget_exceeded(engine)
    await _migrate_eval_run_id(engine)
    await _migrate_workspace_membership(engine)
    await _migrate_stripe_fields(engine)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
