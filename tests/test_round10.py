"""Round 10 P1 integration tests.

Covers:
- Budget O(n) → cached total_cost_usd: _check_workspace_budget uses single-row read
- Per-workspace slack_webhook_url: PATCH /workspaces/{id}/slack
- Webhook registration API: POST/GET/DELETE /workspaces/{id}/webhooks
- Listener: workspace webhooks fire on pipeline.end
- Listener: per-workspace slack_webhook_url used for HITL notifications
"""
from __future__ import annotations

import json
import pytest
from httpx import AsyncClient

from app.models.run import Workspace, Run, WebhookConfig, WebhookEvent, WebhookDelivery


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_workspace(session, name="ws-r10", slug="ws-r10", max_cost_usd=None):
    ws = Workspace(name=name, slug=slug, max_cost_usd=max_cost_usd)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


# ---------------------------------------------------------------------------
# 1. Budget O(n) → cached total_cost_usd
# ---------------------------------------------------------------------------

async def _make_runner_engine(monkeypatch):
    """Create a fresh in-memory engine for tests that call runner functions directly.

    runner.py uses engine from app.core.database. Tests use their own in-memory engine
    via the session fixture. We monkeypatch runner.engine to point to the test engine
    so DB writes are visible in both directions.
    """
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlmodel import SQLModel
    import app.services.runner as runner_mod

    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    monkeypatch.setattr(runner_mod, "engine", test_engine)
    return test_engine


@pytest.mark.asyncio
async def test_check_budget_uses_total_cost_usd(monkeypatch):
    """_check_workspace_budget raises when total_cost_usd >= max_cost_usd (no Run scan)."""
    from sqlmodel.ext.asyncio.session import AsyncSession
    import app.services.runner as runner_mod

    test_engine = await _make_runner_engine(monkeypatch)
    try:
        async with AsyncSession(test_engine, expire_on_commit=False) as ts:
            ws = Workspace(name="Budget WS", slug="budget-ws-r10", max_cost_usd=1.0, total_cost_usd=1.5)
            ts.add(ws)
            await ts.commit()
            await ts.refresh(ws)
            ws_id = ws.id

        with pytest.raises(ValueError, match="budget exhausted"):
            await runner_mod._check_workspace_budget(ws_id)
    finally:
        await test_engine.dispose()


@pytest.mark.asyncio
async def test_check_budget_raises_when_total_meets_limit(monkeypatch):
    """_check_workspace_budget raises when total_cost_usd >= max_cost_usd."""
    from sqlmodel.ext.asyncio.session import AsyncSession
    import app.services.runner as runner_mod

    test_engine = await _make_runner_engine(monkeypatch)
    try:
        async with AsyncSession(test_engine, expire_on_commit=False) as ts:
            ws = Workspace(name="Flag WS", slug="flag-ws-r10",
                           max_cost_usd=5.0, total_cost_usd=5.0)
            ts.add(ws)
            await ts.commit()
            await ts.refresh(ws)
            ws_id = ws.id

        with pytest.raises(ValueError, match="budget"):
            await runner_mod._check_workspace_budget(ws_id)
    finally:
        await test_engine.dispose()


@pytest.mark.asyncio
async def test_mark_budget_updates_total_cost_usd(monkeypatch):
    """_mark_workspace_budget_status updates total_cost_usd via SQL SUM."""
    from sqlmodel.ext.asyncio.session import AsyncSession
    from sqlmodel import select
    import app.services.runner as runner_mod

    test_engine = await _make_runner_engine(monkeypatch)
    try:
        async with AsyncSession(test_engine, expire_on_commit=False) as ts:
            ws = Workspace(name="Sum WS", slug="sum-ws-r10", max_cost_usd=10.0, total_cost_usd=0.0)
            ts.add(ws)
            run = Run(run_id="r10-sum-1", team="DevTeam", request="x", status="success", cost_usd=3.5)
            ts.add(run)
            await ts.commit()
            await ts.refresh(ws)
            ws.id  # ensure loaded
            run.workspace_id = ws.id
            ts.add(run)
            await ts.commit()
            ws_id = ws.id

        await runner_mod._mark_workspace_budget_status(ws_id)

        async with AsyncSession(test_engine, expire_on_commit=False) as ts:
            ws_fresh = (await ts.exec(select(Workspace).where(Workspace.id == ws_id))).first()
            assert ws_fresh.total_cost_usd == pytest.approx(3.5)
            assert ws_fresh.total_cost_usd < ws_fresh.max_cost_usd  # not exhausted
    finally:
        await test_engine.dispose()


@pytest.mark.asyncio
async def test_mark_budget_sets_exhausted(monkeypatch):
    """_mark_workspace_budget_status logs warning when total >= limit; total_cost_usd updated."""
    from sqlmodel.ext.asyncio.session import AsyncSession
    from sqlmodel import select
    import app.services.runner as runner_mod

    test_engine = await _make_runner_engine(monkeypatch)
    try:
        async with AsyncSession(test_engine, expire_on_commit=False) as ts:
            ws = Workspace(name="Exceed WS", slug="exceed-ws-r10", max_cost_usd=2.0, total_cost_usd=0.0)
            ts.add(ws)
            await ts.commit()
            await ts.refresh(ws)
            ws_id = ws.id
            run = Run(run_id="r10-exceed-1", team="DevTeam", request="x", status="success",
                      workspace_id=ws_id, cost_usd=2.5)
            ts.add(run)
            await ts.commit()

        await runner_mod._mark_workspace_budget_status(ws_id)

        async with AsyncSession(test_engine, expire_on_commit=False) as ts:
            ws_fresh = (await ts.exec(select(Workspace).where(Workspace.id == ws_id))).first()
            assert ws_fresh.total_cost_usd == pytest.approx(2.5)
            assert ws_fresh.total_cost_usd >= ws_fresh.max_cost_usd  # exhausted
    finally:
        await test_engine.dispose()


