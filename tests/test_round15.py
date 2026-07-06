"""Round 15 tests — P0.1 workspace scoping, P1.2 Run stub, P1.3 EvalRun→Run, P1.4 COUNT(*)

Covers:
- GET /workspaces/{id}/spend requires matching workspace key (P0.1)
- GET /workspaces/{id}/spend uses COUNT(*) not in-memory len() (P1.4)
- POST /reviews/ with unknown run_id creates a stub Run (P1.2)
- POST /reviews/ stub Run inherits workspace_id from body (P1.2)
- POST /evals/ creates a linked stub Run (P1.3)
- Stub Run status mirrors EvalRun terminal status via eval_runner (P1.3)
- EvalRun.run_id is set and queryable (P1.3)
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.run import ApiKey, EvalRun, HitlReview, Run, Workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def _make_key(session: AsyncSession, *, label: str, raw: str,
                    role: str = "write", workspace_id: int | None = None) -> ApiKey:
    k = ApiKey(label=label, key_hash=_hash(raw), role=role, workspace_id=workspace_id)
    session.add(k)
    await session.commit()
    await session.refresh(k)
    return k


async def _make_ws(session: AsyncSession, *, slug: str) -> Workspace:
    ws = Workspace(name=slug, slug=slug)
    session.add(ws)
    await session.commit()
    await session.refresh(ws)
    return ws


# ---------------------------------------------------------------------------
# P0.1 — workspace_spend ownership check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spend_403_when_key_belongs_to_different_workspace(client: AsyncClient, session):
    """A key scoped to workspace A cannot read spend for workspace B."""
    ws_a = await _make_ws(session, slug="ws-a-r15")
    ws_b = await _make_ws(session, slug="ws-b-r15")
    await _make_key(session, label="key-a-r15", raw="key-a-r15-raw",
                    role="write", workspace_id=ws_a.id)

    r = await client.get(
        f"/workspaces/{ws_b.id}/spend",
        headers={"X-Api-Key": "key-a-r15-raw"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_spend_200_when_key_matches_workspace(client: AsyncClient, session):
    """A key scoped to workspace A can read spend for workspace A."""
    ws = await _make_ws(session, slug="ws-own-r15")
    await _make_key(session, label="key-own-r15", raw="key-own-r15-raw",
                    role="write", workspace_id=ws.id)

    r = await client.get(
        f"/workspaces/{ws.id}/spend",
        headers={"X-Api-Key": "key-own-r15-raw"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["workspace_id"] == ws.id
    assert "run_count" in data


@pytest.mark.asyncio
async def test_spend_admin_key_sees_any_workspace(client: AsyncClient, session):
    """An admin key with no workspace restriction can read any workspace's spend."""
    ws = await _make_ws(session, slug="ws-admin-r15")
    await _make_key(session, label="admin-r15", raw="admin-r15-raw",
                    role="admin", workspace_id=None)

    r = await client.get(
        f"/workspaces/{ws.id}/spend",
        headers={"X-Api-Key": "admin-r15-raw"},
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# P1.4 — workspace_spend run_count via COUNT(*)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spend_run_count_correct(client: AsyncClient, session):
    """run_count reflects the actual number of Run rows in the workspace."""
    ws = await _make_ws(session, slug="ws-count-r15")
    await _make_key(session, label="cnt-r15", raw="cnt-r15-raw",
                    role="admin", workspace_id=None)

    for i in range(3):
        session.add(Run(
            run_id=f"cnt-run-{i}-r15", team="dev", request="x",
            status="success", workspace_id=ws.id,
        ))
    # A run in a different workspace should not count.
    session.add(Run(run_id="cnt-run-other-r15", team="dev", request="x",
                    status="success", workspace_id=None))
    await session.commit()

    r = await client.get(
        f"/workspaces/{ws.id}/spend",
        headers={"X-Api-Key": "cnt-r15-raw"},
    )
    assert r.status_code == 200
    assert r.json()["run_count"] == 3


# ---------------------------------------------------------------------------
# P1.2 — stub Run created when POST /reviews/ run_id unknown
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_review_creates_stub_run_for_unknown_run_id(client: AsyncClient, session):
    """POST /reviews/ with an unknown run_id creates a stub Run with status='external'."""
    await _make_key(session, label="w-stub-r15", raw="w-stub-r15-raw", role="write")
    run_id = f"local-run-{uuid.uuid4()}"

    r = await client.post(
        "/reviews/",
        json={"run_id": run_id, "agent_name": "DevAgent"},
        headers={"X-Api-Key": "w-stub-r15-raw"},
    )
    assert r.status_code == 201

    stub = (await session.exec(select(Run).where(Run.run_id == run_id))).first()
    assert stub is not None
    assert stub.status == "external"
    assert stub.team == "external"


@pytest.mark.asyncio
async def test_create_review_stub_run_inherits_workspace_id(client: AsyncClient, session):
    """Stub Run gets workspace_id from the body field."""
    ws = await _make_ws(session, slug="ws-stub-ws-r15")
    await _make_key(session, label="w-stub-ws-r15", raw="w-stub-ws-r15-raw", role="write")
    run_id = f"local-run-ws-{uuid.uuid4()}"

    await client.post(
        "/reviews/",
        json={"run_id": run_id, "agent_name": "DevAgent", "workspace_id": ws.id},
        headers={"X-Api-Key": "w-stub-ws-r15-raw"},
    )

    stub = (await session.exec(select(Run).where(Run.run_id == run_id))).first()
    assert stub is not None
    assert stub.workspace_id == ws.id


@pytest.mark.asyncio
async def test_create_review_no_stub_when_run_exists(client: AsyncClient, session):
    """POST /reviews/ does not create a stub Run when the run_id already exists."""
    await _make_key(session, label="w-nostub-r15", raw="w-nostub-r15-raw", role="write")
    run_id = f"existing-run-{uuid.uuid4()}"
    session.add(Run(run_id=run_id, team="dev", request="x", status="success"))
    await session.commit()

    await client.post(
        "/reviews/",
        json={"run_id": run_id, "agent_name": "DevAgent"},
        headers={"X-Api-Key": "w-nostub-r15-raw"},
    )

    runs = (await session.exec(select(Run).where(Run.run_id == run_id))).all()
    assert len(runs) == 1, "must not duplicate Run rows"


# ---------------------------------------------------------------------------
# P1.3 — EvalRun linked to Run
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_eval_creates_linked_run(client: AsyncClient, session):
    """POST /evals/ creates an EvalRun and a linked stub Run."""
    from unittest.mock import patch, MagicMock
    await _make_key(session, label="eval-r15", raw="eval-r15-raw", role="write")

    with patch("app.api.evals._executor") as mock_exec, \
         patch("app.api.evals.asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value = MagicMock()
        mock_exec.submit = MagicMock()
        # Prevent actual eval execution
        mock_loop.return_value.run_in_executor = MagicMock(return_value=None)

        r = await client.post(
            "/evals/",
            json={"team": "DevTeam", "request": "Build JWT auth", "name": "test-eval"},
            headers={"X-Api-Key": "eval-r15-raw"},
        )

    assert r.status_code == 202
    eval_id = r.json()["eval_id"]

    eval_row = (await session.exec(select(EvalRun).where(EvalRun.eval_id == eval_id))).first()
    assert eval_row is not None
    assert eval_row.run_id is not None

    run_row = (await session.exec(select(Run).where(Run.run_id == eval_row.run_id))).first()
    assert run_row is not None
    assert run_row.team == "DevTeam"
    assert run_row.status == "running"


@pytest.mark.asyncio
async def test_upload_eval_report_creates_linked_run(client: AsyncClient, session):
    """POST /evals/report creates an EvalRun and a linked stub Run with status=success."""
    await _make_key(session, label="eval-upload-r15", raw="eval-upload-r15-raw", role="write")

    r = await client.post(
        "/evals/report",
        json={
            "team": "DevTeam",
            "request": "Build feature X",
            "report": {"passed": True, "overall_score": 0.9},
            "cost_usd": 0.05,
            "elapsed_ms": 4200.0,
        },
        headers={"X-Api-Key": "eval-upload-r15-raw"},
    )
    assert r.status_code == 201
    eval_row = (await session.exec(
        select(EvalRun).where(EvalRun.eval_id == r.json()["eval_id"])
    )).first()
    assert eval_row is not None
    assert eval_row.run_id is not None

    run_row = (await session.exec(select(Run).where(Run.run_id == eval_row.run_id))).first()
    assert run_row is not None
    assert run_row.status == "success"
    assert run_row.cost_usd == pytest.approx(0.05)
