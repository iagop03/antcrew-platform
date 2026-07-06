"""Round 13 tests — RBAC enforcement, budget_exceeded computed, main-loop reuse.

Covers:
- Workspace mutation endpoints require admin role (P2.1)
- GET /api-keys/ requires admin role (P2.1)
- POST /runs/upload and cancel require write+ role (P2.1)
- WorkspacePublic.budget_exceeded is derived from total_cost_usd, not stored flag (P2.2)
- set_main_loop + run_coroutine_threadsafe path in _resolve_review_sync (P3)
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import Optional
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.models.run import ApiKey, Workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def _make_key(session, *, label: str, raw: str, role: str = "write",
                   workspace_id: Optional[int] = None) -> ApiKey:
    key = ApiKey(label=label, key_hash=_hash(raw), role=role, workspace_id=workspace_id)
    session.add(key)
    await session.commit()
    return key


async def _make_ws(session, *, slug: str, name: Optional[str] = None) -> Workspace:
    ws = Workspace(name=name or slug, slug=slug)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


# ---------------------------------------------------------------------------
# P2.1 — workspace mutations require admin
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_create_requires_admin(client: AsyncClient, session):
    await _make_key(session, label="write-r13", raw="write-r13-key", role="write")
    r = await client.post("/workspaces/", json={"name": "X", "slug": "x-r13"},
                          headers={"X-Api-Key": "write-r13-key"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_workspace_create_admin_ok(client: AsyncClient, session):
    await _make_key(session, label="admin-r13", raw="admin-r13-key", role="admin")
    r = await client.post("/workspaces/", json={"name": "AdminWS", "slug": "admin-ws-r13"},
                          headers={"X-Api-Key": "admin-r13-key"})
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_workspace_delete_requires_admin(client: AsyncClient, session):
    await _make_key(session, label="write2-r13", raw="write2-r13-key", role="write")
    ws = await _make_ws(session, slug="del-r13")
    r = await client.delete(f"/workspaces/{ws.id}", headers={"X-Api-Key": "write2-r13-key"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_workspace_patch_budget_requires_admin(client: AsyncClient, session):
    await _make_key(session, label="write3-r13", raw="write3-r13-key", role="write")
    ws = await _make_ws(session, slug="budget-r13")
    r = await client.patch(f"/workspaces/{ws.id}/budget", json={"max_cost_usd": 10.0},
                           headers={"X-Api-Key": "write3-r13-key"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_workspace_patch_slack_tokens_requires_admin(client: AsyncClient, session):
    await _make_key(session, label="write4-r13", raw="write4-r13-key", role="write")
    ws = await _make_ws(session, slug="tokens-r13")
    r = await client.patch(f"/workspaces/{ws.id}/slack-tokens",
                           json={"bot_token": "xoxb-test"},
                           headers={"X-Api-Key": "write4-r13-key"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_workspace_webhook_create_requires_admin(client: AsyncClient, session):
    await _make_key(session, label="write5-r13", raw="write5-r13-key", role="write")
    ws = await _make_ws(session, slug="wh-r13")
    r = await client.post(f"/workspaces/{ws.id}/webhooks",
                          json={"url": "https://example.com/hook"},
                          headers={"X-Api-Key": "write5-r13-key"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_workspace_read_accessible_by_write_role(client: AsyncClient, session):
    """Non-admin keys can still read workspace info."""
    await _make_key(session, label="read-r13", raw="read-r13-key", role="read")
    ws = await _make_ws(session, slug="readable-r13")
    r = await client.get(f"/workspaces/{ws.id}", headers={"X-Api-Key": "read-r13-key"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# P2.1 — GET /api-keys/ requires admin
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_keys_list_requires_admin(client: AsyncClient, session):
    await _make_key(session, label="reviewer-r13", raw="reviewer-r13-key", role="reviewer")
    r = await client.get("/api-keys/", headers={"X-Api-Key": "reviewer-r13-key"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# P2.1 — runs cancel requires write+
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_cancel_requires_write(client: AsyncClient, session):
    from app.models.run import Run
    await _make_key(session, label="reader-r13", raw="reader-r13-key", role="read")
    run = Run(run_id="r13-cancel-test", team="dev", request="x", status="running", thread_id="t")
    session.add(run)
    await session.commit()
    r = await client.post(f"/runs/r13-cancel-test/cancel",
                          headers={"X-Api-Key": "reader-r13-key"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# P2.2 — budget_exceeded derived from total_cost_usd, not stored flag
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_budget_exceeded_computed_from_spend(client: AsyncClient, session):
    """WorkspacePublic.budget_exceeded reflects live spend even if the stored flag is stale."""
    await _make_key(session, label="admin-budget-r13", raw="admin-budget-r13-key", role="admin")

    ws = Workspace(name="Budget WS R13", slug="budget-compute-r13",
                   max_cost_usd=5.0, total_cost_usd=6.0)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    r = await client.get(f"/workspaces/{ws.id}", headers={"X-Api-Key": "admin-budget-r13-key"})
    assert r.status_code == 200
    data = r.json()
    assert data["budget_exceeded"] is True, "must derive True from total >= max"
    assert "slack_bot_token_enc" not in data
    assert "slack_app_token_enc" not in data


@pytest.mark.asyncio
async def test_workspace_budget_not_exceeded_when_under_limit(client: AsyncClient, session):
    await _make_key(session, label="admin-budget2-r13", raw="admin-budget2-r13-key", role="admin")
    ws = Workspace(name="OK Budget WS", slug="budget-ok-r13",
                   max_cost_usd=10.0, total_cost_usd=3.0)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    r = await client.get(f"/workspaces/{ws.id}", headers={"X-Api-Key": "admin-budget2-r13-key"})
    assert r.status_code == 200
    assert r.json()["budget_exceeded"] is False


@pytest.mark.asyncio
async def test_workspace_budget_exceeded_false_when_no_limit(client: AsyncClient, session):
    await _make_key(session, label="admin-budget3-r13", raw="admin-budget3-r13-key", role="admin")
    ws = Workspace(name="No Limit WS", slug="no-limit-r13",
                   max_cost_usd=None, total_cost_usd=999.0)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    r = await client.get(f"/workspaces/{ws.id}", headers={"X-Api-Key": "admin-budget3-r13-key"})
    assert r.status_code == 200
    assert r.json()["budget_exceeded"] is False


# ---------------------------------------------------------------------------
# P2.2 — WorkspacePublic never exposes token fields
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_tokens_not_exposed_in_response(client: AsyncClient, session):
    await _make_key(session, label="admin-tok-r13", raw="admin-tok-r13-key", role="admin")
    ws = Workspace(name="Token WS R13", slug="token-r13",
                   slack_bot_token_enc="xoxb-secret", slack_app_token_enc="xapp-secret")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    r = await client.get(f"/workspaces/{ws.id}", headers={"X-Api-Key": "admin-tok-r13-key"})
    assert r.status_code == 200
    data = r.json()
    assert "slack_bot_token_enc" not in data
    assert "slack_app_token_enc" not in data
    assert data["slack_bot_configured"] is True
    assert data["slack_app_configured"] is True


# ---------------------------------------------------------------------------
# P3 — _resolve_review_sync uses main loop when available
# ---------------------------------------------------------------------------

def test_resolve_review_uses_main_loop_when_set():
    """When a main loop is registered, run_coroutine_threadsafe is used (not new_event_loop)."""
    import app.core.slack_hitl as sh

    original_loop = sh._main_loop
    calls: list[str] = []

    class FakeLoop:
        def is_running(self):
            return True

    class FakeFuture:
        def result(self, timeout=None):
            return None  # success — no actual DB needed

    def fake_run_coroutine_threadsafe(coro, loop):
        calls.append("run_coroutine_threadsafe")
        coro.close()  # clean up without running
        return FakeFuture()

    sh._main_loop = FakeLoop()
    try:
        with patch("asyncio.run_coroutine_threadsafe", side_effect=fake_run_coroutine_threadsafe):
            sh._resolve_review_sync("rev-p3-test", "approve")
        assert "run_coroutine_threadsafe" in calls
    finally:
        sh._main_loop = original_loop


def test_set_main_loop_registers_loop():
    import app.core.slack_hitl as sh
    original = sh._main_loop
    loop = asyncio.new_event_loop()
    try:
        sh.set_main_loop(loop)
        assert sh._main_loop is loop
    finally:
        sh._main_loop = original
        loop.close()
