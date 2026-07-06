"""Tests for GET /runs/{id}/artifacts and the /evals/ surface."""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.models.run import EvalRun, Run


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_artifacts_not_found(client: AsyncClient):
    r = await client.get("/runs/no-such-run/artifacts")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_artifacts_no_state(client: AsyncClient, session):
    session.add(Run(run_id="art-run-1", team="DevTeam", request="x", status="running"))
    await session.commit()

    r = await client.get("/runs/art-run-1/artifacts")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_artifacts_empty_state(client: AsyncClient, session):
    session.add(Run(
        run_id="art-run-2", team="DevTeam", request="x", status="success",
        state={"tickets": [], "prd": {}},
    ))
    await session.commit()

    r = await client.get("/runs/art-run-2/artifacts")
    assert r.status_code == 200
    data = r.json()
    assert data["run_id"] == "art-run-2"
    assert data["code_artifacts"] == []
    assert data["devops_artifacts"] == []
    assert data["doc_artifacts"] == []
    assert data["test_artifacts"] == []


@pytest.mark.asyncio
async def test_artifacts_returns_code(client: AsyncClient, session):
    state = {
        "code_artifacts": [{"file_path": "main.py", "content": "print('hi')"}],
        "test_artifacts": [{"file_path": "test_main.py", "content": "def test_hi(): pass"}],
    }
    session.add(Run(run_id="art-run-3", team="DevTeam", request="x", status="success", state=state))
    await session.commit()

    r = await client.get("/runs/art-run-3/artifacts")
    assert r.status_code == 200
    data = r.json()
    assert len(data["code_artifacts"]) == 1
    assert data["code_artifacts"][0]["file_path"] == "main.py"
    assert len(data["test_artifacts"]) == 1
    assert data["devops_artifacts"] == []


# ---------------------------------------------------------------------------
# Evals — list and detail (no actual pipeline execution)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evals_list_empty(client: AsyncClient):
    r = await client.get("/evals/")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_evals_list_running(client: AsyncClient, session):
    session.add(EvalRun(
        eval_id="ev-001",
        team="DevTeam",
        request="Build auth",
        name="auth",
        status="running",
    ))
    await session.commit()

    r = await client.get("/evals/")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["eval_id"] == "ev-001"
    assert rows[0]["status"] == "running"


@pytest.mark.asyncio
async def test_evals_list_status_filter(client: AsyncClient, session):
    session.add(EvalRun(eval_id="ev-done", team="DevTeam", request="x", status="done"))
    session.add(EvalRun(eval_id="ev-run", team="DevTeam", request="y", status="running"))
    await session.commit()

    r = await client.get("/evals/?status=done")
    rows = r.json()
    assert all(row["status"] == "done" for row in rows)
    ids = [row["eval_id"] for row in rows]
    assert "ev-done" in ids
    assert "ev-run" not in ids


@pytest.mark.asyncio
async def test_evals_get_not_found(client: AsyncClient):
    r = await client.get("/evals/no-such-eval-id")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_evals_get_detail(client: AsyncClient, session):
    report = {"passed": True, "overall_score": 0.9, "agent_scores": {}}
    session.add(EvalRun(
        eval_id="ev-detail",
        team="DevTeam",
        request="Build feature",
        name="feature",
        status="done",
        report=report,
        cost_usd=0.05,
        elapsed_ms=12500.0,
    ))
    await session.commit()

    r = await client.get("/evals/ev-detail")
    assert r.status_code == 200
    data = r.json()
    assert data["eval_id"] == "ev-detail"
    assert data["status"] == "done"
    assert data["report"]["passed"] is True
    assert data["cost_usd"] == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# Evals — upload report endpoint (POST /evals/report)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evals_upload_report(client: AsyncClient):
    r = await client.post("/evals/report", json={
        "team": "DevTeam",
        "request": "Build payment module",
        "name": "payments",
        "report": {"passed": True, "overall_score": 0.85, "agent_scores": {}},
        "elapsed_ms": 8000.0,
        "cost_usd": 0.12,
    })
    assert r.status_code == 201
    data = r.json()
    assert data["status"] == "done"
    assert data["team"] == "DevTeam"
    assert data["name"] == "payments"
    assert data["report"]["passed"] is True


@pytest.mark.asyncio
async def test_evals_upload_report_unknown_team(client: AsyncClient):
    r = await client.post("/evals/report", json={
        "team": "NoSuchTeam",
        "request": "x",
        "report": {},
    })
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Tickets — workspace check on PATCH /tickets/{id}/status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ticket_move_no_workspace_restriction(client: AsyncClient, session):
    """Without a workspace-scoped key (open mode), any ticket can be moved."""
    from app.models.run import Ticket
    session.add(Run(run_id="run-patch-ok", team="DevTeam", request="x", status="success"))
    session.add(Ticket(ticket_id="T-patch-ok", run_id="run-patch-ok", title="Ticket"))
    await session.commit()

    r = await client.patch("/tickets/T-patch-ok/status", json={"status": "done"})
    assert r.status_code == 200
    assert r.json()["status"] == "done"


@pytest.mark.asyncio
async def test_ticket_move_invalid_status(client: AsyncClient, session):
    from app.models.run import Ticket
    session.add(Run(run_id="run-bad-status", team="DevTeam", request="x", status="success"))
    session.add(Ticket(ticket_id="T-bad-status", run_id="run-bad-status", title="Ticket"))
    await session.commit()

    r = await client.patch("/tickets/T-bad-status/status", json={"status": "invalid_status"})
    assert r.status_code == 422
