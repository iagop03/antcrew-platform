"""Tests for Round 5 features:
- Workspace budget_exceeded flag (post-run enforcement)
- POST /runs/upload (--push-to bridge)
- repo_url validation in POST /run
- CustomTeam removed from default registry
- PATCH /workspaces/{id}/budget clears budget_exceeded
- GET /workspaces/{id}/spend includes budget_exceeded_flag
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.run import Run, Workspace


# ---------------------------------------------------------------------------
# budget_exceeded — computed from total_cost_usd, not a stored flag
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_budget_exceeded_defaults_false(client: AsyncClient):
    r = await client.post("/workspaces/", json={"name": "BudgetWS", "slug": "budget-ws-r5"})
    assert r.status_code == 201
    data = r.json()
    assert "budget_exceeded" in data
    assert data["budget_exceeded"] is False


# ---------------------------------------------------------------------------
# POST /runs/upload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upload_run_basic(client: AsyncClient):
    """POST /runs/upload creates a success run and returns 201."""
    r = await client.post("/runs/upload", json={
        "team": "DevTeam",
        "request": "Build JWT auth",
        "cost_usd": 0.05,
        "thread_id": "local-thread",
    })
    assert r.status_code == 201
    data = r.json()
    assert data["status"] == "success"
    assert data["team"] == "DevTeam"
    assert data["cost_usd"] == pytest.approx(0.05)
    assert data["run_id"]  # UUID assigned


@pytest.mark.asyncio
async def test_upload_run_unknown_team(client: AsyncClient):
    r = await client.post("/runs/upload", json={
        "team": "NonExistentTeam",
        "request": "Something",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_upload_run_empty_request(client: AsyncClient):
    r = await client.post("/runs/upload", json={
        "team": "DevTeam",
        "request": "   ",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_upload_run_with_state_upserts_tickets(client: AsyncClient, session):
    """Tickets in state.tickets are persisted when uploading a run."""
    from sqlmodel import select
    from app.models.run import Ticket

    r = await client.post("/runs/upload", json={
        "team": "DevTeam",
        "request": "Build auth",
        "state": {
            "tickets": [
                {"id": "UP-T1", "title": "Auth ticket", "priority": "high", "status": "open"},
            ],
            "prd": {"title": "Auth PRD"},
        },
    })
    assert r.status_code == 201
    run_id = r.json()["run_id"]

    # Verify ticket was persisted
    result = await session.exec(select(Ticket).where(Ticket.run_id == run_id))
    tickets = result.all()
    assert len(tickets) == 1
    assert tickets[0].ticket_id == "UP-T1"
    assert tickets[0].title == "Auth ticket"


@pytest.mark.asyncio
async def test_upload_run_appears_in_list(client: AsyncClient):
    """An uploaded run appears in GET /runs/ with status success."""
    r = await client.post("/runs/upload", json={
        "team": "ResearchTeam",
        "request": "Research competitors",
        "cost_usd": 0.12,
    })
    assert r.status_code == 201
    run_id = r.json()["run_id"]

    r2 = await client.get("/runs/")
    assert r2.status_code == 200
    ids = [run["run_id"] for run in r2.json()]
    assert run_id in ids


@pytest.mark.asyncio
async def test_upload_run_workspace_scoping(client: AsyncClient, session):
    """An uploaded run is scoped to the API key's workspace."""
    import hashlib
    from app.models.run import ApiKey

    ws = Workspace(name="Upload WS", slug="upload-ws-r5")
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    raw_key = "upload-ws-key-r5"
    session.add(ApiKey(
        label="upload-ws-key",
        key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        workspace_id=ws.id,
    ))
    await session.commit()

    r = await client.post(
        "/runs/upload",
        json={"team": "DevTeam", "request": "Scoped upload"},
        headers={"X-Api-Key": raw_key},
    )
    assert r.status_code == 201
    assert r.json()["workspace_id"] == ws.id


# ---------------------------------------------------------------------------
# repo_url validation in POST /run
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_repo_url_invalid_format(client: AsyncClient):
    """A malformed repo_url is rejected with 422."""
    r = await client.post("/run/", json={
        "team": "DevTeam",
        "request": "Build something",
        "repo_url": "not-a-valid-url",
    })
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_run_repo_url_valid_https_format(client: AsyncClient):
    """A valid HTTPS repo URL passes schema validation (run itself may fail — that's ok)."""
    r = await client.post("/run/", json={
        "team": "DevTeam",
        "request": "Build something",
        "repo_url": "https://github.com/anthropics/anthropic-sdk-python",
    })
    # 202 means the schema accepted it; the run may or may not start (no real git)
    # 422 from schema validation would mean the validator rejected it
    assert r.status_code != 422


