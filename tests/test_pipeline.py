"""Tests for POST /run and GET /runs/:id/state."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.run import Run


# ---------------------------------------------------------------------------
# GET /run/teams
# ---------------------------------------------------------------------------

async def test_list_teams(client):
    r = await client.get("/run/teams")
    assert r.status_code == 200
    data = r.json()
    assert "teams" in data
    assert "DevTeam" in data["teams"]
    assert "ResearchTeam" in data["teams"]


# ---------------------------------------------------------------------------
# POST /run — validation
# ---------------------------------------------------------------------------

async def test_post_run_unknown_team(client):
    r = await client.post("/run/", json={"team": "UnknownTeam", "request": "do stuff"})
    assert r.status_code == 422


async def test_post_run_empty_request(client):
    r = await client.post("/run/", json={"team": "DevTeam", "request": "   "})
    assert r.status_code == 422


async def test_post_run_missing_fields(client):
    r = await client.post("/run/", json={"team": "DevTeam"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /run — successful dispatch
# ---------------------------------------------------------------------------

async def test_post_run_returns_202_with_run_id(client):
    with patch("app.api.pipeline.dispatch", new_callable=AsyncMock) as mock_dispatch:
        mock_dispatch.return_value = "abc123def456"
        r = await client.post("/run/", json={"team": "DevTeam", "request": "Build auth"})

    assert r.status_code == 202
    data = r.json()
    assert data["status"] == "accepted"
    assert data["run_id"] == "abc123def456"
    assert data["team"] == "DevTeam"
    assert "hint" in data


async def test_post_run_returns_202_even_without_run_id(client):
    """If dispatch times out before pipeline.start, run_id is None but still 202."""
    with patch("app.api.pipeline.dispatch", new_callable=AsyncMock) as mock_dispatch:
        mock_dispatch.return_value = None
        r = await client.post("/run/", json={"team": "ResearchTeam", "request": "Research AI"})

    assert r.status_code == 202
    assert r.json()["run_id"] is None


async def test_post_run_passes_thread_id(client):
    with patch("app.api.pipeline.dispatch", new_callable=AsyncMock) as mock_dispatch:
        mock_dispatch.return_value = "r1"
        await client.post(
            "/run/",
            json={"team": "DevTeam", "request": "x", "thread_id": "my-thread"},
        )
    mock_dispatch.assert_called_once_with("DevTeam", "x", "my-thread", max_cost_usd=None)


# ---------------------------------------------------------------------------
# GET /runs/:id/state
# ---------------------------------------------------------------------------

async def test_run_state_not_found(client):
    r = await client.get("/runs/nonexistent/state")
    assert r.status_code == 404


async def test_run_state_still_running(client, session):
    run = Run(run_id="r-running", team="DevTeam", request="x", status="running", state=None)
    session.add(run)
    await session.commit()

    r = await client.get("/runs/r-running/state")
    assert r.status_code == 404
    assert "running" in r.json()["detail"]


async def test_run_state_available(client, session):
    state = {
        "run_id": "r-done",
        "cost_usd": 0.05,
        "state": {"prd": {"title": "Auth"}, "tickets": []},
    }
    run = Run(run_id="r-done", team="DevTeam", request="x", status="success", state=state)
    session.add(run)
    await session.commit()

    r = await client.get("/runs/r-done/state")
    assert r.status_code == 200
    data = r.json()
    assert data["run_id"] == "r-done"
    assert data["state"]["prd"]["title"] == "Auth"


# ---------------------------------------------------------------------------
# runner helpers (unit tests, no HTTP)
# ---------------------------------------------------------------------------

def test_make_team_unknown_raises():
    from app.services.runner import _make_team
    with pytest.raises(ValueError, match="Unknown team"):
        _make_team("FakeTeam")


def test_available_teams_list():
    from app.services.runner import AVAILABLE_TEAMS
    assert "DevTeam" in AVAILABLE_TEAMS
    assert "FullStackTeam" in AVAILABLE_TEAMS
    assert "ResearchTeam" in AVAILABLE_TEAMS
    assert "ContentTeam" in AVAILABLE_TEAMS
