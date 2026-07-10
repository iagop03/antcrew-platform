"""Shared test fixtures for antcrew-platform."""
from __future__ import annotations

import os

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.main import app
from app.core.database import get_session


@pytest.fixture(autouse=True)
def reset_rate_limits():
    """Clear rate-limit sliding windows between tests.

    Without this, the in-memory counter accumulates across the entire test suite
    and starts returning 429 after ~60 requests (the default RATE_LIMIT_RPM).
    """
    from app.core import rate_limit
    rate_limit.reset()
    yield
    rate_limit.reset()

# Override with TEST_DB_URL env var to run against PostgreSQL in CI
TEST_DB_URL = os.environ.get("TEST_DB_URL", "sqlite+aiosqlite:///:memory:")


@pytest.fixture(scope="function")
async def session():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as sess:
        yield sess
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def client(session):
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
