"""Tests for workspace CRUD."""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.run import Workspace


@pytest.mark.asyncio
async def test_list_workspaces_empty(client: AsyncClient):
    r = await client.get("/workspaces/")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_create_workspace(client: AsyncClient):
    r = await client.post("/workspaces/", json={"name": "Backend Team", "slug": "backend-team"})
    assert r.status_code == 201
    data = r.json()
    assert data["slug"] == "backend-team"
    assert data["name"] == "Backend Team"
    assert "id" in data


@pytest.mark.asyncio
async def test_create_workspace_duplicate_slug(client: AsyncClient):
    await client.post("/workspaces/", json={"name": "W1", "slug": "shared-slug"})
    r = await client.post("/workspaces/", json={"name": "W2", "slug": "shared-slug"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_create_workspace_invalid_slug(client: AsyncClient):
    r = await client.post("/workspaces/", json={"name": "Bad", "slug": "Has Spaces"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_get_workspace(client: AsyncClient, session):
    ws = Workspace(name="My WS", slug="my-ws")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    r = await client.get(f"/workspaces/{ws.id}")
    assert r.status_code == 200
    assert r.json()["slug"] == "my-ws"


@pytest.mark.asyncio
async def test_get_workspace_not_found(client: AsyncClient):
    r = await client.get("/workspaces/999999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_multiple_workspaces(client: AsyncClient, session):
    session.add(Workspace(name="Alpha", slug="alpha"))
    session.add(Workspace(name="Beta", slug="beta"))
    await session.commit()

    r = await client.get("/workspaces/")
    slugs = [w["slug"] for w in r.json()]
    assert "alpha" in slugs
    assert "beta" in slugs


@pytest.mark.asyncio
async def test_delete_workspace(client: AsyncClient, session):
    ws = Workspace(name="ToDelete", slug="to-delete")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    r = await client.delete(f"/workspaces/{ws.id}")
    assert r.status_code == 204

    r2 = await client.get(f"/workspaces/{ws.id}")
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_delete_workspace_not_found(client: AsyncClient):
    r = await client.delete("/workspaces/999999")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Workspace-scoped API key enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_scoped_key_filters_runs(client: AsyncClient, session):
    """API key with workspace_id only returns runs from that workspace."""
    import hashlib
    from app.models.run import ApiKey, Run

    ws1 = Workspace(name="Team A", slug="team-a-runs")
    ws2 = Workspace(name="Team B", slug="team-b-runs")
    session.add(ws1)
    session.add(ws2)
    await session.commit()
    await session.refresh(ws1)
    await session.refresh(ws2)

    raw_key = "scoped-key-for-ws1"
    session.add(ApiKey(
        label="ws1-key",
        key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        workspace_id=ws1.id,
    ))
    session.add(Run(run_id="ws1-run", team="DevTeam", request="x", status="success", workspace_id=ws1.id))
    session.add(Run(run_id="ws2-run", team="DevTeam", request="y", status="success", workspace_id=ws2.id))
    await session.commit()

    r = await client.get("/runs/", headers={"X-Api-Key": raw_key})
    assert r.status_code == 200
    run_ids = [d["run_id"] for d in r.json()]
    assert "ws1-run" in run_ids
    assert "ws2-run" not in run_ids


@pytest.mark.asyncio
async def test_workspace_scoped_key_filters_stats(client: AsyncClient, session):
    """Stats are scoped to the API key's workspace."""
    import hashlib
    from app.models.run import ApiKey, Run

    ws1 = Workspace(name="Stats WS", slug="stats-ws")
    session.add(ws1)
    await session.commit()
    await session.refresh(ws1)

    raw_key = "scoped-stats-key"
    session.add(ApiKey(
        label="stats-ws-key",
        key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        workspace_id=ws1.id,
    ))
    session.add(Run(run_id="ws-s1", team="DevTeam", request="x", status="success", cost_usd=0.05, workspace_id=ws1.id))
    session.add(Run(run_id="ws-s2", team="DevTeam", request="y", status="error", cost_usd=0.0, workspace_id=None))
    await session.commit()

    r = await client.get("/runs/stats", headers={"X-Api-Key": raw_key})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["success"] == 1
    assert data["error"] == 0


@pytest.mark.asyncio
async def test_create_api_key_with_workspace(client: AsyncClient, session):
    """API key creation accepts workspace_id."""
    ws = Workspace(name="Key WS", slug="key-ws")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    r = await client.post("/api-keys/", json={"label": "ws-scoped-key", "workspace_id": ws.id})
    assert r.status_code == 201
    assert "key" in r.json()


# ---------------------------------------------------------------------------
# Fix 3+4: GET /tickets/ scoped by workspace
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_scoped_key_filters_tickets(client: AsyncClient, session):
    """API key with workspace_id only returns tickets from that workspace's runs."""
    import hashlib
    from app.models.run import ApiKey, Run, Ticket

    ws1 = Workspace(name="Ticket WS-A", slug="ticket-ws-a")
    ws2 = Workspace(name="Ticket WS-B", slug="ticket-ws-b")
    session.add(ws1)
    session.add(ws2)
    await session.commit()
    await session.refresh(ws1)
    await session.refresh(ws2)

    raw_key = "tickets-scoped-key"
    session.add(ApiKey(
        label="ticket-key",
        key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        workspace_id=ws1.id,
    ))
    # Run and ticket in ws1
    session.add(Run(run_id="tws1-run", team="DevTeam", request="x", status="success", workspace_id=ws1.id))
    session.add(Ticket(ticket_id="TW1", run_id="tws1-run", title="Auth endpoint"))
    # Run and ticket in ws2
    session.add(Run(run_id="tws2-run", team="DevTeam", request="y", status="success", workspace_id=ws2.id))
    session.add(Ticket(ticket_id="TW2", run_id="tws2-run", title="Payment endpoint"))
    await session.commit()

    r = await client.get("/tickets/", headers={"X-Api-Key": raw_key})
    assert r.status_code == 200
    ids = [t["ticket_id"] for t in r.json()]
    assert "TW1" in ids
    assert "TW2" not in ids


# ---------------------------------------------------------------------------
# Fix 5: GET /reviews/ scoped by workspace
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_scoped_key_filters_reviews(client: AsyncClient, session):
    """API key with workspace_id only returns HITL reviews from that workspace's runs."""
    import hashlib
    from app.models.run import ApiKey, Run, HitlReview

    ws1 = Workspace(name="Review WS-A", slug="review-ws-a")
    ws2 = Workspace(name="Review WS-B", slug="review-ws-b")
    session.add(ws1)
    session.add(ws2)
    await session.commit()
    await session.refresh(ws1)
    await session.refresh(ws2)

    raw_key = "reviews-scoped-key"
    session.add(ApiKey(
        label="review-key",
        key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        workspace_id=ws1.id,
    ))
    session.add(Run(run_id="rws1-run", team="DevTeam", request="x", status="running", workspace_id=ws1.id))
    session.add(Run(run_id="rws2-run", team="DevTeam", request="y", status="running", workspace_id=ws2.id))
    session.add(HitlReview(
        review_id="rev-ws1", run_id="rws1-run", agent_name="pm",
        artifact_json="{}", options_json='["approve"]', status="pending",
    ))
    session.add(HitlReview(
        review_id="rev-ws2", run_id="rws2-run", agent_name="pm",
        artifact_json="{}", options_json='["approve"]', status="pending",
    ))
    await session.commit()

    r = await client.get("/reviews/", headers={"X-Api-Key": raw_key})
    assert r.status_code == 200
    ids = [rv["review_id"] for rv in r.json()]
    assert "rev-ws1" in ids
    assert "rev-ws2" not in ids


# ---------------------------------------------------------------------------
# Fix 6: ANTCREW_TEAMS env var registers custom teams
# ---------------------------------------------------------------------------

def test_antcrew_teams_env_var_registers_custom_team(monkeypatch):
    """Custom teams from ANTCREW_TEAMS are added to the runner registry."""
    monkeypatch.setenv("ANTCREW_TEAMS", "myorg.teams.invoice:InvoiceTeam,myorg.teams.data:DataPipelineTeam")
    from app.services.runner import _build_team_registry
    registry = _build_team_registry()

    assert "InvoiceTeam" in registry
    assert registry["InvoiceTeam"] == ("myorg.teams.invoice", "InvoiceTeam")
    assert "DataPipelineTeam" in registry
    assert registry["DataPipelineTeam"] == ("myorg.teams.data", "DataPipelineTeam")
    # Built-in teams still present
    assert "DevTeam" in registry
    assert "ResearchTeam" in registry


def test_antcrew_teams_invalid_entry_is_skipped(monkeypatch, caplog):
    """Malformed ANTCREW_TEAMS entries are logged and skipped, not crashing."""
    import logging
    monkeypatch.setenv("ANTCREW_TEAMS", "BadEntry,good.module:GoodTeam")
    from app.services.runner import _build_team_registry
    with caplog.at_level(logging.WARNING, logger="app.services.runner"):
        registry = _build_team_registry()
    assert "GoodTeam" in registry
    assert any("BadEntry" in m for m in caplog.messages)


# ---------------------------------------------------------------------------
# Fix 1: workspace enforcement on detail routes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_detail_blocked_by_wrong_workspace(client: AsyncClient, session):
    """GET /runs/:id returns 403 when API key's workspace doesn't match the run."""
    import hashlib
    from app.models.run import ApiKey, Run

    ws1 = Workspace(name="Detail WS-A", slug="detail-ws-a")
    ws2 = Workspace(name="Detail WS-B", slug="detail-ws-b")
    session.add(ws1)
    session.add(ws2)
    await session.commit()
    await session.refresh(ws1)
    await session.refresh(ws2)

    raw_key = "detail-scoped-key"
    session.add(ApiKey(
        label="detail-key",
        key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        workspace_id=ws1.id,
    ))
    # Run belongs to ws2 — key is scoped to ws1
    session.add(Run(run_id="detail-ws2-run", team="DevTeam", request="x",
                    status="success", workspace_id=ws2.id))
    await session.commit()

    r = await client.get("/runs/detail-ws2-run", headers={"X-Api-Key": raw_key})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_run_detail_allowed_for_own_workspace(client: AsyncClient, session):
    """GET /runs/:id returns 200 when API key's workspace matches the run."""
    import hashlib
    from app.models.run import ApiKey, Run

    ws = Workspace(name="Own WS", slug="own-ws")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    raw_key = "own-ws-key"
    session.add(ApiKey(
        label="own-key",
        key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        workspace_id=ws.id,
    ))
    session.add(Run(run_id="own-ws-run", team="DevTeam", request="x",
                    status="success", workspace_id=ws.id))
    await session.commit()

    r = await client.get("/runs/own-ws-run", headers={"X-Api-Key": raw_key})
    assert r.status_code == 200
    assert r.json()["run_id"] == "own-ws-run"


@pytest.mark.asyncio
async def test_submit_review_blocked_by_wrong_workspace(client: AsyncClient, session):
    """POST /reviews/:id returns 403 when the review's run belongs to a different workspace."""
    import hashlib
    from app.models.run import ApiKey, Run, HitlReview

    ws1 = Workspace(name="Review Block WS-A", slug="rb-ws-a")
    ws2 = Workspace(name="Review Block WS-B", slug="rb-ws-b")
    session.add(ws1)
    session.add(ws2)
    await session.commit()
    await session.refresh(ws1)
    await session.refresh(ws2)

    raw_key = "review-block-key"
    session.add(ApiKey(
        label="rb-key",
        key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        workspace_id=ws1.id,
    ))
    # Run in ws2, review for that run
    session.add(Run(run_id="rb-ws2-run", team="DevTeam", request="x",
                    status="running", workspace_id=ws2.id))
    session.add(HitlReview(
        review_id="rb-rev-001", run_id="rb-ws2-run", agent_name="pm",
        artifact_json="{}", options_json='["approve","reject"]', status="pending",
    ))
    await session.commit()

    r = await client.post(
        "/reviews/rb-rev-001",
        json={"decision": "approve"},
        headers={"X-Api-Key": raw_key},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Fix 3: upsert_tickets_from_run batch SELECT (correctness, not just performance)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upsert_tickets_batch_creates_and_updates(session):
    """upsert_tickets_from_run handles both new and existing tickets in one call."""
    from sqlmodel import select
    from app.models.run import Run, Ticket
    from app.services.runs import upsert_tickets_from_run

    run = Run(run_id="batch-run", team="DevTeam", request="x", status="success")
    session.add(run)
    # Pre-existing ticket
    session.add(Ticket(ticket_id="BT1", run_id="batch-run", title="Old title"))
    await session.commit()

    state = {
        "tickets": [
            {"id": "BT1", "title": "Updated title", "priority": "high", "status": "open"},
            {"id": "BT2", "title": "Brand new ticket", "priority": "medium", "status": "open"},
        ],
        "prd": {"title": "Test PRD"},
    }

    count = await upsert_tickets_from_run(session, "batch-run", state)
    await session.commit()

    assert count == 2

    result = await session.exec(select(Ticket).where(Ticket.run_id == "batch-run"))
    tickets = {t.ticket_id: t for t in result.all()}
    assert tickets["BT1"].title == "Updated title"
    assert tickets["BT1"].priority == "high"
    assert tickets["BT2"].title == "Brand new ticket"


# ---------------------------------------------------------------------------
# Fix 4a: HITL_TIMEOUT_S env var is read from environment
# ---------------------------------------------------------------------------

def test_hitl_timeout_default():
    """Default HITL timeout is 3600s when env var not set."""
    import os
    os.environ.pop("HITL_TIMEOUT_S", None)
    import importlib
    import app.core.channel as ch_mod
    importlib.reload(ch_mod)
    assert ch_mod._REVIEW_TIMEOUT_S == 3600.0


def test_hitl_timeout_env_var(monkeypatch):
    """HITL_TIMEOUT_S env var overrides the default timeout."""
    monkeypatch.setenv("HITL_TIMEOUT_S", "300")
    import importlib
    import app.core.channel as ch_mod
    importlib.reload(ch_mod)
    assert ch_mod._REVIEW_TIMEOUT_S == 300.0
