"""Round 11 P1 integration tests.

Covers:
- workspace_spend uses cached total_cost_usd (not O(n) run scan)
- workspace_spend run_count is correct
- _do_retention deletes terminal WebhookDelivery + old Events
- _do_retention keeps pending/retrying deliveries and recent events
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.run import Workspace, Run, WebhookDelivery, Event as DBEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_workspace(session, *, name, slug, max_cost_usd=None, total_cost_usd=0.0):
    ws = Workspace(name=name, slug=slug, max_cost_usd=max_cost_usd, total_cost_usd=total_cost_usd)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


async def _make_retention_engine():
    """Isolated in-memory engine for retention tests."""
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    return eng


# ---------------------------------------------------------------------------
# 1. workspace_spend uses total_cost_usd cache
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spend_uses_cached_total(client: AsyncClient, session):
    """GET /workspaces/{id}/spend returns total_cost_usd from the workspace row,
    not the sum of run.cost_usd — even when they differ."""
    ws = await _make_workspace(
        session, name="Spend WS", slug="spend-ws-r11",
        max_cost_usd=10.0, total_cost_usd=7.5,
    )
    # Add runs that sum to 3.0 — different from the cached 7.5
    for i, cost in enumerate([1.0, 1.0, 1.0]):
        run = Run(
            run_id=f"r11-spend-{i}",
            team="T",
            request="x",
            status="success",
            cost_usd=cost,
            workspace_id=ws.id,
        )
        session.add(run)
    await session.commit()

    r = await client.get(f"/workspaces/{ws.id}/spend")
    assert r.status_code == 200
    data = r.json()
    assert data["total_spend_usd"] == pytest.approx(7.5)  # cached value, not 3.0
    assert data["run_count"] == 3
    assert data["budget_usd"] == pytest.approx(10.0)
    assert data["remaining_usd"] == pytest.approx(2.5)
    assert data["exhausted"] is False


@pytest.mark.asyncio
async def test_spend_exhausted_when_cache_exceeds_budget(client: AsyncClient, session):
    """exhausted flag uses cached total, not a fresh sum."""
    ws = await _make_workspace(
        session, name="Over WS", slug="over-ws-r11",
        max_cost_usd=5.0, total_cost_usd=6.0,
    )
    r = await client.get(f"/workspaces/{ws.id}/spend")
    assert r.status_code == 200
    data = r.json()
    assert data["total_spend_usd"] == pytest.approx(6.0)
    assert data["exhausted"] is True
    assert data["remaining_usd"] == pytest.approx(-1.0)


@pytest.mark.asyncio
async def test_spend_no_budget_limit(client: AsyncClient, session):
    """When max_cost_usd is None, remaining_usd is None and exhausted is False."""
    ws = await _make_workspace(
        session, name="Unlimited WS", slug="unlimited-ws-r11",
        max_cost_usd=None, total_cost_usd=999.0,
    )
    r = await client.get(f"/workspaces/{ws.id}/spend")
    assert r.status_code == 200
    data = r.json()
    assert data["budget_usd"] is None
    assert data["remaining_usd"] is None
    assert data["exhausted"] is False
    assert data["total_spend_usd"] == pytest.approx(999.0)


@pytest.mark.asyncio
async def test_spend_zero_runs(client: AsyncClient, session):
    """Workspace with no runs returns run_count=0 and total from cache."""
    ws = await _make_workspace(
        session, name="Empty WS", slug="empty-ws-r11",
        max_cost_usd=10.0, total_cost_usd=0.0,
    )
    r = await client.get(f"/workspaces/{ws.id}/spend")
    assert r.status_code == 200
    data = r.json()
    assert data["run_count"] == 0
    assert data["total_spend_usd"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_spend_not_found(client: AsyncClient, session):
    r = await client.get("/workspaces/99999/spend")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 2. _do_retention deletes terminal deliveries and old events
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retention_deletes_delivered_and_failed():
    """_do_retention removes delivered and failed deliveries older than cutoff."""
    from app.main import _do_retention

    eng = await _make_retention_engine()
    old_ts = datetime.now(timezone.utc) - timedelta(days=60)
    recent_ts = datetime.now(timezone.utc) - timedelta(hours=1)
    future_cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    try:
        async with AsyncSession(eng, expire_on_commit=False) as session:
            # Old terminal deliveries — should be deleted
            session.add(WebhookDelivery(
                run_id="r11-ret-1", url="https://a.com", payload_json="{}",
                status="delivered", created_at=old_ts,
            ))
            session.add(WebhookDelivery(
                run_id="r11-ret-2", url="https://b.com", payload_json="{}",
                status="failed", created_at=old_ts,
            ))
            # Recent terminal delivery — should NOT be deleted (too new)
            session.add(WebhookDelivery(
                run_id="r11-ret-3", url="https://c.com", payload_json="{}",
                status="delivered", created_at=recent_ts,
            ))
            # Old pending delivery — should NOT be deleted (active state)
            session.add(WebhookDelivery(
                run_id="r11-ret-4", url="https://d.com", payload_json="{}",
                status="pending", created_at=old_ts,
            ))
            await session.commit()

        deleted_d, deleted_e = await _do_retention(eng, future_cutoff)
        assert deleted_d == 2
        assert deleted_e == 0

        async with AsyncSession(eng, expire_on_commit=False) as session:
            remaining = (await session.exec(select(WebhookDelivery))).all()
            statuses = {r.status for r in remaining}
            assert len(remaining) == 2  # recent delivered + old pending
            assert "pending" in statuses
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_retention_deletes_old_events():
    """_do_retention removes Event rows older than cutoff."""
    from app.main import _do_retention

    eng = await _make_retention_engine()
    old_ts = datetime.now(timezone.utc) - timedelta(days=45)
    recent_ts = datetime.now(timezone.utc) - timedelta(hours=2)
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    try:
        async with AsyncSession(eng, expire_on_commit=False) as session:
            session.add(DBEvent(
                event_type="pipeline.end", payload={}, timestamp=0.0,
                recorded_at=old_ts,
            ))
            session.add(DBEvent(
                event_type="pipeline.start", payload={}, timestamp=1.0,
                recorded_at=recent_ts,
            ))
            await session.commit()

        deleted_d, deleted_e = await _do_retention(eng, cutoff)
        assert deleted_e == 1
        assert deleted_d == 0

        async with AsyncSession(eng, expire_on_commit=False) as session:
            events = (await session.exec(select(DBEvent))).all()
            assert len(events) == 1
            assert events[0].event_type == "pipeline.start"
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_retention_nothing_to_delete():
    """_do_retention is a no-op when no rows are old enough."""
    from app.main import _do_retention

    eng = await _make_retention_engine()
    recent_ts = datetime.now(timezone.utc) - timedelta(hours=1)
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    try:
        async with AsyncSession(eng, expire_on_commit=False) as session:
            session.add(WebhookDelivery(
                run_id="r11-keep-1", url="https://keep.com", payload_json="{}",
                status="delivered", created_at=recent_ts,
            ))
            await session.commit()

        deleted_d, deleted_e = await _do_retention(eng, cutoff)
        assert deleted_d == 0
        assert deleted_e == 0
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_retention_keeps_retrying():
    """retrying deliveries are never deleted regardless of age."""
    from app.main import _do_retention

    eng = await _make_retention_engine()
    old_ts = datetime.now(timezone.utc) - timedelta(days=60)
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    try:
        async with AsyncSession(eng, expire_on_commit=False) as session:
            session.add(WebhookDelivery(
                run_id="r11-retry-1", url="https://retry.com", payload_json="{}",
                status="retrying", created_at=old_ts,
            ))
            await session.commit()

        deleted_d, _ = await _do_retention(eng, cutoff)
        assert deleted_d == 0
    finally:
        await eng.dispose()
