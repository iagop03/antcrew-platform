"""Tests for v0.2.0 API features: stats, cancel, pagination, search, api-keys, health."""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.run import Run, Ticket, ApiKey


@pytest.mark.asyncio
async def test_stats_empty(client: AsyncClient):
    r = await client.get("/runs/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 0
    assert data["total_cost_usd"] == 0.0


@pytest.mark.asyncio
async def test_stats_counts(client: AsyncClient, session):
    session.add(Run(run_id="r1", team="DevTeam", request="x", status="success", cost_usd=0.10))
    session.add(Run(run_id="r2", team="DevTeam", request="y", status="error", cost_usd=0.0))
    session.add(Run(run_id="r3", team="DevTeam", request="z", status="running", cost_usd=0.0))
    await session.commit()

    r = await client.get("/runs/stats")
    data = r.json()
    assert data["total"] == 3
    assert data["success"] == 1
    assert data["error"] == 1
    assert data["running"] == 1
    assert abs(data["total_cost_usd"] - 0.10) < 0.001


@pytest.mark.asyncio
async def test_cancel_not_found(client: AsyncClient):
    r = await client.post("/runs/no-such-run/cancel")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_cancel_not_running(client: AsyncClient, session):
    session.add(Run(run_id="done-run", team="DevTeam", request="x", status="success"))
    await session.commit()

    r = await client.post("/runs/done-run/cancel")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_cancel_success(client: AsyncClient, session):
    session.add(Run(run_id="live-run", team="DevTeam", request="x", status="running"))
    await session.commit()

    r = await client.post("/runs/live-run/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_since_id_pagination(client: AsyncClient, session):
    for i in range(5):
        session.add(Run(run_id=f"pag-{i}", team="DevTeam", request=f"req {i}", status="success"))
    await session.commit()

    all_r = await client.get("/runs/?limit=10")
    all_runs = all_r.json()
    assert len(all_runs) >= 5

    pivot = all_runs[2]["id"]
    r2 = await client.get(f"/runs/?since_id={pivot}&limit=10")
    older = r2.json()
    assert all(d["id"] < pivot for d in older)


@pytest.mark.asyncio
async def test_ticket_search(client: AsyncClient, session):
    session.add(Ticket(ticket_id="T1", run_id="r1", title="Auth login endpoint"))
    session.add(Ticket(ticket_id="T2", run_id="r1", title="Dashboard UI redesign"))
    await session.commit()

    r = await client.get("/tickets/?search=auth")
    data = r.json()
    assert len(data) == 1
    assert data[0]["ticket_id"] == "T1"


@pytest.mark.asyncio
async def test_ticket_search_empty(client: AsyncClient, session):
    session.add(Ticket(ticket_id="T3", run_id="r1", title="Database migrations"))
    await session.commit()

    r = await client.get("/tickets/?search=xyz-no-match-9999")
    assert r.json() == []


@pytest.mark.asyncio
async def test_api_key_create(client: AsyncClient):
    r = await client.post("/api-keys/", json={"label": "ci-key"})
    assert r.status_code == 201
    data = r.json()
    assert "key" in data
    assert data["label"] == "ci-key"
    assert len(data["key"]) > 20


@pytest.mark.asyncio
async def test_api_key_list(client: AsyncClient, session):
    import hashlib
    raw_key = "test-list-raw"
    raw_admin = "test-list-admin"
    session.add(ApiKey(label="listed-key", key_hash=hashlib.sha256(raw_key.encode()).hexdigest()))
    session.add(ApiKey(label="listed-admin", key_hash=hashlib.sha256(raw_admin.encode()).hexdigest(), role="admin"))
    await session.commit()

    # admin key can list
    r = await client.get("/api-keys/", headers={"X-Api-Key": raw_admin})
    assert r.status_code == 200
    labels = [k["label"] for k in r.json()]
    assert "listed-key" in labels

    # default (write) key gets 403
    r403 = await client.get("/api-keys/", headers={"X-Api-Key": raw_key})
    assert r403.status_code == 403


@pytest.mark.asyncio
async def test_api_key_duplicate_label(client: AsyncClient):
    r1 = await client.post("/api-keys/", json={"label": "dup-key", "role": "admin"})
    assert r1.status_code == 201
    key = r1.json()["key"]
    # Second request uses the admin key (has permission to create keys in multi-key mode)
    r = await client.post("/api-keys/", json={"label": "dup-key"}, headers={"X-Api-Key": key})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_api_key_revoke(client: AsyncClient):
    r1 = await client.post("/api-keys/", json={"label": "revoke-me", "role": "admin"})
    assert r1.status_code == 201
    key = r1.json()["key"]
    # Provide the admin key to authenticate delete in multi-key mode
    r = await client.delete("/api-keys/revoke-me", headers={"X-Api-Key": key})
    assert r.status_code == 204

    # After revocation, no active keys → open mode → list accessible without auth
    r2 = await client.get("/api-keys/")
    labels = [k["label"] for k in r2.json()]
    assert "revoke-me" not in labels


@pytest.mark.asyncio
async def test_api_key_revoke_not_found(client: AsyncClient):
    r = await client.delete("/api-keys/nonexistent-label")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_health_ok(client: AsyncClient):
    r = await client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["db"] is True
    assert "version" in data


@pytest.mark.asyncio
async def test_run_created_by_field(client: AsyncClient, session):
    session.add(Run(run_id="r-attrib", team="DevTeam", request="x", status="success", created_by="dev-key"))
    await session.commit()

    r = await client.get("/runs/r-attrib")
    assert r.status_code == 200
    assert r.json()["created_by"] == "dev-key"
