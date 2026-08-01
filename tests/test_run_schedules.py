"""Tests for Run Schedules CRUD — POST/GET/PATCH/DELETE /run-schedules/."""
from __future__ import annotations

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.run import Workspace, ApiKey


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()


async def _make_workspace_and_key(session: AsyncSession, slug: str, raw_key: str) -> tuple:
    """Create workspace + SHA256-hashed ApiKey. Returns (workspace, api_key)."""
    ws = Workspace(name=slug, slug=slug)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    key_hash = _sha256(raw_key)
    key_prefix = _sha256(raw_key)[:16]
    key = ApiKey(
        label=f"key-{slug}",
        key_hash=key_hash,
        key_prefix=key_prefix,
        workspace_id=ws.id,
        role="admin",
    )
    session.add(key)
    await session.commit()
    await session.refresh(key)
    return ws, key


# All schedule tests need a real workspace_id (NOT NULL constraint).
# We create one workspace+key per test using the session fixture.
_RAW_KEY = "schedtest-default-apikey"


@pytest.fixture
async def sched_key(session: AsyncSession):
    """Create a workspace + admin API key for schedule tests."""
    ws, key = await _make_workspace_and_key(session, "schedtest-ws", _RAW_KEY)
    return ws, key


def _h(client, raw_key: str = _RAW_KEY) -> dict:
    return {"X-Api-Key": raw_key}


# ---------------------------------------------------------------------------
# POST /run-schedules/ — create
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_schedule_valid_cron(client, sched_key):
    """Valid cron expression → 201 with schedule fields."""
    r = await client.post("/run-schedules/", json={
        "name": "nightly audit",
        "goal": "audit security",
        "cron_expr": "0 2 * * *",
    }, headers=_h(client))
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["name"] == "nightly audit"
    assert data["cron_expr"] == "0 2 * * *"
    assert data["enabled"] is True
    assert data["next_run_at"] is not None


@pytest.mark.asyncio
async def test_create_schedule_invalid_cron(client, sched_key):
    """Invalid cron expression → 422."""
    r = await client.post("/run-schedules/", json={
        "name": "bad schedule",
        "goal": "something",
        "cron_expr": "not-a-cron",
    }, headers=_h(client))
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_create_schedule_all_fields(client, sched_key):
    """Create with optional fields → all persisted."""
    r = await client.post("/run-schedules/", json={
        "name": "full sched",
        "goal": "full audit",
        "model": "claude",
        "conditions": ["check auth", "check sql"],
        "full": False,
        "max_cost_usd": 3.50,
        "cron_expr": "*/30 * * * *",
    }, headers=_h(client))
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["max_cost_usd"] == 3.50
    assert d["full"] is False


# ---------------------------------------------------------------------------
# GET /run-schedules/ — list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_schedules_empty(client, sched_key):
    """No schedules → empty list."""
    r = await client.get("/run-schedules/", headers=_h(client))
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_list_schedules_returns_created(client, sched_key):
    """Schedules are listed after creation."""
    await client.post("/run-schedules/", json={
        "name": "s1", "goal": "g1", "cron_expr": "0 * * * *",
    }, headers=_h(client))
    await client.post("/run-schedules/", json={
        "name": "s2", "goal": "g2", "cron_expr": "0 1 * * *",
    }, headers=_h(client))
    r = await client.get("/run-schedules/", headers=_h(client))
    assert r.status_code == 200
    names = [s["name"] for s in r.json()]
    assert "s1" in names
    assert "s2" in names


# ---------------------------------------------------------------------------
# GET /run-schedules/{id} — single
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_schedule_by_id(client, sched_key):
    """GET by ID returns the created schedule."""
    cr = await client.post("/run-schedules/", json={
        "name": "fetch-me", "goal": "x", "cron_expr": "0 6 * * 1",
    }, headers=_h(client))
    assert cr.status_code == 201
    sched_id = cr.json()["id"]

    r = await client.get(f"/run-schedules/{sched_id}", headers=_h(client))
    assert r.status_code == 200
    assert r.json()["name"] == "fetch-me"


@pytest.mark.asyncio
async def test_get_schedule_not_found(client, sched_key):
    """GET on non-existent ID → 404."""
    r = await client.get("/run-schedules/999999", headers=_h(client))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /run-schedules/{id} — update
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_schedule_name(client, sched_key):
    """PATCH name → updated."""
    cr = await client.post("/run-schedules/", json={
        "name": "old-name", "goal": "x", "cron_expr": "0 0 * * *",
    }, headers=_h(client))
    sched_id = cr.json()["id"]

    r = await client.patch(f"/run-schedules/{sched_id}", json={"name": "new-name"}, headers=_h(client))
    assert r.status_code == 200
    assert r.json()["name"] == "new-name"