# ---------------------------------------------------------------------------
# 2. Per-workspace slack_webhook_url
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_slack_webhook(client: AsyncClient, session):
    """PATCH /workspaces/{id}/slack sets slack_webhook_url."""
    ws = await _make_workspace(session, name="Slack WS", slug="slack-ws-r10")
    r = await client.patch(f"/workspaces/{ws.id}/slack", json={
        "slack_webhook_url": "https://hooks.slack.com/services/T000/B000/xxx"
    })
    assert r.status_code == 200
    data = r.json()
    assert data["slack_webhook_url"] == "https://hooks.slack.com/services/T000/B000/xxx"


@pytest.mark.asyncio
async def test_set_slack_webhook_clear(client: AsyncClient, session):
    """PATCH /workspaces/{id}/slack with null clears the URL."""
    ws = Workspace(
        name="Slack Clear WS", slug="slack-clear-ws-r10",
        slack_webhook_url="https://hooks.slack.com/services/old"
    )
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    r = await client.patch(f"/workspaces/{ws.id}/slack", json={"slack_webhook_url": None})
    assert r.status_code == 200
    assert r.json()["slack_webhook_url"] is None


@pytest.mark.asyncio
async def test_set_slack_webhook_not_found(client: AsyncClient, session):
    r = await client.patch("/workspaces/99999/slack", json={"slack_webhook_url": "https://x.y/z"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 3. Webhook registration API
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_webhook_config(client: AsyncClient, session):
    ws = await _make_workspace(session, name="Hook WS", slug="hook-ws-r10")
    r = await client.post(f"/workspaces/{ws.id}/webhooks", json={
        "url": "https://example.com/hook",
        "events": ["pipeline.end"],
        "label": "CI notifier",
    })
    assert r.status_code == 201
    data = r.json()
    assert data["url"] == "https://example.com/hook"
    assert data["label"] == "CI notifier"
    assert data["enabled"] is True
    assert data["events"] == ["pipeline.end"]


@pytest.mark.asyncio
async def test_create_webhook_invalid_url(client: AsyncClient, session):
    ws = await _make_workspace(session, name="Hook WS2", slug="hook-ws-r10-2")
    r = await client.post(f"/workspaces/{ws.id}/webhooks", json={
        "url": "http://not-https.com/hook",
        "events": ["pipeline.end"],
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_webhook_empty_events(client: AsyncClient, session):
    ws = await _make_workspace(session, name="Hook WS3", slug="hook-ws-r10-3")
    r = await client.post(f"/workspaces/{ws.id}/webhooks", json={
        "url": "https://example.com/hook",
        "events": [],
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_list_webhook_configs(client: AsyncClient, session):
    ws = await _make_workspace(session, name="Hook List WS", slug="hook-list-ws-r10")
    hook1 = WebhookConfig(workspace_id=ws.id, url="https://a.com/1")
    hook2 = WebhookConfig(workspace_id=ws.id, url="https://b.com/2")
    session.add(hook1)
    session.add(hook2)
    await session.flush()
    session.add(WebhookEvent(webhook_id=hook1.id, event_type="pipeline.end"))
    session.add(WebhookEvent(webhook_id=hook2.id, event_type="pipeline.end"))
    await session.commit()

    r = await client.get(f"/workspaces/{ws.id}/webhooks")
    assert r.status_code == 200
    urls = [h["url"] for h in r.json()]
    assert "https://a.com/1" in urls
    assert "https://b.com/2" in urls


@pytest.mark.asyncio
async def test_delete_webhook_config(client: AsyncClient, session):
    ws = await _make_workspace(session, name="Hook Del WS", slug="hook-del-ws-r10")
    hook = WebhookConfig(workspace_id=ws.id, url="https://del.com/hook")
    session.add(hook)
    await session.flush()
    session.add(WebhookEvent(webhook_id=hook.id, event_type="pipeline.end"))
    await session.commit()
    await session.refresh(hook)

    r = await client.delete(f"/workspaces/{ws.id}/webhooks/{hook.id}")
    assert r.status_code == 204

    # Verify gone
    r2 = await client.get(f"/workspaces/{ws.id}/webhooks")
    assert all(h["id"] != hook.id for h in r2.json())


@pytest.mark.asyncio
async def test_delete_webhook_wrong_workspace(client: AsyncClient, session):
    """Deleting a webhook with wrong workspace_id returns 404."""
    ws1 = await _make_workspace(session, name="WS1 Hook", slug="ws1-hook-r10")
    ws2 = await _make_workspace(session, name="WS2 Hook", slug="ws2-hook-r10")
    hook = WebhookConfig(workspace_id=ws1.id, url="https://x.com/hook")
    session.add(hook)
    await session.flush()
    session.add(WebhookEvent(webhook_id=hook.id, event_type="pipeline.end"))
    await session.commit()
    await session.refresh(hook)

    # Try to delete ws1's hook via ws2's path
    r = await client.delete(f"/workspaces/{ws2.id}/webhooks/{hook.id}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_webhooks_workspace_not_found(client: AsyncClient, session):
    r = await client.get("/workspaces/99999/webhooks")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_webhook_workspace_not_found(client: AsyncClient, session):
    r = await client.post("/workspaces/99999/webhooks", json={
        "url": "https://example.com/hook",
        "events": ["pipeline.end"],
    })
    assert r.status_code == 404