@pytest.mark.asyncio
async def test_run_repo_url_ftp_rejected(client: AsyncClient):
    r = await client.post("/run/", json={
        "team": "DevTeam",
        "request": "Build something",
        "repo_url": "ftp://example.com/repo",
    })
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# CustomTeam not in default registry
# ---------------------------------------------------------------------------

def test_custom_team_not_in_default_registry():
    """CustomTeam is excluded from the default registry — requires ANTCREW_TEAMS."""
    from app.services.runner import _build_team_registry
    registry = _build_team_registry()
    assert "CustomTeam" not in registry


def test_antcrew_teams_can_add_custom_team(monkeypatch):
    """ANTCREW_TEAMS env var can still register a custom team by any name."""
    monkeypatch.setenv("ANTCREW_TEAMS", "myorg.teams.custom:MyCustomTeam")
    from app.services.runner import _build_team_registry
    registry = _build_team_registry()
    assert "MyCustomTeam" in registry
    assert "CustomTeam" not in registry  # default still absent


# ---------------------------------------------------------------------------
# PATCH /workspaces/{id}/budget clears budget_exceeded when budget is raised
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_budget_patch_resets_exceeded_flag(client: AsyncClient, session):
    """Raising the budget via PATCH results in budget_exceeded=False (computed from spend)."""
    ws = Workspace(name="OverBudget", slug="over-budget-r5", max_cost_usd=1.0, total_cost_usd=1.5)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    r = await client.patch(f"/workspaces/{ws.id}/budget", json={"max_cost_usd": 10.0})
    assert r.status_code == 200
    assert r.json()["budget_exceeded"] is False  # 1.5 < 10.0
    assert r.json()["max_cost_usd"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_budget_patch_clear_limit_resets_exceeded_flag(client: AsyncClient, session):
    """Clearing the budget (null) means budget_exceeded=False (no limit)."""
    ws = Workspace(name="ClearBudget", slug="clear-budget-r5", max_cost_usd=1.0, total_cost_usd=2.0)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    r = await client.patch(f"/workspaces/{ws.id}/budget", json={"max_cost_usd": None})
    assert r.status_code == 200
    assert r.json()["budget_exceeded"] is False  # no limit → never exceeded
    assert r.json()["max_cost_usd"] is None


# ---------------------------------------------------------------------------
# GET /workspaces/{id}/spend — exhausted computed from total_cost_usd
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spend_endpoint_includes_exhausted(client: AsyncClient, session):
    """GET /workspaces/{id}/spend returns exhausted=True when total >= max."""
    ws = Workspace(name="SpendWS", slug="spend-ws-r5", max_cost_usd=5.0, total_cost_usd=6.0)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    r = await client.get(f"/workspaces/{ws.id}/spend")
    assert r.status_code == 200
    data = r.json()
    assert "exhausted" in data
    assert data["exhausted"] is True
    assert "budget_exceeded_flag" not in data


@pytest.mark.asyncio
async def test_spend_endpoint_no_budget_no_exhausted(client: AsyncClient, session):
    ws = Workspace(name="NoBudgetWS", slug="no-budget-ws-r5")
    session.add(ws)
    session.add(Run(run_id="nbws-run-1", team="DevTeam", request="x",
                    status="success", cost_usd=99.99, workspace_id=None))
    await session.commit()
    await session.refresh(ws)

    r = await client.get(f"/workspaces/{ws.id}/spend")
    assert r.status_code == 200
    data = r.json()
    assert data["budget_usd"] is None
    assert data["exhausted"] is False
    assert data["remaining_usd"] is None
    assert data["run_count"] == 0  # run not in this workspace


# ---------------------------------------------------------------------------
# budget_exceeded flag — manual set + spend read-through
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workspace_budget_exceeded_readable_via_spend(client: AsyncClient, session):
    """Spend endpoint reflects exhausted=True when total >= max; raising limit makes it False."""
    ws = Workspace(
        name="ManualExceeded", slug="manual-exceeded-r5",
        max_cost_usd=1.0, total_cost_usd=2.0,
    )
    session.add(ws)
    await session.commit()
    await session.refresh(ws)

    r = await client.get(f"/workspaces/{ws.id}/spend")
    assert r.status_code == 200
    assert r.json()["exhausted"] is True

    # Raise the limit — now total < max so not exhausted
    r2 = await client.patch(f"/workspaces/{ws.id}/budget", json={"max_cost_usd": 100.0})
    assert r2.status_code == 200
    assert r2.json()["budget_exceeded"] is False

    r3 = await client.get(f"/workspaces/{ws.id}/spend")
    assert r3.json()["exhausted"] is False