@pytest.mark.asyncio
async def test_patch_schedule_cron_recalculates_next_run(client, sched_key):
    """Changing cron_expr → next_run_at is recalculated."""
    cr = await client.post("/run-schedules/", json={
        "name": "recalc", "goal": "x", "cron_expr": "0 0 * * *",
    }, headers=_h(client))
    sched_id = cr.json()["id"]

    r = await client.patch(f"/run-schedules/{sched_id}", json={"cron_expr": "0 12 * * *"}, headers=_h(client))
    assert r.status_code == 200
    assert r.json()["cron_expr"] == "0 12 * * *"
    assert r.json()["next_run_at"] is not None


@pytest.mark.asyncio
async def test_patch_schedule_invalid_cron(client, sched_key):
    """Patching with an invalid cron_expr → 422."""
    cr = await client.post("/run-schedules/", json={
        "name": "keep-me", "goal": "x", "cron_expr": "0 0 * * *",
    }, headers=_h(client))
    sched_id = cr.json()["id"]

    r = await client.patch(f"/run-schedules/{sched_id}", json={"cron_expr": "bad"}, headers=_h(client))
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_schedule_disable(client, sched_key):
    """PATCH enabled=false → schedule is disabled."""
    cr = await client.post("/run-schedules/", json={
        "name": "toggle", "goal": "x", "cron_expr": "0 0 * * *",
    }, headers=_h(client))
    sched_id = cr.json()["id"]
    assert cr.json()["enabled"] is True

    r = await client.patch(f"/run-schedules/{sched_id}", json={"enabled": False}, headers=_h(client))
    assert r.status_code == 200
    assert r.json()["enabled"] is False


# ---------------------------------------------------------------------------
# DELETE /run-schedules/{id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_schedule(client, sched_key):
    """DELETE → 204; subsequent GET → 404."""
    cr = await client.post("/run-schedules/", json={
        "name": "del-me", "goal": "x", "cron_expr": "0 0 * * *",
    }, headers=_h(client))
    sched_id = cr.json()["id"]

    r = await client.delete(f"/run-schedules/{sched_id}", headers=_h(client))
    assert r.status_code == 204

    r2 = await client.get(f"/run-schedules/{sched_id}", headers=_h(client))
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_delete_nonexistent_schedule(client, sched_key):
    """DELETE on non-existent ID → 404."""
    r = await client.delete("/run-schedules/999999", headers=_h(client))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Cross-workspace isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_isolation(client, session: AsyncSession):
    """Schedules from workspace A are not visible to workspace B."""
    ws_a, key_a = await _make_workspace_and_key(session, "sched-ws-a", "raw-key-a-schedtest")
    ws_b, key_b = await _make_workspace_and_key(session, "sched-ws-b", "raw-key-b-schedtest")

    # Create a schedule as workspace A
    cr = await client.post(
        "/run-schedules/",
        json={"name": "ws-a-sched", "goal": "x", "cron_expr": "0 0 * * *"},
        headers={"X-Api-Key": "raw-key-a-schedtest"},
    )
    assert cr.status_code == 201
    sched_id = cr.json()["id"]

    # Workspace B cannot list it
    r = await client.get("/run-schedules/", headers={"X-Api-Key": "raw-key-b-schedtest"})
    assert r.status_code == 200
    ids = [s["id"] for s in r.json()]
    assert sched_id not in ids

    # Workspace B cannot fetch it directly
    r2 = await client.get(f"/run-schedules/{sched_id}", headers={"X-Api-Key": "raw-key-b-schedtest"})
    assert r2.status_code == 404


# ---------------------------------------------------------------------------
# dispatch_due_run_schedules — cron firing logic
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_skips_disabled_schedule(session: AsyncSession):
    """dispatch_due_run_schedules does not fire disabled schedules."""
    from datetime import datetime, timezone, timedelta
    from app.api.run_schedules import dispatch_due_run_schedules
    from app.models.run import RunSchedule
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = session.bind

    # Create a disabled schedule whose next_run_at is in the past
    sched = RunSchedule(
        workspace_id=1,
        name="disabled",
        goal="x",
        cron_expr="* * * * *",
        enabled=False,
        next_run_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5),
    )
    session.add(sched)
    await session.commit()

    dispatched = await dispatch_due_run_schedules(engine)
    assert dispatched == 0


@pytest.mark.asyncio
async def test_dispatch_skips_future_schedule(session: AsyncSession):
    """dispatch_due_run_schedules does not fire schedules not yet due."""
    from datetime import datetime, timezone, timedelta
    from app.api.run_schedules import dispatch_due_run_schedules
    from app.models.run import RunSchedule

    engine = session.bind

    sched = RunSchedule(
        workspace_id=1,
        name="future",
        goal="x",
        cron_expr="* * * * *",
        enabled=True,
        next_run_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
    )
    session.add(sched)
    await session.commit()

    dispatched = await dispatch_due_run_schedules(engine)
    assert dispatched == 0
